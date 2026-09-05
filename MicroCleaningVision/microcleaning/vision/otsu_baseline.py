"""Otsu / 自适应阈值污染分割候选（成员 B）。

这是 PR #2 根目录 ``p1_baseline.py`` 的规范版本：输出与 HSV 基线相同的
uint8 Mask 和 ``ContaminationMeasurement``，供人工 Mask 对照。它不是 Demo
默认入口；官方软件链仍使用 ``hsv_baseline.py``。

算法假设污渍比背景暗。亮色或高饱和标记物应继续用 HSV，不要把本候选写成
已经替代 v0.1。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from microcleaning.vision.contamination import ContaminationMeasurement
from microcleaning.vision.hsv_baseline import SegmentationResult


OTSU_BASELINE_VERSION = "otsu-v-baseline-v0.1"


@dataclass(frozen=True)
class OtsuSegmentationPolicy:
    blur_kernel_px: int = 5
    morphology_kernel_px: int = 5
    min_component_area_px: int = 15
    fallback_min_component_area_px: int = 50
    max_area_ratio: float = 0.05
    edge_margin_px: int = 3
    adaptive_block_size: int = 31
    adaptive_c: int = 5

    def validate(self) -> None:
        if self.blur_kernel_px <= 0 or self.blur_kernel_px % 2 == 0:
            raise ValueError("blur_kernel_px必须是正奇数")
        if self.morphology_kernel_px <= 0 or self.morphology_kernel_px % 2 == 0:
            raise ValueError("morphology_kernel_px必须是正奇数")
        if self.min_component_area_px <= 0:
            raise ValueError("min_component_area_px必须大于0")
        if self.fallback_min_component_area_px <= 0:
            raise ValueError("fallback_min_component_area_px必须大于0")
        if not 0.0 < self.max_area_ratio < 1.0:
            raise ValueError("max_area_ratio必须位于0～1之间")
        if self.edge_margin_px < 0:
            raise ValueError("edge_margin_px不能为负")
        if self.adaptive_block_size <= 1 or self.adaptive_block_size % 2 == 0:
            raise ValueError("adaptive_block_size必须是大于1的奇数")


def segment_contamination(
    image: Any,
    *,
    policy: OtsuSegmentationPolicy = OtsuSegmentationPolicy(),
) -> SegmentationResult:
    """用V通道暗区生成mask、像素面积、像素中心和规则分数。"""

    cv2, np = _load_dependencies()
    policy.validate()
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.size == 0:
        raise ValueError("image必须是非空uint8 NumPy图像")
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Otsu基线要求BGR三通道图像")

    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    value = hsv[:, :, 2]
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (policy.morphology_kernel_px, policy.morphology_kernel_px),
    )

    blurred = cv2.GaussianBlur(value, (policy.blur_kernel_px, policy.blur_kernel_px), 0)
    _, raw_mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    raw_mask = cv2.morphologyEx(raw_mask, cv2.MORPH_OPEN, kernel, iterations=1)
    raw_mask = cv2.morphologyEx(raw_mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    mask, kept_components = _keep_valid_components(
        raw_mask,
        min_area_px=policy.min_component_area_px,
        max_area_px=height * width * policy.max_area_ratio,
        edge_margin_px=policy.edge_margin_px,
        cv2=cv2,
        np=np,
    )
    used_fallback = False

    if kept_components == 0:
        raw_mask = cv2.adaptiveThreshold(
            value,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            policy.adaptive_block_size,
            policy.adaptive_c,
        )
        raw_mask = cv2.morphologyEx(raw_mask, cv2.MORPH_OPEN, kernel, iterations=1)
        raw_mask = cv2.morphologyEx(raw_mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        mask, kept_components = _keep_valid_components(
            raw_mask,
            min_area_px=policy.fallback_min_component_area_px,
            max_area_px=height * width * policy.max_area_ratio,
            edge_margin_px=policy.edge_margin_px,
            cv2=cv2,
            np=np,
        )
        used_fallback = kept_components > 0

    area_px = float(cv2.countNonZero(mask))
    if area_px > 0:
        moments = cv2.moments(mask, binaryImage=True)
        centroid = (float(moments["m10"] / moments["m00"]), float(moments["m01"] / moments["m00"]))
        stain_value = float(value[mask > 0].mean())
        background_value = float(value[mask == 0].mean()) if int((mask == 0).sum()) > 0 else 255.0
        contrast = (background_value - stain_value) / 255.0
        confidence = float(np.clip(contrast, 0.0, 1.0))
        if used_fallback:
            confidence = float(np.clip(confidence * 0.7, 0.0, 1.0))
        uncertainty_px = max(1.0, policy.morphology_kernel_px / 2.0)
        version = f"{OTSU_BASELINE_VERSION}+adaptive" if used_fallback else OTSU_BASELINE_VERSION
    else:
        centroid = None
        confidence = 0.0
        uncertainty_px = 0.0
        version = OTSU_BASELINE_VERSION

    measurement = ContaminationMeasurement(
        area_px=area_px,
        centroid_px=centroid,
        uncertainty_px=uncertainty_px,
        confidence=confidence,
        component_count=kept_components,
        algorithm_version=version,
    )
    measurement.validate()
    return SegmentationResult(measurement=measurement, mask=mask)


def _keep_valid_components(
    binary_mask: Any,
    *,
    min_area_px: int,
    max_area_px: float,
    edge_margin_px: int,
    cv2: Any,
    np: Any,
) -> tuple[Any, int]:
    height, width = binary_mask.shape[:2]
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary_mask, connectivity=8)
    mask = np.zeros_like(binary_mask)
    kept = 0
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        left = int(stats[label, cv2.CC_STAT_LEFT])
        top = int(stats[label, cv2.CC_STAT_TOP])
        box_width = int(stats[label, cv2.CC_STAT_WIDTH])
        box_height = int(stats[label, cv2.CC_STAT_HEIGHT])
        if area < min_area_px or area > max_area_px:
            continue
        if (
            left < edge_margin_px
            or top < edge_margin_px
            or left + box_width > width - edge_margin_px
            or top + box_height > height - edge_margin_px
        ):
            continue
        mask[labels == label] = 255
        kept += 1
    return mask, kept


def _load_dependencies() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "Otsu分割需要感知依赖；请在项目.venv安装requirements/perception-opencv.txt"
        ) from exc
    return cv2, np
