"""把已经裁好的 MOVEXY 双轴命令发给 Stage 2 v0.3。两轴都停才进下一段。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from microcleaning.control_system.planning.stage2_axes import Stage2Dispatch
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


class Stage2SerialLink:
    """HELLO v0.3 成功后才发 MOVEXY。失败时发 STOP。不发送 MCV1|PUMP。"""

    def __init__(
        self,
        *,
        port: str | None = None,
        baudrate: int = 115200,
        timeout: float = 2.0,
        armed: bool = False,
        serial_factory: SerialFactory | None = None,
        read_limit: int = 40,
    ) -> None:
        if baudrate <= 0 or timeout <= 0:
            raise ValueError("波特率和超时必须为正")
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.armed = armed
        self._serial_factory = serial_factory
        self._read_limit = read_limit
        self._connection: Any = None

    def transmit(self, dispatch: Stage2Dispatch) -> Stage2TransmitResult:
        if not self.armed:
            raise PermissionError("Stage 2 未武装，拒绝打开发送")
        if not self.port and self._serial_factory is None:
            raise PermissionError("未指定串口，拒绝打开 COM")
        sent: list[str] = []
        replies: list[str] = []
        stopped = False
        try:
            hello = self._exchange(encode_hello())
            replies.append(hello.raw)
            if hello.kind != "STEP_OK":
                stopped = self._stop_into(replies)
                raise Stage2ProtocolError("NO_HELLO", hello.raw)
            for line in dispatch.lines:
                parts = line.split()  # MOVEXY <nx> <dx> <ny> <dy>
                x_steps = int(parts[1])
                y_steps = int(parts[3])
                payload = encode_move_xy(x_steps, parts[2], y_steps, parts[4])
                start = self._exchange(payload)
                replies.append(start.raw)
                if (
                    start.kind != "STEP2_START"
                    or start.x_steps != x_steps
                    or start.x_direction != parts[2]
                    or start.y_steps != y_steps
                    or start.y_direction != parts[4]
                ):
                    stopped = self._stop_into(replies)
                    raise Stage2ProtocolError("BAD_START", start.raw)
                idle = self._wait_idle_xy(max(x_steps, y_steps))
                replies.append(idle.raw)
                if idle.x_busy or idle.y_busy:
                    stopped = self._stop_into(replies)
                    raise TimeoutError("电机在读取上限内仍有轴 BUSY")
                sent.append(line)
        finally:
            self.close()
        return Stage2TransmitResult("STEP_OK v0.3", tuple(sent), tuple(replies), stopped)

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

    def _wait_idle_xy(self, steps: int) -> Stage2Reply:
        # 固件默认 500 Hz。按较长那一轴的步数等它走完，不要在脉冲结束前连续读满就判定失败。
        deadline = time.monotonic() + (max(steps, 1) / 500.0) + 0.5
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
        except (OSError, TimeoutError, Stage2ProtocolError):
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
