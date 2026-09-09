"""离线像素尺度：两点距离与已知毫米换算 mm/px。

这是 OpenPnP「units-per-pixel」一类标定的最小软件形态：只写出 JSON 证据，
禁止写入动作申请或工作台毫米坐标，也不进入状态估计。没有测微尺实拍
时本模块只能用合成点验证算术。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ScaleMeasurement:
    p1_px: tuple[float, float]
    p2_px: tuple[float, float]
    pixel_distance: float
    known_length_mm: float
    mm_per_px: float
    px_per_mm: float
    holdout_error_ratio: float | None
    feeds_action_request: bool
    coordinate_frame: str
    evidence_boundary: str


def measure_mm_per_px(
    *,
    p1_px: tuple[float, float],
    p2_px: tuple[float, float],
    known_length_mm: float,
    holdout_p1_px: tuple[float, float] | None = None,
    holdout_p2_px: tuple[float, float] | None = None,
    holdout_known_mm: float | None = None,
) -> ScaleMeasurement:
    """用一段已知长度把像素距离换成 mm/px；留出段只报相对误差。"""

    if known_length_mm <= 0:
        raise ValueError("known_length_mm必须大于0")
    pixel_distance = _distance(p1_px, p2_px)
    if pixel_distance <= 0:
        raise ValueError("两点不能重合")
    mm_per_px = known_length_mm / pixel_distance
    holdout_error_ratio = None
    holdout_requested = any(
        value is not None for value in (holdout_p1_px, holdout_p2_px, holdout_known_mm)
    )
    if holdout_requested:
        if holdout_p1_px is None or holdout_p2_px is None or holdout_known_mm is None:
            raise ValueError("留出尺度必须同时提供两点和已知毫米")
        if holdout_known_mm <= 0:
            raise ValueError("holdout_known_mm必须大于0")
        holdout_pixels = _distance(holdout_p1_px, holdout_p2_px)
        if holdout_pixels <= 0:
            raise ValueError("留出两点不能重合")
        predicted_mm = holdout_pixels * mm_per_px
        holdout_error_ratio = abs(predicted_mm - holdout_known_mm) / holdout_known_mm
    return ScaleMeasurement(
        p1_px=(float(p1_px[0]), float(p1_px[1])),
        p2_px=(float(p2_px[0]), float(p2_px[1])),
        pixel_distance=pixel_distance,
        known_length_mm=float(known_length_mm),
        mm_per_px=mm_per_px,
        px_per_mm=1.0 / mm_per_px,
        holdout_error_ratio=holdout_error_ratio,
        feeds_action_request=False,
        coordinate_frame="image_px",
        evidence_boundary="离线 mm/px；不得写入动作申请或工作台毫米坐标，不代表喷头对准",
    )


def scale_measurement_payload(measurement: ScaleMeasurement) -> dict[str, object]:
    payload = asdict(measurement)
    payload["p1_px"] = list(measurement.p1_px)
    payload["p2_px"] = list(measurement.p2_px)
    return payload


def _distance(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    return math.dist((float(p1[0]), float(p1[1])), (float(p2[0]), float(p2[1])))
