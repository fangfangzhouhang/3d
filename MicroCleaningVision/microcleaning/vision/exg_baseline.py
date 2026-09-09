"""Excess Green / Excess Red 色彩指数基线（成员 B）。

算法思想来自开源田间杂草定位（OpenWeedLocator 使用的 Excess Green：``2G-R-B``）。
本模块只输出与 HSV/Otsu 相同的像素 Mask，**不**读写 GPIO、串口或泵。

- ``exg``：偏绿区域，适合绿渍对照。
- ``exr``：Excess Red（``1.4R-G-B``），适合本项目红色标记对照。

默认 Demo 入口仍是 HSV；这两条只作为可选 A/B，过关数字仍只看 labeled GT。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from microcleaning.vision.contamination import ContaminationMeasurement
from microcleaning.vision.hsv_baseline import SegmentationResult


EXG_BASELINE_VERSION = "exg-owl-v0.1"
EXR_BASELINE_VERSION = "exr-owl-v0.1"
ColorIndexName = Literal["exg", "exr"]


@dataclass(frozen=True)
class ColorIndexPolicy:
    index: ColorIndexName = "exg"
    morphology_kernel_px: int = 3
    min_component_area_px: int = 30

    def validate(self) -> None:
        if self.index not in {"exg", "exr"}:
            raise ValueError("index必须是exg或exr")
        if self.morphology_kernel_px <= 0 or self.morphology_kernel_px % 2 == 0:
            raise ValueError("形态学核必须是正奇数")
        if self.min_component_area_px <= 0:
            raise ValueError("min_component_area_px必须大于0")


def segment_contamination(
    image: Any,
    *,
    policy: ColorIndexPolicy = ColorIndexPolicy(),
) -> SegmentationResult:
    """用 Excess Green 生成 mask；默认 OWL 风格 ``2G-R-B``。"""

    return _segment_color_index(image, policy=policy)


def segment_excess_red(
    image: Any,
    *,
    policy: ColorIndexPolicy | None = None,
) -> SegmentationResult:
    """用 Excess Red 生成 mask，供红色标记与 HSV 对照。"""

    if policy is None:
        chosen = ColorIndexPolicy(index="exr")
    else:
        chosen = ColorIndexPolicy(
            index="exr",
            morphology_kernel_px=policy.morphology_kernel_px,
            min_component_area_px=policy.min_component_area_px,
        )
    return _segment_color_index(image, policy=chosen)


def _segment_color_index(image: Any, *, policy: ColorIndexPolicy) -> SegmentationResult:
    cv2, np = _load_dependencies()
    policy.validate()
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.size == 0:
        raise ValueError("image必须是非空uint8 NumPy图像")
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("色彩指数基线要求BGR三通道图像")

    index_map = _color_index(image, policy.index, np)
    if int(index_map.max()) == 0:
        raw_mask = np.zeros(image.shape[:2], dtype=np.uint8)
    else:
        _, raw_mask = cv2.threshold(index_map, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    kernel = np.ones((policy.morphology_kernel_px, policy.morphology_kernel_px), dtype=np.uint8)
    cleaned = cv2.morphologyEx(raw_mask, cv2.MORPH_OPEN, kernel)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, kernel)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(cleaned, connectivity=8)
    mask = np.zeros_like(cleaned)
    kept_components = 0
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area >= policy.min_component_area_px:
            mask[labels == label] = 255
            kept_components += 1

    area_px = float(cv2.countNonZero(mask))
    version = EXG_BASELINE_VERSION if policy.index == "exg" else EXR_BASELINE_VERSION
    if area_px > 0:
        moments = cv2.moments(mask, binaryImage=True)
        centroid = (float(moments["m10"] / moments["m00"]), float(moments["m01"] / moments["m00"]))
        score = index_map[mask > 0]
        confidence = float(np.clip(float(score.mean()) / 255.0, 0.0, 1.0))
        uncertainty_px = max(1.0, policy.morphology_kernel_px / 2.0)
    else:
        centroid = None
        confidence = 0.0
        uncertainty_px = 0.0
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


def _color_index(image, index: ColorIndexName, np):
    blue = image[:, :, 0].astype(np.float32)
    green = image[:, :, 1].astype(np.float32)
    red = image[:, :, 2].astype(np.float32)
    if index == "exg":
        raw = 2.0 * green - red - blue
    else:
        raw = 1.4 * red - green - blue
    return np.clip(raw, 0, 255).astype(np.uint8)


def _load_dependencies() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "色彩指数分割需要感知依赖；请在项目.venv安装requirements/perception-opencv.txt"
        ) from exc
    return cv2, np
