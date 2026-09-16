"""从污染mask生成像素级清洗策略和路线（成员 C）。

路线仍位于 ``image_px`` 坐标系，用于Demo预览。只有经过明确标定，路线中的下一步
目标才能被转换成毫米坐标并形成可审批的 ``ActionRequest``。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any


class CleaningStrategy(str, Enum):
    NO_TARGET = "NO_TARGET"
    CENTER_POINT = "CENTER_POINT"
    RASTER_SCAN = "RASTER_SCAN"


@dataclass(frozen=True)
class CleaningPlanPolicy:
    small_target_ratio: float = 0.02
    raster_step_px: int = 16

    def validate(self) -> None:
        if (
            isinstance(self.small_target_ratio, bool)
            or not isinstance(self.small_target_ratio, (int, float))
            or not math.isfinite(self.small_target_ratio)
            or not 0 < self.small_target_ratio < 1
        ):
            raise ValueError("small_target_ratio必须位于0～1")
        if isinstance(self.raster_step_px, bool) or not isinstance(self.raster_step_px, int) or self.raster_step_px <= 0:
            raise ValueError("raster_step_px必须是大于0的整数")


@dataclass(frozen=True)
class CleaningPlan:
    strategy: CleaningStrategy
    coordinate_frame: str
    image_size_px: tuple[int, int]
    contamination_area_px: float
    path_px: tuple[tuple[float, float], ...]
    # 每个索引开始一个连续预览段；同一污染块遇到孔洞也可以拆成多段。
    segment_start_indices: tuple[int, ...]
    reason: str


def plan_cleaning(
    mask: Any,
    *,
    policy: CleaningPlanPolicy = CleaningPlanPolicy(),
) -> CleaningPlan:
    """小污染取中心点，大污染在mask内部生成蛇形扫描点。"""

    cv2, np = _load_dependencies()
    policy.validate()
    if not isinstance(mask, np.ndarray) or mask.dtype != np.uint8 or mask.ndim != 2:
        raise ValueError("mask必须是uint8二维图像")
    if mask.size == 0:
        raise ValueError("mask不能为空")
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    height, width = binary.shape
    area = float(cv2.countNonZero(binary))
    if area <= 0:
        return CleaningPlan(
            CleaningStrategy.NO_TARGET,
            "image_px",
            (width, height),
            0.0,
            (),
            (),
            "mask中没有污染像素",
        )

    component_count, labels, stats, component_centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    component_labels = sorted(
        range(1, component_count),
        key=lambda label: int(stats[label, cv2.CC_STAT_AREA]),
        reverse=True,
    )
    if area / float(width * height) <= policy.small_target_ratio:
        centers = tuple(
            _in_mask_center(labels == label, component_centroids[label], np)
            for label in component_labels
        )
        return CleaningPlan(
            CleaningStrategy.CENTER_POINT,
            "image_px",
            (width, height),
            area,
            centers,
            tuple(range(len(centers))),
            "污染面积占图像比例较小，每个独立污染块生成一个中心点",
        )

    path: list[tuple[float, float]] = []
    segment_starts: list[int] = []
    for label in component_labels:
        component = labels == label
        ys, _ = np.nonzero(component)
        if ys.size == 0:
            continue
        y_min, y_max = int(ys.min()), int(ys.max())
        component_path: list[tuple[float, float]] = []
        row_index = 0
        for y in range(y_min, y_max + 1, policy.raster_step_px):
            row_xs = np.flatnonzero(component[y])
            if row_xs.size == 0:
                continue
            sampled = row_xs[:: policy.raster_step_px].tolist()
            if int(row_xs[-1]) not in sampled:
                sampled.append(int(row_xs[-1]))
            if row_index % 2:
                sampled.reverse()
            component_path.extend((float(x), float(y)) for x in sampled)
            row_index += 1
        if component_path:
            segment_starts.append(len(path))
            for index, point in enumerate(component_path):
                if index and not _line_in_component(component, component_path[index - 1], point):
                    segment_starts.append(len(path))
                path.append(point)
    if not path:
        segment_starts = [0]
        path = [
            _in_mask_center(labels == component_labels[0], component_centroids[component_labels[0]], np)
        ]
    return CleaningPlan(
        CleaningStrategy.RASTER_SCAN,
        "image_px",
        (width, height),
        area,
        tuple(path),
        tuple(segment_starts),
        "污染面积较大；按污染块生成往复式像素路线，跨背景连线拆段；段间不表示连续喷射，仅用于预览",
    )


def _in_mask_center(component: Any, centroid: Any, np: Any) -> tuple[float, float]:
    """保留区域内质心；质心落在背景时取最近白色像素，同距按行列顺序。"""
    x, y = float(centroid[0]), float(centroid[1])
    if component[round(y), round(x)]:
        return x, y
    ys, xs = np.nonzero(component)
    index = int(np.argmin((xs - x) ** 2 + (ys - y) ** 2))
    return float(xs[index]), float(ys[index])


def _line_in_component(component: Any, start: tuple[float, float], end: tuple[float, float]) -> bool:
    """检查线段经过的像素网格；过网格角时也检查两侧，保守避免跨背景。

    只检查几何中心线，不代表喷幅或物理覆盖已经验证。
    """
    x, y = map(round, start)
    end_x, end_y = map(round, end)
    dx, dy = abs(end_x - x), abs(end_y - y)
    sx, sy = (1 if end_x > x else -1), (1 if end_y > y else -1)
    ix = iy = 0
    if not component[y, x]:
        return False
    while ix < dx or iy < dy:
        comparison = (1 + 2 * ix) * dy - (1 + 2 * iy) * dx
        if comparison == 0:
            if not component[y, x + sx] or not component[y + sy, x]:
                return False
            x += sx
            y += sy
            ix += 1
            iy += 1
        elif comparison < 0:
            x += sx
            ix += 1
        else:
            y += sy
            iy += 1
        if not component[y, x]:
            return False
    return True


def simulate_first_action(mask: Any, plan: CleaningPlan, *, radius_px: int = 18) -> Any:
    """只用于Demo：擦除第一个目标附近的mask，产生动作后模拟证据。"""

    cv2, np = _load_dependencies()
    if not isinstance(mask, np.ndarray) or mask.dtype != np.uint8 or mask.ndim != 2:
        raise ValueError("mask必须是uint8二维图像")
    if radius_px <= 0:
        raise ValueError("radius_px必须大于0")
    post = mask.copy()
    if plan.path_px:
        x, y = plan.path_px[0]
        cv2.circle(post, (round(x), round(y)), radius_px, 0, thickness=-1)
    return post


def _load_dependencies() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "清洗路径规划需要NumPy/OpenCV；请安装requirements/perception-opencv.txt"
        ) from exc
    return cv2, np
