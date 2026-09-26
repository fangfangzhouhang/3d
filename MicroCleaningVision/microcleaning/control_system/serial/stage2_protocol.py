"""Stage 2 步进双轴口的文本协议 v0.3。不编码 MCV1/PUMP。

固件握手版本：
    HELLO → STEP_OK v0.3
双轴命令：
    MOVEXY <Nx> <FWD|REV> <Ny> <FWD|REV> → STEP2_START X=.. .. Y=.. ..
    READXY                              → STEP2 X=.. BX=.. Y=.. BY=..
"""

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
    # 单轴（PULSE/READ）字段
    steps: int | None = None
    direction: str | None = None
    busy: bool | None = None
    sent: int | None = None
    # 双轴扩展字段
    x_steps: int | None = None
    x_direction: str | None = None
    y_steps: int | None = None
    y_direction: str | None = None
    x_sent: int | None = None
    x_busy: bool | None = None
    y_sent: int | None = None
    y_busy: bool | None = None


_HELLO_REPLY = "STEP_OK v0.3"
_PULSE_REPLY = re.compile(r"^STEP_START N=(\d+) (FWD|REV)$")
_READ_REPLY = re.compile(r"^STEP_SENT=(\d+) BUSY=([01])(?: FREQ=\d+)?$")
_XY_START_REPLY = re.compile(r"^STEP2_START X=(\d+) (FWD|REV) Y=(\d+) (FWD|REV)$")
_XY_READ_REPLY = re.compile(r"^STEP2 X=(\d+) BX=([01]) Y=(\d+) BY=([01])$")

_DIRECTIONS = {"FWD", "REV"}


def _check_steps(steps: int) -> None:
    if not isinstance(steps, int) or isinstance(steps, bool) or steps <= 0 or steps > 20000:
        raise Stage2ProtocolError("BAD_STEPS", "脉冲数必须是 1 到 20000 的整数")


def _check_direction(direction: str) -> None:
    if direction not in _DIRECTIONS:
        raise Stage2ProtocolError("BAD_DIRECTION", "方向只能是 FWD 或 REV")


def _no_unsafe(line: str, *, reason_detail: str) -> None:
    if "MCV1" in line or "PUMP" in line:
        raise Stage2ProtocolError("UNSAFE_LINE", reason_detail)


def encode_hello() -> bytes:
    return b"HELLO\r\n"


def encode_read() -> bytes:
    return b"READ\r\n"


def encode_read_xy() -> bytes:
    return b"READXY\r\n"


def encode_stop() -> bytes:
    return b"STOP\r\n"


def encode_pulse(steps: int, direction: str) -> bytes:
    _check_steps(steps)
    _check_direction(direction)
    line = f"PULSE {steps} {direction}\r\n"
    _no_unsafe(line, reason_detail="禁止把喷水写进步进口")
    return line.encode("ascii")


def encode_move_xy(
    steps_x: int,
    dir_x: str,
    steps_y: int,
    dir_y: str,
) -> bytes:
    """双轴同时运动命令。任一轴步数可为 0（该轴不动），方向仍需合法。"""

    if not isinstance(steps_x, int) or isinstance(steps_x, bool) or not 0 <= steps_x <= 20000:
        raise Stage2ProtocolError("BAD_STEPS_X", "X 脉冲数必须是 0 到 20000 的整数")
    if not isinstance(steps_y, int) or isinstance(steps_y, bool) or not 0 <= steps_y <= 20000:
        raise Stage2ProtocolError("BAD_STEPS_Y", "Y 脉冲数必须是 0 到 20000 的整数")
    _check_direction(dir_x)
    _check_direction(dir_y)
    line = f"MOVEXY {steps_x} {dir_x} {steps_y} {dir_y}\r\n"
    _no_unsafe(line, reason_detail="禁止把喷水写进步进口")
    return line.encode("ascii")


def parse_stage2_reply(raw: bytes | str) -> Stage2Reply:
    if isinstance(raw, bytes):
        text = raw.decode("ascii", errors="strict").strip()
    else:
        text = raw.strip()
    if text.startswith("MCV1"):
        raise Stage2ProtocolError("WRONG_PROTOCOL", "收到喷水协议，当前口应是 Stage 2")
    if text == _HELLO_REPLY:
        return Stage2Reply("STEP_OK", text)
    if text.startswith("STEP_OK"):
        raise Stage2ProtocolError(
            "WRONG_VERSION",
            f"固件握手版本不符：{text}，需要 v0.3，请重新编译烧录",
        )
    if text == "STEP_STOPPED":
        return Stage2Reply("STEP_STOPPED", text)
    if text.startswith("ERR"):
        return Stage2Reply("ERR", text)

    xy_start = _XY_START_REPLY.match(text)
    if xy_start:
        return Stage2Reply(
            "STEP2_START",
            text,
            x_steps=int(xy_start.group(1)),
            x_direction=xy_start.group(2),
            y_steps=int(xy_start.group(3)),
            y_direction=xy_start.group(4),
        )

    xy_read = _XY_READ_REPLY.match(text)
    if xy_read:
        return Stage2Reply(
            "STEP2",
            text,
            x_sent=int(xy_read.group(1)),
            x_busy=xy_read.group(2) == "1",
            y_sent=int(xy_read.group(3)),
            y_busy=xy_read.group(4) == "1",
        )

    pulse = _PULSE_REPLY.match(text)
    if pulse:
        return Stage2Reply("STEP_START", text, steps=int(pulse.group(1)), direction=pulse.group(2))

    read = _READ_REPLY.match(text)
    if read:
        return Stage2Reply("STEP_SENT", text, sent=int(read.group(1)), busy=read.group(2) == "1")

    if text.startswith("SPEED="):
        return Stage2Reply("SPEED", text)
    raise Stage2ProtocolError("BAD_REPLY", text)
