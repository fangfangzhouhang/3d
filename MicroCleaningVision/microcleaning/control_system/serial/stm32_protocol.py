"""NUCLEO-F401RE 与电脑端之间的最小文本协议。

这个模块只把结构化意图转换成 ASCII 文本，并解析 STM32 返回的文本；它不打开
COM 口，也不控制水泵。硬件组可以直接根据这些函数和配套文档实现同样的固件协议。

协议 v0.1 的目标是先消除软硬件之间的歧义：双方必须对版本、动作编号、时间单位
和完成/失败含义说同一种语言。移动平台命令留到真实机械参数到位后再增加。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


PROTOCOL_VERSION = "MCV1"
MIN_PUMP_DURATION_MS = 100
DEFAULT_MAX_PUMP_DURATION_MS = 500
MAX_LINE_BYTES = 128

_ACTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_ERROR_CODE_PATTERN = re.compile(r"^[A-Z0-9_]{1,32}$")


class STM32ProtocolError(ValueError):
    """协议文本无法被安全、唯一地解释。"""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code


@dataclass(frozen=True)
class STM32Response:
    """STM32 返回的一条已验证消息。"""

    kind: str
    raw: str
    action_id: str | None = None
    estop_active: bool | None = None
    pump_active: bool | None = None
    error_code: str | None = None


def encode_ping() -> bytes:
    """生成只检查通信是否在线的 PING。"""

    return _encode_parts(PROTOCOL_VERSION, "PING")


def encode_status() -> bytes:
    """生成只读取急停和水泵状态的 STATUS。"""

    return _encode_parts(PROTOCOL_VERSION, "STATUS")


def encode_stop() -> bytes:
    """生成停止请求；真实固件必须让它优先于普通动作。"""

    return _encode_parts(PROTOCOL_VERSION, "STOP")


def encode_pump(
    action_id: str,
    duration_ms: int,
    *,
    max_duration_ms: int = DEFAULT_MAX_PUMP_DURATION_MS,
) -> bytes:
    """生成一次有动作编号、有明确毫秒时长的水泵请求。

    这里只验证协议参数，不授予执行权限。主程序未来仍需通过 ``ControllerPort``
    和人工确认后才能把这条消息交给真实串口。
    """

    _validate_action_id(action_id)
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int):
        raise STM32ProtocolError("INVALID_DURATION", "duration_ms 必须是整数毫秒")
    if isinstance(max_duration_ms, bool) or not isinstance(max_duration_ms, int) or max_duration_ms < 1:
        raise STM32ProtocolError("INVALID_LIMIT", "max_duration_ms 必须是正整数")
    if not MIN_PUMP_DURATION_MS <= duration_ms <= max_duration_ms:
        raise STM32ProtocolError(
            "DURATION_OUT_OF_BOUNDS",
            f"duration_ms 必须在 {MIN_PUMP_DURATION_MS} 到 {max_duration_ms} 之间",
        )
    return _encode_parts(PROTOCOL_VERSION, "PUMP", action_id, str(duration_ms))


def parse_response(message: str | bytes) -> STM32Response:
    """解析一条以换行结束的 STM32 响应。"""

    raw = _normalise_line(message)
    parts = raw.split("|")
    if parts[0] != PROTOCOL_VERSION:
        raise STM32ProtocolError("UNSUPPORTED_VERSION", f"收到版本 {parts[0]!r}")
    if len(parts) < 2:
        raise STM32ProtocolError("MALFORMED_RESPONSE", "缺少消息类型")

    kind = parts[1]
    if kind == "PONG":
        _require_field_count(parts, 2)
        return STM32Response(kind="PONG", raw=raw)

    if kind == "STATUS":
        _require_field_count(parts, 4)
        values: dict[str, bool] = {}
        for field in parts[2:]:
            if "=" not in field:
                raise STM32ProtocolError("MALFORMED_STATUS", f"状态字段缺少等号: {field!r}")
            name, value = field.split("=", 1)
            if name in values or name not in {"ESTOP", "PUMP"} or value not in {"0", "1"}:
                raise STM32ProtocolError("MALFORMED_STATUS", f"无法识别状态字段: {field!r}")
            values[name] = value == "1"
        if set(values) != {"ESTOP", "PUMP"}:
            raise STM32ProtocolError("MALFORMED_STATUS", "必须同时返回 ESTOP 和 PUMP")
        return STM32Response(
            kind="STATUS",
            raw=raw,
            estop_active=values["ESTOP"],
            pump_active=values["PUMP"],
        )

    if kind in {"ACK", "DONE"}:
        _require_field_count(parts, 3)
        _validate_action_id(parts[2])
        return STM32Response(kind=kind, raw=raw, action_id=parts[2])

    if kind == "ERR":
        _require_field_count(parts, 4)
        _validate_action_id(parts[2])
        if not _ERROR_CODE_PATTERN.fullmatch(parts[3]):
            raise STM32ProtocolError("INVALID_ERROR_CODE", f"错误码不合法: {parts[3]!r}")
        return STM32Response(kind="ERR", raw=raw, action_id=parts[2], error_code=parts[3])

    raise STM32ProtocolError("UNKNOWN_RESPONSE", f"未知消息类型 {kind!r}")


def _encode_parts(*parts: str) -> bytes:
    line = "|".join(parts) + "\n"
    try:
        payload = line.encode("ascii")
    except UnicodeEncodeError as exc:
        raise STM32ProtocolError("NON_ASCII_MESSAGE", "协议只允许 ASCII 字符") from exc
    if len(payload) > MAX_LINE_BYTES:
        raise STM32ProtocolError("MESSAGE_TOO_LONG", f"消息不得超过 {MAX_LINE_BYTES} 字节")
    return payload


def _normalise_line(message: str | bytes) -> str:
    if isinstance(message, bytes):
        try:
            text = message.decode("ascii")
        except UnicodeDecodeError as exc:
            raise STM32ProtocolError("NON_ASCII_MESSAGE", "响应不是 ASCII 文本") from exc
    elif isinstance(message, str):
        text = message
    else:
        raise STM32ProtocolError("INVALID_MESSAGE_TYPE", "响应必须是 str 或 bytes")

    if len(text.encode("utf-8")) > MAX_LINE_BYTES:
        raise STM32ProtocolError("MESSAGE_TOO_LONG", f"响应不得超过 {MAX_LINE_BYTES} 字节")
    raw = text.rstrip("\r\n")
    if not raw:
        raise STM32ProtocolError("EMPTY_RESPONSE", "响应为空")
    if "\r" in raw or "\n" in raw:
        raise STM32ProtocolError("MULTIPLE_RESPONSES", "一次只能解析一条响应")
    return raw


def _validate_action_id(action_id: str) -> None:
    if not isinstance(action_id, str) or not _ACTION_ID_PATTERN.fullmatch(action_id):
        raise STM32ProtocolError(
            "INVALID_ACTION_ID",
            "action_id 只能包含 1 到 32 个字母、数字、下划线或连字符",
        )


def _require_field_count(parts: list[str], expected: int) -> None:
    if len(parts) != expected:
        raise STM32ProtocolError(
            "MALFORMED_RESPONSE",
            f"{parts[1] if len(parts) > 1 else 'UNKNOWN'} 应有 {expected} 个字段，实际为 {len(parts)}",
        )
