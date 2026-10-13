"""Deterministic, non-executable multi-dwell coverage planning (member C, P2).

The complete supplied mask is retained, including disconnected residuals. No
serial, execution requests, workbench state or physical position ledger is used.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from microcleaning.control_system.planning.coverage_model import (
    MAX_KERNEL_CELLS, Evidence, FootprintKernel, KernelResult, array_hash, binary_mask,
    canonical_json, content_hash, finite_number, immutable_array, integer, nonempty_text,
)
from microcleaning.control_system.planning.coverage_metrics import (
    MAX_CANVAS_CELLS, CoverageEvaluation, evaluate_coverage, footprint_slice,
)

MAX_IMAGE_CELLS = 1_000_000
MAX_CANDIDATES = 250_000
MAX_STATIONS = 256


@dataclass(frozen=True, eq=False)
class CoverageInput:
    mask: np.ndarray
    target_id: str
    mask_kind: str
    source_ref: str
    frame_id: str | None = None
    image_ref: str | None = None
    image_sha256: str | None = None
    segmentation_sha256: str | None = None

    def __post_init__(self) -> None:
        nonempty_text(self.target_id, "target_id")
        nonempty_text(self.source_ref, "mask_source_ref")
        if self.mask_kind not in {"segmented_stain", "manual_region", "manual_roi_segmented", "synthetic", "reference_annotation"}:
            raise ValueError("MASK_KIND_REQUIRED")
        for name in ("frame_id", "image_ref"):
            if getattr(self, name) is not None:
                nonempty_text(getattr(self, name), name)
        for name in ("image_sha256", "segmentation_sha256"):
            if getattr(self, name) is not None:
                Evidence("assumed", name, getattr(self, name))
        # Limit before copying the input. This is a software resource guard.
        if isinstance(self.mask, np.ndarray) and self.mask.size > MAX_IMAGE_CELLS:
            raise ValueError("INPUT_IMAGE_RESOURCE_LIMIT")
        object.__setattr__(self, "mask", binary_mask(self.mask))

    def to_dict(self) -> dict:
        return {"coordinate_frame": "image_px", "shape_hw": list(self.mask.shape),
            "target_id": self.target_id, "mask_kind": self.mask_kind,
            "area_kind": "manual_region_px" if self.mask_kind == "manual_region" else "target_mask_px",
            "source_ref": self.source_ref, "frame_id": self.frame_id, "image_ref": self.image_ref,
            "image_sha256": self.image_sha256, "segmentation_sha256": self.segmentation_sha256,
            "mask_sha256": array_hash(self.mask)}

    @property
    def sha256(self) -> str:
        return content_hash(self.to_dict())


@dataclass(frozen=True)
class PositionUncertainty:
    radius: float
    unit: str
    evidence: Evidence

    def __post_init__(self) -> None:
        object.__setattr__(self, "radius", finite_number(self.radius, "position_radius", inclusive=True))
        if self.unit not in {"px", "mm"} or not isinstance(self.evidence, Evidence):
            raise ValueError("POSITION_UNITS_AND_EVIDENCE_REQUIRED")


@dataclass(frozen=True, eq=False)
class CoverageConstraints:
    allowed_centers_mask: np.ndarray | None = None
    allowed_spray_mask: np.ndarray | None = None
    forbidden_mask: np.ndarray | None = None
    evidence: Evidence | None = None
    position_uncertainty: PositionUncertainty | None = None

    def __post_init__(self) -> None:
        names = ("allowed_centers_mask", "allowed_spray_mask", "forbidden_mask")
        if any(getattr(self, n) is not None for n in names) and not isinstance(self.evidence, Evidence):
            raise ValueError("CONSTRAINT_MASKS_REQUIRE_EVIDENCE")
        if self.evidence is not None and not isinstance(self.evidence, Evidence):
            raise ValueError("CONSTRAINT_EVIDENCE_INVALID")
        if self.position_uncertainty is not None and not isinstance(self.position_uncertainty, PositionUncertainty):
            raise ValueError("POSITION_UNCERTAINTY_INVALID")
        for name in names:
            mask = getattr(self, name)
            if mask is not None:
                if isinstance(mask, np.ndarray) and mask.size > MAX_IMAGE_CELLS:
                    raise ValueError("CONSTRAINT_IMAGE_RESOURCE_LIMIT")
                object.__setattr__(self, name, binary_mask(mask, name))

    def to_dict(self) -> dict:
        return {"evidence": None if self.evidence is None else self.evidence.to_dict(),
            "position_uncertainty": None if self.position_uncertainty is None else asdict(self.position_uncertainty),
            "masks": {name: None if getattr(self, name) is None else array_hash(getattr(self, name))
                for name in ("allowed_centers_mask", "allowed_spray_mask", "forbidden_mask")},
            "outside_image": "forbidden", "missing_domain": "image_extent_simulation_only",
            "error_envelope": "outward_rounded_axis_aligned_pixel_box"}


@dataclass(frozen=True)
class CoveragePolicy:
    target_coverage: float = 0.95
    max_stations: int = 64
    candidate_stride_px: int = 1
    start_px: tuple[float, float] = (0.0, 0.0)
    coverage_basis: str = "nominal"

    def __post_init__(self) -> None:
        rate = finite_number(self.target_coverage, "target_coverage")
        if rate > 1:
            raise ValueError("TARGET_COVERAGE_MUST_NOT_EXCEED_ONE")
        object.__setattr__(self, "target_coverage", rate)
        count = integer(self.max_stations, "max_stations", minimum=1)
        if count > MAX_STATIONS:
            raise ValueError("STATION_RESOURCE_LIMIT")
        object.__setattr__(self, "max_stations", count)
        object.__setattr__(self, "candidate_stride_px", integer(self.candidate_stride_px, "candidate_stride", minimum=1))
        if len(self.start_px) != 2:
            raise ValueError("START_XY_REQUIRED")
        object.__setattr__(self, "start_px", tuple(finite_number(v, "start", inclusive=True) for v in self.start_px))
        if self.coverage_basis not in {"nominal", "conservative"}:
            raise ValueError("COVERAGE_BASIS_INVALID")


@dataclass(frozen=True)
class CoverageStation:
    index: int
    x_px: int
    y_px: int
    newly_covered_area_px: int
    objective_gain_px: int
    cumulative_coverage_rate: float
    cumulative_objective_rate: float


@dataclass(frozen=True, eq=False)
class CoveragePlan:
    input: CoverageInput
    model: KernelResult
    policy: CoveragePolicy
    constraints_json: str
    status: str
    reason_codes: tuple[str, ...]
    stations: tuple[CoverageStation, ...]
    evaluation: CoverageEvaluation | None
    candidate_count: int
    warnings: tuple[str, ...]
    route_length_px: float
    executable: bool = field(default=False, init=False)

    @property
    def points_px(self) -> tuple[tuple[int, int], ...]:
        return tuple((p.x_px, p.y_px) for p in self.stations)

    @property
    def metrics(self):
        return None if self.evaluation is None else self.evaluation.metrics

    @property
    def covered_mask(self):
        return None if self.evaluation is None else self.evaluation.covered_mask

    @property
    def uncovered_mask(self):
        return None if self.evaluation is None else self.evaluation.uncovered_mask

    def to_dict(self) -> dict:
        payload = {"schema_version": "coverage-plan-v1", "algorithm": "greedy-discrete-coverage-v1",
            "input": self.input.to_dict(), "model": self.model.to_dict(), "policy": asdict(self.policy),
            "constraints": json.loads(self.constraints_json), "status": self.status,
            "reason_codes": list(self.reason_codes), "stations": [asdict(s) for s in self.stations],
            "visit_order_px": self.points_px, "selection_order_is_visit_order": True,
            "metrics": None if self.metrics is None else self.metrics.to_dict(),
            "masks": None if self.evaluation is None else self.evaluation.masks_to_dict(),
            "candidate_count": self.candidate_count, "warnings": list(self.warnings),
            "route_length_px": self.route_length_px, "route_semantics": "straight_visit_links_not_actual_xy_trajectory",
            "executable": False, "feeds_action_request": False, "send_to_controller": False}
        return {"plan_id": content_hash(payload), **payload}


def _correlate(mask: np.ndarray, footprint: np.ndarray, anchor: tuple[int, int]) -> np.ndarray:
    # float64 plus integer rounding avoids float32 gain errors in larger kernels.
    counts = cv2.filter2D(mask.astype(np.float64), cv2.CV_64F, footprint.astype(np.float64),
        anchor=anchor, borderType=cv2.BORDER_CONSTANT)
    return np.rint(counts).astype(np.int32)


def _error_kernels(kernel: FootprintKernel, uncertainty: PositionUncertainty | None):
    if uncertainty is None:
        return kernel.mask, kernel.anchor_px, None
    if uncertainty.unit == "mm" and kernel.scale is None:
        raise ValueError("POSITION_PIXEL_SCALE_MISSING")
    rx = uncertainty.radius if uncertainty.unit == "px" else uncertainty.radius / kernel.scale.mm_per_px_x
    ry = uncertainty.radius if uncertainty.unit == "px" else uncertainty.radius / kernel.scale.mm_per_px_y
    if not all(math.isfinite(v) for v in (rx, ry)):
        raise ValueError("ERROR_ENVELOPE_RESOURCE_LIMIT")
    bx, by = math.ceil(rx), math.ceil(ry)
    h, w = kernel.mask.shape
    if (h + 2 * by) * (w + 2 * bx) > MAX_KERNEL_CELLS:
        raise ValueError("ERROR_ENVELOPE_RESOURCE_LIMIT")
    box = np.ones((2 * by + 1, 2 * bx + 1), dtype=np.uint8)
    padded = np.pad(kernel.mask.astype(np.uint8), ((by, by), (bx, bx)))
    outer = cv2.dilate(padded, box, borderType=cv2.BORDER_CONSTANT, borderValue=0) != 0
    inner = cv2.erode(kernel.mask.astype(np.uint8), box, borderType=cv2.BORDER_CONSTANT, borderValue=0) != 0
    return outer, (kernel.anchor_px[0] + bx, kernel.anchor_px[1] + by), inner


def _coverage_setup(input, model, policy, constraints):
    if not isinstance(input, CoverageInput) or not isinstance(policy, CoveragePolicy):
        raise ValueError("COVERAGE_INPUT_AND_POLICY_REQUIRED")
    if isinstance(model, FootprintKernel):
        model = KernelResult(model.profile, model)
    if not isinstance(model, KernelResult):
        raise ValueError("KERNEL_RESULT_REQUIRED")
    constraints = constraints if constraints is not None else CoverageConstraints()
    if not isinstance(constraints, CoverageConstraints):
        raise ValueError("COVERAGE_CONSTRAINTS_REQUIRED")
    target = input.mask
    if policy.start_px[0] > target.shape[1] - 1 or policy.start_px[1] > target.shape[0] - 1:
        raise ValueError("START_OUTSIDE_IMAGE")
    for name in ("allowed_centers_mask", "allowed_spray_mask", "forbidden_mask"):
        value = getattr(constraints, name)
        if value is not None and value.shape != target.shape:
            raise ValueError("CONSTRAINT_IMAGE_SHAPE_MISMATCH")
    kernel = model.kernel
    if kernel is not None and kernel.scale and kernel.scale.image_shape_hw not in (None, target.shape):
        raise ValueError("SCALE_IMAGE_SHAPE_MISMATCH")
    return model, constraints


@dataclass(frozen=True, eq=False)
class CoverageDomain:
    """Shared geometric feasibility, never a physical authorization.

    feasible_centers_mask checks the complete error-expanded footprint;
    candidate_centers_mask additionally applies the policy's sampling grid.
    An unavailable domain has no masks, rather than an invented safe region.
    """
    status: str
    reason_codes: tuple[str, ...]
    feasible_centers_mask: np.ndarray | None
    candidate_centers_mask: np.ndarray | None
    conservative_kernel_mask: np.ndarray | None
    candidate_count: int


def prepare_coverage_domain(input: CoverageInput, model: KernelResult | FootprintKernel, *,
        policy: CoveragePolicy = CoveragePolicy(), constraints: CoverageConstraints | None = None) -> CoverageDomain:
    """Read-only domain used by P2 and offline P5 baselines with identical rules."""
    model, constraints = _coverage_setup(input, model, policy, constraints)
    kernel = model.kernel
    def unavailable(status, reasons):
        return CoverageDomain(status, tuple(reasons), None, None, None, 0)
    if kernel is None:
        return unavailable("MODEL_UNAVAILABLE", model.reason_codes)
    if ((input.mask.shape[0] + kernel.mask.shape[0] - 1)
            * (input.mask.shape[1] + kernel.mask.shape[1] - 1) > MAX_CANVAS_CELLS):
        return unavailable("RESOURCE_LIMIT", ("EVALUATION_CANVAS_RESOURCE_LIMIT",))
    if policy.coverage_basis == "conservative" and constraints.position_uncertainty is None:
        return unavailable("CONSTRAINTS_UNAVAILABLE", ("POSITION_UNCERTAINTY_REQUIRED",))
    try:
        outer, outer_anchor, inner = _error_kernels(kernel, constraints.position_uncertainty)
    except ValueError as exc:
        status = "RESOURCE_LIMIT" if "RESOURCE_LIMIT" in str(exc) else "CONSTRAINTS_UNAVAILABLE"
        return unavailable(status, (str(exc),))
    allowed = (np.ones(input.mask.shape, dtype=np.bool_) if constraints.allowed_spray_mask is None
        else constraints.allowed_spray_mask.copy())
    if constraints.forbidden_mask is not None:
        allowed &= ~constraints.forbidden_mask
    feasible = _correlate(allowed, outer, outer_anchor) == int(np.count_nonzero(outer))
    if constraints.allowed_centers_mask is not None:
        feasible &= constraints.allowed_centers_mask
    grid = np.zeros(input.mask.shape, dtype=np.bool_)
    grid[::policy.candidate_stride_px, ::policy.candidate_stride_px] = True
    candidates = feasible & grid
    count = int(np.count_nonzero(candidates))
    if count > MAX_CANDIDATES:
        return unavailable("RESOURCE_LIMIT", ("CANDIDATE_RESOURCE_LIMIT",))
    return CoverageDomain("READY", (), immutable_array(feasible), immutable_array(candidates),
        None if inner is None else immutable_array(inner), count)


def plan_coverage(input: CoverageInput, model: KernelResult | FootprintKernel, *,
        policy: CoveragePolicy = CoveragePolicy(), constraints: CoverageConstraints | None = None) -> CoveragePlan:
    """Max incremental gain, then nearest previous point, then row, then column.

    Candidate centers include off-target pixels. The full outer footprint must
    fit the allowed spray domain, including explicit positional uncertainty.
    Unknown safety/position evidence stays unknown even in a complete plan.
    """
    model, constraints = _coverage_setup(input, model, policy, constraints)
    target, kernel = input.mask, model.kernel
    warnings = ["PREVIEW_ONLY", "GEOMETRIC_COVERAGE_NOT_REMOVAL"]
    if model.profile.evidence.status == "assumed":
        warnings.append("ASSUMED_FOOTPRINT")
    if kernel is not None and kernel.scale and kernel.scale.evidence.status == "assumed":
        warnings.append("ASSUMED_PIXEL_SCALE")
    if (constraints.evidence is None or constraints.evidence.status != "measured"
            or constraints.allowed_spray_mask is None or constraints.allowed_centers_mask is None):
        warnings.append("SAFETY_NOT_MEASURED")
    if constraints.allowed_spray_mask is None:
        warnings.append("SPRAY_DOMAIN_IMAGE_EXTENT_ASSUMED")
    if constraints.allowed_centers_mask is None:
        warnings.append("CENTER_DOMAIN_IMAGE_EXTENT_ASSUMED")
    if constraints.position_uncertainty is None:
        warnings.append("POSITION_UNCERTAINTY_UNKNOWN")
    elif constraints.position_uncertainty.evidence.status == "assumed":
        warnings.append("ASSUMED_POSITION_UNCERTAINTY")
    stations = []
    candidate_count = 0
    def finish(status, reasons, evaluation=None):
        route = 0.0
        previous = policy.start_px
        for station in stations:
            point = (station.x_px, station.y_px)
            route += math.hypot(point[0] - previous[0], point[1] - previous[1])
            previous = point
        return CoveragePlan(input, model, policy, canonical_json(constraints.to_dict()), status, tuple(reasons),
            tuple(stations), evaluation, candidate_count, tuple(warnings), route)
    if kernel is not None:
        canvas_h = target.shape[0] + kernel.mask.shape[0] - 1
        canvas_w = target.shape[1] + kernel.mask.shape[1] - 1
        if canvas_h * canvas_w > MAX_CANVAS_CELLS:
            return finish("RESOURCE_LIMIT", ("EVALUATION_CANVAS_RESOURCE_LIMIT",))
    if not np.any(target):
        try:
            evaluation = None if kernel is None else evaluate_coverage(target, kernel, ())
        except ValueError as exc:
            return finish("METRICS_UNAVAILABLE", (str(exc),))
        return finish("NO_TARGET", ("TARGET_MASK_EMPTY",), evaluation)
    if kernel is None:
        return finish("MODEL_UNAVAILABLE", model.reason_codes)
    domain = prepare_coverage_domain(input, model, policy=policy, constraints=constraints)
    if domain.status != "READY":
        return finish(domain.status, domain.reason_codes)
    legal = domain.candidate_centers_mask.copy()
    inner = domain.conservative_kernel_mask
    candidate_count = domain.candidate_count
    objective = kernel.mask if policy.coverage_basis == "nominal" else inner
    uncovered = target.copy()
    nominal_union = np.zeros(target.shape, dtype=np.bool_)
    total = int(np.count_nonzero(target))
    previous = policy.start_px
    reason = "NO_FEASIBLE_CANDIDATE" if not candidate_count else "MAX_STATIONS_REACHED"
    for _ in range(policy.max_stations):
        if not np.any(legal):
            reason = "NO_FEASIBLE_CANDIDATE" if not candidate_count else "CANDIDATES_EXHAUSTED"
            break
        gains = _correlate(uncovered, objective, kernel.anchor_px)
        gains[~legal] = -1
        best = int(gains.max())
        if best <= 0:
            reason = "NO_ADDITIONAL_COVERAGE"
            break
        ys, xs = np.nonzero(gains == best)
        distances = (xs.astype(np.float64) - previous[0]) ** 2 + (ys.astype(np.float64) - previous[1]) ** 2
        chosen = int(np.lexsort((xs, ys, distances))[0])
        point = (int(xs[chosen]), int(ys[chosen]))
        section, stamp = footprint_slice(target.shape, objective, kernel.anchor_px, point)
        actual_gain = int(np.count_nonzero(uncovered[section] & stamp))
        if actual_gain != best:
            raise RuntimeError("COVERAGE_CORRELATION_MISMATCH")
        uncovered[section] &= ~stamp
        nominal_section, nominal_stamp = footprint_slice(target.shape, kernel.mask, kernel.anchor_px, point)
        nominal_gain = int(np.count_nonzero(target[nominal_section] & nominal_stamp & ~nominal_union[nominal_section]))
        nominal_union[nominal_section] |= nominal_stamp
        nominal_rate = int(np.count_nonzero(target & nominal_union)) / total
        objective_rate = (total - int(np.count_nonzero(uncovered))) / total
        stations.append(CoverageStation(len(stations) + 1, *point, nominal_gain, best, nominal_rate, objective_rate))
        legal[point[1], point[0]] = False
        previous = point
        if objective_rate >= policy.target_coverage:
            reason = "COVERAGE_TARGET_REACHED"
            break
    points = tuple((s.x_px, s.y_px) for s in stations)
    try:
        evaluation = evaluate_coverage(target, kernel, points, conservative_kernel_mask=inner)
    except ValueError as exc:
        status = "RESOURCE_LIMIT" if "RESOURCE_LIMIT" in str(exc) else "METRICS_UNAVAILABLE"
        return finish(status, (str(exc),))
    status = "COMPLETE" if reason == "COVERAGE_TARGET_REACHED" else "PARTIAL"
    return finish(status, (reason,), evaluation)
