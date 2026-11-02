"""Headless touch-panel jogging through the existing Stage 2 execution path.

``None`` disables an axis; active axes use ``FWD`` or ``REV`` and the same
steps value. Millimetre labels assume 320 steps/mm, not measured travel.
Every attempt consumes arm(), including invalid input and human rejection.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Literal, Protocol
from uuid import uuid4

from demo import SerialFactory
from demo import motion_mode
from microcleaning.control_system.planning import stage2_position
from microcleaning.control_system.planning.stage2_axes import MAX_PULSE_STEPS, single_move_dispatch
from microcleaning.control_system.planning.stage2_position import DEFAULT_POSITION_PATH, Stage2Position
from microcleaning.control_system.planning.work_frame import load_motor_calibration
from microcleaning.control_system.safety.motion_gate import MotionRequest
from microcleaning.control_system.serial.resource_lease import ResourceLease, device_resources


STEP_PRESETS = (32, 160, 320, 1600, 3200, 6400)
STEP_LABELS = ("0.1 mm", "0.5 mm", "1 mm", "5 mm", "10 mm", "20 mm")  # nominal 320 steps/mm
# Presets are UI conveniences only: jog() accepts any positive step count.
# The sole cap is the firmware/motion-gate per-line limit MAX_PULSE_STEPS,
# rejected whole (never clipped) in jog() below. Panel cap removed 2026-11-02
# at the project lead's request (1600 steps per jog was too small).
Direction = Literal["FWD", "REV"]


class JogConfirm(Protocol):
    """The caller supplies a human decision for this exact request and plan."""

    def __call__(self, request: MotionRequest, plan: dict) -> bool: ...


@dataclass(frozen=True)
class JogResult:
    status: Literal["sent", "failed", "declined", "denied"]
    reason_codes: tuple[str, ...]
    message: str
    position: Stage2Position
    run_dir: Path | None = None
    motion: motion_mode.Stage2MotionOutcome | None = None


class JogController:
    """Synchronous control core; a future UI should call jog() in its worker.

    Configuration follows closed_loop_station: load_motor_calibration(JSON),
    an explicitly supplied port (never scanned), 115200 baud and 2 s timeout.
    A fake serial_factory can replace the port for headless software tests.
    No nozzle offset or image geometry is applied to manual pulse commands.
    Construction never establishes zero, arms motion, or opens a connection.
    """

    def __init__(
        self,
        *,
        stage2_calibration: str | Path | None = None,
        position_path: str | Path = DEFAULT_POSITION_PATH,
        output_root: str | Path = Path("output/manual_jog"),
        serial_port: str | None = None,
        baudrate: int = 115200,
        serial_timeout: float = 2.0,
        serial_factory: SerialFactory | None = None,
        confirm: JogConfirm | None = None,
        confirm_canceller=None,
    ) -> None:
        if isinstance(baudrate, bool) or not isinstance(baudrate, int) or baudrate <= 0:
            raise ValueError("baudrate must be a positive integer")
        if (isinstance(serial_timeout, bool) or not isinstance(serial_timeout, (int, float))
                or not math.isfinite(serial_timeout) or serial_timeout <= 0):
            raise ValueError("serial_timeout must be finite and positive")
        self._calibration = load_motor_calibration(stage2_calibration) if stage2_calibration is not None else None
        self._position_path = Path(position_path)
        self._output_root = Path(output_root)
        self._serial_port = serial_port
        self._baudrate = baudrate
        self._serial_timeout = serial_timeout
        self._serial_factory = serial_factory
        self._confirm = confirm
        self._confirm_canceller = confirm_canceller
        self._lock = Lock()
        self._armed = False
        self._busy = False

    @property
    def armed(self) -> bool:
        with self._lock:
            return self._armed

    def arm(self) -> bool:
        """Enable exactly one future attempt; cannot rearm an active operation."""
        with self._lock:
            if self._busy:
                return False
            self._armed = True
            return True

    def disarm(self) -> None:
        with self._lock:
            self._armed = False

    def stop(self) -> Stage2Position:
        """触屏「停止」按钮的单一入口；UI 只调这里，不散写停止逻辑。

        当前实现：解除武装，并经装配时注入的 confirm_canceller 取消正在
        等待人工确认的点动（确认被拒后执行链不会打开串口）。它不能停
        已经发出的运动——串口真停（STOP 令两轴与泵立即停）由 stop 分支
        替换本方法实现，UI 与本签名不变。返回当前账本位置；真停后账本
        失效时这里会返回未知位置，UI 已有未知态呈现。
        """
        self.disarm()
        if self._confirm_canceller is not None:
            self._confirm_canceller()
        return self.get_position()

    def get_position(self) -> Stage2Position:
        """Reload the existing ledger, including unknown/pending state."""
        return stage2_position.load_position(self._position_path)

    def set_zero(self, *, confirmed: bool = False) -> Stage2Position:
        """Record human-aligned zero using the existing ledger; never home.

        ``confirmed=True`` must come from the operator after alignment. This
        operation needs no serial connection and always clears motion arming.
        """
        with self._lock:
            self._armed = False
            if self._busy:
                raise PermissionError("JOG_BUSY")
            if confirmed is not True:
                raise PermissionError("POSITION_REFERENCE_NOT_CONFIRMED")
            self._busy = True
        try:
            with ResourceLease(device_resources(self._serial_port, self._position_path)):
                return stage2_position.set_zero(self._position_path)
        finally:
            self._finish()

    def jog(self, dx_dir: Direction | None, dy_dir: Direction | None, steps: int) -> JogResult:
        """Request one MOVEXY; reject over-firmware-cap values without clipping."""
        with self._lock:
            was_armed, self._armed = self._armed, False
            busy = self._busy
            if not busy:
                self._busy = True
        if busy:
            return self._denied("JOG_BUSY", "已有点动或归零操作进行中")
        run_dir = None
        try:
            if not was_armed:
                return self._denied("JOG_NOT_ARMED", "请先 arm()；一次武装只允许一次点动尝试")
            if not isinstance(steps, int) or isinstance(steps, bool) or steps <= 0:
                return self._denied("JOG_INVALID_STEPS", "步数必须是正整数")
            if steps > MAX_PULSE_STEPS:
                return self._denied("JOG_STEPS_OVER_FIRMWARE_CAP", f"单条 MOVEXY 固件上限 {MAX_PULSE_STEPS} 步；超限整条拒绝，不截断")
            if any(value is not None and value not in ("FWD", "REV") for value in (dx_dir, dy_dir)):
                return self._denied("JOG_INVALID_DIRECTION", "方向只能是 FWD/REV；未运动轴用 None")
            if dx_dir is None and dy_dir is None:
                return self._denied("JOG_NO_AXIS", "至少选择一个运动轴")
            with ResourceLease(device_resources(self._serial_port, self._position_path)):
                if self.get_position().xy() is None:
                    return self._denied("POSITION_UNKNOWN", "位置未知；先人工对位，再显式 set_zero(confirmed=True)")
                if not self._serial_port and self._serial_factory is None:
                    return self._denied("JOG_SERIAL_PORT_REQUIRED", "必须显式指定串口或测试工厂，不扫描端口")
                dispatch = single_move_dispatch(
                    steps if dx_dir is not None else 0, dx_dir or "FWD",
                    steps if dy_dir is not None else 0, dy_dir or "FWD",
                    budget=MAX_PULSE_STEPS,
                )
                run_id = f"jog_{uuid4().hex}"
                run_dir = self._output_root / run_id
                run_dir.mkdir(parents=True, exist_ok=False)
                motion = motion_mode._stage2_move(
                    run_id=run_id, run_dir=run_dir, dispatch=dispatch,
                    start_reference="image_center", calibration=self._calibration,
                    position_path=self._position_path,
                    motion_confirm=self._confirm_motion if self._confirm is not None else None,
                    arm_stage2_xy=was_armed, serial_port=self._serial_port,
                    baudrate=self._baudrate, serial_timeout=self._serial_timeout,
                    serial_factory=self._serial_factory,
                )
                if motion.status == "sent":
                    status, reasons, message = "sent", ("JOG_SENT",), "点动完成；位置为脉冲账本估算"
                elif motion.status == "failed":
                    status, reasons, message = "failed", (motion.error.reason_code,), "点动失败；请检查 Stage2 回执与位置状态"
                elif motion.status == "human_declined":
                    status, reasons, message = "declined", ("JOG_HUMAN_DECLINED",), "人工拒绝，未打开串口"
                else:
                    status = "denied"
                    reasons = motion.final_decision.reason_codes
                    message = f"Stage2 未放行：{motion.status}；{', '.join(reasons)}"
                return JogResult(status, reasons, message, motion.position_after, run_dir, motion)
        except PermissionError as exc:
            return JogResult("denied", (str(exc),), str(exc), self.get_position(), run_dir)
        except Exception as exc:
            # The existing executor owns STOP and position invalidation. Never
            # reopen a port or restore a stale position from this adapter.
            reason = getattr(exc, "reason_code", "JOG_EXECUTION_ERROR")
            return JogResult("failed", (reason,), str(exc), self.get_position(), run_dir)
        finally:
            self._finish()

    def _confirm_motion(self, request: MotionRequest, plan: dict) -> bool:
        # Strings/objects must not silently become a human approval through bool().
        return self._confirm(request, plan) is True

    def _denied(self, code: str, message: str) -> JogResult:
        return JogResult("denied", (code,), message, self.get_position())

    def _finish(self) -> None:
        with self._lock:
            self._armed = False
            self._busy = False
