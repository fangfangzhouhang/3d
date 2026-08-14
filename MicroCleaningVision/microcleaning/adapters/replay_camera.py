"""本地图像回放适配器（成员 A 负责）。

适配器（Adapter，负责把外部输入转换成项目统一接口）不会打开相机。它把预先登记
的前图、后图及质量分数转换为 Observation，让软件闭环可以先于硬件开发。
"""

from __future__ import annotations

from dataclasses import dataclass

from microcleaning.adapters.ports import CameraPort
from microcleaning.contracts import Observation
from microcleaning.perception.image_quality import ImageQuality, build_observation


@dataclass(frozen=True)
class ReplayFrame:
    phase: str
    frame_id: str
    raw_image_ref: str
    quality: ImageQuality


class ReplayCamera(CameraPort):
    """按 `pre` / `post` 阶段返回登记好的回放图像。"""

    def __init__(self, frames: tuple[ReplayFrame, ...]) -> None:
        self._frames = {frame.phase: frame for frame in frames}
        if len(self._frames) != len(frames):
            raise ValueError("同一个 phase 只能登记一张回放图像")

    def capture(self, task_id: str, phase: str) -> Observation:
        try:
            frame = self._frames[phase]
        except KeyError as exc:
            raise KeyError(f"没有登记 {phase!r} 阶段的回放图像") from exc
        return build_observation(
            task_id=task_id,
            frame_id=frame.frame_id,
            raw_image_ref=frame.raw_image_ref,
            quality=frame.quality,
        )
