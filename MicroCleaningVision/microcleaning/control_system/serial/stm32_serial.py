"""NUCLEO 串口控制器（成员 C）。

默认只发送 PING/STATUS。把已批准的 ``PUMP_IN_PLACE`` 翻译成 ``MCV1|PUMP`` 必须同时满足：
SafetyDecision 为 ALLOW、令牌未使用，以及显式 ``arm_pump=True``（CLI 的 ``--arm-pump``）。
不发送 XY/MOVE。打开 COM 口不等于允许喷水，更不等于清洗有效。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from microcleaning.contracts import ActionRequest, ExecutionReceipt, SafetyDecision, SafetyOutcome
from microcleaning.control_system.safety.fixed_rule import MAX_IN_PLACE_DURATION_MS, PUMP_IN_PLACE
from microcleaning.control_system.safety.governor import ReplaySafetyLimits, request_digest
from microcleaning.control_system.serial.stm32_protocol import (
    STM32ProtocolError,
    STM32Response,
    encode_ping,
    encode_pump,
    encode_status,
    encode_stop,
    parse_response,
)
from microcleaning.ports import ControllerPort


SerialFactory = Callable[[], Any]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class STM32SerialController(ControllerPort):
    """把已批准的定点短喷翻译为 MCV1 文本；未武装时拒绝 PUMP。"""

    def __init__(
        self,
        *,
        port: str | None = None,
        baudrate: int = 115200,
        timeout: float = 2.0,
        arm_pump: bool = False,
        max_pump_duration_ms: int = MAX_IN_PLACE_DURATION_MS,
        serial_factory: SerialFactory | None = None,
        policy_version: str = ReplaySafetyLimits().version,
    ) -> None:
        if baudrate <= 0:
            raise ValueError("baudrate 必须是正整数")
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        if max_pump_duration_ms < 1:
            raise ValueError("max_pump_duration_ms 必须是正整数")
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.arm_pump = arm_pump
        self.max_pump_duration_ms = max_pump_duration_ms
        self._serial_factory = serial_factory
        self._policy_version = policy_version
        self._used_tokens: set[str] = set()
        self._connection: Any = None

    def ping(self) -> STM32Response:
        return self._command(encode_ping(), expected_kind="PONG")

    def status(self) -> STM32Response:
        return self._command(encode_status(), expected_kind="STATUS")

    def stop(self) -> STM32Response:
        """发送 MCV1|STOP。不要求 --arm-pump，也不发送 PUMP。"""

        ack = self._command(encode_stop(), expected_kind=None, reset_buffer=True)
        if ack.kind == "ERR":
            return ack
        if ack.kind != "ACK" or ack.action_id != "STOP":
            raise STM32ProtocolError(
                "UNEXPECTED_RESPONSE",
                f"STOP 期望 ACK|STOP，收到 {ack.kind}",
            )
        done = self._read_response()
        if done.kind != "DONE" or done.action_id != "STOP":
            raise STM32ProtocolError(
                "UNEXPECTED_RESPONSE",
                f"STOP 期望 DONE|STOP，收到 {done.kind}",
            )
        return done

    def execute(self, request: ActionRequest, decision: SafetyDecision) -> ExecutionReceipt:
        if not self.arm_pump:
            raise PermissionError("PUMP 被拒绝：控制器未武装，需要显式 --arm-pump")
        if request.primitive != PUMP_IN_PLACE:
            raise PermissionError("STM32SerialController 只翻译 PUMP_IN_PLACE，不发送 XY/MOVE")
        self._require_allow(request, decision)
        started = _now()
        try:
            status = self.status()
        except (TimeoutError, STM32ProtocolError, OSError) as exc:
            return ExecutionReceipt(
                request.action_id,
                "stm32_serial",
                started,
                _now(),
                request.target_centroid_mm,
                0,
                0.0,
                "UNKNOWN",
                "UNKNOWN",
                False,
                _error_code(exc),
            )
        if status.estop_active:
            return ExecutionReceipt(
                request.action_id,
                "stm32_serial",
                started,
                _now(),
                request.target_centroid_mm,
                0,
                0.0,
                "ESTOP",
                "ESTOP_ACTIVE",
                False,
                "ESTOP",
            )

        try:
            payload = encode_pump(
                request.action_id,
                request.duration_ms,
                max_duration_ms=self.max_pump_duration_ms,
            )
            ack = self._command(payload, expected_kind=None, reset_buffer=True)
            if ack.kind == "ERR":
                return ExecutionReceipt(
                    request.action_id,
                    "stm32_serial",
                    started,
                    _now(),
                    request.target_centroid_mm,
                    0,
                    0.0,
                    "ERR",
                    "ESTOP_ACTIVE" if ack.error_code == "ESTOP" else "REPORTED",
                    False,
                    ack.error_code,
                )
            if ack.kind != "ACK" or ack.action_id != request.action_id:
                return ExecutionReceipt(
                    request.action_id,
                    "stm32_serial",
                    started,
                    _now(),
                    request.target_centroid_mm,
                    0,
                    0.0,
                    ack.kind,
                    "UNKNOWN",
                    False,
                    "INVALID_ACK",
                )
            done = self._read_response()
        except (TimeoutError, STM32ProtocolError, OSError) as exc:
            return ExecutionReceipt(
                request.action_id,
                "stm32_serial",
                started,
                _now(),
                request.target_centroid_mm,
                0,
                0.0,
                "UNKNOWN",
                "UNKNOWN",
                False,
                _error_code(exc),
            )

        if done.kind == "ERR":
            return ExecutionReceipt(
                request.action_id,
                "stm32_serial",
                started,
                _now(),
                request.target_centroid_mm,
                0,
                0.0,
                "ERR",
                "REPORTED",
                False,
                done.error_code,
            )
        if done.kind != "DONE" or done.action_id != request.action_id:
            return ExecutionReceipt(
                request.action_id,
                "stm32_serial",
                started,
                _now(),
                request.target_centroid_mm,
                request.duration_ms if done.kind == "ACK" else 0,
                request.pressure,
                done.kind,
                "UNKNOWN",
                False,
                "DONE_TIMEOUT" if done.kind == "ACK" else "INVALID_DONE",
            )
        return ExecutionReceipt(
            request.action_id,
            "stm32_serial",
            started,
            _now(),
            request.target_centroid_mm,
            request.duration_ms,
            request.pressure,
            "DONE",
            "CLEAR",
            True,
        )

    def close(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        closer = getattr(connection, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass

    def _require_allow(self, request: ActionRequest, decision: SafetyDecision) -> None:
        if decision.outcome is not SafetyOutcome.ALLOW or not decision.approval_token:
            raise PermissionError("STM32SerialController 只接受 ALLOW 审批")
        if decision.action_id != request.action_id or decision.state_id != request.state_id:
            raise PermissionError("审批与动作或状态不匹配")
        if decision.request_digest != request_digest(request):
            raise PermissionError("动作在审批后被修改")
        if decision.policy_version != self._policy_version:
            raise PermissionError("无法识别安全策略版本")
        if not decision.expires_at or datetime.fromisoformat(decision.expires_at) <= datetime.now(timezone.utc):
            raise PermissionError("审批已经过期")
        if decision.approval_token in self._used_tokens:
            raise PermissionError("审批令牌不能重复使用")
        self._used_tokens.add(decision.approval_token)

    def _command(
        self,
        payload: bytes,
        *,
        expected_kind: str | None,
        reset_buffer: bool = True,
    ) -> STM32Response:
        connection = self._ensure_connection()
        if reset_buffer:
            reset = getattr(connection, "reset_input_buffer", None)
            if callable(reset):
                try:
                    reset()
                except Exception:
                    pass
        connection.write(payload)
        flush = getattr(connection, "flush", None)
        if callable(flush):
            flush()
        return self._read_response(expected_kind=expected_kind)

    def _read_response(self, *, expected_kind: str | None = None) -> STM32Response:
        connection = self._ensure_connection()
        raw = connection.readline()
        if not raw:
            raise TimeoutError("RESPONSE_TIMEOUT")
        parsed = parse_response(raw)
        if expected_kind is not None and parsed.kind != expected_kind:
            raise STM32ProtocolError(
                "UNEXPECTED_RESPONSE",
                f"期望 {expected_kind}，收到 {parsed.kind}",
            )
        return parsed

    def _ensure_connection(self) -> Any:
        if self._connection is not None:
            return self._connection
        if self._serial_factory is not None:
            self._connection = self._serial_factory()
            return self._connection
        if not self.port:
            raise PermissionError("未指定串口，拒绝打开 COM")
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError(
                "缺少 pyserial。确认需要实机通信后运行：\n"
                r".\.venv\Scripts\python.exe -m pip install -r requirements\control-serial.txt"
            ) from exc
        self._connection = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=self.timeout,
            write_timeout=self.timeout,
        )
        return self._connection


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "RESPONSE_TIMEOUT"
    if isinstance(exc, STM32ProtocolError):
        return exc.reason_code
    return "SERIAL_CONNECTION_FAILED"
