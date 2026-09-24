"""工作平面占位：像素 → 假设毫米。不得把结果写成已验收标定。

几何是相似变换（缩放 + 可选翻转 + 旋转 + 原点平移），再加喷头相对光心的偏移。
完整单应性（homography，把倾斜平面上的像素投到工作平面）预留 ``homography_3x3``。
成员 B 的离线 ``mm_per_px`` JSON 可以填进 ``mm_per_px``，仍禁止 ``feeds_action_request``。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ASSUMED_WORK_FRAME = "assumed_work_mm"
DEFAULT_ASSUMED_MM_PER_PX = 0.01


@dataclass(frozen=True)
class WorkFrameConfig:
    """以后改标定，只改这里或 JSON，不要改规划规则。"""

    version: str = "work-frame-placeholder-v0"
    mm_per_px: float | None = None
    assumed_mm_per_px: float = DEFAULT_ASSUMED_MM_PER_PX
    origin_px: tuple[float, float] = (0.0, 0.0)
    rotation_deg: float = 0.0
    flip_y: bool = True
    nozzle_offset_mm: tuple[float, float] = (0.0, 0.0)
    travel_min_mm: tuple[float, float] | None = None
    travel_max_mm: tuple[float, float] | None = None
    homography_3x3: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None = None
    scale_json_ref: str | None = None
    feeds_action_request: bool = False

    def validate(self) -> None:
        if self.feeds_action_request:
            raise ValueError("WorkFrameConfig 禁止 feeds_action_request=true；未验收标定不得写入动作申请")
        if self.mm_per_px is not None and self.mm_per_px <= 0:
            raise ValueError("mm_per_px必须大于0")
        if self.assumed_mm_per_px <= 0:
            raise ValueError("assumed_mm_per_px必须大于0")
        if self.homography_3x3 is not None:
            _require_3x3(self.homography_3x3)

    def effective_mm_per_px(self) -> float:
        if self.mm_per_px is not None:
            return float(self.mm_per_px)
        return float(self.assumed_mm_per_px)

    def scale_source(self) -> str:
        if self.mm_per_px is not None:
            return "mm_per_px_preview_only"
        return "assumed_placeholder"

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "mm_per_px": self.mm_per_px,
            "assumed_mm_per_px": self.assumed_mm_per_px,
            "effective_mm_per_px": self.effective_mm_per_px(),
            "scale_source": self.scale_source(),
            "origin_px": list(self.origin_px),
            "rotation_deg": self.rotation_deg,
            "flip_y": self.flip_y,
            "nozzle_offset_mm": list(self.nozzle_offset_mm),
            "travel_min_mm": list(self.travel_min_mm) if self.travel_min_mm is not None else None,
            "travel_max_mm": list(self.travel_max_mm) if self.travel_max_mm is not None else None,
            "homography_3x3": [list(row) for row in self.homography_3x3] if self.homography_3x3 is not None else None,
            "scale_json_ref": self.scale_json_ref,
            "feeds_action_request": False,
            "coordinate_frame": ASSUMED_WORK_FRAME,
            "evidence_boundary": "假设工作平面预览；不是已验收 work_mm，不得申请 SPRAY_AT_POINT",
        }


def pixel_to_assumed_mm(x_px: float, y_px: float, frame: WorkFrameConfig) -> tuple[float, float]:
    """把一个像素点换到假设工作坐标（毫米），再加喷头偏移。"""

    frame.validate()
    if frame.homography_3x3 is not None:
        x_mm, y_mm = _apply_homography(float(x_px), float(y_px), frame.homography_3x3)
    else:
        scale = frame.effective_mm_per_px()
        dx = float(x_px) - float(frame.origin_px[0])
        dy = float(y_px) - float(frame.origin_px[1])
        if frame.flip_y:
            dy = -dy
        x_mm = dx * scale
        y_mm = dy * scale
        theta = math.radians(frame.rotation_deg)
        if theta != 0.0:
            cosine = math.cos(theta)
            sine = math.sin(theta)
            x_mm, y_mm = x_mm * cosine - y_mm * sine, x_mm * sine + y_mm * cosine
    return (x_mm + float(frame.nozzle_offset_mm[0]), y_mm + float(frame.nozzle_offset_mm[1]))


def out_of_travel(x_mm: float, y_mm: float, frame: WorkFrameConfig) -> bool:
    if frame.travel_min_mm is None or frame.travel_max_mm is None:
        return False
    return not (
        frame.travel_min_mm[0] <= x_mm <= frame.travel_max_mm[0]
        and frame.travel_min_mm[1] <= y_mm <= frame.travel_max_mm[1]
    )


def work_frame_from_dict(payload: dict[str, Any]) -> WorkFrameConfig:
    homography = payload.get("homography_3x3")
    parsed_h = None
    if homography is not None:
        parsed_h = tuple(tuple(float(value) for value in row) for row in homography)
        _require_3x3(parsed_h)
    frame = WorkFrameConfig(
        version=str(payload.get("version", "work-frame-placeholder-v0")),
        mm_per_px=_optional_float(payload.get("mm_per_px")),
        assumed_mm_per_px=float(payload.get("assumed_mm_per_px", DEFAULT_ASSUMED_MM_PER_PX)),
        origin_px=_pair(payload.get("origin_px", (0.0, 0.0))),
        rotation_deg=float(payload.get("rotation_deg", 0.0)),
        flip_y=bool(payload.get("flip_y", True)),
        nozzle_offset_mm=_pair(payload.get("nozzle_offset_mm", (0.0, 0.0))),
        travel_min_mm=_optional_pair(payload.get("travel_min_mm")),
        travel_max_mm=_optional_pair(payload.get("travel_max_mm")),
        homography_3x3=parsed_h,
        scale_json_ref=payload.get("scale_json_ref"),
        feeds_action_request=False,
    )
    if payload.get("feeds_action_request"):
        raise ValueError("WorkFrame JSON 禁止 feeds_action_request=true")
    frame.validate()
    return frame


def load_mm_per_px_from_scale_json(path: str | Path) -> tuple[float, str]:
    """读取 B 的离线尺度 JSON，只取 mm_per_px。"""

    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    value = payload.get("mm_per_px")
    if value is None or float(value) <= 0:
        raise ValueError(f"尺度JSON缺少有效 mm_per_px：{source}")
    return float(value), source.as_posix()


def _apply_homography(
    x_px: float,
    y_px: float,
    matrix: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
) -> tuple[float, float]:
    w = matrix[2][0] * x_px + matrix[2][1] * y_px + matrix[2][2]
    if w == 0:
        raise ValueError("单应性第三行使齐次坐标为0")
    x_mm = (matrix[0][0] * x_px + matrix[0][1] * y_px + matrix[0][2]) / w
    y_mm = (matrix[1][0] * x_px + matrix[1][1] * y_px + matrix[1][2]) / w
    return (x_mm, y_mm)


def _require_3x3(matrix: Any) -> None:
    if len(matrix) != 3 or any(len(row) != 3 for row in matrix):
        raise ValueError("homography_3x3必须是3x3")


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _pair(value: Any) -> tuple[float, float]:
    return (float(value[0]), float(value[1]))


def _optional_pair(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    return _pair(value)
