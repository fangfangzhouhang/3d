"""学习得到的喷点建议。三个硬件标志固定为 false，不能当成动作申请。"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from microcleaning.control_system.planning.coverage_metrics import CoverageEvaluation
from microcleaning.data_learning.plan_imitation.config import ImitationStudy
from microcleaning.data_learning.plan_imitation.decode import decode_and_order, route_length_px
from microcleaning.data_learning.plan_imitation.teacher import evaluate_points, feasible_centers, teacher_example

SCHEMA = "learned-coverage-proposal-v1"
WARNINGS = (
    "PREVIEW_ONLY",
    "GEOMETRIC_COVERAGE_NOT_REMOVAL",
    "LEARNED_PLAN_NOT_AN_ACTION",
    "TEACHER_IS_GREEDY_NOT_HUMAN_LABEL",
    "ASSUMED_FOOTPRINT",
    "SAFETY_NOT_MEASURED",
)


@dataclass(frozen=True, eq=False)
class LearnedCoverageProposal:
    points_px: tuple[tuple[int, int], ...]
    route_length_px: float
    status: str
    reason_codes: tuple[str, ...]
    teacher_points_px: tuple[tuple[int, int], ...]
    teacher_route_length_px: float
    teacher_coverage_rate: float | None
    teacher_status: str
    evaluation: CoverageEvaluation | None
    kind: str
    source_ref: str
    warnings: tuple[str, ...] = WARNINGS
    algorithm: str = "heatmap-imitation-nn-v1"
    executable: bool = field(default=False, init=False)
    feeds_action_request: bool = field(default=False, init=False)
    send_to_controller: bool = field(default=False, init=False)

    @property
    def coverage_rate(self) -> float | None:
        if self.evaluation is None or self.evaluation.metrics.geometric_coverage_rate is None:
            return None
        return float(self.evaluation.metrics.geometric_coverage_rate)

    @property
    def station_count(self) -> int:
        return len(self.points_px)

    def to_dict(self) -> dict:
        if self.executable or self.feeds_action_request or self.send_to_controller:
            raise RuntimeError("LEARNED_PLAN_HARDWARE_FLAG_BROKEN")
        metrics = None if self.evaluation is None else self.evaluation.metrics.to_dict()
        return {
            "schema_version": SCHEMA,
            "algorithm": self.algorithm,
            "coordinate_frame": "image_px",
            "points_px": self.points_px,
            "visit_order": "nearest_neighbor_from_start_not_firmware_trajectory",
            "station_count": self.station_count,
            "route_length_px": self.route_length_px,
            "route_semantics": "straight_visit_links_not_actual_xy_trajectory",
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "metrics": metrics,
            "geometric_coverage_rate": self.coverage_rate,
            "teacher": {
                "algorithm": "greedy-discrete-coverage-v1",
                "points_px": self.teacher_points_px,
                "station_count": len(self.teacher_points_px),
                "route_length_px": self.teacher_route_length_px,
                "geometric_coverage_rate": self.teacher_coverage_rate,
                "status": self.teacher_status,
                "label_meaning": "imitation_target_not_human_or_cleaning_ground_truth",
            },
            "kind": self.kind,
            "source_ref": self.source_ref,
            "warnings": list(self.warnings),
            "executable": False,
            "feeds_action_request": False,
            "send_to_controller": False,
        }


def _status_for(rate: float | None, study: ImitationStudy, point_count: int, domain_status: str) -> tuple[str, tuple[str, ...]]:
    if domain_status != "READY":
        return domain_status, (domain_status,)
    if rate is None:
        return "NO_TARGET", ("TARGET_MASK_EMPTY",)
    if rate >= study.target_coverage:
        return "COMPLETE", ("COVERAGE_TARGET_REACHED",)
    if point_count == 0:
        return "PARTIAL", ("NO_PEAK_ABOVE_THRESHOLD",)
    return "PARTIAL", ("MAX_STATIONS_OR_THRESHOLD",)


def propose_from_heatmap(mask: np.ndarray, heatmap: np.ndarray, study: ImitationStudy, *,
        kind: str = "supplied", source_ref: str = "plan-imitation:heatmap",
        target_id: str = "imitation", mask_kind: str = "synthetic",
        teacher=None) -> LearnedCoverageProposal:
    """热图只提供候选分数。点是否允许，由 prepare_coverage_domain 决定。"""
    sample, _model, domain, _kernel = feasible_centers(
        mask, study, source_ref=source_ref, target_id=target_id, mask_kind=mask_kind,
    )
    if teacher is None:
        teacher = teacher_example(sample.mask, study, kind=kind, source_ref=source_ref + ":teacher",
            target_id=target_id, mask_kind=mask_kind)
    points: tuple[tuple[int, int], ...] = ()
    evaluation = None
    if domain.status == "READY" and domain.candidate_centers_mask is not None:
        points = decode_and_order(np.asarray(heatmap), domain.candidate_centers_mask, study)
        evaluation = evaluate_points(sample.mask, points, study)
    rate = None if evaluation is None else evaluation.metrics.geometric_coverage_rate
    status, reasons = _status_for(None if rate is None else float(rate), study, len(points), domain.status)
    return LearnedCoverageProposal(
        points_px=points,
        route_length_px=route_length_px(points, study.start_px),
        status=status,
        reason_codes=reasons,
        teacher_points_px=teacher.points_px,
        teacher_route_length_px=teacher.route_length_px,
        teacher_coverage_rate=teacher.coverage_rate,
        teacher_status=teacher.status,
        evaluation=evaluation,
        kind=kind,
        source_ref=source_ref,
    )
