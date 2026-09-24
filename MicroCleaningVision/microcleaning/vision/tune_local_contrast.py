"""用人工 Mask 对照，自动搜索 LocalContrastPolicy。

只在开发集上改参数，一次只动一个字段。留出集只在搜完后打一次分，
用来决定能不能写入生效文件。这不是深度学习训练，也不发泵。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping, Sequence

from microcleaning.data_learning.eval_split import eval_split_for
from microcleaning.data_learning.mask_evaluation import evaluate_masks
from microcleaning.vision.local_contrast_baseline import (
    LOCAL_CONTRAST_VERSION,
    LocalContrastPolicy,
    segment_contamination,
)

WHITEWASH_AREA_RATIO = 0.50
DEFAULT_SEARCH_SPACE: dict[str, tuple[Any, ...]] = {
    "min_component_area_px": (10, 20, 40, 80, 160),
    "max_aspect_ratio": (3.0, 4.0, 6.0, 8.0, 12.0),
    "min_residual": (4.0, 6.0, 8.0, 12.0, 16.0),
    "max_area_ratio": (0.10, 0.15, 0.20, 0.30, 0.40),
}


@dataclass(frozen=True)
class LabeledExample:
    stem: str
    image: Any
    ground_truth_mask: Any
    eval_split: str
    image_path: str | None = None
    mask_path: str | None = None


@dataclass(frozen=True)
class PolicyScore:
    mean_iou: float
    mean_precision: float
    mean_recall: float
    disaster_rate: float
    mean_false_positive_px: float
    count: int
    per_image: tuple[dict[str, Any], ...]

    def rank_key(self) -> tuple[float, float, float]:
        return (
            self.mean_iou - self.disaster_rate,
            min(self.mean_precision, self.mean_recall),
            -self.mean_false_positive_px,
        )


@dataclass(frozen=True)
class TuneResult:
    start_policy: LocalContrastPolicy
    best_policy: LocalContrastPolicy
    develop_baseline: PolicyScore
    develop_best: PolicyScore
    holdout_baseline: PolicyScore | None
    holdout_best: PolicyScore | None
    history: tuple[dict[str, Any], ...]
    apply_allowed: bool
    apply_reason: str

    def as_report(self) -> dict[str, Any]:
        return {
            "algorithm": "local",
            "base_version": LOCAL_CONTRAST_VERSION,
            "feeds_action_request": False,
            "start_policy": asdict(self.start_policy),
            "best_policy": asdict(self.best_policy),
            "develop_baseline": _score_payload(self.develop_baseline),
            "develop_best": _score_payload(self.develop_best),
            "holdout_baseline": _score_payload(self.holdout_baseline),
            "holdout_best": _score_payload(self.holdout_best),
            "apply_allowed": self.apply_allowed,
            "apply_reason": self.apply_reason,
            "history": list(self.history),
            "note": "调参只看开发集；是否写入生效文件看留出集是否变差。不是语义分割训练。",
        }


def split_examples(examples: Sequence[LabeledExample]) -> tuple[list[LabeledExample], list[LabeledExample]]:
    develop = [item for item in examples if _split_of(item) == "develop"]
    holdout = [item for item in examples if _split_of(item) == "holdout"]
    return develop, holdout


def score_policy(policy: LocalContrastPolicy, examples: Sequence[LabeledExample]) -> PolicyScore:
    if not examples:
        raise ValueError("没有可评分的labeled图")
    policy.validate()
    rows: list[dict[str, Any]] = []
    disasters = 0
    for item in examples:
        predicted = segment_contamination(item.image, policy=policy).mask
        evaluation = evaluate_masks(item.ground_truth_mask, predicted)
        height, width = evaluation.height_px, evaluation.width_px
        area_ratio = evaluation.predicted_area_px / max(height * width, 1)
        disaster = area_ratio >= WHITEWASH_AREA_RATIO
        if disaster:
            disasters += 1
        rows.append(
            {
                "image_stem": item.stem,
                "eval_split": _split_of(item),
                "iou": evaluation.iou,
                "precision": evaluation.precision,
                "recall": evaluation.recall,
                "false_positive_px": evaluation.false_positive_px,
                "false_negative_px": evaluation.false_negative_px,
                "area_error_px": evaluation.area_error_px,
                "centroid_error_px": evaluation.centroid_error_px,
                "predicted_area_ratio": area_ratio,
                "whitewash": disaster,
            }
        )
    count = len(rows)
    return PolicyScore(
        mean_iou=sum(row["iou"] for row in rows) / count,
        mean_precision=sum(row["precision"] for row in rows) / count,
        mean_recall=sum(row["recall"] for row in rows) / count,
        disaster_rate=disasters / count,
        mean_false_positive_px=sum(row["false_positive_px"] for row in rows) / count,
        count=count,
        per_image=tuple(rows),
    )


def decide_apply(
    *,
    develop_baseline: PolicyScore,
    develop_best: PolicyScore,
    holdout_baseline: PolicyScore | None,
    holdout_best: PolicyScore | None,
) -> tuple[bool, str]:
    if develop_best.mean_iou <= develop_baseline.mean_iou + 1e-6:
        return False, "开发集 mean IoU 没有提升，保持代码内默认参数"
    if holdout_baseline is None or holdout_best is None:
        return True, "开发集有提升，但没有留出集对照，写入后不能当过关"
    if holdout_best.mean_iou + 1e-4 < holdout_baseline.mean_iou:
        return False, "留出集 mean IoU 下降，拒绝写入生效文件"
    if holdout_best.disaster_rate > holdout_baseline.disaster_rate + 1e-9:
        return False, "留出集整图涂白变多，拒绝写入生效文件"
    return True, "开发集提升且留出集未变差，可以写入生效文件"


def policy_field_diff(
    before: LocalContrastPolicy,
    after: LocalContrastPolicy,
) -> tuple[tuple[str, Any, Any], ...]:
    changes = []
    for name in asdict(before):
        old = getattr(before, name)
        new = getattr(after, name)
        if old != new:
            changes.append((name, old, new))
    return tuple(changes)


def try_single_field(
    policy: LocalContrastPolicy,
    examples: Sequence[LabeledExample],
    field: str,
    candidates: Sequence[Any],
    *,
    on_trial: Any | None = None,
) -> tuple[LocalContrastPolicy, PolicyScore, tuple[dict[str, Any], ...]]:
    """只试一个字段的候选值，返回该字段上最好的策略。"""

    baseline = score_policy(policy, examples)
    best_policy = policy
    best_score = baseline
    history: list[dict[str, Any]] = []
    for value in candidates:
        if getattr(policy, field) == value:
            continue
        trial = replace(policy, **{field: value})
        try:
            trial.validate()
        except ValueError:
            continue
        trial_score = score_policy(trial, examples)
        record = {
            "field": field,
            "value": value,
            "eval_split": "develop",
            "stems": [item.stem for item in examples],
            "mean_iou": trial_score.mean_iou,
            "mean_precision": trial_score.mean_precision,
            "mean_recall": trial_score.mean_recall,
            "disaster_rate": trial_score.disaster_rate,
        }
        history.append(record)
        if on_trial is not None:
            on_trial(record, baseline)
        if trial_score.rank_key() > best_score.rank_key():
            best_policy = trial
            best_score = trial_score
    return best_policy, best_score, tuple(history)


def tune_local_contrast_policy(
    examples: Sequence[LabeledExample],
    *,
    start_policy: LocalContrastPolicy | None = None,
    search_space: Mapping[str, Sequence[Any]] | None = None,
    max_rounds: int = 2,
) -> TuneResult:
    develop, holdout = split_examples(examples)
    if not develop:
        raise ValueError("没有开发集 labeled 对，不能调参")
    current = start_policy or LocalContrastPolicy()
    current.validate()
    space = search_space or DEFAULT_SEARCH_SPACE
    history: list[dict[str, Any]] = []
    develop_baseline = score_policy(current, develop)
    holdout_baseline = score_policy(current, holdout) if holdout else None

    for round_index in range(max(1, max_rounds)):
        round_improved = False
        for field, candidates in space.items():
            field_best_policy = current
            field_best_score = score_policy(current, develop)
            for value in candidates:
                if getattr(current, field) == value:
                    continue
                trial = replace(current, **{field: value})
                try:
                    trial.validate()
                except ValueError:
                    continue
                trial_score = score_policy(trial, develop)
                history.append(
                    {
                        "round": round_index,
                        "field": field,
                        "value": value,
                        "eval_split": "develop",
                        "stems": [item.stem for item in develop],
                        "mean_iou": trial_score.mean_iou,
                        "disaster_rate": trial_score.disaster_rate,
                    }
                )
                if trial_score.rank_key() > field_best_score.rank_key():
                    field_best_policy = trial
                    field_best_score = trial_score
            if field_best_policy != current:
                current = field_best_policy
                round_improved = True
        if not round_improved:
            break

    develop_best = score_policy(current, develop)
    holdout_best = score_policy(current, holdout) if holdout else None
    apply_allowed, apply_reason = decide_apply(
        develop_baseline=develop_baseline,
        develop_best=develop_best,
        holdout_baseline=holdout_baseline,
        holdout_best=holdout_best,
    )
    return TuneResult(
        start_policy=start_policy or LocalContrastPolicy(),
        best_policy=current,
        develop_baseline=develop_baseline,
        develop_best=develop_best,
        holdout_baseline=holdout_baseline,
        holdout_best=holdout_best,
        history=tuple(history),
        apply_allowed=apply_allowed,
        apply_reason=apply_reason,
    )


def _split_of(item: LabeledExample) -> str:
    if item.eval_split in {"develop", "holdout"}:
        return item.eval_split
    return eval_split_for(item.stem)


def _score_payload(score: PolicyScore | None) -> dict[str, Any] | None:
    if score is None:
        return None
    return {
        "count": score.count,
        "mean_iou": score.mean_iou,
        "mean_precision": score.mean_precision,
        "mean_recall": score.mean_recall,
        "disaster_rate": score.disaster_rate,
        "mean_false_positive_px": score.mean_false_positive_px,
        "per_image": list(score.per_image),
    }
