"""P5 offline geometric comparisons, diameter and positional-error sensitivity.

Baselines are explicitly forced dwell simulations, not hardware strategies.
All methods share the complete input, kernel, feasibility rules and budget.
Fixed-plan sweeps retain unsafe points and mark them unranked. No file/UI I/O.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field, replace

import cv2
import numpy as np

from microcleaning.control_system.planning.cleaning_plan import CleaningPlanPolicy, CleaningStrategy, plan_cleaning
from microcleaning.control_system.planning.coverage_model import (
    Evidence, FootprintKernel, KernelResult, build_kernel, canonical_json, content_hash,
    finite_number, integer,
)
from microcleaning.control_system.planning.coverage_metrics import (
    MAX_EVALUATION_POINTS, CoverageEvaluation, CoverageMetrics, evaluate_coverage, footprint_slice,
)
from microcleaning.control_system.planning.coverage_planner import (
    CoverageConstraints, CoverageDomain, CoverageInput, CoveragePolicy, PositionUncertainty,
    plan_coverage, prepare_coverage_domain,
)

METHODS = ("CENTER_POINT_COMPONENTS", "RASTER_SCAN_DWELL", "COVERAGE_AWARE")
ANALYSIS_VERSION = "coverage-analysis-v1"
MAX_ANALYSIS_RUNS = 256
MAX_ANALYSIS_MASK_BYTES = 192 * 1024 * 1024
EVALUATION_NAMES = ("footprint_mask", "covered_mask", "uncovered_mask", "overlap_mask",
    "coverage_counts", "conservative_covered_mask")


@dataclass(frozen=True)
class AnalysisSpec:
    station_budgets: tuple[int, ...] = (1, 4, 8, 16, 32, 64)
    target_coverage: float = 0.95
    candidate_stride_px: int = 1
    start_px: tuple[float, float] = (0.0, 0.0)
    coverage_basis: str = "nominal"
    raster_step_px: int = 16  # Recorded legacy software spacing, not a nozzle size.
    diameters: tuple[float, ...] = ()  # Explicit scenarios in the profile's unit.
    position_errors: tuple[float, ...] = ()
    position_error_unit: str = "px"

    def __post_init__(self) -> None:
        budgets = tuple(integer(v, "budget", minimum=1) for v in self.station_budgets)
        if not budgets or len(set(budgets)) != len(budgets) or tuple(sorted(budgets)) != budgets:
            raise ValueError("BUDGETS_REQUIRE_UNIQUE_ASCENDING_VALUES")
        object.__setattr__(self, "station_budgets", budgets)
        policy = CoveragePolicy(self.target_coverage, max(budgets), self.candidate_stride_px,
            self.start_px, self.coverage_basis)
        for name in ("target_coverage", "candidate_stride_px", "start_px"):
            object.__setattr__(self, name, getattr(policy, name))
        object.__setattr__(self, "raster_step_px", integer(self.raster_step_px, "raster_step", minimum=1))
        for name in ("diameters", "position_errors"):
            values = tuple(finite_number(v, name, inclusive=name == "position_errors") for v in getattr(self, name))
            if len(set(values)) != len(values) or tuple(sorted(values)) != values:
                raise ValueError("SCENARIOS_REQUIRE_UNIQUE_ASCENDING_VALUES")
            object.__setattr__(self, name, values)
        if self.position_error_unit not in {"px", "mm"}:
            raise ValueError("POSITION_ERROR_UNIT_REQUIRED")
        if self.run_count > MAX_ANALYSIS_RUNS:
            raise ValueError("ANALYSIS_RUN_RESOURCE_LIMIT")

    @property
    def run_count(self) -> int:
        return len(METHODS) * len(self.station_budgets) * (1 + 2 * len(self.diameters) + 2 * len(self.position_errors))

    def policy(self, budget: int) -> CoveragePolicy:
        return CoveragePolicy(self.target_coverage, budget, self.candidate_stride_px, self.start_px, self.coverage_basis)


@dataclass(frozen=True)
class PointIssue:
    proposal_index: int  # One based, before filtering or budget/goal stopping.
    point_px: tuple[int, int]
    reason: str


def _issues(points, domain: CoverageDomain) -> tuple[PointIssue, ...]:
    issues = []
    h, w = domain.candidate_centers_mask.shape
    for index, (x, y) in enumerate(points, 1):
        if not (0 <= x < w and 0 <= y < h):
            reason = "CENTER_OUTSIDE_IMAGE"
        elif not domain.feasible_centers_mask[y, x]:
            reason = "FULL_FOOTPRINT_OR_CENTER_DOMAIN_VIOLATION"
        elif not domain.candidate_centers_mask[y, x]:
            reason = "CANDIDATE_GRID_MISMATCH"
        else:
            continue
        issues.append(PointIssue(index, (x, y), reason))
    return tuple(issues)


def _warnings(model, constraints) -> tuple[str, ...]:
    warnings = ["PREVIEW_ONLY", "GEOMETRIC_COVERAGE_NOT_REMOVAL", "DWELL_SIMULATION_NOT_CONTINUOUS_SPRAY"]
    if model.profile.evidence.status == "assumed":
        warnings.append("ASSUMED_FOOTPRINT")
    if model.kernel and model.kernel.scale and model.kernel.scale.evidence.status == "assumed":
        warnings.append("ASSUMED_PIXEL_SCALE")
    if (constraints.evidence is None or constraints.evidence.status != "measured"
            or constraints.allowed_spray_mask is None or constraints.allowed_centers_mask is None):
        warnings.append("SAFETY_NOT_MEASURED")
    for name, label in (("allowed_spray_mask", "SPRAY"), ("allowed_centers_mask", "CENTER")):
        if getattr(constraints, name) is None:
            warnings.append(f"{label}_DOMAIN_IMAGE_EXTENT_ASSUMED")
    if constraints.position_uncertainty is None:
        warnings.append("POSITION_UNCERTAINTY_UNKNOWN")
    elif constraints.position_uncertainty.evidence.status == "assumed":
        warnings.append("ASSUMED_POSITION_UNCERTAINTY")
    return tuple(warnings)


@dataclass(frozen=True, eq=False)
class AnalysisRun:
    method: str
    phase: str
    parameter_name: str | None
    parameter_value: float | None
    parameter_unit: str | None
    source_run_id: str | None
    model: KernelResult
    policy: CoveragePolicy
    constraints_json: str
    legacy_auto_strategy: str
    points_px: tuple[tuple[int, int], ...]
    proposed_points_px: tuple[tuple[int, int], ...]
    rejected_proposals: tuple[PointIssue, ...]
    constraint_violations: tuple[PointIssue, ...]
    evaluation: CoverageEvaluation | None
    raw_prefix_metrics: CoverageMetrics | None
    raw_prefix_count: int
    candidate_count: int
    status: str
    reason_codes: tuple[str, ...]
    audit_reason_codes: tuple[str, ...]
    warnings: tuple[str, ...]
    route_length_px: float
    greedy_plan_id: str | None
    planning_evaluation_seconds: float
    audit_seconds: float
    executable: bool = field(default=False, init=False)

    @property
    def metrics(self):
        return None if self.evaluation is None else self.evaluation.metrics

    @property
    def qualifies_for_comparison(self) -> bool:
        return (self.evaluation is not None and self.metrics.geometric_coverage_rate is not None
            and not self.constraint_violations and self.status in {"COMPLETE", "PARTIAL"})

    def to_dict(self, *, include_timing: bool = True) -> dict:
        payload = {"method": self.method, "phase": self.phase,
            "parameter": {"name": self.parameter_name, "value": self.parameter_value, "unit": self.parameter_unit},
            "source_run_id": self.source_run_id, "model": self.model.to_dict(), "policy": asdict(self.policy),
            "constraints": json.loads(self.constraints_json), "legacy_auto_strategy": self.legacy_auto_strategy,
            "baseline_semantics": "forced_geometry_filtered_dwell" if self.method != "COVERAGE_AWARE" else "greedy_dwell",
            "rounding": "python_round_ties_to_even", "points_px": self.points_px,
            "proposed_points_px": self.proposed_points_px,
            "rejected_proposals": [asdict(i) for i in self.rejected_proposals],
            "constraint_violations": [asdict(i) for i in self.constraint_violations],
            "metrics": None if self.metrics is None else self.metrics.to_dict(),
            "masks": None if self.evaluation is None else self.evaluation.masks_to_dict(),
            "raw_prefix_metrics": None if self.raw_prefix_metrics is None else self.raw_prefix_metrics.to_dict(),
            "raw_prefix_count": self.raw_prefix_count, "raw_prefix_is_audit_not_ranked": True,
            "candidate_count": self.candidate_count, "status": self.status, "reason_codes": self.reason_codes,
            "audit_reason_codes": self.audit_reason_codes, "warnings": self.warnings,
            "qualifies_for_comparison": self.qualifies_for_comparison, "route_length_px": self.route_length_px,
            "route_semantics": "straight_visit_links_not_actual_xy_trajectory", "greedy_plan_id": self.greedy_plan_id,
            "executable": False, "feeds_action_request": False, "send_to_controller": False}
        result = {"run_id": content_hash(payload), **payload}
        if include_timing:
            result["timing"] = {"planning_evaluation_seconds": self.planning_evaluation_seconds,
                "audit_seconds": self.audit_seconds,
                "semantics": "single_local_call_including_domain_and_metrics_excluding_kernel_build_and_file_encoding"}
        return result


@dataclass(frozen=True, eq=False)
class CoverageAnalysis:
    input: CoverageInput
    model: KernelResult
    constraints: CoverageConstraints
    spec: AnalysisSpec
    runs: tuple[AnalysisRun, ...]
    elapsed_seconds: float
    executable: bool = field(default=False, init=False)

    def to_dict(self, *, include_timing: bool = True) -> dict:
        payload = {"schema_version": ANALYSIS_VERSION, "input": self.input.to_dict(),
            "base_model": self.model.to_dict(), "base_constraints": self.constraints.to_dict(),
            "spec": asdict(self.spec), "methods": METHODS,
            "runs": [r.to_dict(include_timing=False) for r in self.runs],
            "comparison_semantics": "same_full_mask_kernel_constraints_grid_start_goal_and_budget_ceiling",
            "fixed_plan_semantics": "original_selected_points_retained_violations_unranked",
            "diameter_semantics": "explicit_assumed_circle_scenarios_in_profile_unit",
            "position_error_semantics": "conditional_outward_rounded_pixel_box_not_probability_or_physical_validation",
            "executable": False, "feeds_action_request": False, "send_to_controller": False}
        result = {"analysis_id": content_hash(payload), **payload}
        if include_timing:
            result["runs"] = [r.to_dict() for r in self.runs]
            result["elapsed_seconds"] = self.elapsed_seconds
        return result


def _baseline_points(input, method, spec):
    mask = input.mask.astype(np.uint8) * 255
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count - 1 > MAX_EVALUATION_POINTS:
        raise ValueError("BASELINE_POINT_RESOURCE_LIMIT")
    if method == "RASTER_SCAN_DWELL":
        # Count the legacy foreground-index samples and optional row tail
        # before its unbounded route allocation. Every component starts on a
        # nonempty row, so the component-count guard applies to raster too.
        proposed_count = 0
        for label in range(1, count):
            y_min = int(stats[label, cv2.CC_STAT_TOP])
            y_stop = y_min + int(stats[label, cv2.CC_STAT_HEIGHT])
            for y in range(y_min, y_stop, spec.raster_step_px):
                n = int(np.count_nonzero(labels[y] == label))
                if n:
                    proposed_count += (n - 1) // spec.raster_step_px + 1
                    proposed_count += (n - 1) % spec.raster_step_px != 0
                    if proposed_count > MAX_EVALUATION_POINTS:
                        raise ValueError("BASELINE_POINT_RESOURCE_LIMIT")
        # Any nonempty mask exceeds this explicitly forced threshold. Reuse the
        # exact legacy row sampling and component order without editing it.
        plan = plan_cleaning(mask, policy=CleaningPlanPolicy(small_target_ratio=0.5 / mask.size,
            raster_step_px=spec.raster_step_px, start_px=spec.start_px))
        points = tuple((round(x), round(y)) for x, y in plan.path_px)
    else:
        remaining = list(range(1, count))
        points, previous = [], spec.start_px
        while remaining:
            chosen = min(remaining, key=lambda label: (
                (centroids[label][0] - previous[0]) ** 2 + (centroids[label][1] - previous[1]) ** 2, label))
            remaining.remove(chosen)
            previous = tuple(float(v) for v in centroids[chosen])
            points.append(tuple(round(v) for v in previous))
        points = tuple(points)
    if len(points) > MAX_EVALUATION_POINTS:
        raise ValueError("BASELINE_POINT_RESOURCE_LIMIT")
    return points


def _select_baseline(input, kernel, points, policy, domain):
    selected = []
    uncovered = input.mask.copy()
    total = int(uncovered.sum())
    objective = kernel.mask if policy.coverage_basis == "nominal" else domain.conservative_kernel_mask
    for point in points:
        selected.append(point)
        section, stamp = footprint_slice(input.mask.shape, objective, kernel.anchor_px, point)
        uncovered[section] &= ~stamp
        if (total - int(uncovered.sum())) / total >= policy.target_coverage:
            return tuple(selected), "COMPLETE", ("COVERAGE_TARGET_REACHED",)
        if len(selected) >= policy.max_stations:
            return tuple(selected), "PARTIAL", ("MAX_STATIONS_REACHED",)
    return tuple(selected), "PARTIAL", ("BASELINE_POINTS_EXHAUSTED" if points else "NO_FEASIBLE_BASELINE_POINT",)


def _run(input, model, constraints, spec, budget, method, legacy, *, phase="comparison",
        parameter_name=None, parameter_value=None, parameter_unit=None, reference=None):
    policy = spec.policy(budget)
    began = time.perf_counter()
    evaluation, raw_metrics, greedy_id = None, None, None
    points, proposals, rejected, violations, reasons, audit_reasons = (), (), (), (), (), ()
    count, raw_count, audit_seconds = 0, 0, 0.0
    if reference is not None:
        # Fixed means retain the original coordinates even when the new
        # kernel/constraints are unavailable; metrics must then stay unknown.
        points = proposals = reference.points_px
        raw_count = len(points)
    if reference is not None and reference.evaluation is None:
        status, reasons = "REFERENCE_UNAVAILABLE", ("FIXED_REFERENCE_HAS_NO_EVALUATION",)
    elif reference is None and (method == "COVERAGE_AWARE" or not np.any(input.mask)):
        plan = plan_coverage(input, model, policy=policy, constraints=constraints)
        points = proposals = plan.points_px
        evaluation, count, status, reasons = plan.evaluation, plan.candidate_count, plan.status, plan.reason_codes
        raw_metrics, raw_count = plan.metrics, len(points)
        greedy_id = plan.to_dict()["plan_id"] if method == "COVERAGE_AWARE" else None
    else:
        domain = prepare_coverage_domain(input, model, policy=policy, constraints=constraints)
        status, reasons, count = domain.status, domain.reason_codes, domain.candidate_count
        if domain.status == "READY":
            if reference is not None:
                violations = _issues(points, domain)
                evaluation = evaluate_coverage(input.mask, model.kernel, points,
                    conservative_kernel_mask=domain.conservative_kernel_mask)
                rate = (evaluation.metrics.geometric_coverage_rate if policy.coverage_basis == "nominal"
                    else evaluation.metrics.conservative_coverage_rate)
                status = "NO_TARGET" if rate is None else "COMPLETE" if rate >= policy.target_coverage else "PARTIAL"
                reasons = ("FIXED_POINTS_REEVALUATED",)
                if violations:
                    status, reasons = "CONSTRAINT_VIOLATION", ("FIXED_PLAN_NO_LONGER_FEASIBLE",)
                raw_metrics, raw_count = evaluation.metrics, len(points)
            else:
                try:
                    proposals = _baseline_points(input, method, spec)
                    rejected = _issues(proposals, domain)
                    bad = {i.proposal_index for i in rejected}
                    valid = tuple(p for i, p in enumerate(proposals, 1) if i not in bad)
                    points, status, reasons = _select_baseline(input, model.kernel, valid, policy, domain)
                    evaluation = evaluate_coverage(input.mask, model.kernel, points,
                        conservative_kernel_mask=domain.conservative_kernel_mask)
                except ValueError as exc:
                    status = "RESOURCE_LIMIT" if "RESOURCE_LIMIT" in str(exc) else "METRICS_UNAVAILABLE"
                    reasons = (str(exc),)
    planning_seconds = time.perf_counter() - began
    if evaluation is not None and reference is None and method != "COVERAGE_AWARE" and proposals:
        audit_began = time.perf_counter()
        raw_count = min(budget, len(proposals))
        try:
            raw_metrics = evaluate_coverage(input.mask, model.kernel, proposals[:raw_count],
                conservative_kernel_mask=domain.conservative_kernel_mask).metrics
        except ValueError as exc:
            audit_reasons = (str(exc),)
        audit_seconds = time.perf_counter() - audit_began
    route = sum(math.dist(a, b) for a, b in zip((policy.start_px, *points), points))
    return AnalysisRun(method, phase, parameter_name, parameter_value, parameter_unit,
        None if reference is None else reference.to_dict(include_timing=False)["run_id"], model, policy,
        canonical_json(constraints.to_dict()), legacy, points, proposals, rejected, violations,
        evaluation, raw_metrics, raw_count, count, status, tuple(reasons), tuple(audit_reasons),
        _warnings(model, constraints), route, greedy_id, planning_seconds, audit_seconds)


def analyze_coverage(input: CoverageInput, model: KernelResult | FootprintKernel, *,
        spec: AnalysisSpec = AnalysisSpec(), constraints: CoverageConstraints | None = None) -> CoverageAnalysis:
    """Compare dwell baselines and greedy, then fixed vs replanned scenarios.

    Timing is descriptive for this local call, never part of deterministic IDs.
    Circle diameter sweeps cannot silently replace an explicit/non-circular kernel.
    """
    if not isinstance(input, CoverageInput) or not isinstance(spec, AnalysisSpec):
        raise ValueError("ANALYSIS_INPUT_AND_SPEC_REQUIRED")
    if isinstance(model, FootprintKernel):
        model = KernelResult(model.profile, model)
    if not isinstance(model, KernelResult):
        raise ValueError("ANALYSIS_KERNEL_RESULT_REQUIRED")
    constraints = constraints if constraints is not None else CoverageConstraints()
    if not isinstance(constraints, CoverageConstraints):
        raise ValueError("ANALYSIS_CONSTRAINTS_REQUIRED")
    if input.mask.size * spec.run_count * 9 > MAX_ANALYSIS_MASK_BYTES:
        raise ValueError("ANALYSIS_MASK_RESOURCE_LIMIT")
    if spec.diameters and (model.profile.shape != "circle" or (model.kernel is not None
            and model.kernel.rasterization != "integer_pixel_centers_closed_disk")):
        raise ValueError("DIAMETER_SWEEP_REQUIRES_CIRCLE_FACTORY_KERNEL")
    began = time.perf_counter()
    area = int(np.count_nonzero(input.mask))
    # Record only the unchanged legacy auto branch; allocating its full raster
    # merely to read the enum would bypass the analysis point-resource guard.
    legacy = (CleaningStrategy.NO_TARGET if area == 0 else CleaningStrategy.CENTER_POINT
        if area / input.mask.size <= CleaningPlanPolicy().small_target_ratio
        else CleaningStrategy.RASTER_SCAN).value
    runs, references = [], {}
    for budget in spec.station_budgets:
        for method in METHODS:
            result = _run(input, model, constraints, spec, budget, method, legacy)
            references[method, budget] = result
            runs.append(result)
    scenarios = []
    for diameter in spec.diameters:
        scenario = build_kernel(model.profile.with_diameter(diameter), None if model.kernel is None else model.kernel.scale)
        scenarios.append(("diameter", diameter, model.profile.unit, scenario, constraints))
    for error in spec.position_errors:
        uncertainty = PositionUncertainty(error, spec.position_error_unit,
            Evidence("assumed", f"analysis:{content_hash(constraints.to_dict())}:error={error:g}{spec.position_error_unit}"))
        scenarios.append(("position_error", error, spec.position_error_unit, model,
            replace(constraints, position_uncertainty=uncertainty)))
    for name, value, unit, scenario_model, scenario_constraints in scenarios:
        for budget in spec.station_budgets:
            for method in METHODS:
                for mode in ("fixed", "replan"):
                    runs.append(_run(input, scenario_model, scenario_constraints, spec, budget, method, legacy,
                        phase=f"{name}_{mode}", parameter_name=name, parameter_value=value, parameter_unit=unit,
                        reference=references[method, budget] if mode == "fixed" else None))
    return CoverageAnalysis(input, model, constraints, spec, tuple(runs), time.perf_counter() - began)
