"""固定规则动作申请（成员 C 的目标规划与控制仿真模块）。"""

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
    *,
    target_centroid_mm: tuple[float, float] | None = None,
) -> ActionRequest | None:
    """提出下一步点动作；路线中的目标可覆盖污染整体中心。"""
    target = target_centroid_mm or state.target_centroid_mm
    if state.target_area_px <= 0 or target is None:
        return None
    if not state.calibration_valid or state.coordinate_frame != "work_mm":
        return None
    return ActionRequest(
        action_id=f"action_{uuid4().hex[:12]}",
        task_id=state.task_id,
        state_id=state.state_id,
        target_centroid_mm=target,
        coordinate_frame=state.coordinate_frame,
        primitive="SPRAY_AT_POINT",
        duration_ms=policy.duration_ms,
        pressure=policy.pressure,
        constraints={"mode": "software_replay_only"},
        expected_effect="reduce measured contamination area after one bounded replay step",
        rule_version=policy.version,
    )
