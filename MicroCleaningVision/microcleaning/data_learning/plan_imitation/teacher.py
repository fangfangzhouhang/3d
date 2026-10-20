"""用现有贪心覆盖规划当教师。标签是喷点热图，不是人工清洗真值。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from microcleaning.control_system.planning.coverage_metrics import evaluate_coverage
from microcleaning.control_system.planning.coverage_model import (
    Evidence,
    FootprintProfile,
    KernelResult,
    build_kernel,
)
from microcleaning.control_system.planning.coverage_planner import (
    CoverageInput,
    CoveragePlan,
    plan_coverage,
    prepare_coverage_domain,
)
from microcleaning.data_learning.plan_imitation.config import ImitationStudy

_KERNELS: dict[int, KernelResult] = {}


def virtual_kernel(diameter_px: int) -> KernelResult:
    cached = _KERNELS.get(diameter_px)
    if cached is not None:
        return cached
    profile = FootprintProfile(
        profile_id=f"plan-imitation-virtual-disk-{diameter_px}px",
        diameter=float(diameter_px),
        unit="px",
        evidence=Evidence("assumed", "plan-imitation:synthetic-virtual-footprint"),
        semantics="geometric_proxy",
    )
    result = build_kernel(profile)
    if result.kernel is None:
        raise RuntimeError("VIRTUAL_KERNEL_UNAVAILABLE:" + ",".join(result.reason_codes))
    _KERNELS[diameter_px] = result
    return result


def coverage_input(mask: np.ndarray, *, source_ref: str, target_id: str = "imitation",
        mask_kind: str = "synthetic") -> CoverageInput:
    array = np.asarray(mask)
    if array.dtype != np.uint8 or (array.size and int(array.max()) > 1):
        array = (array > 0).astype(np.uint8)
    return CoverageInput(array, target_id, mask_kind, source_ref)


def teacher_plan(mask: np.ndarray, study: ImitationStudy, *, source_ref: str = "plan-imitation:greedy-teacher",
        target_id: str = "imitation", mask_kind: str = "synthetic") -> CoveragePlan:
    """调用 plan_coverage。返回的 CoveragePlan.executable 固定为 False。"""
    model = virtual_kernel(study.diameter_px)
    sample = coverage_input(mask, source_ref=source_ref, target_id=target_id, mask_kind=mask_kind)
    return plan_coverage(sample, model, policy=study.policy())


def stations_to_heatmap(shape_hw: tuple[int, int], points_px: tuple[tuple[int, int], ...], sigma_px: float) -> np.ndarray:
    """在教师驻点上放高斯峰，峰值 1。没有驻点时热图为 0。"""
    heat = np.zeros(shape_hw, dtype=np.float32)
    if not points_px:
        return heat
    yy, xx = np.mgrid[0:shape_hw[0], 0:shape_hw[1]]
    denom = 2.0 * float(sigma_px) ** 2
    for x_px, y_px in points_px:
        peak = np.exp(-((xx - x_px) ** 2 + (yy - y_px) ** 2) / denom).astype(np.float32)
        heat = np.maximum(heat, peak)
    return heat


@dataclass(frozen=True)
class TeacherExample:
    mask: np.ndarray
    heatmap: np.ndarray
    points_px: tuple[tuple[int, int], ...]
    route_length_px: float
    coverage_rate: float | None
    station_count: int
    kind: str
    status: str
    source_ref: str

    @property
    def executable(self) -> bool:
        return False


def teacher_example(mask: np.ndarray, study: ImitationStudy, *, kind: str = "supplied",
        source_ref: str = "plan-imitation:greedy-teacher", target_id: str = "imitation",
        mask_kind: str = "synthetic") -> TeacherExample:
    plan = teacher_plan(mask, study, source_ref=source_ref, target_id=target_id, mask_kind=mask_kind)
    if plan.executable:
        raise RuntimeError("TEACHER_PLAN_BECAME_EXECUTABLE")
    rate = None if plan.metrics is None else plan.metrics.geometric_coverage_rate
    heatmap = stations_to_heatmap(plan.input.mask.shape, plan.points_px, study.heatmap_sigma_px)
    return TeacherExample(
        mask=np.asarray(plan.input.mask, dtype=np.uint8),
        heatmap=heatmap,
        points_px=plan.points_px,
        route_length_px=float(plan.route_length_px),
        coverage_rate=None if rate is None else float(rate),
        station_count=len(plan.points_px),
        kind=kind,
        status=plan.status,
        source_ref=source_ref,
    )


def feasible_centers(mask: np.ndarray, study: ImitationStudy, *, source_ref: str, target_id: str,
        mask_kind: str):
    """复用 P2 可行域：完整足迹必须落在允许喷湿范围内。"""
    model = virtual_kernel(study.diameter_px)
    sample = coverage_input(mask, source_ref=source_ref, target_id=target_id, mask_kind=mask_kind)
    domain = prepare_coverage_domain(sample, model, policy=study.policy())
    kernel = model.kernel
    assert kernel is not None
    return sample, model, domain, kernel


def evaluate_points(mask: np.ndarray, points_px: tuple[tuple[int, int], ...], study: ImitationStudy):
    kernel = virtual_kernel(study.diameter_px).kernel
    assert kernel is not None
    return evaluate_coverage(np.asarray(mask), kernel, points_px)
