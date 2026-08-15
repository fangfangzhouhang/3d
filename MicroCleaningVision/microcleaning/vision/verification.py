"""前后面积复检（成员 B 的视觉识别与测量模块）。"""

from __future__ import annotations

from dataclasses import dataclass

from microcleaning.contracts import ExecutionReceipt, NextRoute, Observation, VerificationResult


@dataclass(frozen=True)
class VerificationPolicy:
    """回放演示阈值；真实实验前必须用基线数据重新确定。"""

    minimum_removal_rate: float = 0.80


def verify_area_change(
    *,
    task_id: str,
    pre: Observation,
    post: Observation | None,
    pre_area_px: float,
    post_area_px: float | None,
    receipt: ExecutionReceipt | None,
    images_comparable: bool = True,
    damage_flag: bool = False,
    policy: VerificationPolicy = VerificationPolicy(),
) -> VerificationResult:
    """比较面积并给出 STOP / RETRY / HUMAN；RETRY 不代表自动重试。"""
    if damage_flag:
        return VerificationResult(task_id, pre.observation_id, getattr(post, "observation_id", None), post_area_px, None, True, NextRoute.HUMAN, ("DAMAGE_SUSPECTED",))
    if not receipt or not receipt.success or post is None or post_area_px is None:
        return VerificationResult(task_id, pre.observation_id, getattr(post, "observation_id", None), post_area_px, None, False, NextRoute.HUMAN, ("EXECUTION_OR_POST_EVIDENCE_MISSING",))
    if pre.quality_flags or post.quality_flags or not images_comparable:
        return VerificationResult(task_id, pre.observation_id, post.observation_id, post_area_px, None, False, NextRoute.HUMAN, ("PRE_POST_NOT_COMPARABLE",))
    if pre_area_px <= 0:
        return VerificationResult(task_id, pre.observation_id, post.observation_id, post_area_px, 1.0, False, NextRoute.STOP, ("NO_INITIAL_TARGET",))
    removal_rate = max(0.0, min(1.0, (pre_area_px - post_area_px) / pre_area_px))
    route = NextRoute.STOP if removal_rate >= policy.minimum_removal_rate else NextRoute.RETRY
    reason = "REPLAY_THRESHOLD_MET" if route is NextRoute.STOP else "RESIDUAL_REMAINS"
    return VerificationResult(task_id, pre.observation_id, post.observation_id, post_area_px, removal_rate, False, route, (reason,))
