"""工作台局部接口：主线程收事件，设备线程处理指令，取消不写串口。"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from queue import Empty, Queue
from threading import Event, Lock, local
from time import monotonic
from typing import Any
from uuid import uuid4


class OperationCancelled(RuntimeError):
    reason_code = "USER_CANCELLED"


class Cancellation:
    def __init__(self) -> None:
        self.event = Event()
        self.reason = "USER_CANCELLED"
        self.requested_at: str | None = None
        self._local = local()

    def cancel(self, reason: str = "USER_CANCELLED") -> None:
        if not self.event.is_set():
            self.reason = reason
            self.requested_at = datetime.now(timezone.utc).isoformat()
            self.event.set()

    def check(self) -> None:
        if self.event.is_set() and not getattr(self._local, "stopping", False):
            raise OperationCancelled(self.reason)

    @contextmanager
    def stopping(self):
        """仅原设备所有者的有界 STOP 收尾暂时免于取消中断。"""
        before = getattr(self._local, "stopping", False)
        self._local.stopping = True
        try:
            yield
        finally:
            self._local.stopping = before


@dataclass(frozen=True)
class WorkbenchEvent:
    kind: str
    payload: dict[str, Any]


class EventBus:
    def __init__(self) -> None:
        self.events: Queue[WorkbenchEvent] = Queue()
        self._frame_lock = Lock()
        self._frame = None

    def emit(self, kind: str, **payload) -> None:
        self.events.put(WorkbenchEvent(kind, deepcopy(payload)))

    def frame(self, image) -> None:
        # 实时帧只保留最新一张，慢 UI 不积累视频队列。
        with self._frame_lock:
            self._frame = image.copy()

    def latest_frame(self):
        with self._frame_lock:
            frame, self._frame = self._frame, None
        return frame

    def drain(self, limit: int = 100) -> list[WorkbenchEvent]:
        result = []
        for _ in range(limit):
            try:
                result.append(self.events.get_nowait())
            except Empty:
                break
        return result


class ConfirmationBroker:
    """回复绑定当前 request_id；旧回复、重复点击和取消后回复均无效。"""

    def __init__(self, bus: EventBus, cancel: Cancellation, timeout: float = 1200) -> None:
        self.bus, self.cancel, self.timeout = bus, cancel, timeout
        self._lock = Lock()
        self._pending: str | None = None
        self._choices: tuple[str, ...] | None = None
        self._answered = False
        self._answers: Queue[tuple[str, Any]] = Queue()

    def ask(self, prompt: str, *, facts: dict | None = None) -> bool:
        self.cancel.check()
        request_id = uuid4().hex
        with self._lock:
            if self._pending is not None:
                raise RuntimeError("CONFIRMATION_ALREADY_PENDING")
            self._pending = request_id
            self._choices = None
            self._answered = False
        self.bus.emit("confirmation", request_id=request_id, prompt=prompt, facts=facts or {})
        deadline = monotonic() + self.timeout
        try:
            while monotonic() < deadline:
                self.cancel.check()
                try:
                    identity, answer = self._answers.get(timeout=0.05)
                except Empty:
                    continue
                if identity == request_id:
                    self.cancel.check()
                    return answer
            self.bus.emit("log", message="确认等待已过期；本次动作未获批准。")
            return False
        finally:
            with self._lock:
                self._pending = None
                self._choices = None
            self.bus.emit("confirmation_closed", request_id=request_id)

    def ask_choice(self, prompt: str, *, choices: tuple[str, ...], facts: dict | None = None) -> dict:
        """复检路由单独表达；选择复洗本身不授权移动或喷水。"""
        if not choices or "pause" not in choices or any(c not in {"rewash", "next", "retake", "pause"} for c in choices):
            raise ValueError("INVALID_RECHECK_CHOICES")
        self.cancel.check()
        request_id = uuid4().hex
        with self._lock:
            if self._pending is not None:
                raise RuntimeError("CONFIRMATION_ALREADY_PENDING")
            self._pending, self._choices = request_id, choices
            self._answered = False
        self.bus.emit("recheck_choice", request_id=request_id, prompt=prompt, choices=choices, facts=facts or {})
        deadline = monotonic() + self.timeout
        try:
            while monotonic() < deadline:
                self.cancel.check()
                try:
                    identity, answer = self._answers.get(timeout=0.05)
                except Empty:
                    continue
                if identity == request_id:
                    self.cancel.check()
                    return answer
            self.bus.emit("log", message="复检选择等待已过期；保留当前记录并停止，不发送新动作。")
            return {"choice": "pause", "reason": "复检选择等待超时"}
        finally:
            with self._lock:
                self._pending, self._choices = None, None
            self.bus.emit("confirmation_closed", request_id=request_id)

    def reply(self, request_id: str, answer: Any, *, reason: str = "") -> bool:
        with self._lock:
            if self.cancel.event.is_set() or not self._pending or self._answered or request_id != self._pending:
                return False
            if self._choices is None:
                if type(answer) is not bool:
                    return False
            elif not isinstance(answer, str) or answer not in self._choices:
                return False
            self._answered = True
            self._answers.put((request_id, answer if self._choices is None else {"choice": answer, "reason": str(reason).strip()}))
            return True
