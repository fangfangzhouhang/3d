"""本地图像回放适配器（成员 A 的数据与模型模块）。

适配器（Adapter，负责把外部输入转换成项目统一接口）不会打开相机。它支持两种
明确区分的输入：

1. 旧 Mock 测试可以显式提供 ``quality``，只验证接口；
2. Gate 1 不提供 ``quality``，适配器必须读取真实图片并从像素计算质量。

第二种模式仍不是相机硬件证据，但它证明了真实像素进入软件，而不是只传递路径。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from microcleaning.ports import CameraPort
from microcleaning.contracts import Observation
from microcleaning.data_learning.image_quality import (
    QUALITY_ALGORITHM_VERSION,
    ImageInspection,
    ImageQuality,
    ImageQualityPolicy,
    build_observation,
    inspect_image_file,
)


@dataclass(frozen=True)
class ReplayFrame:
    phase: str
    frame_id: str
    raw_image_ref: str
    quality: ImageQuality | None = None


class ReplayCamera(CameraPort):
    """按 `pre` / `post` 阶段返回登记好的回放图像。"""

    def __init__(
        self,
        frames: tuple[ReplayFrame, ...],
        *,
        base_dir: str | Path | None = None,
        quality_policy: ImageQualityPolicy = ImageQualityPolicy(),
    ) -> None:
        self._frames = {frame.phase: frame for frame in frames}
        if len(self._frames) != len(frames):
            raise ValueError("同一个 phase 只能登记一张回放图像")
        self._base_dir = Path(base_dir) if base_dir is not None else Path.cwd()
        self._quality_policy = quality_policy
        self._inspections: dict[str, ImageInspection] = {}

    def capture(self, task_id: str, phase: str) -> Observation:
        try:
            frame = self._frames[phase]
        except KeyError as exc:
            raise KeyError(f"没有登记 {phase!r} 阶段的回放图像") from exc
        quality = frame.quality
        software_version = "visual-replay-v0"
        if quality is None:
            image_path = Path(frame.raw_image_ref)
            if not image_path.is_absolute():
                image_path = self._base_dir / image_path
            inspection = inspect_image_file(image_path, policy=self._quality_policy)
            self._inspections[phase] = inspection
            quality = inspection.quality
            software_version = f"visual-replay-v0/{QUALITY_ALGORITHM_VERSION}"
        return build_observation(
            task_id=task_id,
            frame_id=frame.frame_id,
            raw_image_ref=frame.raw_image_ref,
            quality=quality,
            software_version=software_version,
        )

    def inspection(self, phase: str) -> ImageInspection:
        """返回某阶段最近一次真实像素检查；Mock 模式没有该记录。"""

        try:
            return self._inspections[phase]
        except KeyError as exc:
            raise KeyError(f"{phase!r} 尚未完成真实像素检查") from exc
