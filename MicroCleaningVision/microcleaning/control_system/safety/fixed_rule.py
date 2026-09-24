"""固定规则动作申请（成员 C 的目标规划与控制仿真模块）。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from microcleaning.contracts import ActionRequest, StateEstimate


PUMP_IN_PLACE = "PUMP_IN_PLACE"
NOZZLE_FIXED_FRAME = "nozzle_fixed"
IN_PLACE_TARGET_MM = (0.0, 0.0)
DEFAULT_IN_PLACE_DURATION_MS = 200
MAX_IN_PLACE_DURATION_MS = 300
PUMP_IN_PLACE_RULE_VERSION = "pump-in-place-v0"


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


def propose_pump_in_place(
    state: StateEstimate,
    policy: FixedActionPolicy | None = None,
) -> ActionRequest | None:
    """视野内有目标时申请一次定点短喷；不要求 work_mm 标定，也不申请 XY。

    ``ActionRequest.target_centroid_mm`` 仍必填，因此使用 ``nozzle_fixed`` 坐标系的
    ``(0, 0)`` 表示“喷头所在位置原地脉冲”，而不是把像素中心伪装成毫米坐标。
    """
    if policy is None:
        policy = FixedActionPolicy(
            duration_ms=DEFAULT_IN_PLACE_DURATION_MS,
            version=PUMP_IN_PLACE_RULE_VERSION,
        )
    if state.target_area_px <= 0 or state.target_centroid_px is None:
        return None
    return ActionRequest(
        action_id=f"action_{uuid4().hex[:12]}",
        task_id=state.task_id,
        state_id=state.state_id,
        target_centroid_mm=IN_PLACE_TARGET_MM,
        coordinate_frame=NOZZLE_FIXED_FRAME,
        primitive=PUMP_IN_PLACE,
        duration_ms=policy.duration_ms,
        pressure=policy.pressure,
        constraints={
            "xy_motion": False,
            "calibration_required": False,
            "work_mm": False,
            "target_meaning": "nozzle_fixed (0,0) is an in-place pulse at the fixed nozzle",
            "target_centroid_px": [
                float(state.target_centroid_px[0]),
                float(state.target_centroid_px[1]),
            ],
            "host_duration_limit_ms": MAX_IN_PLACE_DURATION_MS,
        },
        expected_effect="bounded in-place pump pulse; does not claim cleaning success or XY motion",
        rule_version=policy.version,
    )
