"""决定多个清洗目标里下一块先处理谁（成员 C）。

序列规划（sequence planning，只排目标先后）不画像素路线，也不识别图像。
像素路线仍由 ``plan_cleaning`` 负责。本模块不依赖视觉侧的目标类型。

比较方式与 ``cleaning_plan._order_component_labels`` 一致，但这里不调用它：
那个函数吃的是 OpenCV 连通域编号。不能把它改成依赖 ``SequenceTarget``，
否则旧的像素路线测试会绑到这个数据对象上。

最近邻从 ``start_px`` 出发，每次在剩余目标里选离当前点最近的一块，再把当前点
移到它的中心。面积策略按面积从大到小。加权策略使用下面的分数，距离始终相对
``start_px``，不随已经选中的目标更新::

    score = w_area * area - w_distance * distance
            + w_retry * retry_count + w_difficulty * difficulty
            - w_risk * risk

分数越高越靠前。因此重试次数和难度会把目标提前，风险会把目标推后。
这是确定性规则，不是已经证明的最佳清洗顺序。

并列时用 ``target_id`` 的字符串顺序作第二关键字，较小者优先。
不使用随机数、时钟或字典遍历顺序。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

STRATEGY_NEAREST_NEIGHBOR = "nearest_neighbor"
STRATEGY_AREA_DESC = "area_desc"
STRATEGY_WEIGHTED_SCORE = "weighted_score"
STRATEGIES = (
    STRATEGY_NEAREST_NEIGHBOR,
    STRATEGY_AREA_DESC,
    STRATEGY_WEIGHTED_SCORE,
)

DEFAULT_W_AREA = 1.0
DEFAULT_W_DISTANCE = 1.0
DEFAULT_W_RETRY = 1.0
DEFAULT_W_DIFFICULTY = 1.0
DEFAULT_W_RISK = 1.0


class _ScoreParts(NamedTuple):
    distance_from_start_px: float
    area_term: float
    distance_term: float
    retry_term: float
    difficulty_term: float
    risk_term: float
    score: float


@dataclass(frozen=True)
class SequenceTarget:
    """一块待排序的清洗目标。坐标和面积都是像素，不是毫米。"""

    target_id: str
    centroid_px: tuple[float, float]
    area_px: float
    retry_count: int = 0
    difficulty: float = 0.0
    risk: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.target_id, str) or self.target_id == "":
            raise ValueError("target_id必须是非空字符串")
        object.__setattr__(self, "centroid_px", _point(self.centroid_px, "centroid_px"))
        area = _finite_number(self.area_px, "area_px")
        if area < 0:
            raise ValueError("area_px必须大于等于0")
        object.__setattr__(self, "area_px", area)
        object.__setattr__(self, "retry_count", _non_negative_int(self.retry_count, "retry_count"))
        object.__setattr__(self, "difficulty", _finite_number(self.difficulty, "difficulty"))
        object.__setattr__(self, "risk", _finite_number(self.risk, "risk"))


@dataclass(frozen=True)
class SequenceScoreWeights:
    """加权策略的权重。默认每一项都是 1.0，调用时可以整组替换。"""

    w_area: float = DEFAULT_W_AREA
    w_distance: float = DEFAULT_W_DISTANCE
    w_retry: float = DEFAULT_W_RETRY
    w_difficulty: float = DEFAULT_W_DIFFICULTY
    w_risk: float = DEFAULT_W_RISK

    def __post_init__(self) -> None:
        object.__setattr__(self, "w_area", _finite_number(self.w_area, "w_area"))
        object.__setattr__(self, "w_distance", _finite_number(self.w_distance, "w_distance"))
        object.__setattr__(self, "w_retry", _finite_number(self.w_retry, "w_retry"))
        object.__setattr__(self, "w_difficulty", _finite_number(self.w_difficulty, "w_difficulty"))
        object.__setattr__(self, "w_risk", _finite_number(self.w_risk, "w_risk"))


@dataclass(frozen=True)
class SequenceScoreBreakdown:
    """一个目标的排序解释。

    ``score`` 始终用相对 ``start_px`` 的距离按加权公式计算。
    ``nearest_neighbor`` 实际比较的是 ``stepwise_distance_px``（本步到当前点的距离）。
    ``area_desc`` 实际比较的是 ``area_px``。
    ``weighted_score`` 实际比较的是 ``score``。
    """

    target_id: str
    distance_from_start_px: float
    stepwise_distance_px: float
    area_px: float
    retry_count: int
    difficulty: float
    risk: float
    area_term: float
    distance_term: float
    retry_term: float
    difficulty_term: float
    risk_term: float
    score: float


@dataclass(frozen=True)
class SequencePlan:
    """下一块洗谁，以及同一规则下的完整顺序。"""

    selected_target_id: str | None
    ordered_target_ids: tuple[str, ...]
    strategy: str
    score_breakdown: tuple[SequenceScoreBreakdown, ...]
    reason: str


def plan_sequence(
    targets: Sequence[SequenceTarget],
    *,
    strategy: str = STRATEGY_NEAREST_NEIGHBOR,
    start_px: tuple[float, float] = (0.0, 0.0),
    weights: SequenceScoreWeights | None = None,
) -> SequencePlan:
    """排列目标并选出下一块。

    空序列不报错，返回空顺序，``selected_target_id`` 为 None。
    """

    chosen_strategy = _validate_strategy(strategy)
    origin = _point(start_px, "start_px")
    active_weights = weights if weights is not None else SequenceScoreWeights()
    if not isinstance(active_weights, SequenceScoreWeights):
        raise TypeError("weights必须是 SequenceScoreWeights")
    normalized = _validate_targets(targets)
    if not normalized:
        return SequencePlan(
            None,
            (),
            chosen_strategy,
            (),
            "没有可执行目标",
        )

    if chosen_strategy == STRATEGY_NEAREST_NEIGHBOR:
        ordered, tie_ids = _order_nearest(normalized, origin)
    elif chosen_strategy == STRATEGY_AREA_DESC:
        ordered, tie_ids = _order_area(normalized)
    else:
        ordered, tie_ids = _order_weighted(normalized, origin, active_weights)

    breakdown = _build_breakdown(ordered, origin, active_weights)
    reason = _explain(chosen_strategy, origin, active_weights, breakdown, tie_ids)
    return SequencePlan(
        ordered[0].target_id,
        tuple(target.target_id for target in ordered),
        chosen_strategy,
        breakdown,
        reason,
    )


def _validate_strategy(strategy: object) -> str:
    if not isinstance(strategy, str) or strategy not in STRATEGIES:
        allowed = ", ".join(STRATEGIES)
        raise ValueError(f"strategy必须是 {allowed} 之一")
    return strategy


def _validate_targets(targets: object) -> tuple[SequenceTarget, ...]:
    if isinstance(targets, (str, bytes)) or not isinstance(targets, Sequence):
        raise TypeError("targets必须是 SequenceTarget 序列")
    normalized: list[SequenceTarget] = []
    seen: list[str] = []
    for item in targets:
        if not isinstance(item, SequenceTarget):
            raise TypeError("targets中的每一项必须是 SequenceTarget")
        if item.target_id in seen:
            raise ValueError(f"target_id必须唯一，重复的是 {item.target_id}")
        seen.append(item.target_id)
        normalized.append(item)
    return tuple(normalized)


def _order_nearest(
    targets: Sequence[SequenceTarget],
    start: tuple[float, float],
) -> tuple[tuple[SequenceTarget, ...], tuple[str, ...]]:
    remaining = list(targets)
    ordered: list[SequenceTarget] = []
    cursor = start
    while remaining:
        current = cursor
        chosen = min(
            remaining,
            key=lambda target, current=current: (
                _distance(target.centroid_px, current),
                target.target_id,
            ),
        )
        remaining.remove(chosen)
        ordered.append(chosen)
        cursor = chosen.centroid_px
    winner = ordered[0]
    winner_distance = _distance(winner.centroid_px, start)
    tie_ids = _matching_ids(
        targets,
        winner.target_id,
        lambda target: _distance(target.centroid_px, start) == winner_distance,
    )
    return tuple(ordered), tie_ids


def _order_area(
    targets: Sequence[SequenceTarget],
) -> tuple[tuple[SequenceTarget, ...], tuple[str, ...]]:
    ordered = tuple(sorted(targets, key=lambda target: (-target.area_px, target.target_id)))
    winner = ordered[0]
    tie_ids = _matching_ids(
        targets,
        winner.target_id,
        lambda target: target.area_px == winner.area_px,
    )
    return ordered, tie_ids


def _order_weighted(
    targets: Sequence[SequenceTarget],
    start: tuple[float, float],
    weights: SequenceScoreWeights,
) -> tuple[tuple[SequenceTarget, ...], tuple[str, ...]]:
    ordered = tuple(
        sorted(
            targets,
            key=lambda target: (-_parts(target, start, weights).score, target.target_id),
        )
    )
    winner = ordered[0]
    winner_score = _parts(winner, start, weights).score
    tie_ids = _matching_ids(
        targets,
        winner.target_id,
        lambda target: _parts(target, start, weights).score == winner_score,
    )
    return ordered, tie_ids


def _matching_ids(
    targets: Sequence[SequenceTarget],
    winner_id: str,
    same_primary,
) -> tuple[str, ...]:
    peer_ids = [
        target.target_id
        for target in targets
        if target.target_id != winner_id and same_primary(target)
    ]
    peer_ids.sort()
    return tuple(peer_ids)


def _build_breakdown(
    ordered: Sequence[SequenceTarget],
    start: tuple[float, float],
    weights: SequenceScoreWeights,
) -> tuple[SequenceScoreBreakdown, ...]:
    rows: list[SequenceScoreBreakdown] = []
    cursor = start
    for target in ordered:
        stepwise = _distance(target.centroid_px, cursor)
        parts = _parts(target, start, weights)
        rows.append(
            SequenceScoreBreakdown(
                target_id=target.target_id,
                distance_from_start_px=parts.distance_from_start_px,
                stepwise_distance_px=stepwise,
                area_px=target.area_px,
                retry_count=target.retry_count,
                difficulty=target.difficulty,
                risk=target.risk,
                area_term=parts.area_term,
                distance_term=parts.distance_term,
                retry_term=parts.retry_term,
                difficulty_term=parts.difficulty_term,
                risk_term=parts.risk_term,
                score=parts.score,
            )
        )
        cursor = target.centroid_px
    return tuple(rows)


def _parts(
    target: SequenceTarget,
    start: tuple[float, float],
    weights: SequenceScoreWeights,
) -> _ScoreParts:
    distance = _distance(target.centroid_px, start)
    area_term = weights.w_area * target.area_px
    distance_term = weights.w_distance * distance
    retry_term = weights.w_retry * target.retry_count
    difficulty_term = weights.w_difficulty * target.difficulty
    risk_term = weights.w_risk * target.risk
    score = area_term - distance_term + retry_term + difficulty_term - risk_term
    return _ScoreParts(
        distance,
        area_term,
        distance_term,
        retry_term,
        difficulty_term,
        risk_term,
        score,
    )


def _explain(
    strategy: str,
    start: tuple[float, float],
    weights: SequenceScoreWeights,
    breakdown: Sequence[SequenceScoreBreakdown],
    tie_ids: tuple[str, ...],
) -> str:
    winner = breakdown[0]
    order_text = ", ".join(row.target_id for row in breakdown)
    if strategy == STRATEGY_NEAREST_NEIGHBOR:
        lead = (
            f"策略 nearest_neighbor：从 start_px ({_fmt(start[0])}, {_fmt(start[1])}) 出发，"
            f"下一块选择 {winner.target_id}，因为它到当前点的距离 "
            f"{_fmt(winner.stepwise_distance_px)} px 最小。"
        )
        tie = _tie_note(
            winner.target_id,
            tie_ids,
            "到 start_px 的距离相同",
        )
    elif strategy == STRATEGY_AREA_DESC:
        lead = (
            f"策略 area_desc：下一块选择 {winner.target_id}，"
            f"因为它的面积 {_fmt(winner.area_px)} px 最大。"
        )
        tie = _tie_note(winner.target_id, tie_ids, "面积相同")
    else:
        lead = (
            f"策略 weighted_score：下一块选择 {winner.target_id}。"
            f"得分 {_fmt(winner.score)} = {_formula(winner, weights)}；"
            f"distance 是目标中心到 start_px ({_fmt(start[0])}, {_fmt(start[1])}) 的像素距离。"
        )
        tie = _tie_note(winner.target_id, tie_ids, "得分相同")
    return f"{lead}{tie}完整顺序：{order_text}。"


def _tie_note(winner_id: str, tie_ids: tuple[str, ...], detail: str) -> str:
    if not tie_ids:
        return ""
    others = ", ".join(tie_ids)
    return f"{winner_id} 与 {others} {detail}，按 target_id 选择较小者。"


def _formula(row: SequenceScoreBreakdown, weights: SequenceScoreWeights) -> str:
    return (
        f"{_fmt(weights.w_area)}*area({_fmt(row.area_px)})"
        f" - {_fmt(weights.w_distance)}*distance({_fmt(row.distance_from_start_px)})"
        f" + {_fmt(weights.w_retry)}*retry({row.retry_count})"
        f" + {_fmt(weights.w_difficulty)}*difficulty({_fmt(row.difficulty)})"
        f" - {_fmt(weights.w_risk)}*risk({_fmt(row.risk)})"
    )


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])


def _point(value: object, name: str) -> tuple[float, float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise ValueError(f"{name}必须是像素坐标 (x, y)")
    return (
        _finite_number(value[0], f"{name}.x"),
        _finite_number(value[1], f"{name}.y"),
    )


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}必须是有限数值")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name}必须是有限数值")
    return number


def _non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name}必须是大于等于0的整数")
    return value


def _fmt(value: float) -> str:
    return format(value, ".10g")


__all__ = (
    "DEFAULT_W_AREA",
    "DEFAULT_W_DIFFICULTY",
    "DEFAULT_W_DISTANCE",
    "DEFAULT_W_RETRY",
    "DEFAULT_W_RISK",
    "STRATEGY_AREA_DESC",
    "STRATEGY_NEAREST_NEIGHBOR",
    "STRATEGY_WEIGHTED_SCORE",
    "SequencePlan",
    "SequenceScoreBreakdown",
    "SequenceScoreWeights",
    "SequenceTarget",
    "plan_sequence",
)
