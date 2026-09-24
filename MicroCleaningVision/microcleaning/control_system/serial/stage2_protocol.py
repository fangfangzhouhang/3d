"""Stage 2 步进测试口的文本。不编码 MCV1，不打开串口。"""

from __future__ import annotations

import re
from dataclasses import dataclass


class Stage2ProtocolError(ValueError):
    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code


@dataclass(frozen=True)
class Stage2Reply:
    kind: str
    raw: str
    steps: int | None = None
    direction: str | None = None
    busy: bool | None = None
    sent: int | None = None


_PULSE_REPLY = re.compile(r"^STEP_START N=(\d+) (FWD|REV)$")
_READ_REPLY = re.compile(r"^STEP_SENT=(\d+) BUSY=([01])(?: FREQ=\d+)?$")


def encode_hello() -> bytes:
    return b"HELLO\r\n"


def encode_read() -> bytes:
    return b"READ\r\n"


def encode_stop() -> bytes:
    return b"STOP\r\n"


def encode_pulse(steps: int, direction: str) -> bytes:
    if not isinstance(steps, int) or isinstance(steps, bool) or steps <= 0 or steps > 20000:
        raise Stage2ProtocolError("BAD_STEPS", "脉冲数必须是 1 到 20000 的整数")
    if direction not in {"FWD", "REV"}:
        raise Stage2ProtocolError("BAD_DIRECTION", "方向只能是 FWD 或 REV")
    line = f"PULSE {steps} {direction}\r\n"
    if "MCV1" in line or "MOVE" in line:
        raise Stage2ProtocolError("UNSAFE_LINE", "禁止把喷水或 MOVE 写进步进口")
    return line.encode("ascii")


def parse_stage2_reply(raw: bytes | str) -> Stage2Reply:
    if isinstance(raw, bytes):
        text = raw.decode("ascii", errors="strict").strip()
    else:
        text = raw.strip()
    if text.startswith("MCV1"):
        raise Stage2ProtocolError("WRONG_PROTOCOL", "收到喷水协议，当前口应是 Stage 2")
    if text == "STEP_OK v0.2":
        return Stage2Reply("STEP_OK", text)
    if text == "STEP_STOPPED":
        return Stage2Reply("STEP_STOPPED", text)
    if text.startswith("ERR"):
        return Stage2Reply("ERR", text)
    pulse = _PULSE_REPLY.match(text)
    if pulse:
        return Stage2Reply("STEP_START", text, steps=int(pulse.group(1)), direction=pulse.group(2))
    read = _READ_REPLY.match(text)
    if read:
        return Stage2Reply("STEP_SENT", text, sent=int(read.group(1)), busy=read.group(2) == "1")
    if text.startswith("SPEED="):
        return Stage2Reply("SPEED", text)
    raise Stage2ProtocolError("BAD_REPLY", text)
