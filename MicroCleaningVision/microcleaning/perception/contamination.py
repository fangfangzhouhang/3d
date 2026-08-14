"""污染测量结果（成员 B 负责）。

这里暂时不实现 OpenCV 阈值分割，而是先固定分割模块必须交付的结果格式。
成员 B 后续无论使用 HSV、形态学还是模型，都只能把结果写进这个格式，不能直接
提出喷射动作。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContaminationMeasurement:
    """一次污染分割的最小结果。"""

    area_px: float
    centroid_px: tuple[float, float] | None
    uncertainty_mm: float
    confidence: float
    mask_ref: str | None = None

    def validate(self) -> None:
        """拒绝负面积、非法坐标和伪造的置信度。"""
        if self.area_px < 0 or self.area_px != self.area_px:
            raise ValueError("area_px 必须是非负有限数值")
        if self.uncertainty_mm < 0 or self.uncertainty_mm != self.uncertainty_mm:
            raise ValueError("uncertainty_mm 必须是非负有限数值")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence 必须在 0～1 之间")
        if self.centroid_px is not None and len(self.centroid_px) != 2:
            raise ValueError("centroid_px 必须是 (x, y) 或 None")
