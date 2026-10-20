"""从热图取峰，再用最近邻排访问顺序。几何判断复用现有足迹，不新写一套覆盖公式。"""

from __future__ import annotations

import math

import numpy as np

from microcleaning.data_learning.plan_imitation.config import ImitationStudy


def route_length_px(points_px: tuple[tuple[int, int], ...] | list[tuple[int, int]],
        start_px: tuple[float, float]) -> float:
    route = 0.0
    previous = start_px
    for point in points_px:
        route += math.hypot(point[0] - previous[0], point[1] - previous[1])
        previous = point
    return route


def nearest_neighbor_order(points_px: tuple[tuple[int, int], ...] | list[tuple[int, int]],
        start_px: tuple[float, float]) -> tuple[tuple[int, int], ...]:
    """确定性最近邻：距离相同则先行、再列。这是访问顺序，不是固件插补轨迹。"""
    remaining = [(int(x), int(y)) for x, y in points_px]
    ordered: list[tuple[int, int]] = []
    previous = start_px
    while remaining:
        chosen = min(remaining, key=lambda point: (
            (point[0] - previous[0]) ** 2 + (point[1] - previous[1]) ** 2,
            point[1],
            point[0],
        ))
        remaining.remove(chosen)
        ordered.append(chosen)
        previous = chosen
    return tuple(ordered)


def decode_peaks(heatmap: np.ndarray, feasible: np.ndarray | None, study: ImitationStudy) -> tuple[tuple[int, int], ...]:
    """只在可行驻点上取峰。不可行像素不能靠挪进图像里变成允许喷点。"""
    scores = np.asarray(heatmap, dtype=np.float64)
    if scores.ndim != 2:
        raise ValueError("HEATMAP_MUST_BE_2D")
    if feasible is None:
        allowed = np.ones(scores.shape, dtype=bool)
    else:
        allowed = np.asarray(feasible, dtype=bool)
        if allowed.shape != scores.shape:
            raise ValueError("FEASIBLE_MASK_SHAPE_MISMATCH")
    scores = scores.copy()
    scores[~allowed] = -np.inf
    chosen: list[tuple[int, int]] = []
    limit = float(study.min_peak_distance_px) ** 2
    for _ in range(study.max_stations):
        flat = int(np.argmax(scores))
        y_px, x_px = np.unravel_index(flat, scores.shape)
        value = float(scores[y_px, x_px])
        if not math.isfinite(value) or value < study.peak_threshold:
            break
        chosen.append((int(x_px), int(y_px)))
        y0 = max(0, int(y_px) - int(math.ceil(study.min_peak_distance_px)))
        y1 = min(scores.shape[0], int(y_px) + int(math.ceil(study.min_peak_distance_px)) + 1)
        x0 = max(0, int(x_px) - int(math.ceil(study.min_peak_distance_px)))
        x1 = min(scores.shape[1], int(x_px) + int(math.ceil(study.min_peak_distance_px)) + 1)
        yy, xx = np.ogrid[y0:y1, x0:x1]
        close = (yy - int(y_px)) ** 2 + (xx - int(x_px)) ** 2 <= limit
        window = scores[y0:y1, x0:x1]
        window[close] = -np.inf
        scores[y0:y1, x0:x1] = window
    return nearest_neighbor_order(chosen, study.start_px)


def decode_and_order(heatmap: np.ndarray, feasible: np.ndarray | None, study: ImitationStudy) -> tuple[tuple[int, int], ...]:
    return decode_peaks(heatmap, feasible, study)
