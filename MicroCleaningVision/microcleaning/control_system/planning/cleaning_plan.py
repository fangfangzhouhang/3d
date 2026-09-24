"""从污染mask生成像素级清洗策略和路线（成员 C）。

几何规则复用 OpenCV 连通域，覆盖方式是往复扫描（boustrophedon，像耕地一样
来回扫）；多块访问顺序默认最近邻（nearest neighbor，每次去离当前点最近的一块）。
路线仍位于 ``image_px``。毫米、喷头偏移和步进换算在 ``path_preview`` 中预览，
默认不得写入 ``ActionRequest``。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

VISIT_NEAREST_NEIGHBOR = "nearest_neighbor"
VISIT_AREA_DESC = "area_desc"
VISIT_ORDERS = (VISIT_NEAREST_NEIGHBOR, VISIT_AREA_DESC)


class CleaningStrategy(str, Enum):
    NO_TARGET = "NO_TARGET"
    CENTER_POINT = "CENTER_POINT"
    RASTER_SCAN = "RASTER_SCAN"


@dataclass(frozen=True)
class CleaningPlanPolicy:
    small_target_ratio: float = 0.02
    raster_step_px: int = 16
    visit_order: str = VISIT_NEAREST_NEIGHBOR
    start_px: tuple[float, float] = (0.0, 0.0)

    def validate(self) -> None:
        if not 0 < self.small_target_ratio < 1:
            raise ValueError("small_target_ratio必须位于0～1")
        if self.raster_step_px <= 0:
            raise ValueError("raster_step_px必须大于0")
        if self.visit_order not in VISIT_ORDERS:
            raise ValueError(f"visit_order必须是{VISIT_ORDERS}之一")
        if len(self.start_px) != 2:
            raise ValueError("start_px必须是 (x, y)")


@dataclass(frozen=True)
class CleaningPlan:
    strategy: CleaningStrategy
    coordinate_frame: str
    image_size_px: tuple[int, int]
    contamination_area_px: float
    path_px: tuple[tuple[float, float], ...]
    segment_start_indices: tuple[int, ...]
    reason: str
    visit_order: str = VISIT_NEAREST_NEIGHBOR


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
            policy.visit_order,
        )

    component_count, labels, stats, component_centroids = cv2.connectedComponentsWithStats(
        binary, connectivity=8
    )
    component_labels = _order_component_labels(
        list(range(1, component_count)),
        component_centroids,
        stats=stats,
        cv2=cv2,
        visit_order=policy.visit_order,
        start_px=policy.start_px,
    )
    order_note = (
        "块顺序为最近邻（从 start_px 出发每次去最近的一块）"
        if policy.visit_order == VISIT_NEAREST_NEIGHBOR
        else "块顺序为面积从大到小"
    )
    if area / float(width * height) <= policy.small_target_ratio:
        centers = tuple(
            (float(component_centroids[label][0]), float(component_centroids[label][1]))
            for label in component_labels
        )
        return CleaningPlan(
            CleaningStrategy.CENTER_POINT,
            "image_px",
            (width, height),
            area,
            centers,
            tuple(range(len(centers))),
            f"污染面积占图像比例较小，每个独立污染块生成一个中心点；{order_note}",
            policy.visit_order,
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
            path.extend(component_path)
    if not path:
        segment_starts = [0]
        path = [
            (
                float(component_centroids[component_labels[0]][0]),
                float(component_centroids[component_labels[0]][1]),
            )
        ]
    return CleaningPlan(
        CleaningStrategy.RASTER_SCAN,
        "image_px",
        (width, height),
        area,
        tuple(path),
        tuple(segment_starts),
        "污染面积较大；每个独立污染块分别生成往复式扫描段（boustrophedon），"
        f"段间移动默认关闭喷射；{order_note}",
        policy.visit_order,
    )


def cleaning_plan_to_dict(plan: CleaningPlan) -> dict[str, object]:
    return {
        "strategy": plan.strategy.value,
        "coordinate_frame": plan.coordinate_frame,
        "image_size_px": list(plan.image_size_px),
        "contamination_area_px": plan.contamination_area_px,
        "path_px": [list(point) for point in plan.path_px],
        "segment_start_indices": list(plan.segment_start_indices),
        "reason": plan.reason,
        "visit_order": plan.visit_order,
    }


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


def _order_component_labels(
    labels: list[int],
    centroids: Any,
    *,
    stats: Any,
    cv2: Any,
    visit_order: str,
    start_px: tuple[float, float],
) -> list[int]:
    if visit_order == VISIT_AREA_DESC:
        return sorted(labels, key=lambda label: int(stats[label, cv2.CC_STAT_AREA]), reverse=True)
    remaining = list(labels)
    ordered: list[int] = []
    current_x, current_y = float(start_px[0]), float(start_px[1])
    while remaining:
        def distance_sq(label: int, x: float = current_x, y: float = current_y) -> float:
            cx = float(centroids[label][0])
            cy = float(centroids[label][1])
            return (cx - x) ** 2 + (cy - y) ** 2

        chosen = min(remaining, key=distance_sq)
        remaining.remove(chosen)
        ordered.append(chosen)
        current_x = float(centroids[chosen][0])
        current_y = float(centroids[chosen][1])
    return ordered


def _load_dependencies() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "清洗路径规划需要NumPy/OpenCV；请安装requirements/perception-opencv.txt"
        ) from exc
    return cv2, np
