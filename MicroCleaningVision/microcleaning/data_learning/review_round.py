"""一轮可视化对照：跑 B 算法 → 对比人工 Mask → 分析 → 只改一个参数 → 再跑一次后停下。

每一步都在终端打印提示。默认不写入生效策略，方便人检查叠加图后再决定。
这不是深度学习训练，也不发泵。
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence, TextIO

from microcleaning.data_learning.eval_split import HOLD_OUT_STEMS
from microcleaning.vision.local_contrast_baseline import (
    LOCAL_CONTRAST_VERSION,
    LocalContrastPolicy,
    save_local_contrast_policy,
    segment_contamination,
)
from microcleaning.vision.run_baseline import _draw_contamination
from microcleaning.vision.tune_local_contrast import (
    DEFAULT_SEARCH_SPACE,
    LabeledExample,
    PolicyScore,
    decide_apply,
    policy_field_diff,
    score_policy,
    split_examples,
    try_single_field,
)

FIELD_HELP = {
    "min_component_area_px": "丢掉过小的碎点（减轻小斑误检）",
    "max_aspect_ratio": "丢掉过细长的条（减轻铜线一类误检）",
    "min_residual": "和周围差多少才算污渍（漏检弱斑 vs 纹理误检）",
    "max_area_ratio": "一块最大能占画面多少（整图涂白 vs 大块被切掉）",
}
MIN_DEVELOP_TO_TUNE = 3
DEVELOP_STEM_HINT = "public_001、public_003～public_010"


def run_review_round(
    examples: Sequence[LabeledExample],
    *,
    output_dir: Path,
    start_policy: LocalContrastPolicy | None = None,
    apply: bool = False,
    apply_path: Path | None = None,
    stream: TextIO | None = None,
    allow_single_image: bool = False,
) -> dict[str, Any]:
    """执行一轮对照并返回摘要；默认不把新参数写成全局生效文件。"""

    out = stream or sys.stdout
    started = time.perf_counter()
    develop, holdout = split_examples(examples)
    _print_pairing(out, examples)

    inspect_only = False
    inspect_reason = ""
    work_set = develop
    if not develop and not holdout:
        raise ValueError("没有 labeled 对。请先放好原图和同名人工 Mask。")
    if not develop:
        inspect_only = True
        names = ", ".join(item.stem for item in holdout)
        inspect_reason = (
            f"{names} 已经配对成功，但属于冻结留出图 "
            f"（{', '.join(sorted(HOLD_OUT_STEMS))}），不能拿来改参数。"
            f"可调参的开发图是 {DEVELOP_STEM_HINT}。"
            "本轮只做对照，不改算法。请去掉 --stems，或换成开发图。"
        )
        work_set = list(holdout)
    elif len(develop) < MIN_DEVELOP_TO_TUNE and not allow_single_image:
        inspect_only = True
        inspect_reason = (
            f"当前只有 {len(develop)} 张开发图。只按一两张图改参数很容易过拟合："
            "这张变好，换一张就变差。本轮只对照、分析，不改算法。"
            f"请不要加 --stems，让全部开发集一起调（至少 {MIN_DEVELOP_TO_TUNE} 张）。"
        )
        work_set = list(develop)

    policy = start_policy or LocalContrastPolicy()
    policy.validate()
    output_dir.mkdir(parents=True, exist_ok=True)
    before_dir = output_dir / "before"
    after_dir = output_dir / "after"

    _banner(out, "第 1 步 / 7", "正在用 B 的邻域差异算法跑图，并标出 Mask 与物体中心")
    _say(out, f"算法版本：{LOCAL_CONTRAST_VERSION}")
    _say(out, f"开发图 {len(develop)} 张；冻结留出 {len(holdout)} 张")
    if inspect_only:
        _say(out, inspect_reason)
    step1 = time.perf_counter()
    before_runs = _run_and_mark(work_set, policy, before_dir, out)
    _elapsed(out, step1, extra=f"共分割 {len(work_set)} 张真实图")

    _banner(out, "第 2 步 / 7", "对比中：算法 Mask vs 人工 Mask")
    step2 = time.perf_counter()
    before_score = score_policy(policy, work_set)
    title = "第一轮对比指标（冻结留出，仅检查）" if not develop else "第一轮对比指标（开发集）"
    if inspect_only and not develop:
        title = "第一轮对比指标（冻结留出，只检查不调参）"
    _print_score_table(out, before_score, title=title)
    _elapsed(out, step2, extra="每张都重新用当前参数分割后再和人工 Mask 算 IoU")

    _banner(out, "第 3 步 / 7", "针对表现不好的地方做简要分析")
    diagnosis = diagnose_score(before_score)
    for line in diagnosis["lines"]:
        _say(out, line)

    after_policy = policy
    after_score = before_score
    after_runs = before_runs
    tried: list[dict[str, Any]] = []
    changes: tuple[tuple[str, Any, Any], ...] = ()

    if inspect_only:
        _banner(out, "第 4～6 步 / 7", "跳过改参")
        _say(out, "本轮不修改算法。")
        _say(out, inspect_reason)
    else:
        _banner(out, "第 4 步 / 7", "正在修改对应的算法（开发集上只改一个字段，改完即停）")
        _say(out, f"在 {len(develop)} 张开发图上试候选；每个候选都会打印真实 IoU。")
        step4 = time.perf_counter()
        after_policy, after_score, tried = _adjust_one_field(
            policy, develop, diagnosis["field_order"], out
        )
        _elapsed(out, step4, extra=f"共试了 {len(tried)} 组参数，每组都跑完全部 {len(develop)} 张开发图")
        changes = policy_field_diff(policy, after_policy)

        _banner(out, "第 5 步 / 7", "修改完成，说明本轮动了哪些参数")
        if not changes:
            _say(out, "本轮没有找到比当前更好的单字段改动，保持原参数。")
        else:
            for name, old, new in changes:
                help_text = FIELD_HELP.get(name, "")
                extra = f"（{help_text}）" if help_text else ""
                _say(out, f"  {name}: {old} → {new} {extra}")

            _banner(out, "第 6 步 / 7", "带着修改后的算法再跑一次，继续对比")
            step6 = time.perf_counter()
            after_runs = _run_and_mark(develop, after_policy, after_dir, out)
            after_score = score_policy(after_policy, develop)
            _print_score_table(out, after_score, title="第二轮对比指标（开发集，改参后）")
            _print_delta(out, before_score, after_score)
            _elapsed(out, step6)

    holdout_before = None
    holdout_after = None
    if inspect_only:
        apply_allowed, apply_reason = False, "本轮只对照，不改参数，不能写入生效文件"
        if not develop:
            holdout_before = before_score
            holdout_after = before_score
        elif holdout:
            holdout_before = score_policy(policy, holdout)
            holdout_after = holdout_before
    else:
        holdout_before = score_policy(policy, holdout) if holdout else None
        holdout_after = score_policy(after_policy, holdout) if holdout else None
        if holdout_before is not None and holdout_after is not None:
            _say(out, "留出集只在改完后看一次，供你检查，不是下一轮继续拧旋钮的依据：")
            _say(
                out,
                "  留出 mean IoU "
                f"{holdout_before.mean_iou:.4f} → {holdout_after.mean_iou:.4f}，"
                f"灾难率 {holdout_before.disaster_rate:.0%} → {holdout_after.disaster_rate:.0%}",
            )
        apply_allowed, apply_reason = decide_apply(
            develop_baseline=before_score,
            develop_best=after_score,
            holdout_baseline=holdout_before,
            holdout_best=holdout_after,
        )

    candidate_path = output_dir / "local_contrast_policy.json"
    save_local_contrast_policy(
        after_policy,
        candidate_path,
        extra={
            "apply_allowed": apply_allowed,
            "apply_reason": apply_reason,
            "round": "review-one-pass",
            "inspect_only": inspect_only,
        },
    )
    applied = False
    if apply and changes and apply_allowed and apply_path is not None:
        save_local_contrast_policy(
            after_policy,
            apply_path,
            extra={"applied": True, "apply_reason": apply_reason},
        )
        applied = True

    report = {
        "algorithm": "local",
        "base_version": LOCAL_CONTRAST_VERSION,
        "feeds_action_request": False,
        "inspect_only": inspect_only,
        "inspect_reason": inspect_reason,
        "start_policy": asdict(policy),
        "best_policy": asdict(after_policy),
        "changes": [{"field": name, "from": old, "to": new} for name, old, new in changes],
        "diagnosis": diagnosis,
        "develop_before": _score_brief(before_score if develop else None),
        "develop_after": _score_brief(after_score if develop and not inspect_only else (before_score if develop else None)),
        "holdout_before": _score_brief(holdout_before),
        "holdout_after": _score_brief(holdout_after),
        "apply_allowed": apply_allowed,
        "apply_reason": apply_reason,
        "applied": applied,
        "before_runs": before_runs,
        "after_runs": after_runs,
        "candidate_policy": candidate_path.as_posix(),
        "elapsed_s": time.perf_counter() - started,
        "note": "只走了一轮。请打开叠加图检查后再决定是否 --apply。",
    }
    report_path = output_dir / "review_round.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    _banner(out, "第 7 步 / 7", "本轮结束，请你检查。程序在这里停下，不会自动再改一轮")
    _say(out, f"叠加图：{before_dir}")
    if changes:
        _say(out, f"改参后叠加图：{after_dir}")
    _say(out, f"本轮摘要：{report_path}")
    _say(out, apply_reason)
    if applied:
        _say(out, f"已写入生效策略：{apply_path}")
    else:
        _say(out, "没有写入全局生效文件。")
    _elapsed(out, started, extra="全程真实计算，没有插入 sleep，也没有跳过分割")
    _say(out, "看见污渍不会发泵。这不能当成识别过关，也不能当成清洗有效。")
    return report


def diagnose_score(score: PolicyScore) -> dict[str, Any]:
    """根据 IoU / P / R / 涂白，给出人话分析和建议先动的字段。"""

    precision = score.mean_precision
    recall = score.mean_recall
    lines: list[str] = [
        f"开发集平均 IoU={score.mean_iou:.4f}，精确率 P={precision:.4f}，召回率 R={recall:.4f}，"
        f"整图涂白 {score.disaster_rate:.0%}。",
    ]
    field_order: list[str] = []
    if score.disaster_rate > 0:
        lines.append("主要问题：有图几乎整张涂白，先收紧单块最大面积。")
        field_order.append("max_area_ratio")
    if recall + 0.08 < precision:
        lines.append("主要问题：漏检偏多（召回低）。真污渍没圈全，先让弱差异更容易被留下。")
        field_order.extend(["min_residual", "max_area_ratio", "min_component_area_px"])
    elif precision + 0.08 < recall:
        lines.append("主要问题：误检偏多（精确率低）。把背景/细线当成污渍了，先丢掉碎点和细长条。")
        field_order.extend(["min_component_area_px", "max_aspect_ratio", "min_residual"])
    else:
        lines.append("重叠不够均匀，先微调“和周围差多少才算污渍”。")
        field_order.extend(["min_residual", "min_component_area_px", "max_aspect_ratio", "max_area_ratio"])

    worst = sorted(score.per_image, key=lambda row: row["iou"])[:3]
    if worst:
        lines.append("最差的几张（请对照叠加图看）：")
        for row in worst:
            cause = _row_cause(row)
            lines.append(
                f"  {row['image_stem']}: IoU={row['iou']:.4f}，P={row['precision']:.4f}，"
                f"R={row['recall']:.4f}，FP={row['false_positive_px']:.0f}，"
                f"FN={row['false_negative_px']:.0f}。{cause}"
            )
    unique_fields = list(dict.fromkeys(field_order))
    lines.append(f"本轮打算先试的旋钮顺序：{' → '.join(unique_fields)}")
    return {"lines": lines, "field_order": unique_fields, "worst": [row["image_stem"] for row in worst]}


def _adjust_one_field(
    policy: LocalContrastPolicy,
    develop: Sequence[LabeledExample],
    field_order: Sequence[str],
    out: TextIO,
) -> tuple[LocalContrastPolicy, PolicyScore, list[dict[str, Any]]]:
    tried: list[dict[str, Any]] = []
    for field in field_order:
        candidates = DEFAULT_SEARCH_SPACE.get(field)
        if not candidates:
            continue
        _say(out, f"正在试字段 {field}：{FIELD_HELP.get(field, '')}")

        def _on_trial(record: dict[str, Any], baseline: PolicyScore) -> None:
            delta = record["mean_iou"] - baseline.mean_iou
            _say(
                out,
                f"  试 {record['field']}={record['value']}："
                f"IoU={record['mean_iou']:.4f}  P={record['mean_precision']:.4f}  "
                f"R={record['mean_recall']:.4f}  相对当前 {delta:+.4f}",
            )

        new_policy, new_score, history = try_single_field(
            policy, develop, field, candidates, on_trial=_on_trial
        )
        tried.extend(history)
        if new_policy != policy:
            return new_policy, new_score, tried
        _say(out, f"  {field} 的候选都没有比现在更好，换下一个字段。")
    return policy, score_policy(policy, develop), tried


def _run_and_mark(
    examples: Sequence[LabeledExample],
    policy: LocalContrastPolicy,
    output_dir: Path,
    out: TextIO,
) -> list[dict[str, Any]]:
    cv2, _np = _load_cv2()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for item in examples:
        result = segment_contamination(item.image, policy=policy)
        run_dir = output_dir / item.stem
        run_dir.mkdir(parents=True, exist_ok=True)
        mask_path = run_dir / "mask.png"
        overlay_path = run_dir / "contamination_overlay.png"
        if not cv2.imwrite(str(mask_path), result.mask):
            raise OSError(f"无法写入mask：{mask_path}")
        overlay = _draw_contamination(item.image, result.mask, result.measurement.centroid_px, cv2)
        if not cv2.imwrite(str(overlay_path), overlay):
            raise OSError(f"无法写入叠加图：{overlay_path}")
        measurement = result.measurement
        height, width = item.image.shape[:2]
        _say(out, f"  {item.stem}  尺寸 {width}x{height}")
        if item.image_path:
            _say(out, f"    原图     {item.image_path}")
        if item.mask_path:
            _say(out, f"    人工Mask {item.mask_path}")
        _say(
            out,
            f"    算法面积 {measurement.area_px:.0f} px，中心 {measurement.centroid_px}，"
            f"连通块 {measurement.component_count}",
        )
        _say(out, f"    算法叠加图 {overlay_path}")
        rows.append(
            {
                "image_stem": item.stem,
                "area_px": measurement.area_px,
                "centroid_px": measurement.centroid_px,
                "component_count": measurement.component_count,
                "mask": mask_path.as_posix(),
                "overlay": overlay_path.as_posix(),
            }
        )
    return rows


def _print_score_table(out: TextIO, score: PolicyScore, *, title: str) -> None:
    _say(out, title)
    _say(out, f"{'图片':<16} {'IoU':>8} {'P':>8} {'R':>8} {'FP':>8} {'FN':>8} {'中心误差':>10}")
    for row in score.per_image:
        centroid = row.get("centroid_error_px")
        centroid_text = "   None" if centroid is None else f"{centroid:8.1f}"
        _say(
            out,
            f"{str(row['image_stem']):<16} {row['iou']:8.4f} {row['precision']:8.4f} "
            f"{row['recall']:8.4f} {row['false_positive_px']:8.0f} "
            f"{row['false_negative_px']:8.0f} {centroid_text:>10}",
        )
    _say(
        out,
        f"{'平均':<16} {score.mean_iou:8.4f} {score.mean_precision:8.4f} "
        f"{score.mean_recall:8.4f} {score.mean_false_positive_px:8.1f} "
        f"{'':>8} {'':>10}",
    )
    _say(out, "IoU=重叠好不好；P=涂白的里有多少真是污渍；R=真污渍找回多少；FP=多涂；FN=漏掉。")


def _print_delta(out: TextIO, before: PolicyScore, after: PolicyScore) -> None:
    def delta(new: float, old: float) -> str:
        change = new - old
        sign = "+" if change >= 0 else ""
        return f"{sign}{change:.4f}"

    _say(out, "和第一轮相比：")
    _say(out, f"  mean IoU {before.mean_iou:.4f} → {after.mean_iou:.4f}（{delta(after.mean_iou, before.mean_iou)}）")
    _say(out, f"  P {before.mean_precision:.4f} → {after.mean_precision:.4f}（{delta(after.mean_precision, before.mean_precision)}）")
    _say(out, f"  R {before.mean_recall:.4f} → {after.mean_recall:.4f}（{delta(after.mean_recall, before.mean_recall)}）")
    if after.mean_iou > before.mean_iou + 1e-6:
        _say(out, "效果有提升，但仍请你打开改参前后叠加图确认位置没有跑偏。")
    elif after.mean_iou + 1e-6 < before.mean_iou:
        _say(out, "数值变差了。本轮候选不会当作更好版本；请你检查后决定是否回退。")
    else:
        _say(out, "平均 IoU 几乎没变。请看单张叠加图，不要只看平均数。")


def _row_cause(row: dict[str, Any]) -> str:
    if row.get("whitewash"):
        return "整图涂白。"
    fp = float(row.get("false_positive_px") or 0)
    fn = float(row.get("false_negative_px") or 0)
    if fn > fp * 1.5 and fn > 0:
        return "漏检为主。"
    if fp > fn * 1.5 and fp > 0:
        return "误检为主。"
    return "边界和位置对不齐。"


def _score_brief(score: PolicyScore | None) -> dict[str, Any] | None:
    if score is None:
        return None
    return {
        "count": score.count,
        "mean_iou": score.mean_iou,
        "mean_precision": score.mean_precision,
        "mean_recall": score.mean_recall,
        "disaster_rate": score.disaster_rate,
        "per_image": list(score.per_image),
    }


def _banner(out: TextIO, step: str, title: str) -> None:
    print(file=out)
    print("=" * 72, file=out)
    print(f"[{step}] {title}", file=out)
    print("=" * 72, file=out)
    out.flush()


def _say(out: TextIO, message: str) -> None:
    print(message, file=out)
    out.flush()


def _print_pairing(out: TextIO, examples: Sequence[LabeledExample]) -> None:
    _say(out, "配对检查（按文件名主干对应；--output-dir 只决定结果放哪，不选图）：")
    if not examples:
        _say(out, "  （没有配对成功的图）")
        return
    for item in examples:
        role = "留出/冻结，不调参" if item.eval_split == "holdout" else "开发"
        _say(out, f"  {item.stem}  [{role}]")
        _say(out, f"    原图     {item.image_path or '（内存图，无路径）'}")
        _say(out, f"    人工Mask {item.mask_path or '（内存 Mask，无路径）'}")
    _say(out, f"冻结留出名单：{', '.join(sorted(HOLD_OUT_STEMS))}")
    _say(out, f"开发图示例：{DEVELOP_STEM_HINT}")


def _elapsed(out: TextIO, started: float, *, extra: str = "") -> None:
    seconds = time.perf_counter() - started
    suffix = f"；{extra}" if extra else ""
    _say(out, f"本步实际用时 {seconds:.3f} 秒（未插入延时）{suffix}")


def _load_cv2():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "对照流程需要感知依赖；请在项目.venv安装requirements/perception-opencv.txt"
        ) from exc
    return cv2, np
