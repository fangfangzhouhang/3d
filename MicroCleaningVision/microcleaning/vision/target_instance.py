"""单目标实例与复检（成员 B 的视觉识别与测量模块）。

整幅面积会把别的污渍变小算进这一块。这里先用连通域（connected component，
连在一起的前景像素）把 Mask 拆成目标，再用搜索框、重叠和质心
（centroid，该块像素的中心）把后图的一块配回指定的前图目标。

只有唯一配上的那一对面积才交给 ``verify_area_change``。配不上、配到多块，
或质心落到另一块上，结论是人工复核，不用整幅前景像素充数。
像素规则通过不等于已经洗净，也不读取规划或串口。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Any

from microcleaning.contracts import ExecutionReceipt, NextRoute, Observation, VerificationResult
from microcleaning.vision.verification import VerificationPolicy, verify_area_change


_CONNECTIVITY = 8


def _non_negative_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value == value
        and value >= 0
    )


def _non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _unit_interval(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value == value
        and 0.0 <= float(value) <= 1.0
    )


@dataclass(frozen=True)
class TargetInstance:
    """一个连通域对应的目标。面积和质心只属于这一块，不是整幅 Mask。"""

    target_id: str
    centroid_px: tuple[float, float]
    area_px: float
    bbox: tuple[int, int, int, int]
    component_label: int
    confidence: float
    mask_ref: str | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """拒绝空编号、非法框、负面积和伪造的置信度。"""
        if not isinstance(self.target_id, str) or not self.target_id:
            raise ValueError("target_id 必须是非空字符串")
        if self.area_px <= 0 or self.area_px != self.area_px:
            raise ValueError("area_px 必须是有限正数")
        if not 0.0 <= self.confidence <= 1.0 or self.confidence != self.confidence:
            raise ValueError("confidence 必须在 0～1 之间")
        if len(self.centroid_px) != 2:
            raise ValueError("centroid_px 必须是 (x, y)")
        if not all(_non_negative_number(value) for value in self.centroid_px):
            raise ValueError("centroid_px 必须包含非负有限数值")
        if len(self.bbox) != 4 or not all(_non_negative_int(value) for value in self.bbox):
            raise ValueError("bbox 必须是 (x, y, width, height)")
        if self.bbox[2] <= 0 or self.bbox[3] <= 0:
            raise ValueError("bbox 的宽和高必须大于 0")
        if not _non_negative_int(self.component_label) or self.component_label < 1:
            raise ValueError("component_label 必须是从 1 开始的整数")
        if self.mask_ref is not None and not isinstance(self.mask_ref, str):
            raise ValueError("mask_ref 必须是字符串或 None")


@dataclass(frozen=True)
class TargetMatchPolicy:
    """把后图块配回前图目标的合成规则，不是已验收的配准或清洗阈值。"""

    roi_margin_px: int = 8
    min_overlap_ratio: float = 0.20
    max_centroid_distance_px: float = 16.0

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not _non_negative_int(self.roi_margin_px):
            raise ValueError("roi_margin_px 必须是非负整数")
        if not _unit_interval(self.min_overlap_ratio) or self.min_overlap_ratio <= 0:
            raise ValueError("min_overlap_ratio 必须在 0～1 之间且大于 0")
        if (
            isinstance(self.max_centroid_distance_px, bool)
            or not isinstance(self.max_centroid_distance_px, (int, float))
            or self.max_centroid_distance_px <= 0
            or self.max_centroid_distance_px != self.max_centroid_distance_px
        ):
            raise ValueError("max_centroid_distance_px 必须是有限正数")


@dataclass(frozen=True)
class TargetVerification:
    """指定目标的复检。``result`` 在配上之后才来自 ``verify_area_change``。"""

    target_id: str
    pre_area_px: float | None
    post_area_px: float | None
    removal_rate: float | None
    match_quality: float
    result: VerificationResult
    match_status: str

    def __post_init__(self) -> None:
        if self.match_status not in {"matched", "unmatched", "ambiguous"}:
            raise ValueError("match_status 必须是 matched、unmatched 或 ambiguous")
        if not _unit_interval(self.match_quality):
            raise ValueError("match_quality 必须在 0～1 之间")


@dataclass(frozen=True)
class _Component:
    label: int
    area_px: int
    centroid_px: tuple[float, float]
    bbox: tuple[int, int, int, int]


def extract_target_instances(
    mask: Any,
    *,
    mask_ref: str | None = None,
) -> list[TargetInstance]:
    """按 8 连通提取目标。空 Mask 得到空列表；贴着图像边界的小块也保留。

    编号按扫描顺序从 T1 开始，同一张 Mask 重复提取的结果相同。
    二值 Mask 没有单独的像素分数，因此置信度记为 1，不是校准过的概率。
    """

    labels, components = _connected_components(mask)
    del labels
    if mask_ref is not None and not isinstance(mask_ref, str):
        raise ValueError("mask_ref 必须是字符串或 None")
    return [
        TargetInstance(
            target_id=f"T{component.label}",
            centroid_px=component.centroid_px,
            area_px=float(component.area_px),
            bbox=component.bbox,
            component_label=component.label,
            confidence=1.0,
            mask_ref=mask_ref,
        )
        for component in components
    ]


def verify_single_target(
    *,
    task_id: str,
    pre: Observation,
    post: Observation | None,
    pre_target: TargetInstance,
    pre_mask: Any,
    post_mask: Any,
    receipt: ExecutionReceipt | None,
    images_comparable: bool = True,
    damage_flag: bool = False,
    policy: VerificationPolicy = VerificationPolicy(),
    match_policy: TargetMatchPolicy = TargetMatchPolicy(),
) -> TargetVerification:
    """把后图里的一块配回 ``pre_target``，配上才比较这一对的面积。

    面积取该连通域的统计值，不取整幅前景，也不采用实例上另填的面积。
    搜索框内没有任何前景时，这一对的后面积是 0。框内有对不上的块、
    或同时有多块都满足重叠和质心距离时，不硬配，结论交给人工。
    """

    pre_target.validate()
    match_policy.validate()
    pre_binary = _as_mask(pre_mask, name="pre_mask")
    post_binary = _as_mask(post_mask, name="post_mask")
    if pre_binary.shape != post_binary.shape:
        return _unmatched(
            task_id,
            pre,
            post,
            pre_target,
            "TARGET_MASK_MISMATCH",
            pre_area_px=_area_if_present(pre_binary, pre_target.component_label),
        )

    pre_labels, pre_components = _connected_components(pre_binary)
    pre_component = _find_component(pre_components, pre_target.component_label)
    if pre_component is None:
        return _unmatched(task_id, pre, post, pre_target, "TARGET_NOT_IN_MASK", pre_area_px=None)

    post_labels, post_components = _connected_components(post_binary)
    qualified, roi_has_foreground = _qualify_post_components(
        pre_labels,
        pre_component,
        post_labels,
        post_components,
        match_policy,
    )
    pre_area = float(pre_component.area_px)
    if len(qualified) > 1:
        return _unmatched(
            task_id,
            pre,
            post,
            pre_target,
            "TARGET_MATCH_AMBIGUOUS",
            pre_area_px=pre_area,
            match_status="ambiguous",
        )
    if len(qualified) == 1:
        component, overlap_ratio, distance = qualified[0]
        post_area = float(component.area_px)
        quality = _match_quality(overlap_ratio, distance, match_policy.max_centroid_distance_px)
        return _verified_pair(
            task_id=task_id,
            pre=pre,
            post=post,
            pre_target=pre_target,
            pre_area=pre_area,
            post_area=post_area,
            match_quality=quality,
            receipt=receipt,
            images_comparable=images_comparable,
            damage_flag=damage_flag,
            policy=policy,
        )
    if not roi_has_foreground:
        return _verified_pair(
            task_id=task_id,
            pre=pre,
            post=post,
            pre_target=pre_target,
            pre_area=pre_area,
            post_area=0.0,
            match_quality=1.0,
            receipt=receipt,
            images_comparable=images_comparable,
            damage_flag=damage_flag,
            policy=policy,
        )
    return _unmatched(
        task_id,
        pre,
        post,
        pre_target,
        "TARGET_UNMATCHED",
        pre_area_px=pre_area,
    )


def _verified_pair(
    *,
    task_id: str,
    pre: Observation,
    post: Observation | None,
    pre_target: TargetInstance,
    pre_area: float,
    post_area: float,
    match_quality: float,
    receipt: ExecutionReceipt | None,
    images_comparable: bool,
    damage_flag: bool,
    policy: VerificationPolicy,
) -> TargetVerification:
    result = verify_area_change(
        task_id=task_id,
        pre=pre,
        post=post,
        pre_area_px=pre_area,
        post_area_px=post_area,
        receipt=receipt,
        images_comparable=images_comparable,
        damage_flag=damage_flag,
        policy=policy,
    )
    return TargetVerification(
        target_id=pre_target.target_id,
        pre_area_px=pre_area,
        post_area_px=post_area,
        removal_rate=result.removal_rate,
        match_quality=match_quality,
        result=result,
        match_status="matched",
    )


def _unmatched(
    task_id: str,
    pre: Observation,
    post: Observation | None,
    pre_target: TargetInstance,
    reason: str,
    *,
    pre_area_px: float | None,
    match_status: str = "unmatched",
) -> TargetVerification:
    post_id = None if post is None else post.observation_id
    result = VerificationResult(
        task_id,
        pre.observation_id,
        post_id,
        None,
        None,
        False,
        NextRoute.HUMAN,
        (reason,),
    )
    return TargetVerification(
        target_id=pre_target.target_id,
        pre_area_px=pre_area_px,
        post_area_px=None,
        removal_rate=None,
        match_quality=0.0,
        result=result,
        match_status=match_status,
    )


def _qualify_post_components(
    pre_labels: Any,
    pre_component: _Component,
    post_labels: Any,
    post_components: list[_Component],
    policy: TargetMatchPolicy,
) -> tuple[list[tuple[_Component, float, float]], bool]:
    """同时满足搜索框、重叠比和质心距离的后图块才算候选。"""

    np = _numpy()
    x0, y0, x1, y1 = _search_roi(pre_component.bbox, policy.roi_margin_px, post_labels.shape)
    roi_has_foreground = bool(np.any(post_labels[y0:y1, x0:x1] > 0))
    pre_region = pre_labels == pre_component.label
    qualified: list[tuple[_Component, float, float]] = []
    for component in post_components:
        overlap_px = int(np.sum(pre_region & (post_labels == component.label)))
        overlap_ratio = overlap_px / pre_component.area_px
        distance = hypot(
            component.centroid_px[0] - pre_component.centroid_px[0],
            component.centroid_px[1] - pre_component.centroid_px[1],
        )
        center_x, center_y = component.centroid_px
        center_in_roi = x0 <= center_x < x1 and y0 <= center_y < y1
        if (
            overlap_ratio >= policy.min_overlap_ratio
            and distance <= policy.max_centroid_distance_px
            and center_in_roi
        ):
            qualified.append((component, overlap_ratio, distance))
    return qualified, roi_has_foreground


def _search_roi(
    bbox: tuple[int, int, int, int],
    margin: int,
    shape: tuple[int, ...],
) -> tuple[int, int, int, int]:
    """前图目标框向外扩一圈，并裁到图像内。返回的右下边界不包含。"""

    height, width = int(shape[0]), int(shape[1])
    x, y, box_w, box_h = bbox
    x0 = max(0, x - margin)
    y0 = max(0, y - margin)
    x1 = min(width, x + box_w + margin)
    y1 = min(height, y + box_h + margin)
    return x0, y0, x1, y1


def _match_quality(overlap_ratio: float, distance: float, max_distance: float) -> float:
    """重叠越高、质心越近，配回质量越接近 1。"""

    ratio = min(1.0, max(0.0, overlap_ratio))
    closeness = 1.0 - (distance / max_distance)
    closeness = min(1.0, max(0.0, closeness))
    return ratio * closeness


def _connected_components(mask: Any) -> tuple[Any, list[_Component]]:
    binary = _as_mask(mask, name="mask")
    cv2, np = _load_dependencies()
    foreground = np.where(binary > 0, np.uint8(255), np.uint8(0))
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        foreground,
        connectivity=_CONNECTIVITY,
    )
    components: list[_Component] = []
    for label in range(1, int(count)):
        components.append(
            _Component(
                label=label,
                area_px=int(stats[label, cv2.CC_STAT_AREA]),
                centroid_px=(float(centroids[label, 0]), float(centroids[label, 1])),
                bbox=(
                    int(stats[label, cv2.CC_STAT_LEFT]),
                    int(stats[label, cv2.CC_STAT_TOP]),
                    int(stats[label, cv2.CC_STAT_WIDTH]),
                    int(stats[label, cv2.CC_STAT_HEIGHT]),
                ),
            )
        )
    return labels, components


def _area_if_present(mask: Any, label: int) -> float | None:
    _, components = _connected_components(mask)
    component = _find_component(components, label)
    if component is None:
        return None
    return float(component.area_px)


def _find_component(components: list[_Component], label: int) -> _Component | None:
    for component in components:
        if component.label == label:
            return component
    return None


def _as_mask(mask: Any, *, name: str) -> Any:
    np = _numpy()
    if not isinstance(mask, np.ndarray) or mask.ndim != 2 or mask.dtype != np.uint8 or mask.size == 0:
        raise ValueError(f"{name}必须是非空二维uint8 Mask")
    return mask


def _numpy() -> Any:
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "单目标提取需要感知依赖；请在项目.venv安装requirements/perception-opencv.txt"
        ) from exc
    return np


def _load_dependencies() -> tuple[Any, Any]:
    np = _numpy()
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "单目标提取需要感知依赖；请在项目.venv安装requirements/perception-opencv.txt"
        ) from exc
    return cv2, np
