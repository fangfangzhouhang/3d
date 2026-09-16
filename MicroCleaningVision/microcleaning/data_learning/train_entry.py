"""成员 A 的训练入口。

默认 workflow=review：终端逐步提示，对照人工 Mask，只改一轮邻域差异参数后停下。
语义分割 / YOLO / AutoDL 在此入口明确拒绝。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from microcleaning.data_learning.eval_split import eval_split_for
from microcleaning.data_learning.review_round import run_review_round
from microcleaning.vision.local_contrast_baseline import (
    ACTIVE_LOCAL_CONTRAST_POLICY_PATH,
    LocalContrastPolicy,
    load_local_contrast_policy,
    save_local_contrast_policy,
)
from microcleaning.vision.tune_local_contrast import LabeledExample, tune_local_contrast_policy


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
OPENCV_BACKENDS = frozenset({"opencv-tune", "opencv", "local"})
BLOCKED_BACKENDS = {
    "semantic-seg": "语义分割训练尚未实现。当前入口只做 OpenCV 邻域差异调参。",
    "torch": "PyTorch 训练尚未实现。当前入口只做 OpenCV 邻域差异调参。",
    "yolo": "禁止 AGPL YOLO，也不要把 legacy YOLO 当训练入口。",
    "autodl": "AutoDL 训练尚未开放。请先用 --backend opencv-tune。",
}


def load_labeled_examples(
    *,
    raw_root: Path,
    mask_dir: Path,
    metadata_path: Path,
) -> list[LabeledExample]:
    cv2, _np = _load_dependencies()
    from microcleaning.vision.hsv_baseline import read_bgr_image

    if not mask_dir.is_dir():
        raise FileNotFoundError(f"缺少人工Mask目录：{mask_dir}")
    if not raw_root.is_dir():
        raise FileNotFoundError(f"缺少原图目录：{raw_root}")

    statuses = _annotation_status_by_stem(metadata_path) if metadata_path.is_file() else {}
    examples: list[LabeledExample] = []
    skipped: list[str] = []
    missing_image: list[str] = []
    mask_files = sorted(path for path in mask_dir.glob("*.png") if path.is_file())
    if not mask_files:
        raise FileNotFoundError(f"Mask目录里没有 png：{mask_dir}")

    for mask_path in mask_files:
        stem = mask_path.stem
        status = statuses.get(stem)
        if status is not None and status != "labeled":
            skipped.append(stem)
            continue
        image_path = _find_raw_image(raw_root, stem)
        if image_path is None:
            missing_image.append(stem)
            continue
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask is None or mask.size == 0:
            skipped.append(stem)
            continue
        examples.append(
            LabeledExample(
                stem=stem,
                image=read_bgr_image(image_path),
                ground_truth_mask=mask,
                eval_split=eval_split_for(stem),
                image_path=str(image_path),
                mask_path=str(mask_path),
            )
        )
    if not examples:
        detail = ""
        if missing_image:
            detail += f"；有Mask但找不到原图：{', '.join(missing_image)}"
        if skipped:
            detail += f"；已跳过：{', '.join(skipped)}"
        raise FileNotFoundError(f"没有可用的 labeled 原图/Mask 对{detail}")
    return examples


def run_opencv_tune(
    *,
    raw_root: Path,
    mask_dir: Path,
    metadata_path: Path,
    output_dir: Path,
    apply: bool,
    apply_path: Path,
    start_policy_path: Path | None = None,
    max_rounds: int = 2,
) -> tuple[int, Path]:
    examples = load_labeled_examples(
        raw_root=raw_root,
        mask_dir=mask_dir,
        metadata_path=metadata_path,
    )
    start_policy = (
        load_local_contrast_policy(start_policy_path)
        if start_policy_path is not None
        else LocalContrastPolicy()
    )
    result = tune_local_contrast_policy(
        examples,
        start_policy=start_policy,
        max_rounds=max_rounds,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "tune_report.json"
    policy_path = output_dir / "local_contrast_policy.json"
    report_path.write_text(
        json.dumps(result.as_report(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    save_local_contrast_policy(
        result.best_policy,
        policy_path,
        extra={
            "apply_allowed": result.apply_allowed,
            "apply_reason": result.apply_reason,
            "report": report_path.as_posix(),
        },
    )
    print(f"调参报告：{report_path}")
    print(f"候选策略：{policy_path}")
    print(f"开发集 IoU：{result.develop_baseline.mean_iou:.4f} → {result.develop_best.mean_iou:.4f}")
    if result.holdout_best is not None and result.holdout_baseline is not None:
        print(
            "留出集 IoU："
            f"{result.holdout_baseline.mean_iou:.4f} → {result.holdout_best.mean_iou:.4f}"
        )
    print(result.apply_reason)
    if not apply:
        return 0, report_path
    if not result.apply_allowed:
        print("未写入生效文件。", file=sys.stderr)
        return 2, report_path
    save_local_contrast_policy(
        result.best_policy,
        apply_path,
        extra={
            "applied": True,
            "apply_reason": result.apply_reason,
            "report": report_path.as_posix(),
        },
    )
    print(f"已写入生效策略：{apply_path}")
    print("run_baseline / Demo 的 local 会自动读取该文件；加 --no-tuned-policy 可回退 v0.1。")
    return 0, report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="对照人工Mask自动调参。默认 review：终端逐步提示，只改一轮后停下等人检查。",
    )
    parser.add_argument(
        "--workflow",
        choices=("review", "search"),
        default="review",
        help="review=终端逐步提示、只改一轮后停下；search=静默网格搜索",
    )
    parser.add_argument(
        "--backend",
        default="opencv-tune",
        help="opencv-tune 为已实现；semantic-seg/torch/yolo/autodl 会拒绝",
    )
    parser.add_argument("--raw-root", type=Path, default=Path("data") / "raw_images")
    parser.add_argument("--mask-dir", type=Path, default=Path("data") / "annotations" / "masks")
    parser.add_argument("--metadata", type=Path, default=Path("data") / "metadata.csv")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="调参报告目录；默认 output/data_learning/tuning/<时间>/",
    )
    parser.add_argument(
        "--apply-path",
        type=Path,
        default=ACTIVE_LOCAL_CONTRAST_POLICY_PATH,
        help="--apply 时写入的生效策略路径",
    )
    parser.add_argument("--start-policy", type=Path, help="可选的起始策略 JSON")
    parser.add_argument(
        "--stems",
        nargs="+",
        help="只处理这些文件名主干。单张默认只对照不改参；调参请不要加这个参数",
    )
    parser.add_argument(
        "--allow-single-image",
        action="store_true",
        help="允许在不足 3 张开发图时仍然改参（容易过拟合，仅调试用）",
    )
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="仅当开发集提升且留出集未变差时，写入生效策略 JSON",
    )
    args = parser.parse_args(argv)
    backend = args.backend.strip().lower()
    if backend in BLOCKED_BACKENDS:
        print(BLOCKED_BACKENDS[backend], file=sys.stderr)
        return 2
    if backend not in OPENCV_BACKENDS:
        print(
            f"未知 backend={args.backend}。可用：opencv-tune。拒绝：semantic-seg / torch / yolo / autodl。",
            file=sys.stderr,
        )
        return 2
    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = (
            Path("output")
            / "data_learning"
            / "tuning"
            / f"local_{stamp}_{uuid4().hex[:8]}"
        )
    start_policy = (
        load_local_contrast_policy(args.start_policy)
        if args.start_policy is not None
        else LocalContrastPolicy()
    )
    if args.workflow == "review":
        examples = load_labeled_examples(
            raw_root=args.raw_root,
            mask_dir=args.mask_dir,
            metadata_path=args.metadata,
        )
        examples = _filter_stems(examples, args.stems)
        if args.stems:
            print("注意：--stems 只选择要处理的图；--output-dir 只是输出目录名字，两者不是一回事。")
        if not args.metadata.is_file():
            print("未找到 metadata.csv，将按 Mask 文件名和原图配对。")
        print(f"已配对 {len(examples)} 张人工 Mask。")
        run_review_round(
            examples,
            output_dir=output_dir,
            start_policy=start_policy,
            apply=args.apply,
            apply_path=args.apply_path,
            allow_single_image=args.allow_single_image,
        )
        return 0
    code, _report = run_opencv_tune(
        raw_root=args.raw_root,
        mask_dir=args.mask_dir,
        metadata_path=args.metadata,
        output_dir=output_dir,
        apply=args.apply,
        apply_path=args.apply_path,
        start_policy_path=args.start_policy,
        max_rounds=args.max_rounds,
    )
    return code


def _find_raw_image(raw_root: Path, image_name: str) -> Path | None:
    direct = raw_root / image_name
    if direct.is_file():
        return direct
    matches = [path for path in raw_root.rglob(image_name) if path.is_file()]
    if matches:
        return min(matches, key=lambda path: len(path.parts))
    stem = Path(image_name).stem
    stem_matches = [
        path
        for path in raw_root.rglob("*")
        if path.is_file() and path.stem == stem and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    if len(stem_matches) == 1:
        return stem_matches[0]
    if stem_matches:
        return min(stem_matches, key=lambda path: len(path.parts))
    return None


def _annotation_status_by_stem(metadata_path: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    with metadata_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("image_name") or "").strip()
            if not name:
                continue
            statuses[Path(name).stem] = (row.get("annotation_status") or "").strip()
    return statuses


def _filter_stems(examples: list[LabeledExample], stems: list[str] | None) -> list[LabeledExample]:
    if not stems:
        return examples
    wanted = {Path(stem).stem for stem in stems}
    filtered = [item for item in examples if item.stem in wanted]
    if not filtered:
        raise FileNotFoundError(f"指定的文件名没有配对成功：{', '.join(sorted(wanted))}")
    return filtered


def _load_dependencies():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "训练入口需要感知依赖；请在项目.venv安装requirements/perception-opencv.txt"
        ) from exc
    return cv2, np


if __name__ == "__main__":
    raise SystemExit(main())
