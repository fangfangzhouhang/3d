"""污染测量结果（成员 B 的视觉识别与测量模块）。

这个对象只描述图像里测到了什么。没有相机标定时，B 能诚实给出的单位只有像素，
因此定位误差使用 ``uncertainty_px``，不能提前写成毫米误差。毫米坐标由独立标定
把像素转换到工作台后，才进入共享 ``StateEstimate``。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContaminationMeasurement:
    """一次污染分割的最小结果。"""

    area_px: float
    centroid_px: tuple[float, float] | None
    uncertainty_px: float
    confidence: float
    mask_ref: str | None = None
    component_count: int = 0
    algorithm_version: str = "unavailable"

    def validate(self) -> None:
        """拒绝负面积、非法坐标和伪造的置信度。"""
        if self.area_px < 0 or self.area_px != self.area_px:
            raise ValueError("area_px 必须是非负有限数值")
        if self.uncertainty_px < 0 or self.uncertainty_px != self.uncertainty_px:
            raise ValueError("uncertainty_px 必须是非负有限数值")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence 必须在 0～1 之间")
        if self.centroid_px is not None:
            if len(self.centroid_px) != 2:
                raise ValueError("centroid_px 必须是 (x, y) 或 None")
            if not all(isinstance(value, (int, float)) and value == value and value >= 0 for value in self.centroid_px):
                raise ValueError("centroid_px 必须包含非负有限数值")
        if not isinstance(self.component_count, int) or self.component_count < 0:
            raise ValueError("component_count 必须是非负整数")
