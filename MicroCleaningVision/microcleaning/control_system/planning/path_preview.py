"""把像素路线、假设毫米和步进预览串成可读对照，不申请硬件动作。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from microcleaning.control_system.planning.cleaning_plan import (
    CleaningPlan,
    CleaningPlanPolicy,
    CleaningStrategy,
    cleaning_plan_to_dict,
    plan_cleaning,
)
from microcleaning.control_system.planning.stepper_preview import (
    MotionPreview,
    StepperConfig,
    preview_motion,
    stepper_from_dict,
)
from microcleaning.control_system.planning.work_frame import (
    ASSUMED_WORK_FRAME,
    WorkFrameConfig,
    out_of_travel,
    pixel_to_assumed_mm,
    work_frame_from_dict,
)


@dataclass(frozen=True)
class PathPlaceholderConfig:
    work: WorkFrameConfig = field(default_factory=WorkFrameConfig)
    stepper: StepperConfig = field(default_factory=StepperConfig)
    assumed_spray_width_mm: float | None = None
    visit_start_px: tuple[float, float] = (0.0, 0.0)

    def validate(self) -> None:
        self.work.validate()
        self.stepper.validate()
        if self.assumed_spray_width_mm is not None and self.assumed_spray_width_mm <= 0:
            raise ValueError("assumed_spray_width_mm若填写必须大于0")

    def to_dict(self) -> dict[str, object]:
        return {
            "work": self.work.to_dict(),
            "stepper": self.stepper.to_dict(),
            "assumed_spray_width_mm": self.assumed_spray_width_mm,
            "visit_start_px": list(self.visit_start_px),
        }


@dataclass(frozen=True)
class PathWaypoint:
    index: int
    segment_id: int
    pump_on: bool
    x_px: float
    y_px: float
    x_mm: float
    y_mm: float

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "segment_id": self.segment_id,
            "pump_on": self.pump_on,
            "px": [self.x_px, self.y_px],
            "assumed_mm": [self.x_mm, self.y_mm],
            "coordinate_frame": ASSUMED_WORK_FRAME,
        }


@dataclass(frozen=True)
class PathPreview:
    plan: CleaningPlan
    waypoints: tuple[PathWaypoint, ...]
    placeholders: PathPlaceholderConfig
    motion: MotionPreview
    narrative: tuple[str, ...]
    evidence_boundary: str

    def to_dict(self) -> dict[str, object]:
        return {
            "cleaning_plan": cleaning_plan_to_dict(self.plan),
            "waypoints": [point.to_dict() for point in self.waypoints],
            "placeholders": self.placeholders.to_dict(),
            "motion": self.motion.to_dict(),
            "narrative": list(self.narrative),
            "feeds_action_request": False,
            "send_to_controller": False,
            "evidence_boundary": self.evidence_boundary,
        }


def load_path_placeholders(path: str | Path | None = None) -> PathPlaceholderConfig:
    if path is None:
        config = PathPlaceholderConfig()
        config.validate()
        return config
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return path_placeholders_from_dict(payload)


def path_placeholders_from_dict(payload: dict[str, Any]) -> PathPlaceholderConfig:
    if payload.get("feeds_action_request"):
        raise ValueError("path placeholders 禁止 feeds_action_request=true")
    work_payload = payload.get("work", payload)
    stepper_payload = payload.get("stepper", {})
    start = payload.get("visit_start_px", (0.0, 0.0))
    config = PathPlaceholderConfig(
        work=work_frame_from_dict(work_payload),
        stepper=stepper_from_dict(stepper_payload),
        assumed_spray_width_mm=(
            None if payload.get("assumed_spray_width_mm") is None else float(payload["assumed_spray_width_mm"])
        ),
        visit_start_px=(float(start[0]), float(start[1])),
    )
    config.validate()
    return config


def resolve_plan_policy(placeholders: PathPlaceholderConfig) -> CleaningPlanPolicy:
    placeholders.validate()
    raster_step_px = CleaningPlanPolicy().raster_step_px
    if placeholders.assumed_spray_width_mm is not None:
        raster_step_px = max(
            1,
            int(round(placeholders.assumed_spray_width_mm / placeholders.work.effective_mm_per_px())),
        )
    return CleaningPlanPolicy(
        raster_step_px=raster_step_px,
        start_px=placeholders.visit_start_px,
    )


def plan_and_preview(
    mask: Any,
    *,
    placeholders: PathPlaceholderConfig | None = None,
    policy: CleaningPlanPolicy | None = None,
) -> PathPreview:
    config = placeholders or PathPlaceholderConfig()
    chosen_policy = policy or resolve_plan_policy(config)
    return build_path_preview(plan_cleaning(mask, policy=chosen_policy), placeholders=config)


def build_path_preview(
    plan: CleaningPlan,
    *,
    placeholders: PathPlaceholderConfig | None = None,
) -> PathPreview:
    config = placeholders or PathPlaceholderConfig()
    config.validate()
    waypoints = _waypoints_from_plan(plan, config.work)
    exceeded = any(out_of_travel(point.x_mm, point.y_mm, config.work) for point in waypoints)
    motion = preview_motion(
        tuple((point.x_mm, point.y_mm) for point in waypoints),
        plan.segment_start_indices,
        config.stepper,
        start_mm=(0.0, 0.0),
        out_of_travel=exceeded,
    )
    narrative = format_path_narrative(plan, waypoints, config, motion)
    evidence = (
        "路径规则+假设毫米+步进对照；feeds_action_request=false；"
        "不发送MOVE/PUMP；不能写成已标定或清洗有效"
    )
    return PathPreview(plan, waypoints, config, motion, narrative, evidence)


def format_path_narrative(
    plan: CleaningPlan,
    waypoints: tuple[PathWaypoint, ...],
    placeholders: PathPlaceholderConfig,
    motion: MotionPreview,
) -> tuple[str, ...]:
    steps_x, source_x = placeholders.stepper.steps_per_mm("x")
    steps_y, source_y = placeholders.stepper.steps_per_mm("y")
    lines = [
        "========== 路径预览（软件仿真，不发 MOVE / 不发泵） ==========",
        f"1. 策略：{plan.strategy.value}；块顺序：{plan.visit_order}；坐标系：{plan.coordinate_frame}",
        f"2. 规则说明：{plan.reason}",
        (
            "3. 尺度占位："
            f"有效 mm/px={placeholders.work.effective_mm_per_px():.6f}（{placeholders.work.scale_source()}）；"
            f"原点px={placeholders.work.origin_px}；旋转={placeholders.work.rotation_deg}°；"
            f"flip_y={placeholders.work.flip_y}"
        ),
        (
            "4. 喷头相对显微镜光心偏移占位："
            f"{placeholders.work.nozzle_offset_mm} mm（以后只改这一项，不改走法）"
        ),
        (
            "5. 步进占位：X "
            f"{steps_x:.3f} 步/mm（{source_x}），Y {steps_y:.3f} 步/mm（{source_y}）；"
            f"公式 步/mm=(步/圈×细分)/导程；当前回零={placeholders.stepper.homed}"
        ),
    ]
    if plan.strategy is CleaningStrategy.NO_TARGET:
        lines.append("6. 没有污渍：空路径，电机对照表为空。")
        lines.append("7. 本预览不得写入 ActionRequest，也不得发往 STM32。")
        lines.append("==============================================================")
        return tuple(lines)

    segment_count = len(plan.segment_start_indices)
    lines.append(
        f"6. 路径点 {len(waypoints)} 个，分成 {segment_count} 段；段内泵开，段间与回零→首点泵关。"
    )
    preview_points = waypoints[:8]
    for point in preview_points:
        pump = "开" if point.pump_on else "关"
        lines.append(
            f"   点{point.index + 1} 段{point.segment_id + 1} 泵{pump}  "
            f"px=({point.x_px:.1f},{point.y_px:.1f})  "
            f"假设mm=({point.x_mm:.3f},{point.y_mm:.3f})"
        )
    if len(waypoints) > 8:
        lines.append(f"   … 其余 {len(waypoints) - 8} 个点见 summary.json 的 path_preview.waypoints")
    if plan.strategy is CleaningStrategy.CENTER_POINT:
        lines.append("   中心点策略：电机对照主要是空驶到点；到达后的短喷仍是 PUMP_IN_PLACE，本表不发 MOVE。")

    shown_legs = motion.legs[:6]
    for leg in shown_legs:
        pump = "开" if leg.pump_on else "关"
        lines.append(
            f"   段动作{leg.index + 1} [{leg.role}] 泵{pump}  "
            f"Δmm=({leg.delta_mm[0]:.3f},{leg.delta_mm[1]:.3f})  "
            f"步=({leg.steps_x},{leg.steps_y})  "
            f"转=({leg.revolutions_x:.4f},{leg.revolutions_y:.4f})"
        )
    if len(motion.legs) > 6:
        lines.append(f"   … 其余 {len(motion.legs) - 6} 条电机对照见 path_preview.motion.legs")
    lines.append(
        f"7. 合计：喷涂路程 {motion.total_spray_mm:.3f} mm，空驶 {motion.total_travel_mm:.3f} mm；"
        f"|步| X={motion.total_abs_steps_x} Y={motion.total_abs_steps_y}"
    )
    if motion.out_of_travel:
        lines.append("   警告：有点超出 travel_min/max 占位，仍不发 MOVE。")
    if not motion.homed:
        lines.append("   未 HOME：上面的步数只是对照，禁止当真机指令。")
    lines.append("8. feeds_action_request=false，send_to_controller=false。当前固件仍是 MCV1，没有 MOVE。")
    lines.append("==============================================================")
    return tuple(lines)


def draw_path_overlay(image: Any, preview: PathPreview) -> Any:
    cv2, _np = _load_cv2()
    overlay = image.copy()
    plan = preview.plan
    points = [(round(x), round(y)) for x, y in plan.path_px]
    segment_starts = set(plan.segment_start_indices)
    for end_index in range(1, len(points)):
        start = points[end_index - 1]
        end = points[end_index]
        if end_index in segment_starts:
            _dashed_line(overlay, start, end, (180, 180, 180), cv2)
        else:
            cv2.line(overlay, start, end, (0, 255, 0), 2)
    label_indices = set(_label_indices(plan))
    for index, point in enumerate(points):
        radius = 7 if index == 0 else 4
        color = (0, 0, 255) if index == 0 else (255, 0, 255)
        cv2.circle(overlay, point, radius, color, -1)
        if index in label_indices:
            _put_label(overlay, f"{index + 1}", (point[0] + 6, point[1] - 6), cv2)
    _put_label(overlay, f"{plan.strategy.value}", (4, 16), cv2)
    _put_label(overlay, "NO MOVE", (4, 32), cv2)
    return overlay


def _waypoints_from_plan(plan: CleaningPlan, frame: WorkFrameConfig) -> tuple[PathWaypoint, ...]:
    if not plan.path_px:
        return ()
    starts = plan.segment_start_indices
    waypoints: list[PathWaypoint] = []
    segment_id = -1
    start_set = set(starts)
    for index, (x_px, y_px) in enumerate(plan.path_px):
        if index in start_set:
            segment_id += 1
        x_mm, y_mm = pixel_to_assumed_mm(x_px, y_px, frame)
        waypoints.append(
            PathWaypoint(
                index=index,
                segment_id=max(segment_id, 0),
                pump_on=True,
                x_px=float(x_px),
                y_px=float(y_px),
                x_mm=x_mm,
                y_mm=y_mm,
            )
        )
    return tuple(waypoints)


def _label_indices(plan: CleaningPlan) -> tuple[int, ...]:
    count = len(plan.path_px)
    if count <= 16:
        return tuple(range(count))
    labels = set(plan.segment_start_indices)
    starts = list(plan.segment_start_indices) + [count]
    for begin, end in zip(starts, starts[1:]):
        if end - 1 >= begin:
            labels.add(end - 1)
    labels.add(0)
    labels.add(count - 1)
    return tuple(sorted(labels))


def _dashed_line(image: Any, start: tuple[int, int], end: tuple[int, int], color: tuple[int, int, int], cv2: Any) -> None:
    distance = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
    if distance < 1:
        return
    steps = max(1, int(distance / 7))
    for index in range(steps):
        if index % 2:
            continue
        t0 = index / steps
        t1 = min(1.0, (index + 1) / steps)
        p0 = (round(start[0] + (end[0] - start[0]) * t0), round(start[1] + (end[1] - start[1]) * t0))
        p1 = (round(start[0] + (end[0] - start[0]) * t1), round(start[1] + (end[1] - start[1]) * t1))
        cv2.line(image, p0, p1, color, 1)


def _put_label(image: Any, text: str, origin: tuple[int, int], cv2: Any) -> None:
    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)


def _load_cv2() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "路径叠加图需要NumPy/OpenCV；请安装requirements/perception-opencv.txt"
        ) from exc
    return cv2, np
