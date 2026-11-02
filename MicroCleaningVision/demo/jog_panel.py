"""触屏点动页的无头逻辑：模式门、人工确认桥与 JogController 装配。

本模块不 import tkinter。线程模型与工作台既有约定一致（参照
``workbench_events.ConfirmationBroker``）：``jog()`` 在 worker 线程执行，
人工确认回调在 worker 线程被调用，经队列 + Event 桥回 Tk 主线程弹窗；
请求单次有效、超时自动拒绝、迟到回复无效、取消后回复无效。

模式语义与 ``closed_loop_station`` 的三模式一致：Mock 全功能（假串口 +
独立账本）；Real 按现有 Stage2 资源路径接真实 JogController；仅连接
模式不装配控制器，整页点动与归零禁用。
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from uuid import uuid4

from demo.manual_jog import JogController
from microcleaning.control_system.planning.stage2_axes import parse_movexy_line
from microcleaning.control_system.planning.stage2_position import DEFAULT_POSITION_PATH, load_position, set_zero


CONFIRM_TIMEOUT_SECONDS = 20.0
STEPS_PER_MM = 320.0  # 1600 步/转 ÷ 5 mm/转；账本估算换算，不是实测位移

G0_WARNING = "G0 物理急停未过关：实机点动前须完成急停验收；本页操作不替代急停装置。"
CONNECT_ONLY_REASON = "仅连接模式禁用点动与归零：该模式只连接设备、预览与采图，禁止运动。"
RUNTIME_ACTIVE_REASON = "清洗任务进行中，设备由任务占用，点动禁用；任务结束后再用本页。"
BUSY_REASON = "上一次点动尚未结束，请等待结果。"
POSITION_UNKNOWN_REASON = "位置未知：先人工对位并归零，再点动。"


def jog_mode(args) -> str:
    """与 closed_loop_station 三模式同口径的判定；未知一律按 Mock 软件模式。"""
    if getattr(args, "connect_only", False):
        return "connect_only"
    if getattr(args, "real", False):
        return "real"
    return "mock"


def mode_banner(mode: str) -> str:
    return {
        "mock": "MOCK · 软件模拟：假串口点动，不连接真实电机。",
        "real": "REAL · 实物模式：点动经现有运动关卡与人工确认后才发送。",
        "connect_only": "真实设备·仅连接：本页只显示状态，点动与归零禁用。",
    }[mode]


def jog_block_reason(*, mode: str, runtime_active: bool, busy: bool, position_known: bool) -> str | None:
    """返回不可点动的中文原因；None 表示可点动。纯函数，供 UI 与测试共用。"""
    if mode == "connect_only":
        return CONNECT_ONLY_REASON
    if runtime_active:
        return RUNTIME_ACTIVE_REASON
    if busy:
        return BUSY_REASON
    if not position_known:
        return POSITION_UNKNOWN_REASON
    return None


def position_text(position) -> str:
    """账本位置的人读文本：步数 + 毫米估算；未知时明确说明。"""
    xy = position.xy()
    if xy is None:
        return "位置未知（账本无有效零点）"
    x, y = xy
    return f"X {x:,} 步（≈{x / STEPS_PER_MM:.2f} mm） · Y {y:,} 步（≈{y / STEPS_PER_MM:.2f} mm） · 账本估算"


def _axis_text(steps: int, direction: str, axis: str) -> str:
    if steps == 0:
        return f"{axis} 轴不移动"
    way = "正向" if direction == "FWD" else "反向"
    return f"{axis} 轴{way} {steps:,} 步（≈{steps / STEPS_PER_MM:.2f} mm）"


def plan_display_lines(request, plan: dict) -> list[str]:
    """确认弹窗展示行：方向/步数/毫米/位置前→后，全部来自 request 与 plan。"""
    lines: list[str] = []
    for line in request.lines:
        dx, dy = parse_movexy_line(line)
        lines.append(_axis_text(abs(dx), "FWD" if dx >= 0 else "REV", "X"))
        lines.append(_axis_text(abs(dy), "FWD" if dy >= 0 else "REV", "Y"))
    before, after = plan.get("position_before"), plan.get("position_after")
    if before is not None and after is not None:
        lines.append(
            f"账本位置：({before[0]:,}, {before[1]:,}) 步 → ({after[0]:,}, {after[1]:,}) 步（账本估算）"
        )
    lines.append("确认后才会打开串口发送；拒绝或超时都不会运动。")
    return lines


class JogConfirmBridge:
    """worker 线程 ↔ Tk 主线程的人工确认桥。

    worker 侧调用 ``ask()`` / ``ask_zero()`` 阻塞等待；主线程用
    ``poll_request()`` 非阻塞取待确认请求、渲染弹窗，再用 ``reply()``
    回填。同一时刻只允许一个待确认请求。
    """

    def __init__(self, timeout: float = CONFIRM_TIMEOUT_SECONDS) -> None:
        self.timeout = float(timeout)
        self.cancel_event = threading.Event()
        self._incoming: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._current: dict | None = None
        self._answer_event: threading.Event | None = None
        self._answer = False
        self._answered = False
        self._pending_ui: dict | None = None

    # ---- worker 线程侧 ----
    def ask(self, request, plan: dict) -> bool:
        return self._ask("motion", plan_display_lines(request, plan))

    def ask_zero(self) -> bool:
        return self._ask("zero", [
            "将把当前人工对位点记为零点（不是自动回零）。",
            "请确认载物台已由人对到参考点；记错零点后所有点动位置都是错的。",
        ])

    def _ask(self, kind: str, lines: list[str]) -> bool:
        if self.cancel_event.is_set():
            return False
        event = threading.Event()
        with self._lock:
            if self._current is not None:
                return False
            self._current = {"id": uuid4().hex, "kind": kind, "lines": list(lines),
                             "deadline": time.monotonic() + self.timeout}
            self._answer_event, self._answer, self._answered = event, False, False
        self._incoming.put(self._current)
        try:
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if self.cancel_event.is_set():
                    return False
                if event.wait(0.05):
                    return self._answer
            return False
        finally:
            with self._lock:
                self._current = None
                self._answer_event = None

    # ---- Tk 主线程侧 ----
    def poll_request(self) -> dict | None:
        """返回仍待确认的请求（供弹窗渲染）；已解决或过期返回 None。"""
        try:
            request = self._incoming.get_nowait()
            self._pending_ui = request
        except queue.Empty:
            request = getattr(self, "_pending_ui", None)
        with self._lock:
            current = self._current
        if current is None or request is None or request["id"] != current["id"]:
            self._pending_ui = None
            return None
        if self._answered:
            self._pending_ui = None
            return None
        return request

    def reply(self, answer: bool) -> bool:
        with self._lock:
            if self._current is None or self._answered or self.cancel_event.is_set():
                return False
            self._answer = bool(answer)
            self._answered = True
            event = self._answer_event
        if event is not None:
            event.set()
        return True

    def cancel_all(self) -> None:
        """「停止」的确认侧实现：拒绝待确认请求并置取消标志。

        由 JogController.stop() 经 confirm_canceller 注入调用，是停止在
        确认桥一侧的唯一动作；不声称能停已发出的运动。
        """
        self.reply(False)
        self.cancel_event.set()

    def reset_cancel(self) -> None:
        self.cancel_event.clear()


def build_jog_controller(args, *, confirm, confirm_canceller=None) -> JogController | None:
    """按模式装配 JogController；仅连接模式返回 None（整页禁用）。

    Real 的串口/标定/账本来源与 closed_loop_station 的 Stage2 路径一致：
    显式 --serial-port、--stage2-calibration、--position-path，不扫口。
    Mock 用项目替身串口与独立账本，并以仓库自带 firmware/mm_per_px.json
    过运动关卡的标定检查（串口是替身，不产生真实运动）。
    """
    mode = jog_mode(args)
    if mode == "connect_only":
        return None
    output_root = Path(getattr(args, "output_root", Path("output/closed_loop"))) / "manual_jog"
    if mode == "mock":
        from demo.closed_loop_fixture import MockF103Serial, MockFrames
        scenario = getattr(args, "mock_scenario", "success")
        serial = MockF103Serial(MockFrames(scenario), scenario)
        position_path = output_root / "mock_position.json"
        position_path.parent.mkdir(parents=True, exist_ok=True)
        if load_position(position_path).xy() is None:
            set_zero(position_path)
        calibration = Path(__file__).resolve().parents[1] / "firmware" / "mm_per_px.json"
        return JogController(stage2_calibration=calibration, position_path=position_path,
            output_root=output_root / "runs", serial_timeout=float(getattr(args, "serial_timeout", 2.0)),
            serial_factory=serial.factory, confirm=confirm, confirm_canceller=confirm_canceller)
    return JogController(stage2_calibration=getattr(args, "stage2_calibration", None),
        position_path=getattr(args, "position_path", None) or DEFAULT_POSITION_PATH,
        output_root=output_root / "runs", serial_port=getattr(args, "serial_port", None),
        baudrate=int(getattr(args, "baudrate", 115200)),
        serial_timeout=float(getattr(args, "serial_timeout", 2.0)), confirm=confirm,
        confirm_canceller=confirm_canceller)
