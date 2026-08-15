"""图像质量入口（成员 A 的数据与模型模块）。

本文件区分两个容易混淆的概念：

``ImageMetrics`` 是从真实像素测得的原始量，例如拉普拉斯方差和过曝比例；
``ImageQuality`` 是依据一版明确策略，把原始量转换为 0～1 质量分数后的判断。

Gate 1 使用的默认阈值只是“链路冒烟阈值”，用于证明真实图片能够进入软件，不能
直接写成显微成像的科学阈值。正式阈值必须由固定相机、光照和倍率的数据重新确定。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from microcleaning.contracts import Observation


QUALITY_ALGORITHM_VERSION = "quality-gate1-v0"


@dataclass(frozen=True)
class ImageQuality:
    """一张图像的三项基础可信度，取值都在 0～1。"""

    focus: float
    illumination: float
    confidence: float
    minimum: float = 0.70

    def flags(self) -> tuple[str, ...]:
        """返回机器可读的质量问题；空元组表示通过当前质量门。"""
        _validate_score("focus", self.focus)
        _validate_score("illumination", self.illumination)
        _validate_score("confidence", self.confidence)
        return tuple(
            flag
            for value, flag in (
                (self.focus, "FOCUS_LOW"),
                (self.illumination, "ILLUMINATION_LOW"),
                (self.confidence, "CONFIDENCE_LOW"),
            )
            if value < self.minimum
        )


@dataclass(frozen=True)
class ImageMetrics:
    """从一张已解码图片直接测得、尚未归一化的质量指标。"""

    width_px: int
    height_px: int
    channels: int
    laplacian_variance: float
    mean_intensity: float
    dark_fraction: float
    bright_fraction: float


@dataclass(frozen=True)
class ImageQualityPolicy:
    """把原始指标转换成质量分数的显式策略。

    默认值只服务于手机/USB 显微镜的 Gate 1 链路检查。不同分辨率、倍率和表面纹理
    会改变拉普拉斯方差，因此不能把这些值当作跨设备通用标准。
    """

    focus_reference: float = 100.0
    acceptable_mean_low: float = 50.0
    acceptable_mean_high: float = 205.0
    dark_pixel_max: int = 10
    bright_pixel_min: int = 245
    max_clipped_fraction: float = 0.20
    minimum_quality: float = 0.70

    def validate(self) -> None:
        if self.focus_reference <= 0:
            raise ValueError("focus_reference 必须大于 0")
        if not 0 <= self.acceptable_mean_low < self.acceptable_mean_high <= 255:
            raise ValueError("可接受亮度范围必须位于 0～255 且下限小于上限")
        if not 0 <= self.dark_pixel_max < self.bright_pixel_min <= 255:
            raise ValueError("暗/亮像素阈值必须位于 0～255 且暗阈值小于亮阈值")
        if not 0 < self.max_clipped_fraction <= 1:
            raise ValueError("max_clipped_fraction 必须位于 (0, 1]")
        if not 0 <= self.minimum_quality <= 1:
            raise ValueError("minimum_quality 必须位于 0～1")


@dataclass(frozen=True)
class ImageInspection:
    """一次真实文件检查的可追溯结果。"""

    path: str
    sha256: str
    algorithm_version: str
    policy: ImageQualityPolicy
    metrics: ImageMetrics
    quality: ImageQuality


def inspect_image_file(
    image_path: str | Path,
    *,
    policy: ImageQualityPolicy = ImageQualityPolicy(),
) -> ImageInspection:
    """读取本地图片并计算 Gate 1 质量指标。

    该函数会真正解码像素。文件不存在、不是普通文件、OpenCV 无法解码时都会失败，
    不会仅凭一个看起来像图片的路径伪造 Observation。
    """

    cv2, np = _load_perception_dependencies()
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"图片不存在：{path}")
    if not path.is_file():
        raise ValueError(f"图片路径不是普通文件：{path}")
    payload = path.read_bytes()
    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"OpenCV 无法解码图片：{path}")
    metrics, quality = measure_image_quality(image, policy=policy)
    return ImageInspection(
        path=str(path),
        sha256=hashlib.sha256(payload).hexdigest(),
        algorithm_version=QUALITY_ALGORITHM_VERSION,
        policy=policy,
        metrics=metrics,
        quality=quality,
    )


def measure_image_quality(
    image: Any,
    *,
    policy: ImageQualityPolicy = ImageQualityPolicy(),
) -> tuple[ImageMetrics, ImageQuality]:
    """从已解码像素计算原始指标和暂定质量判断。"""

    cv2, np = _load_perception_dependencies()
    policy.validate()
    if not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("image 必须是非空 NumPy 图像数组")
    if image.ndim == 2:
        gray = image
        channels = 1
    elif image.ndim == 3 and image.shape[2] in (3, 4):
        conversion = cv2.COLOR_BGRA2GRAY if image.shape[2] == 4 else cv2.COLOR_BGR2GRAY
        gray = cv2.cvtColor(image, conversion)
        channels = int(image.shape[2])
    else:
        raise ValueError("只支持灰度、BGR 或 BGRA 图像")
    if gray.dtype != np.uint8:
        raise ValueError("Gate 1 只接受 8 位图像；请先明确高位深图像的归一化规则")

    laplacian_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    mean_intensity = float(gray.mean())
    dark_fraction = float(np.count_nonzero(gray <= policy.dark_pixel_max) / gray.size)
    bright_fraction = float(np.count_nonzero(gray >= policy.bright_pixel_min) / gray.size)
    metrics = ImageMetrics(
        width_px=int(gray.shape[1]),
        height_px=int(gray.shape[0]),
        channels=channels,
        laplacian_variance=laplacian_variance,
        mean_intensity=mean_intensity,
        dark_fraction=dark_fraction,
        bright_fraction=bright_fraction,
    )

    focus = _clamp01(laplacian_variance / policy.focus_reference)
    if mean_intensity < policy.acceptable_mean_low:
        brightness_score = mean_intensity / policy.acceptable_mean_low
    elif mean_intensity > policy.acceptable_mean_high:
        brightness_score = (255.0 - mean_intensity) / (255.0 - policy.acceptable_mean_high)
    else:
        brightness_score = 1.0
    clipped_score = 1.0 - max(dark_fraction, bright_fraction) / policy.max_clipped_fraction
    illumination = min(_clamp01(brightness_score), _clamp01(clipped_score))
    confidence = min(focus, illumination)
    quality = ImageQuality(
        focus=focus,
        illumination=illumination,
        confidence=confidence,
        minimum=policy.minimum_quality,
    )
    quality.flags()
    return metrics, quality


def build_observation(
    *,
    task_id: str,
    frame_id: str,
    raw_image_ref: str,
    quality: ImageQuality,
    software_version: str = "visual-replay-v0",
) -> Observation:
    """把图像引用和质量测量封装成统一观测记录。"""
    if not task_id or not frame_id or not raw_image_ref:
        raise ValueError("task_id、frame_id 和 raw_image_ref 不能为空")
    return Observation(
        observation_id=f"obs_{uuid4().hex[:12]}",
        task_id=task_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        frame_id=frame_id,
        raw_image_ref=raw_image_ref,
        focus_quality=quality.focus,
        illumination_quality=quality.illumination,
        confidence=quality.confidence,
        quality_flags=quality.flags(),
        software_version=software_version,
    )


def _validate_score(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or value != value or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} 必须是 0～1 之间的有限数值")


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _load_perception_dependencies() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "真实图像检查需要感知依赖；请在项目 .venv 中安装 "
            "requirements/perception-opencv.txt"
        ) from exc
    return cv2, np
