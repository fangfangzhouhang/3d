"""图像质量入口（成员 A 负责）。

本文件先解决一个容易被忽略的问题：图像“能打开”不等于图像“可测量”。
当前框架接收已经计算出的分数，统一生成质量标记和 ``Observation``；后续成员 A
再把拉普拉斯清晰度、曝光比例等真实算法接到这里，不需要改下游接口。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from microcleaning.contracts import Observation


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

