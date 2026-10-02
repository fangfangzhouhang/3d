"""工作平面占位：像素 → 假设毫米。不得把结果写成已验收标定。

几何是相似变换（缩放 + 可选翻转 + 旋转 + 原点平移），再加喷头相对光心的偏移。
完整单应性（homography，把倾斜平面上的像素投到工作平面）预留 ``homography_3x3``。
成员 B 的离线 ``mm_per_px`` JSON 可以填进 ``mm_per_px``，仍禁止 ``feeds_action_request``。
"""

from __future__ import annotations

import hashlib
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
    mm_per_px_y: float | None = None

    def validate(self) -> None:
        if self.feeds_action_request:
            raise ValueError("WorkFrameConfig 禁止 feeds_action_request=true；未验收标定不得写入动作申请")
        if self.mm_per_px is not None and self.mm_per_px <= 0:
            raise ValueError("mm_per_px必须大于0")
        if self.mm_per_px_y is not None and self.mm_per_px_y <= 0:
            raise ValueError("mm_per_px_y必须大于0")
        if self.assumed_mm_per_px <= 0:
            raise ValueError("assumed_mm_per_px必须大于0")
        if self.homography_3x3 is not None:
            _require_3x3(self.homography_3x3)

    def effective_mm_per_px(self) -> float:
        """X 方向（也是未单独给 Y 时两轴共用）的 mm/px。"""
        if self.mm_per_px is not None:
            return float(self.mm_per_px)
        return float(self.assumed_mm_per_px)

    def effective_mm_per_px_y(self) -> float:
        if self.mm_per_px_y is not None:
            return float(self.mm_per_px_y)
        return self.effective_mm_per_px()

    def scale_source(self) -> str:
        if self.mm_per_px is not None:
            return "mm_per_px_preview_only"
        return "assumed_placeholder"

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "mm_per_px": self.mm_per_px,
            "mm_per_px_y": self.mm_per_px_y,
            "assumed_mm_per_px": self.assumed_mm_per_px,
            "effective_mm_per_px": self.effective_mm_per_px(),
            "effective_mm_per_px_y": self.effective_mm_per_px_y(),
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
        dx = float(x_px) - float(frame.origin_px[0])
        dy = float(y_px) - float(frame.origin_px[1])
        if frame.flip_y:
            dy = -dy
        x_mm = dx * frame.effective_mm_per_px()
        y_mm = dy * frame.effective_mm_per_px_y()
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
        mm_per_px_y=_optional_float(payload.get("mm_per_px_y")),
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


@dataclass(frozen=True)
class MotorCalibration:
    """``scripts/calibrate_motor_mm_per_px.py`` 写出的电机位移标定。

    真正测到的是「步 / 像素」；mm 依赖 5 mm/圈 的铭牌假设，仍不是验收坐标。
    """

    ref: str
    sha256: str
    mm_per_px_x: float
    mm_per_px_y: float
    nozzle_px: tuple[float, float] | None
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ref": self.ref,
            "sha256": self.sha256,
            "mm_per_px_x": self.mm_per_px_x,
            "mm_per_px_y": self.mm_per_px_y,
            "nozzle_px": None if self.nozzle_px is None else list(self.nozzle_px),
            "warnings": list(self.warnings),
        }


def load_motor_calibration(path: str | Path) -> MotorCalibration:
    """读取电机位移标定 JSON；缺字段、非正数或企图喂动作申请都直接报错。"""

    source = Path(path)
    raw = source.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"标定文件不是 JSON 对象：{source}")
    if payload.get("feeds_action_request"):
        raise ValueError(f"标定文件禁止 feeds_action_request=true：{source}")
    scales = []
    for key in ("mm_per_px_x", "mm_per_px_y"):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"标定文件缺少有效 {key}：{source}")
        scales.append(float(value))
    warnings = payload.get("warnings") or []
    if not isinstance(warnings, list):
        raise ValueError(f"标定文件 warnings 必须是列表：{source}")
    nozzle = payload.get("nozzle_px")
    return MotorCalibration(
        ref=source.as_posix(),
        sha256=hashlib.sha256(raw).hexdigest(),
        mm_per_px_x=scales[0],
        mm_per_px_y=scales[1],
        nozzle_px=None if nozzle is None else _pair(nozzle),
        warnings=tuple(str(item) for item in warnings),
    )


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
