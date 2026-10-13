"""Exact discrete footprint unions and geometric metrics; no image or file I/O."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from collections.abc import Iterable

import numpy as np

from microcleaning.control_system.planning.coverage_model import (
    FootprintKernel, array_hash, binary_mask, immutable_array, integer,
)

MAX_CANVAS_CELLS = 4_000_000
MAX_EVALUATION_POINTS = 4096


@dataclass(frozen=True)
class CoverageMetrics:
    target_area_px: int
    covered_area_px: int
    uncovered_area_px: int
    geometric_coverage_rate: float | None
    footprint_area_px: int
    outside_target_area_px: int
    outside_image_area_px: int
    overlap_target_area_px: int
    station_count: int
    conservative_covered_area_px: int | None
    conservative_coverage_rate: float | None
    target_area_mm2: float | None
    uncovered_area_mm2: float | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, eq=False)
class CoverageEvaluation:
    metrics: CoverageMetrics
    footprint_mask: np.ndarray
    covered_mask: np.ndarray
    uncovered_mask: np.ndarray
    overlap_mask: np.ndarray
    coverage_counts: np.ndarray
    conservative_covered_mask: np.ndarray | None

    def masks_to_dict(self) -> dict:
        result = {}
        for name in ("footprint_mask", "covered_mask", "uncovered_mask", "overlap_mask",
                "coverage_counts", "conservative_covered_mask"):
            value = getattr(self, name)
            result[name] = None if value is None else {"shape_hw": list(value.shape),
                "dtype": str(value.dtype), "sha256": array_hash(value)}
        return result


def pixel_points(points: Iterable[tuple[int, int]], shape_hw: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    result = []
    for point in points:
        if len(result) >= MAX_EVALUATION_POINTS:
            raise ValueError("EVALUATION_POINT_RESOURCE_LIMIT")
        if len(point) != 2:
            raise ValueError("STATION_XY_REQUIRED")
        x, y = (integer(v, "station") for v in point)
        if x >= shape_hw[1] or y >= shape_hw[0]:
            raise ValueError("STATION_OUTSIDE_IMAGE")
        result.append((x, y))
    return tuple(result)


def footprint_slice(shape_hw: tuple[int, int], mask: np.ndarray, anchor_px: tuple[int, int],
        point: tuple[int, int]) -> tuple[tuple[slice, slice], np.ndarray]:
    """Clipped stamping shared by the planner; exact (x,y) anchor, including asymmetry."""
    x0, y0 = point[0] - anchor_px[0], point[1] - anchor_px[1]
    h, w = shape_hw
    xa, ya = max(0, x0), max(0, y0)
    xb, yb = min(w, x0 + mask.shape[1]), min(h, y0 + mask.shape[0])
    return (slice(ya, yb), slice(xa, xb)), mask[ya-y0:yb-y0, xa-x0:xb-x0]


def evaluate_coverage(mask: np.ndarray, kernel: FootprintKernel, points: Iterable[tuple[int, int]],
        *, conservative_kernel_mask: np.ndarray | None = None) -> CoverageEvaluation:
    """Count union once, duplicates as overlap, and off-image spill on a padded canvas.

    Conservative coverage is conditional on an explicit discrete error envelope;
    it remains None when positional uncertainty was not supplied.
    """
    target = binary_mask(mask)
    if not isinstance(kernel, FootprintKernel):
        raise ValueError("FOOTPRINT_KERNEL_REQUIRED")
    if kernel.scale and kernel.scale.image_shape_hw not in (None, target.shape):
        raise ValueError("SCALE_IMAGE_SHAPE_MISMATCH")
    pts = pixel_points(points, target.shape)
    h, w = target.shape
    kh, kw = kernel.mask.shape
    canvas_shape = (h + kh - 1, w + kw - 1)
    if canvas_shape[0] * canvas_shape[1] > MAX_CANVAS_CELLS:
        raise ValueError("EVALUATION_CANVAS_RESOURCE_LIMIT")
    counts = np.zeros(canvas_shape, dtype=np.uint32)
    for x, y in pts:
        counts[y:y + kh, x:x + kw] += kernel.mask
    ax, ay = kernel.anchor_px
    visible_counts = counts[ay:ay+h, ax:ax+w]
    footprint = visible_counts > 0
    covered = footprint & target
    uncovered = target & ~footprint
    overlap = (visible_counts >= 2) & target
    total = int(np.count_nonzero(target))
    covered_area = int(np.count_nonzero(covered))
    footprint_area = int(np.count_nonzero(counts))
    outside_image = footprint_area - int(np.count_nonzero(footprint))
    conservative = None
    conservative_area = None
    if conservative_kernel_mask is not None:
        cmask = binary_mask(conservative_kernel_mask, "conservative_kernel")
        if cmask.shape != kernel.mask.shape or np.any(cmask & ~kernel.mask):
            raise ValueError("CONSERVATIVE_KERNEL_MUST_BE_SUBSET")
        union = np.zeros(target.shape, dtype=np.bool_)
        for point in pts:
            section, stamp = footprint_slice(target.shape, cmask, kernel.anchor_px, point)
            union[section] |= stamp
        conservative = target & union
        conservative_area = int(np.count_nonzero(conservative))
    def mm2(area: int) -> float | None:
        if kernel.scale is None:
            return None
        value = area * (kernel.scale.mm_per_px_x * kernel.scale.mm_per_px_y)
        if not math.isfinite(value):
            raise ValueError("AREA_SCALE_OUT_OF_RANGE")
        return value
    metrics = CoverageMetrics(total, covered_area, total-covered_area,
        None if not total else covered_area / total, footprint_area, footprint_area-covered_area,
        outside_image, int(np.count_nonzero(overlap)), len(pts), conservative_area,
        None if not total or conservative_area is None else conservative_area / total,
        mm2(total), mm2(total-covered_area))
    arrays = [footprint, covered, uncovered, overlap, visible_counts, conservative]
    return CoverageEvaluation(metrics, *(None if a is None else immutable_array(a) for a in arrays))
