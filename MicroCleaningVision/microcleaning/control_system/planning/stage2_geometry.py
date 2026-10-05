"""Stage 2 的几何装配；旧单帧入口与新闭环共用。"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from microcleaning.control_system.planning.path_preview import PathPlaceholderConfig, PathPreview, build_path_preview
from microcleaning.control_system.planning.cleaning_plan import CleaningPlan, CleaningStrategy
from microcleaning.control_system.planning.stage2_axes import Stage2Dispatch, dispatch_motion, parse_movexy_line, single_move_dispatch, stage2_stepper
from microcleaning.control_system.safety.motion_gate import STAGE2_RUN_STEP_CAP, MotionRequest, new_motion_request_id
from microcleaning.control_system.planning.work_frame import MotorCalibration


def stage2_placeholders(
    base: PathPlaceholderConfig,
    image_shape: tuple[int, ...],
    calibration: MotorCalibration | None,
) -> tuple[PathPlaceholderConfig, str]:
    """复用原 Demo：当前位置作为起点，默认画面中心，旧入口可用 nozzle_px。"""
    height, width = image_shape[:2]
    start_px = (float(width // 2), float(height // 2))
    reference = "image_center"
    if calibration is not None and calibration.nozzle_px is not None:
        start_px = calibration.nozzle_px
        reference = "nozzle_px"
    work = replace(base.work, origin_px=start_px)
    if calibration is not None:
        work = replace(
            work,
            version=f"{base.work.version}+motor-calibration",
            mm_per_px=calibration.mm_per_px_x,
            mm_per_px_y=calibration.mm_per_px_y,
            scale_json_ref=calibration.ref,
        )
    config = PathPlaceholderConfig(
        work=work,
        stepper=stage2_stepper(),
        assumed_spray_width_mm=base.assumed_spray_width_mm,
        visit_start_px=start_px,
    )
    return config, reference


OFFSET_FRAME = "stage2_signed_steps_scope_to_nozzle"


@dataclass(frozen=True)
class NozzleOffset:
    """同一点从显微镜移至固定喷头时，移动载物台所需的有符号步数。"""
    version: str
    setup_id: str
    scope_to_nozzle_delta_steps: tuple[int, int] | None
    coordinate_frame: str = OFFSET_FRAME
    axes_confirmed: bool = False
    calibration_source: str | None = None
    uncertainty_steps: tuple[float, float] | None = None
    motor_calibration_sha256: str | None = None
    mock_only: bool = False

    def validate(self, calibration: MotorCalibration | None, *, real: bool) -> None:
        if self.coordinate_frame != OFFSET_FRAME or not self.version or not self.setup_id:
            raise ValueError("OFFSET_METADATA_INVALID")
        value = self.scope_to_nozzle_delta_steps
        if value is None:
            raise ValueError("NOZZLE_OFFSET_UNKNOWN")
        if len(value) != 2 or any(not isinstance(v, int) or isinstance(v, bool) for v in value):
            raise ValueError("OFFSET_REQUIRES_SIGNED_INTEGER_PAIR")
        if self.axes_confirmed is not True:
            raise ValueError("STAGE_AXIS_DIRECTIONS_UNCONFIRMED")
        if self.uncertainty_steps is None or len(self.uncertainty_steps) != 2 or any(
            not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0
            for v in self.uncertainty_steps
        ):
            raise ValueError("OFFSET_UNCERTAINTY_MISSING")
        if not self.calibration_source:
            raise ValueError("OFFSET_SOURCE_MISSING")
        if real and (self.mock_only or calibration is None or calibration.warnings):
            raise ValueError("REAL_MOTOR_CALIBRATION_REQUIRED")
        if real and self.motor_calibration_sha256 != calibration.sha256:
            raise ValueError("OFFSET_MOTOR_CALIBRATION_MISMATCH")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def load_nozzle_offset(path: str | Path) -> NozzleOffset:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("OFFSET_JSON_OBJECT_REQUIRED")
    delta = payload.get("scope_to_nozzle_delta_steps")
    uncertainty = payload.get("uncertainty_steps")
    return NozzleOffset(
        version=payload.get("version", ""), setup_id=payload.get("setup_id", ""),
        scope_to_nozzle_delta_steps=None if delta is None else tuple(delta),
        coordinate_frame=payload.get("coordinate_frame", ""),
        axes_confirmed=payload.get("axes_confirmed") is True,
        calibration_source=payload.get("calibration_source"),
        uncertainty_steps=None if uncertainty is None else tuple(uncertainty),
        motor_calibration_sha256=payload.get("motor_calibration_sha256"),
        mock_only=payload.get("mock_only") is True,
    )


@dataclass(frozen=True)
class CycleGeometry:
    preview: PathPreview
    outbound: Stage2Dispatch
    returning: Stage2Dispatch
    outbound_request: MotionRequest
    return_request: MotionRequest
    observation_position: tuple[int, int] | None
    execution_position: tuple[int, int] | None
    target_delta_steps: tuple[int, int]
    offset_delta_steps: tuple[int, int]
    cycle_abs_steps: tuple[int, int]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "outbound": self.outbound.to_dict(), "returning": self.returning.to_dict(),
            "outbound_request": asdict(self.outbound_request), "return_request": asdict(self.return_request),
            "observation_position": self.observation_position, "execution_position": self.execution_position,
            "target_delta_steps": self.target_delta_steps, "offset_delta_steps": self.offset_delta_steps,
            "cycle_abs_steps": self.cycle_abs_steps, "reasons": self.reasons,
            "return_reference": "saved_original_overview", "offset_application_count": 1,
        }


def build_cycle_geometry(
    plan: CleaningPlan, *, base: PathPlaceholderConfig, calibration: MotorCalibration | None,
    offset: NozzleOffset, observation_position: tuple[int, int] | None, task_id: str,
    used_abs_steps: tuple[int, int] = (0, 0), budget: int = STAGE2_RUN_STEP_CAP, real: bool = False,
) -> CycleGeometry:
    """复用 preview/dispatch，去程加一次偏移，回程回原观察位，预留整个任务预算。"""
    offset.validate(calibration, real=real)
    if not isinstance(budget, int) or isinstance(budget, bool) or not 1 <= budget <= STAGE2_RUN_STEP_CAP:
        raise ValueError("BUDGET_MUST_NOT_EXCEED_1600")
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in used_abs_steps):
        raise ValueError("USED_BUDGET_INVALID")
    if base.work.nozzle_offset_mm != (0.0, 0.0) or (calibration and calibration.nozzle_px is not None):
        raise ValueError("MULTIPLE_NOZZLE_COMPENSATIONS")
    if base.work.homography_3x3 is not None:
        raise ValueError("HOMOGRAPHY_REFERENCE_NOT_SUPPORTED_IN_V1")
    w, h = plan.image_size_px
    placeholders, _ = stage2_placeholders(base, (h, w), calibration)
    preview = build_path_preview(plan, placeholders=placeholders)
    target = dispatch_motion(preview.motion, budget=budget)
    dx, dy = offset.scope_to_nozzle_delta_steps
    shift = _signed_dispatch(dx, dy, budget)
    outbound = Stage2Dispatch(
        target.lines + shift.lines,
        target.planned_abs_steps_x + shift.planned_abs_steps_x,
        target.planned_abs_steps_y + shift.planned_abs_steps_y,
        target.transmit_abs_steps_x + shift.transmit_abs_steps_x,
        target.transmit_abs_steps_y + shift.transmit_abs_steps_y,
        budget, target.truncated or shift.truncated,
    )
    target_net = _net(target.lines)
    net = _net(outbound.lines)
    returning = _signed_dispatch(-net[0], -net[1], budget)
    execution = None if observation_position is None else (observation_position[0] + net[0], observation_position[1] + net[1])
    cycle_abs = (outbound.transmit_abs_steps_x + returning.transmit_abs_steps_x,
                 outbound.transmit_abs_steps_y + returning.transmit_abs_steps_y)
    reasons = []
    if plan.strategy is not CleaningStrategy.CENTER_POINT or len(plan.path_px) != 1:
        reasons.append("EXECUTION_STRATEGY_UNSUPPORTED_V1")
    if outbound.truncated or returning.truncated:
        reasons.append("INCOMPLETE_DISPATCH")
    if any(used + required > budget for used, required in zip(used_abs_steps, cycle_abs)):
        reasons.append("TASK_BUDGET_INCLUDES_RETURN_EXCEEDED")
    # 单次 dispatch 也不能超过上限，即使多段互相抵消。
    if outbound.planned_abs_steps_x > budget or outbound.planned_abs_steps_y > budget:
        reasons.append("OUTBOUND_BUDGET_EXCEEDED")
    if preview.motion.out_of_travel:
        reasons.append("PATH_OUT_OF_TRAVEL")
    reference = calibration.ref if calibration else "mock://declared-stage2-geometry"
    geometry_hash = hashlib.sha256(json.dumps({"offset": offset.to_dict(), "work": placeholders.to_dict(),
        "motor": None if calibration is None else calibration.sha256}, sort_keys=True, allow_nan=False).encode()).hexdigest()
    warnings = () if calibration is None else calibration.warnings
    def request(dispatch, before):
        return MotionRequest(new_motion_request_id(), task_id, dispatch.lines, before, "image_center",
            reference, geometry_hash, warnings, dispatch.truncated)
    return CycleGeometry(preview, outbound, returning, request(outbound, observation_position),
        request(returning, execution), observation_position, execution, target_net, (dx, dy), cycle_abs, tuple(reasons))


def _signed_dispatch(x: int, y: int, budget: int) -> Stage2Dispatch:
    return single_move_dispatch(abs(x), "FWD" if x >= 0 else "REV", abs(y), "FWD" if y >= 0 else "REV", budget=budget)


def _net(lines: tuple[str, ...]) -> tuple[int, int]:
    deltas = [parse_movexy_line(line) for line in lines]
    return sum(x for x, _ in deltas), sum(y for _, y in deltas)
