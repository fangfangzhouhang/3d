"""同一条已打开的 F103 串口上，先走完步进，成功之后才允许喷水。

STEP 与 MCV1 仍由各自的编码器和解码器处理。本模块只管理这一条物理连接：
同一时刻只有一个命令流占用它；切换协议前先读完已经到达的回复；清接收缓存时
留下另一协议尚未读完的回复。不发送 ``TO_NEEDLE`` / ``TO_SCOPE``，不放宽运动步数
上限，也不把 ``ACK`` 或 ``DONE`` 当成清洗成功。``DONE`` 只表示喷水输出流程结束。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from microcleaning.contracts import ExecutionReceipt, SafetyDecision, SafetyOutcome
from microcleaning.control_system.planning.stage2_axes import Stage2Dispatch
from microcleaning.control_system.safety.motion_gate import MotionRequest
from microcleaning.control_system.serial.stage2_link import Stage2SerialLink, Stage2TransmitError
from microcleaning.control_system.serial.stm32_serial import STM32SerialController


SerialFactory = Callable[[], Any]

_registry_lock = threading.Lock()
_LIVE: dict[str, Any] = {}


class F103SessionBusy(RuntimeError):
    """这条物理串口已经有一个命令流，另一个控制器不能再打开或占用它。"""


@dataclass(frozen=True)
class F103StepThenPumpResult:
    """一次「步进然后喷水」的软件回执。``output_finished`` 为真只表示收到了 MCV1 DONE。"""

    serial_opened: bool
    pump_called: bool
    pump_bytes: int
    output_finished: bool
    stopped: bool
    motion_reason: str | None = None
    receipt: ExecutionReceipt | None = None


def com_occupied_by_session(port: str | None) -> bool:
    """会话仍然占着这个 COM 时，别的控制器不能再打开它。"""

    if not port:
        return False
    with _registry_lock:
        holder = _LIVE.get(port)
        return holder is not None and holder.is_open


def classify_reply_line(raw: bytes | str) -> str:
    """区分步进回复、喷水回复和上电横幅。认不出的内容不当成任何一种成功。"""

    if isinstance(raw, bytes):
        try:
            text = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            return "noise"
    else:
        text = str(raw).strip()
    if not text:
        return "empty"
    if text.startswith("MCV1") or text.startswith("PUMP_"):
        return "mcv1"
    if text.startswith(("STEP", "ERR", "SPEED=")):
        return "step"
    return "noise"


class _GuardedConnection:
    """包住已经打开的串口。清缓存只丢掉横幅，协议回复先被拿走。"""

    def __init__(self, raw: Any) -> None:
        self.raw = raw
        self.preserved: list[bytes] = []
        self.finished: list[bytes] = []
        self.pump_bytes = 0
        self._partial = b""

    def write(self, data: bytes) -> int:
        payload = bytes(data)
        if payload.startswith(b"MCV1|PUMP|"):
            self.pump_bytes += len(payload)
        written = self.raw.write(data)
        return written if isinstance(written, int) else len(payload)

    def flush(self) -> None:
        flush = getattr(self.raw, "flush", None)
        if callable(flush):
            flush()

    def readline(self) -> bytes:
        return self.raw.readline()

    def reset_input_buffer(self) -> None:
        # 先把已经在缓冲区里的协议回复拿出来，再让底层丢掉剩余横幅。
        for line in self._take_lines():
            if classify_reply_line(line) in {"step", "mcv1"}:
                self.preserved.append(line)
        reset = getattr(self.raw, "reset_input_buffer", None)
        if callable(reset):
            reset()

    def finish(self, protocol: str) -> None:
        """切换前读完当前协议已经到达的回复，另一协议的回复留在 preserved。"""

        for line in self._take_lines():
            kind = classify_reply_line(line)
            if kind == protocol:
                self.finished.append(line)
            elif kind in {"step", "mcv1"}:
                self.preserved.append(line)

    def close_raw(self) -> None:
        closer = getattr(self.raw, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass

    def _take_lines(self) -> list[bytes]:
        raw = self.raw
        queue = getattr(raw, "_queue", None)
        if isinstance(queue, list):
            lines = [_as_bytes(item) for item in queue if item]
            queue.clear()
            return lines
        waiting = getattr(raw, "in_waiting", None)
        if not isinstance(waiting, int) or waiting <= 0:
            return []
        data = raw.read(waiting)
        if isinstance(data, str):
            data = data.encode("ascii", errors="strict")
        if not data:
            return []
        return self._split_complete(bytes(data))

    def _split_complete(self, data: bytes) -> list[bytes]:
        blob = self._partial + data
        lines: list[bytes] = []
        while blob:
            cut = len(blob)
            for marker in (b"\n", b"\r"):
                found = blob.find(marker)
                if found >= 0:
                    cut = min(cut, found + len(marker))
            if cut == len(blob) and b"\n" not in blob and b"\r" not in blob:
                break
            lines.append(blob[:cut])
            blob = blob[cut:]
        self._partial = blob
        return lines


class F103SerialSession:
    """一个物理串口。步进链路和喷水控制器按顺序租用，不能同时占用。"""

    def __init__(
        self,
        *,
        port: str | None = None,
        baudrate: int = 115200,
        timeout: float = 2.0,
        connection: Any = None,
        serial_factory: SerialFactory | None = None,
    ) -> None:
        if baudrate <= 0 or timeout <= 0:
            raise ValueError("波特率和超时必须为正")
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._external_raw = connection
        self._factory = serial_factory
        self._guard: _GuardedConnection | None = None
        self._owns_raw = False
        self._opened = False
        self._ever_opened = False
        self._closed = False
        self._owner: str | None = None
        self._lease: object | None = None
        self._running = False
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        return self._opened and not self._closed

    @property
    def pump_bytes(self) -> int:
        if self._guard is None:
            return 0
        return self._guard.pump_bytes

    @property
    def preserved_replies(self) -> tuple[bytes, ...]:
        if self._guard is None:
            return ()
        return tuple(self._guard.preserved)

    @property
    def finished_replies(self) -> tuple[bytes, ...]:
        if self._guard is None:
            return ()
        return tuple(self._guard.finished)

    def acquire(self, owner: str, lease: object) -> _GuardedConnection:
        """让一个命令流独占这条串口。同一个租约可以重入，另一个租约不行。"""

        with self._lock:
            if self._closed:
                raise F103SessionBusy("会话已关闭")
            if self._owner is not None and self._lease is not lease:
                raise F103SessionBusy(f"串口会话正由 {self._owner} 占用，拒绝 {owner} 同时进入")
            self._owner = owner
            self._lease = lease
            try:
                return self._open_locked()
            except Exception:
                self._owner = None
                self._lease = None
                raise

    def release(self, lease: object) -> None:
        with self._lock:
            if self._lease is not lease:
                return
            protocol = "step" if self._owner == "stage2" else "mcv1"
            guard = self._guard
            if guard is not None:
                guard.finish(protocol)
            self._owner = None
            self._lease = None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            guard = self._guard
            owns = self._owns_raw
            port = self.port
            self._opened = False
            self._owner = None
            self._lease = None
        with _registry_lock:
            if port and _LIVE.get(port) is self:
                del _LIVE[port]
        if owns and guard is not None:
            guard.close_raw()

    def run_step_then_pump(
        self,
        link: Stage2SerialLink,
        controller: STM32SerialController,
        *,
        dispatch: Stage2Dispatch,
        motion_request: MotionRequest,
        motion_decision: SafetyDecision,
        pump_request: Any,
        pump_decision: SafetyDecision,
    ) -> F103StepThenPumpResult:
        """两边都已武装且为 ALLOW 才打开串口。步进失败或未批准时不调用喷水。"""

        if self._closed:
            raise F103SessionBusy("会话已关闭")
        self._bind(link, controller)
        with self._lock:
            if self._running:
                raise F103SessionBusy("会话正在执行，拒绝并发进入")
            self._running = True
        try:
            if not _step_then_pump_authorized(link, controller, motion_decision, pump_decision):
                return _empty_result()
            try:
                link.transmit(dispatch, request=motion_request, decision=motion_decision)
            except PermissionError:
                return _empty_result(serial_opened=self._ever_opened, pump_bytes=self.pump_bytes)
            except Stage2TransmitError as exc:
                return F103StepThenPumpResult(
                    serial_opened=self._ever_opened,
                    pump_called=False,
                    pump_bytes=self.pump_bytes,
                    output_finished=False,
                    stopped=exc.stopped,
                    motion_reason=exc.reason_code,
                )
            try:
                receipt = controller.execute(pump_request, pump_decision)
            except PermissionError:
                return F103StepThenPumpResult(
                    serial_opened=self._ever_opened,
                    pump_called=True,
                    pump_bytes=self.pump_bytes,
                    output_finished=False,
                    stopped=False,
                )
            except Exception:
                return F103StepThenPumpResult(
                    serial_opened=self._ever_opened,
                    pump_called=True,
                    pump_bytes=self.pump_bytes,
                    output_finished=False,
                    stopped=self._stop_pump(controller),
                )
            output_finished = bool(receipt.success and receipt.controller_state == "DONE")
            stopped = False if output_finished else self._stop_pump(controller)
            return F103StepThenPumpResult(
                serial_opened=self._ever_opened,
                pump_called=True,
                pump_bytes=self.pump_bytes,
                output_finished=output_finished,
                stopped=stopped,
                receipt=receipt,
            )
        finally:
            with self._lock:
                self._running = False
            try:
                controller.close()
            finally:
                self.close()

    def _bind(self, link: Stage2SerialLink, controller: STM32SerialController) -> None:
        self._adopt_raw(link.external_connection)
        self._adopt_raw(controller.external_connection)
        _bind_endpoint(link, self)
        _bind_endpoint(controller, self)

    def _adopt_raw(self, raw: Any) -> None:
        if raw is None:
            return
        if self._external_raw is None:
            self._external_raw = raw
            return
        if self._external_raw is not raw:
            raise F103SessionBusy("会话只能绑定一个已经打开的串口连接")

    def _stop_pump(self, controller: STM32SerialController) -> bool:
        try:
            done = controller.stop()
        except Exception:
            return False
        return done.kind == "DONE" and done.action_id == "STOP"

    def _open_locked(self) -> _GuardedConnection:
        if self._closed:
            raise F103SessionBusy("会话已关闭")
        if self._opened and self._guard is not None:
            return self._guard
        if self.port and com_occupied_by_session(self.port):
            holder = _LIVE.get(self.port)
            if holder is not self:
                raise F103SessionBusy(f"{self.port} 已由单串口会话占用，拒绝再打开")
        raw, owns = self._create_raw()
        self._guard = _GuardedConnection(raw)
        self._owns_raw = owns
        self._opened = True
        self._ever_opened = True
        if self.port:
            with _registry_lock:
                holder = _LIVE.get(self.port)
                if holder is not None and holder is not self and holder.is_open:
                    self._opened = False
                    self._guard = None
                    if owns:
                        closer = getattr(raw, "close", None)
                        if callable(closer):
                            try:
                                closer()
                            except Exception:
                                pass
                    raise F103SessionBusy(f"{self.port} 已由单串口会话占用，拒绝再打开")
                _LIVE[self.port] = self
        return self._guard

    def _create_raw(self) -> tuple[Any, bool]:
        if self._external_raw is not None:
            return self._external_raw, False
        if self._factory is not None:
            return self._factory(), True
        if not self.port:
            raise PermissionError("未指定串口，拒绝打开 COM")
        return _open_pyserial(self.port, self.baudrate, self.timeout), True


def _bind_endpoint(endpoint: Any, session: F103SerialSession) -> None:
    current = endpoint.bound_session
    if current is not None and current is not session:
        raise F103SessionBusy("控制器已绑定其他串口会话")
    endpoint.bind_session(session)


def _step_then_pump_authorized(
    link: Stage2SerialLink,
    controller: STM32SerialController,
    motion_decision: SafetyDecision,
    pump_decision: SafetyDecision,
) -> bool:
    return bool(
        link.armed
        and controller.arm_pump
        and _decision_currently_allows(motion_decision)
        and _decision_currently_allows(pump_decision)
    )


def _decision_currently_allows(decision: SafetyDecision) -> bool:
    if decision.outcome is not SafetyOutcome.ALLOW or not decision.approval_token or not decision.expires_at:
        return False
    try:
        expires = datetime.fromisoformat(decision.expires_at)
    except ValueError:
        return False
    if expires.tzinfo is None:
        return False
    return datetime.now(timezone.utc) < expires


def _empty_result(*, serial_opened: bool = False, pump_bytes: int = 0) -> F103StepThenPumpResult:
    return F103StepThenPumpResult(
        serial_opened=serial_opened,
        pump_called=False,
        pump_bytes=pump_bytes,
        output_finished=False,
        stopped=False,
    )


def _as_bytes(item: Any) -> bytes:
    if isinstance(item, bytes):
        return item
    if isinstance(item, str):
        return item.encode("ascii")
    return bytes(item)


def _open_pyserial(port: str, baudrate: int, timeout: float) -> Any:
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError(
            "缺少 pyserial。确认需要实机通信后运行：\n"
            r".\.venv\Scripts\python.exe -m pip install -r requirements\control-serial.txt"
        ) from exc
    return serial.Serial(
        port=port,
        baudrate=baudrate,
        bytesize=8,
        parity="N",
        stopbits=1,
        timeout=timeout,
        write_timeout=timeout,
    )
