"""状态估计器（成员 B 的视觉识别与测量模块）。

Observation 是“拍到了什么”，StateEstimate 是“系统据此相信什么”。这个文件把
图像质量、污染测量、标定状态和控制器状态集中到同一份状态，供决策层只读使用。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import uuid4

from microcleaning.contracts import Observation, StateEstimate
from microcleaning.vision.contamination import ContaminationMeasurement


def estimate_state(
    observation: Observation,
    measurement: ContaminationMeasurement,
    *,
    calibration_version: str = "unavailable",
    calibration_valid: bool = False,
    target_centroid_mm: tuple[float, float] | None = None,
    device_state: Mapping[str, object] | None = None,
    prior_actions: Sequence[str] = (),
) -> StateEstimate:
    """生成状态；默认标定无效、控制器不可用，遵循失效安全原则。"""
    measurement.validate()
    if target_centroid_mm is not None and not calibration_valid:
        raise ValueError("没有有效标定时不得提供毫米坐标")
    devices = {
        "controller_connected": False,
        "interlock_ok": False,
        "e_stop_active": False,
    }
    if device_state:
        devices.update(device_state)
    # 图像质量来自 Observation，调用方不能通过 device_state 把坏图伪装成合格图。
    devices["observation_quality_ok"] = not observation.quality_flags
    return StateEstimate(
        state_id=f"state_{uuid4().hex[:12]}",
        task_id=observation.task_id,
        observation_id=observation.observation_id,
        target_centroid_mm=target_centroid_mm,
        target_area_px=measurement.area_px,
        coordinate_frame="work_mm",
        uncertainty_mm=measurement.uncertainty_mm,
        device_state=devices,
        calibration_version=calibration_version,
        calibration_valid=calibration_valid,
        prior_actions=tuple(prior_actions),
    )
