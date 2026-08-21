"""第一版 HSV 污染分割基线（成员 B）。

默认参数寻找“高饱和度红色标记物”。这些参数是Demo起点，不是经过真实显微数据
验证的通用阈值。真实样品颜色改变时，应保存一版新策略并做人工标注对照。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from microcleaning.vision.contamination import ContaminationMeasurement


HSV_BASELINE_VERSION = "hsv-red-baseline-v0.1"


@dataclass(frozen=True)
class HSVSegmentationPolicy:
    hue_low_1: int = 0
    hue_high_1: int = 12
    hue_low_2: int = 168
    hue_high_2: int = 179
    saturation_min: int = 80
    value_min: int = 45
    min_component_area_px: int = 30
    morphology_kernel_px: int = 3

    def validate(self) -> None:
        if not 0 <= self.hue_low_1 <= self.hue_high_1 <= 179:
            raise ValueError("第一段Hue范围必须位于0～179")
        if not 0 <= self.hue_low_2 <= self.hue_high_2 <= 179:
            raise ValueError("第二段Hue范围必须位于0～179")
        if not 0 <= self.saturation_min <= 255 or not 0 <= self.value_min <= 255:
            raise ValueError("S/V阈值必须位于0～255")
        if self.min_component_area_px <= 0:
            raise ValueError("min_component_area_px必须大于0")
        if self.morphology_kernel_px <= 0 or self.morphology_kernel_px % 2 == 0:
            raise ValueError("形态学核必须是正奇数")


@dataclass(frozen=True)
class SegmentationResult:
    """运行时结果；mask是0/255的uint8图像。"""

    measurement: ContaminationMeasurement
    mask: Any


def read_bgr_image(image_path: str | Path) -> Any:
    """读取jpg/png为BGR像素；不接受仅有路径但无法解码的伪输入。"""

    cv2, np = _load_dependencies()
    path = Path(image_path)
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise ValueError("视觉基线只支持jpg/jpeg/png")
    if not path.is_file():
        raise FileNotFoundError(f"图片不存在：{path}")
    image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"OpenCV无法解码图片：{path}")
    return image


def segment_contamination(
    image: Any,
    *,
    policy: HSVSegmentationPolicy = HSVSegmentationPolicy(),
) -> SegmentationResult:
    """输出mask、像素面积、像素中心和规则分数。"""

    cv2, np = _load_dependencies()
    policy.validate()
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.size == 0:
        raise ValueError("image必须是非空uint8 NumPy图像")
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("HSV基线要求BGR三通道图像")

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lower_1 = np.array((policy.hue_low_1, policy.saturation_min, policy.value_min), dtype=np.uint8)
    upper_1 = np.array((policy.hue_high_1, 255, 255), dtype=np.uint8)
    lower_2 = np.array((policy.hue_low_2, policy.saturation_min, policy.value_min), dtype=np.uint8)
    upper_2 = np.array((policy.hue_high_2, 255, 255), dtype=np.uint8)
    raw_mask = cv2.bitwise_or(cv2.inRange(hsv, lower_1, upper_1), cv2.inRange(hsv, lower_2, upper_2))
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
    if area_px > 0:
        moments = cv2.moments(mask, binaryImage=True)
        centroid = (float(moments["m10"] / moments["m00"]), float(moments["m01"] / moments["m00"]))
        saturation = hsv[:, :, 1][mask > 0]
        confidence = float(np.clip(float(saturation.mean()) / 255.0, 0.0, 1.0))
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
        algorithm_version=HSV_BASELINE_VERSION,
    )
    measurement.validate()
    return SegmentationResult(measurement=measurement, mask=mask)


def _load_dependencies() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "HSV分割需要感知依赖；请在项目.venv安装requirements/perception-opencv.txt"
        ) from exc
    return cv2, np
