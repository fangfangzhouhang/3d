"""把已经裁好的 MOVEXY 双轴命令发给 Stage 2 v0.3。两轴都停才进下一段。

发送前必须持有运动关卡（``safety/motion_gate.py``）签发的一次性 ALLOW，
并且 ``armed=True``：两把钥匙缺一不可。
握手之后任何一步出错（读超时、串口异常、协议不符、Ctrl+C），都先发 STOP，
再抛出 ``Stage2TransmitError``，并带上已完成的行、在途的行和全部回复。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from microcleaning.contracts import SafetyDecision
from microcleaning.control_system.planning.stage2_axes import Stage2Dispatch
from microcleaning.control_system.safety.motion_gate import MotionRequest, require_motion_allow
from microcleaning.control_system.serial.stage2_protocol import (
    Stage2ProtocolError,
    Stage2Reply,
    encode_hello,
    encode_move_xy,
    encode_read_xy,
    encode_stop,
    parse_stage2_reply,
)


SerialFactory = Callable[[], Any]

DEFAULT_SPEED_HZ = 500
MIN_SPEED_HZ = 10
MAX_SPEED_HZ = 20000


def idle_wait_seconds(steps: int, speed_hz: int) -> float:
    """按较长那一轴的步数和固件脉冲频率估算走完要多久，再留 0.5 s 余量。"""

    return max(steps, 1) / float(speed_hz) + 0.5


@dataclass(frozen=True)
class Stage2TransmitResult:
    hello: str
    sent_lines: tuple[str, ...]
    replies: tuple[str, ...]
    stopped: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "hello": self.hello,
            "sent_lines": list(self.sent_lines),
            "replies": list(self.replies),
            "stopped": self.stopped,
            "protocol": "stage2-movexy-v0.3",
        }


class Stage2TransmitError(RuntimeError):
    """发送中途失败。抛出前已经尝试过 STOP。

    ``motion_attempted`` 为真表示至少写出过一条 MOVEXY，此时台面位置不再可信。
    """

    def __init__(
        self,
        reason_code: str,
        detail: str,
        *,
        sent_lines: tuple[str, ...],
        in_flight_line: str | None,
        replies: tuple[str, ...],
        stopped: bool,
        motion_attempted: bool,
    ) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail
        self.sent_lines = sent_lines
        self.in_flight_line = in_flight_line
        self.replies = replies
        self.stopped = stopped
        self.motion_attempted = motion_attempted

    def to_dict(self) -> dict[str, object]:
        return {
            "reason_code": self.reason_code,
            "detail": self.detail,
            "sent_lines": list(self.sent_lines),
            "in_flight_line": self.in_flight_line,
            "replies": list(self.replies),
            "stopped": self.stopped,
            "motion_attempted": self.motion_attempted,
            "protocol": "stage2-movexy-v0.3",
        }


class Stage2SerialLink:
    """HELLO v0.3 成功后才发 MOVEXY。失败时先发 STOP。不发送 MCV1|PUMP。

    可以改用已经打开的 ``connection``，或和喷水控制器一起绑定 ``F103SerialSession``。
    占用期间不再打开第二口；这条链路自己仍然只编码、只解析步进句子。
    """

    def __init__(
        self,
        *,
        port: str | None = None,
        baudrate: int = 115200,
        timeout: float = 2.0,
        armed: bool = False,
        serial_factory: SerialFactory | None = None,
        connection: Any = None,
        session: Any = None,
        read_limit: int = 40,
        speed_hz: int = DEFAULT_SPEED_HZ,
    ) -> None:
        if baudrate <= 0 or timeout <= 0:
            raise ValueError("波特率和超时必须为正")
        if not MIN_SPEED_HZ <= speed_hz <= MAX_SPEED_HZ:
            raise ValueError(f"speed_hz 必须在 {MIN_SPEED_HZ} 到 {MAX_SPEED_HZ} 之间，与固件 SPEED 范围一致")
        if connection is not None and session is not None:
            raise ValueError("connection 与 session 只能传一个")
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.armed = armed
        self.speed_hz = speed_hz
        self._serial_factory = serial_factory
        self._external_connection = connection
        self._session = session
        self._owns_connection = connection is None and session is None
        self._lease = object()
        self._read_limit = read_limit
        self._connection: Any = None

    @property
    def external_connection(self) -> Any:
        return self._external_connection

    @property
    def bound_session(self) -> Any:
        return self._session

    def bind_session(self, session: Any) -> None:
        """改走共享会话。调用方已经打开的连接仍由会话持有，这里不再关闭它。"""

        if self._session is not None and self._session is not session:
            raise RuntimeError("运动链路已绑定其他会话")
        self._session = session
        self._owns_connection = False

    def transmit(
        self,
        dispatch: Stage2Dispatch,
        *,
        request: MotionRequest,
        decision: SafetyDecision,
    ) -> Stage2TransmitResult:
        """只发已获运动关卡 ALLOW 的行。核对不过时抛 PermissionError，不打开 COM。"""

        if not self.armed:
            raise PermissionError("Stage 2 未武装，拒绝打开发送")
        if (
            self._session is None
            and self._external_connection is None
            and not self.port
            and self._serial_factory is None
        ):
            raise PermissionError("未指定串口，拒绝打开 COM")
        require_motion_allow(request, decision, dispatch.lines)
        sent: list[str] = []
        replies: list[str] = []
        in_flight: str | None = None
        motion_attempted = False
        try:
            try:
                self._clear_input()
                hello = self._exchange(encode_hello())
                replies.append(hello.raw)
                if hello.kind != "STEP_OK":
                    raise Stage2ProtocolError("NO_HELLO", hello.raw)
                for line in dispatch.lines:
                    parts = line.split()  # MOVEXY <nx> <dx> <ny> <dy>
                    x_steps = int(parts[1])
                    y_steps = int(parts[3])
                    payload = encode_move_xy(x_steps, parts[2], y_steps, parts[4])
                    in_flight = line
                    motion_attempted = True
                    start = self._exchange(payload)
                    replies.append(start.raw)
                    if (
                        start.kind != "STEP2_START"
                        or start.x_steps != x_steps
                        or start.x_direction != parts[2]
                        or start.y_steps != y_steps
                        or start.y_direction != parts[4]
                    ):
                        raise Stage2ProtocolError("BAD_START", start.raw)
                    idle = self._wait_idle_xy(max(x_steps, y_steps))
                    replies.append(idle.raw)
                    if idle.x_busy or idle.y_busy:
                        raise TimeoutError("电机在读取上限内仍有轴 BUSY")
                    # 固件对零步轴可能保留旧计数；只核对本次确实运动的轴。
                    if (x_steps and idle.x_sent != x_steps) or (y_steps and idle.y_sent != y_steps):
                        raise Stage2ProtocolError("INCOMPLETE_MOTION", idle.raw)
                    sent.append(line)
                    in_flight = None
            except (Exception, KeyboardInterrupt) as exc:
                stopped = self._stop_into(replies)
                raise Stage2TransmitError(
                    _reason_code(exc),
                    str(exc),
                    sent_lines=tuple(sent),
                    in_flight_line=in_flight,
                    replies=tuple(replies),
                    stopped=stopped,
                    motion_attempted=motion_attempted,
                ) from exc
        finally:
            self.close()
        return Stage2TransmitResult("STEP_OK v0.3", tuple(sent), tuple(replies), False)

    def close(self) -> None:
        session = self._session
        owns = self._owns_connection
        connection = self._connection
        self._connection = None
        if session is not None:
            session.release(self._lease)
        if not owns or connection is None:
            return
        closer = getattr(connection, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass

    def _clear_input(self) -> None:
        # 板子刚上电时会先吐横幅；握手前清掉，避免把横幅当成 HELLO 的回复。
        connection = self._ensure_connection()
        reset = getattr(connection, "reset_input_buffer", None)
        if callable(reset):
            reset()

    def _wait_idle_xy(self, steps: int) -> Stage2Reply:
        # 按较长那一轴的步数和当前脉冲频率等它走完，不要在脉冲结束前连续读满就判定失败。
        deadline = time.monotonic() + idle_wait_seconds(steps, self.speed_hz)
        last = Stage2Reply("STEP2", "", x_busy=True, y_busy=True)
        while time.monotonic() < deadline:
            last = self._exchange(encode_read_xy())
            if last.kind == "STEP2" and not last.x_busy and not last.y_busy:
                return last
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            time.sleep(min(0.05, remaining))
        return last

    def _stop_into(self, replies: list[str]) -> bool:
        try:
            stopped = self._exchange(encode_stop())
            replies.append(stopped.raw)
        except Exception:
            return False
        return stopped.kind == "STEP_STOPPED"

    def _exchange(self, payload: bytes) -> Stage2Reply:
        if b"MCV1" in payload or b"PUMP" in payload:
            raise Stage2ProtocolError("UNSAFE_LINE", "步进口拒绝喷水报文")
        connection = self._ensure_connection()
        connection.write(payload)
        flush = getattr(connection, "flush", None)
        if callable(flush):
            flush()
        raw = connection.readline()
        if not raw:
            raise TimeoutError("RESPONSE_TIMEOUT")
        return parse_stage2_reply(raw)

    def _ensure_connection(self) -> Any:
        if self._connection is not None:
            return self._connection
        if self._session is not None:
            self._owns_connection = False
            self._connection = self._session.acquire("stage2", self._lease)
            return self._connection
        if self._external_connection is not None:
            self._owns_connection = False
            self._connection = self._external_connection
            return self._connection
        from microcleaning.control_system.serial.f103_session import F103SessionBusy, com_occupied_by_session

        if self.port and com_occupied_by_session(self.port):
            raise F103SessionBusy(f"{self.port} 已由单串口会话占用，拒绝再打开")
        self._owns_connection = True
        if self._serial_factory is not None:
            self._connection = self._serial_factory()
            return self._connection
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


def _reason_code(exc: BaseException) -> str:
    if isinstance(exc, Stage2ProtocolError):
        return exc.reason_code
    if isinstance(exc, KeyboardInterrupt):
        return "INTERRUPTED"
    if isinstance(exc, TimeoutError):
        return "TIMEOUT"
    if isinstance(exc, OSError):
        return "SERIAL_IO_ERROR"
    return type(exc).__name__.upper()
