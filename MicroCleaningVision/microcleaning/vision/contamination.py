"""污染测量结果（成员 B 的视觉识别与测量模块）。

这个对象只描述图像里测到了什么。没有相机标定时，B 能诚实给出的单位只有像素，
因此定位误差使用 ``uncertainty_px``，不能提前写成毫米误差。毫米坐标由独立标定
把像素转换到工作台后，才进入共享 ``StateEstimate``。

这是视觉内部的测量合同，不是 firmware 的 MCV1 串口帧。测量对象不携带
COM 口、泵时长或引脚；只有 C 的控制器才能将经批准的动作翻译为固件命令。
"""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ContaminationMeasurement:
    """一次污染分割的最小结果。

    area_px 是污染像素数量，centroid_px 为图像中的 (x, y)，无中心时为 None。
    uncertainty_px 是像素位置不确定度，confidence 是 [0, 1] 内的算法分数，
    不是经校准的成功概率。mask_ref 和 algorithm_version 用于追溯。
    component_count 保留默认 0，兼容未提供区域数量的已有调用。

    validate() 显式校验而不修改或补齐数据；图像尺寸和 Mask 内容不在此对象中，
    因此这里不验证坐标上界、面积与 Mask 一致性或中心是否落在污染区域内。
    """

    area_px: float
    centroid_px: tuple[float, float] | None
    uncertainty_px: float
    confidence: float
    mask_ref: str | None = None
    component_count: int = 0
    algorithm_version: str = "unavailable"

    def validate(self) -> None:
        """拒绝非法类型、布尔值、非有限数值和越界分数，统一抛出 ValueError。"""
        if not _is_finite_number(self.area_px) or self.area_px < 0:
            raise ValueError("area_px 必须是非负有限数值")
        if not _is_finite_number(self.uncertainty_px) or self.uncertainty_px < 0:
            raise ValueError("uncertainty_px 必须是非负有限数值")
        if not _is_finite_number(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence 必须是 0～1 之间的有限数值")
        if self.centroid_px is not None:
            if not isinstance(self.centroid_px, (tuple, list)) or len(self.centroid_px) != 2:
                raise ValueError("centroid_px 必须是 (x, y) 或 None")
            if not all(_is_finite_number(value) and value >= 0 for value in self.centroid_px):
                raise ValueError("centroid_px 必须包含非负有限数值")
        if isinstance(self.component_count, bool) or not isinstance(self.component_count, int) or self.component_count < 0:
            raise ValueError("component_count 必须是非负整数")


def _is_finite_number(value: object) -> bool:
    """保持原有 Python int/float 数值接口，不将字符串或布尔值隐式转成数字。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False
