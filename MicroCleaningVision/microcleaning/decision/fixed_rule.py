"""固定规则决策（成员 C 负责）。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from microcleaning.contracts import ActionRequest, StateEstimate


@dataclass(frozen=True)
class FixedActionPolicy:
    """软件回放使用的固定动作参数，不代表真实硬件参数。"""

    duration_ms: int = 200
    pressure: float = 0.30
    version: str = "fixed-replay-rule-v0"


def propose_action(
    state: StateEstimate,
    policy: FixedActionPolicy = FixedActionPolicy(),
) -> ActionRequest | None:
    """有污染且有定位时提出申请；没有目标时返回 None。"""
    if state.target_area_px <= 0 or state.target_centroid_mm is None:
        return None
    return ActionRequest(
        action_id=f"action_{uuid4().hex[:12]}",
        task_id=state.task_id,
        state_id=state.state_id,
        target_centroid_mm=state.target_centroid_mm,
        coordinate_frame=state.coordinate_frame,
        primitive="SPRAY_AT_POINT",
        duration_ms=policy.duration_ms,
        pressure=policy.pressure,
        constraints={"mode": "software_replay_only"},
        expected_effect="reduce measured contamination area in replay",
        rule_version=policy.version,
    )

