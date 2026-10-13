"""P3: pure RGB rendering of a non-executable coverage plan.

Pixel layers use the exact raster kernel and full target mask. Visit links are
geometric illustrations, not measured platform trajectories. Playback changes
only the displayed prefix; the immutable full plan stays intact.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from microcleaning.control_system.planning.coverage_model import (
    array_hash, canonical_json, immutable_array, integer,
)
from microcleaning.control_system.planning.coverage_metrics import (
    CoverageMetrics, evaluate_coverage, footprint_slice,
)
from microcleaning.control_system.planning.coverage_planner import CoveragePlan

COLORS = {
    "target": (238, 171, 62), "covered": (36, 184, 158),
    "uncovered": (239, 91, 114), "overlap": (151, 106, 222),
    "conservative": (97, 222, 237), "footprints": (88, 188, 245),
    "route": (255, 221, 105), "stations": (18, 57, 78),
}


@dataclass(frozen=True)
class RenderOptions:
    original: bool = True
    target: bool = True
    covered: bool = True
    uncovered: bool = True
    overlap: bool = False
    conservative: bool = False
    footprints: bool = True
    stations: bool = True
    route: bool = True
    alpha: float = 0.62

    def __post_init__(self) -> None:
        for name in COLORS:
            if not isinstance(getattr(self, name), bool):
                raise ValueError("LAYER_BOOLEAN_REQUIRED")
        if not isinstance(self.original, bool):
            raise ValueError("LAYER_BOOLEAN_REQUIRED")
        if isinstance(self.alpha, bool) or not isinstance(self.alpha, (float, int)):
            raise ValueError("LAYER_ALPHA_REQUIRED")
        if not math.isfinite(self.alpha) or not 0 <= self.alpha <= 1:
            raise ValueError("LAYER_ALPHA_OUT_OF_RANGE")


def validate_rgb(image_rgb: np.ndarray, shape_hw: tuple[int, int]) -> np.ndarray:
    if (not isinstance(image_rgb, np.ndarray) or image_rgb.dtype != np.uint8
            or image_rgb.shape != (*shape_hw, 3)):
        raise ValueError("ORIGINAL_RGB_IMAGE_SHAPE_REQUIRED")
    return immutable_array(image_rgb)


@dataclass(frozen=True, eq=False)
class CoverageVisualization:
    image_rgb: np.ndarray
    metrics: CoverageMetrics | None
    step_count: int
    metadata_json: str

    def to_dict(self) -> dict:
        return json.loads(self.metadata_json)


def render_coverage(image_rgb: np.ndarray, plan: CoveragePlan, *,
        options: RenderOptions = RenderOptions(), step_count: int | None = None) -> CoverageVisualization:
    if not isinstance(plan, CoveragePlan) or plan.executable or not isinstance(options, RenderOptions):
        raise ValueError("NON_EXECUTABLE_COVERAGE_PLAN_REQUIRED")
    original = validate_rgb(image_rgb, plan.input.mask.shape)
    count = len(plan.stations) if step_count is None else integer(step_count, "step_count")
    if count > len(plan.stations):
        raise ValueError("PLAYBACK_STEP_OUT_OF_RANGE")
    evaluation = plan.evaluation
    kernel = plan.model.kernel
    if count != len(plan.stations) and evaluation is not None and kernel is not None:
        # Conservative erosion belongs to P2. Do not duplicate its private
        # implementation: prefix conservative coverage is explicitly unknown.
        evaluation = evaluate_coverage(plan.input.mask, kernel, plan.points_px[:count])
    pixels = original.copy() if options.original else np.full_like(original, 30)

    def tint(mask: np.ndarray, name: str) -> None:
        pixels[mask] = np.rint((1 - options.alpha) * pixels[mask]
            + options.alpha * np.array(COLORS[name])).astype(np.uint8)

    if options.target:
        tint(plan.input.mask, "target")
    if evaluation is not None:
        for name, mask in (("covered", evaluation.covered_mask),
                ("uncovered", evaluation.uncovered_mask), ("overlap", evaluation.overlap_mask)):
            if getattr(options, name):
                tint(mask, name)
        if options.conservative and evaluation.conservative_covered_mask is not None:
            yy, xx = np.indices(plan.input.mask.shape)
            tint(evaluation.conservative_covered_mask & ((xx + yy) % 4 == 0), "conservative")
    if kernel is not None and options.footprints:
        outline = kernel.mask & ~(cv2.erode(kernel.mask.astype(np.uint8), np.ones((3, 3), np.uint8),
            borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool))
        for point in plan.points_px[:count]:
            (ys, xs), stamp = footprint_slice(plan.input.mask.shape, outline, kernel.anchor_px, point)
            pixels[ys, xs][stamp] = COLORS["footprints"]
    canvas = Image.fromarray(pixels)
    draw = ImageDraw.Draw(canvas)
    points = plan.points_px[:count]
    if options.route:
        previous = plan.policy.start_px
        for point in points:
            distance = math.dist(previous, point)
            if distance:
                for offset in range(0, math.ceil(distance), 8):
                    a, b = offset / distance, min(offset + 4, distance) / distance
                    segment = [(previous[0] + (point[0] - previous[0]) * t,
                        previous[1] + (point[1] - previous[1]) * t) for t in (a, b)]
                    draw.line(segment, fill=COLORS["route"], width=1)
            previous = point
    if options.stations:
        for index, (x, y) in enumerate(points, 1):
            draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=COLORS["stations"], outline="white", width=1)
            draw.text((x, y), str(index), fill="white", anchor="mm", font=ImageFont.load_default(size=10))
    result = immutable_array(np.asarray(canvas))
    metrics = None if evaluation is None else evaluation.metrics
    metadata = {"schema_version": "coverage-visualization-v1", "plan_id": plan.to_dict()["plan_id"],
        "step_count": count, "total_steps": len(plan.stations), "options": asdict(options),
        "display_metrics": None if metrics is None else metrics.to_dict(),
        "prefix_conservative_available": evaluation is not None and evaluation.conservative_covered_mask is not None,
        "legend_rgb": COLORS, "image_shape_hw": list(plan.input.mask.shape),
        "overlay_pixels_sha256": array_hash(result), "coordinate_frame": "image_px",
        "route_semantics": "straight_visit_links_not_actual_xy_trajectory",
        "executable": False, "send_to_controller": False}
    return CoverageVisualization(result, metrics, count, canonical_json(metadata))


def _font(size: int):
    for path in (Path("C:/Windows/Fonts/msyh.ttc"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")):
        if path.is_file():
            return ImageFont.truetype(str(path), size), path.name == "msyh.ttc"
    return ImageFont.load_default(size=size), False


def render_report(plan: CoveragePlan, view: CoverageVisualization) -> Image.Image:
    """Human-readable PNG with the same view's metrics and provenance banner."""
    if view.to_dict()["plan_id"] != plan.to_dict()["plan_id"]:
        raise ValueError("VISUALIZATION_PLAN_MISMATCH")
    title_font, chinese = _font(28)
    font, _ = _font(16)
    small, _ = _font(13)
    width = 1000
    image = Image.fromarray(view.image_rgb)
    factor = min(944 / image.width, 660 / image.height)
    viewport = (max(1, round(image.width * factor)), max(1, round(image.height * factor)))
    image = image.resize(viewport, Image.Resampling.NEAREST)
    height = 192 + image.height + 110
    report = Image.new("RGB", (width, height), "#edf3f1")
    draw = ImageDraw.Draw(report)
    title = "智能覆盖规划 | 仅模拟" if chinese else "Coverage planning | SIMULATION ONLY"
    draw.text((28, 18), title, font=title_font, fill="#173d3e")
    profile = plan.model.profile
    diameter = "unknown" if profile.diameter is None else f"{profile.diameter:g} {profile.unit}"
    draw.text((28, 65), f"{profile.profile_id}  |  diameter: {diameter}  |  {profile.evidence.status}", font=font, fill="#627c79")
    metrics = view.metrics
    rate = "unavailable" if metrics is None or metrics.geometric_coverage_rate is None else f"{metrics.geometric_coverage_rate:.1%}"
    omitted = "unavailable" if metrics is None else str(metrics.uncovered_area_px)
    area_label = "人工区域" if plan.input.mask_kind == "manual_region" else "目标 Mask"
    text = f"预计几何覆盖 {rate}    遗漏 {omitted} px    驻点 {view.step_count}/{len(plan.stations)}    {area_label}" if chinese else f"Geometric coverage {rate} | uncovered {omitted} px | dwell {view.step_count}/{len(plan.stations)}"
    draw.rounded_rectangle((28, 101, 972, 148), radius=10, fill="white")
    draw.text((44, 113), text, font=font, fill="#173d3e")
    warning = "预计几何覆盖不代表实际清除率；虚线仅为访问顺序；不会发送硬件动作。" if chinese else "Geometric coverage is not stain removal. Dashed visit links are not actual XY travel. No hardware actions."
    draw.text((28, 162), warning, font=small, fill="#9b5720")
    x, y = (width - image.width) // 2, 192
    report.paste(image, (x, y))
    labels = [("covered", "预计覆盖", "covered"), ("uncovered", "预计遗漏", "uncovered"),
        ("overlap", "重复覆盖", "overlap"), ("footprints", "覆盖核边界", "kernel outline")]
    for index, (key, zh, en) in enumerate(labels):
        lx = 28 + index * 230
        ly = y + image.height + 22
        draw.rounded_rectangle((lx, ly, lx + 16, ly + 16), radius=3, fill=COLORS[key])
        draw.text((lx + 24, ly - 2), zh if chinese else en, font=small, fill="#173d3e")
    reasons = ", ".join(plan.reason_codes) or plan.status
    draw.text((28, height - 47), f"{reasons} | {plan.input.mask_kind} | plan {plan.to_dict()['plan_id'][:16]}", font=small, fill="#627c79")
    draw.text((28, height - 25), "executable=false | preview only | source: " + profile.evidence.source_ref[:90], font=small, fill="#627c79")
    return report
