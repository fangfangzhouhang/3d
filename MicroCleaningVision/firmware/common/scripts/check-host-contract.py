"""Python 编码 → 真实 C 入口 → Python 解析；仅 stdin/stdout，无 COM。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from microcleaning.control_system.serial import stage2_protocol as xy
from microcleaning.control_system.serial import stm32_protocol as pump


def check(peer: Path) -> dict[str, object]:
    requests = [
        xy.encode_hello(), xy.encode_read_xy(),
        xy.encode_move_xy(3, "FWD", 2, "REV"), b"@XY 6\n",
        xy.encode_read_xy(), xy.encode_stop(),
        pump.encode_ping(), pump.encode_status(),
        pump.encode_pump("joint_1", 300), b"@TIME 300\n",
        pump.encode_pump("joint_1", 300), pump.encode_status(), pump.encode_stop(),
        pump.encode_pump("aborted_1", 300), pump.encode_stop(), pump.encode_status(),
        b"@ESTOP\n", pump.encode_status(),
        xy.encode_move_xy(1, "FWD", 0, "FWD"),
    ]
    run = subprocess.run(
        [str(peer.resolve())], input=b"".join(requests), capture_output=True, timeout=10,
    )
    if run.returncode:
        raise RuntimeError(f"C peer failed: {run.returncode}: {run.stderr.decode('utf-8', errors='replace')}")
    chunks = run.stdout.decode("ascii").replace("\r\n", "\n").split("@END\n")
    if len(chunks) != len(requests) + 1 or chunks[-1]:
        raise AssertionError("C peer framing mismatch")
    transcript = []
    for request, chunk in zip(requests, chunks):
        replies = chunk.splitlines()
        parsed = [pump.parse_response(line) if line.startswith("MCV1|")
                  else xy.parse_stage2_reply(line) for line in replies]
        transcript.append({"request": request.decode("ascii").strip(), "replies": replies,
                           "kinds": [reply.kind for reply in parsed]})
    expected = [
        ["STEP_OK"], ["STEP2"], ["STEP2_START"], [], ["STEP2"], ["STEP_STOPPED"],
        ["PONG"], ["STATUS"], ["ACK"], ["DONE"], ["DONE"], ["STATUS"], ["ACK", "DONE"],
        ["ACK"], ["ERR", "ACK", "DONE"], ["STATUS"], [], ["STATUS"], ["ERR"],
    ]
    if [row["kinds"] for row in transcript] != expected:
        raise AssertionError(json.dumps(transcript, ensure_ascii=False, indent=2))
    done_xy = xy.parse_stage2_reply(transcript[4]["replies"][0])
    assert (done_xy.x_sent, done_xy.y_sent, done_xy.x_busy, done_xy.y_busy) == (3, 2, False, False)
    for index in (11, 15):
        assert pump.parse_response(transcript[index]["replies"][0]).pump_active is False
    assert pump.parse_response(transcript[14]["replies"][0]).error_code == "STOPPED"
    assert pump.parse_response(transcript[17]["replies"][0]).estop_active is True
    return {"type": "offline_python_c_protocol", "hardware_used": False,
            "contracts_changed": False, "commands_checked": len(requests),
            "transcript": transcript,
            "limits": "Real C entry uses fake GPIO/time. Not COM, physical position, flow or combined host authorization."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("peer", type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.peer), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
