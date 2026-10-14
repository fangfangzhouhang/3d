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
class DwellHop:
    """一个驻点的相对移动。空行表示就地喷，不另发 MOVEXY。"""

    index: int
    point_px: tuple[float, float]
    dispatch: Stage2Dispatch
    position_before: tuple[int, int] | None
    position_after: tuple[int, int] | None
    request: MotionRequest | None

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index, "point_px": [self.point_px[0], self.point_px[1]],
            "lines": list(self.dispatch.lines), "position_before": self.position_before,
            "position_after": self.position_after,
            "request_id": None if self.request is None else self.request.request_id,
        }


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
    dwells: tuple[DwellHop, ...] = ()
    motion_source: str = "center_point"

    def to_dict(self) -> dict[str, object]:
        return {
            "outbound": self.outbound.to_dict(), "returning": self.returning.to_dict(),
            "outbound_request": asdict(self.outbound_request), "return_request": asdict(self.return_request),
            "observation_position": self.observation_position, "execution_position": self.execution_position,
            "target_delta_steps": self.target_delta_steps, "offset_delta_steps": self.offset_delta_steps,
            "cycle_abs_steps": self.cycle_abs_steps, "reasons": self.reasons,
            "return_reference": "saved_original_overview", "offset_application_count": 1,
            "motion_source": self.motion_source, "dwells": [hop.to_dict() for hop in self.dwells],
        }


def build_cycle_geometry(
    plan: CleaningPlan, *, base: PathPlaceholderConfig, calibration: MotorCalibration | None,
    offset: NozzleOffset, observation_position: tuple[int, int] | None, task_id: str,
    used_abs_steps: tuple[int, int] = (0, 0), budget: int = STAGE2_RUN_STEP_CAP, real: bool = False,
) -> CycleGeometry:
    """复用 preview/dispatch，去程加一次偏移，回程回原观察位，预留整个任务预算。"""
    offset.validate(calibration, real=real)
    if not isinstance(budget, int) or isinstance(budget, bool) or not 1 <= budget <= STAGE2_RUN_STEP_CAP:
        raise ValueError(f"BUDGET_MUST_NOT_EXCEED_{STAGE2_RUN_STEP_CAP}")
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


def build_station_cycle_geometry(
    plan: CleaningPlan, *, base: PathPlaceholderConfig, calibration: MotorCalibration | None,
    offset: NozzleOffset, observation_position: tuple[int, int] | None, task_id: str,
    used_abs_steps: tuple[int, int] = (0, 0), budget: int = STAGE2_RUN_STEP_CAP, real: bool = False,
) -> CycleGeometry:
    """两个及以上驻点：第一跳加一次针头偏移，其后只走驻点之间的像素差。"""

    if len(plan.path_px) < 2:
        raise ValueError("COVERAGE_STATIONS_REQUIRE_AT_LEAST_TWO")
    offset.validate(calibration, real=real)
    if not isinstance(budget, int) or isinstance(budget, bool) or not 1 <= budget <= STAGE2_RUN_STEP_CAP:
        raise ValueError(f"BUDGET_MUST_NOT_EXCEED_{STAGE2_RUN_STEP_CAP}")
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in used_abs_steps):
        raise ValueError("USED_BUDGET_INVALID")
    if base.work.nozzle_offset_mm != (0.0, 0.0) or (calibration and calibration.nozzle_px is not None):
        raise ValueError("MULTIPLE_NOZZLE_COMPENSATIONS")
    if base.work.homography_3x3 is not None:
        raise ValueError("HOMOGRAPHY_REFERENCE_NOT_SUPPORTED_IN_V1")
    width, height = plan.image_size_px
    placeholders, _ = stage2_placeholders(base, (height, width), calibration)
    preview = build_path_preview(plan, placeholders=placeholders)
    legs = preview.motion.legs
    if len(legs) != len(plan.path_px):
        raise ValueError("COVERAGE_STATION_LEGS_MISMATCH")
    dx, dy = offset.scope_to_nozzle_delta_steps
    shift = _signed_dispatch(dx, dy, budget)
    cursor = observation_position
    hop_dispatches: list[Stage2Dispatch] = []
    for index, leg in enumerate(legs):
        step = _signed_dispatch(leg.steps_x, leg.steps_y, budget)
        if index == 0:
            step = _join(step, shift)
        hop_dispatches.append(step)
        net = _net(step.lines)
        cursor = None if cursor is None else (cursor[0] + net[0], cursor[1] + net[1])
    outbound = hop_dispatches[0]
    for extra in hop_dispatches[1:]:
        outbound = _join(outbound, extra)
    target_lines: list[str] = []
    for leg in legs:
        target_lines.extend(_signed_dispatch(leg.steps_x, leg.steps_y, budget).lines)
    target_net = _net(tuple(target_lines))
    net = _net(outbound.lines)
    returning = _signed_dispatch(-net[0], -net[1], budget)
    execution = None if observation_position is None else (observation_position[0] + net[0], observation_position[1] + net[1])
    cycle_abs = (outbound.transmit_abs_steps_x + returning.transmit_abs_steps_x,
                 outbound.transmit_abs_steps_y + returning.transmit_abs_steps_y)
    reasons = []
    if any(step.truncated for step in hop_dispatches) or returning.truncated:
        reasons.append("INCOMPLETE_DISPATCH")
    if preview.motion.out_of_travel:
        reasons.append("PATH_OUT_OF_TRAVEL")
    reference = calibration.ref if calibration else "mock://declared-stage2-geometry"
    geometry_hash = hashlib.sha256(json.dumps({"offset": offset.to_dict(), "work": placeholders.to_dict(),
        "motor": None if calibration is None else calibration.sha256}, sort_keys=True, allow_nan=False).encode()).hexdigest()
    warnings = () if calibration is None else calibration.warnings

    def request(dispatch, before):
        return MotionRequest(new_motion_request_id(), task_id, dispatch.lines, before, "image_center",
            reference, geometry_hash, warnings, dispatch.truncated)

    cursor = observation_position
    dwells = []
    for index, (point, step) in enumerate(zip(plan.path_px, hop_dispatches)):
        before = cursor
        hop_net = _net(step.lines)
        after = None if before is None else (before[0] + hop_net[0], before[1] + hop_net[1])
        dwells.append(DwellHop(index, (float(point[0]), float(point[1])), step, before, after,
            None if not step.lines else request(step, before)))
        cursor = after
    return CycleGeometry(preview, outbound, returning, request(outbound, observation_position),
        request(returning, execution), observation_position, execution, target_net, (dx, dy), cycle_abs,
        tuple(reasons), tuple(dwells), "coverage_stations")


def coverage_station_points(mask, parameters: dict, *, mask_kind: str = "segmented_stain"):
    """读取显式直径下的驻点像素。CoveragePlan 本身仍不可执行。"""

    from microcleaning.control_system.planning.coverage_model import Evidence, FootprintProfile, build_kernel
    from microcleaning.control_system.planning.coverage_planner import CoverageInput, CoveragePolicy, plan_coverage

    diameter = parameters.get("diameter")
    if diameter is None:
        return None
    profile = FootprintProfile(
        str(parameters.get("profile_id") or "workbench-virtual-circle-v1"), float(diameter),
        str(parameters.get("unit") or "px"),
        Evidence("assumed", str(parameters.get("evidence_ref") or "explicit-workbench-diameter")),
    )
    policy = CoveragePolicy(
        target_coverage=float(parameters.get("target_coverage", 0.95)),
        max_stations=int(parameters.get("max_stations", 64)),
        candidate_stride_px=int(parameters.get("candidate_stride_px", 2)),
    )
    model = build_kernel(profile, None)
    if not model.available:
        return None
    plan = plan_coverage(CoverageInput(mask, "execution", mask_kind, "cycle-pre-mask"), model, policy=policy)
    if plan.executable or plan.status not in {"COMPLETE", "PARTIAL"} or not plan.stations:
        return None
    return plan.points_px


def motion_plan_for_mask(mask, parameters, *, fallback_plan: CleaningPlan, mask_kind: str = "segmented_stain", **kwargs):
    """有可用驻点时走驻点几何；否则沿用原来的单点计划。"""

    points = None
    note = "本轮使用单点中心"
    if isinstance(parameters, dict) and parameters.get("diameter") is not None:
        try:
            points = coverage_station_points(mask, parameters, mask_kind=mask_kind)
        except Exception as exc:
            points = None
            note = f"覆盖驻点没有接入本轮移动：{type(exc).__name__}，改用单点中心"
        else:
            note = f"本轮按显式直径移动 {len(points)} 个覆盖驻点" if points else "没有可用的覆盖驻点，本轮改用单点中心"
    if not points:
        return build_cycle_geometry(fallback_plan, **kwargs), note
    import numpy as np
    height, width = mask.shape[:2]
    plan = CleaningPlan(
        CleaningStrategy.CENTER_POINT, "image_px", (int(width), int(height)), float(np.count_nonzero(mask)),
        tuple((float(x), float(y)) for x, y in points), tuple(range(len(points))), "explicit_coverage_stations",
    )
    if len(points) == 1:
        return replace(build_cycle_geometry(plan, **kwargs), motion_source="coverage_stations"), note
    return build_station_cycle_geometry(plan, **kwargs), note


def _signed_dispatch(x: int, y: int, budget: int) -> Stage2Dispatch:
    return single_move_dispatch(abs(x), "FWD" if x >= 0 else "REV", abs(y), "FWD" if y >= 0 else "REV", budget=budget)


def _join(left: Stage2Dispatch, right: Stage2Dispatch) -> Stage2Dispatch:
    return Stage2Dispatch(
        left.lines + right.lines,
        left.planned_abs_steps_x + right.planned_abs_steps_x,
        left.planned_abs_steps_y + right.planned_abs_steps_y,
        left.transmit_abs_steps_x + right.transmit_abs_steps_x,
        left.transmit_abs_steps_y + right.transmit_abs_steps_y,
        left.budget, left.truncated or right.truncated,
    )


def _net(lines: tuple[str, ...]) -> tuple[int, int]:
    deltas = [parse_movexy_line(line) for line in lines]
    return sum(x for x, _ in deltas), sum(y for _, y in deltas)
