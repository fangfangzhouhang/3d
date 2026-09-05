"""批量比较 B 的算法 Mask 与 A 转换的人工 Mask。

只写评价 JSON，不修改算法。metadata 里 unlabeled 的 Mask 视为候选真值，
不能当成已人工验收的 Ground Truth。
"""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from microcleaning.data_learning.mask_evaluation import evaluate_mask_files

RAW_DIR = PROJECT_ROOT / "data" / "raw_images" / "public"
MASK_DIR = PROJECT_ROOT / "data" / "annotations" / "masks"
METADATA = PROJECT_ROOT / "data" / "metadata.csv"
VISION_ROOT = PROJECT_ROOT / "output" / "vision"
EVAL_ROOT = PROJECT_ROOT / "output" / "data_learning" / "evaluations"
ALGORITHMS = ("otsu", "hsv")


def main() -> int:
    statuses = _annotation_status()
    stems = sorted(path.stem for path in RAW_DIR.glob("*.jpg"))
    EVAL_ROOT.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []

    for stem in stems:
        mask_path = MASK_DIR / f"{stem}.png"
        status = statuses.get(f"{stem}.jpg", "unknown")
        if not mask_path.is_file():
            failures.append({"image": stem, "error": f"缺少人工Mask：{mask_path}"})
            continue
        for algorithm in ALGORITHMS:
            run_dir = _latest_run(algorithm, stem)
            if run_dir is None:
                failures.append({"image": stem, "error": f"缺少{algorithm}输出，请先 run_baseline"})
                continue
            predicted = run_dir / "mask.png"
            try:
                result = evaluate_mask_files(mask_path, predicted)
            except Exception as exc:
                failures.append({"image": stem, "error": f"{algorithm}评价失败：{exc}"})
                continue
            payload = asdict(result)
            payload.update(
                {
                    "image_stem": stem,
                    "algorithm": algorithm,
                    "annotation_status": status,
                    "ground_truth_mask": _rel(mask_path),
                    "predicted_mask": _rel(predicted),
                    "run_dir": _rel(run_dir),
                    "overlay": _rel(run_dir / "contamination_overlay.png"),
                    "summary": _rel(run_dir / "summary.json"),
                    "evidence_boundary": (
                        "已复核人工Mask对照"
                        if status == "labeled"
                        else "自动转换Mask对照；尚未人工复核，不能当正式Ground Truth"
                    ),
                }
            )
            out_path = EVAL_ROOT / f"{stem}_{algorithm}_evaluation.json"
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            rows.append(payload)
            print(f"{stem:12} {algorithm:4}  IoU={result.iou:.4f}  area_err={result.area_error_px:6d}  centroid_err={_fmt(result.centroid_error_px)}  [{status}]")

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_dir": _rel(EVAL_ROOT),
        "image_count": len(stems),
        "row_count": len(rows),
        "labeled_count": sum(1 for row in rows if row["annotation_status"] == "labeled"),
        "failures": failures,
        "note": "只有 annotation_status=labeled 的图可进入正式A/B结论；其余仅供看图和失败分类。",
        "rows": rows,
    }
    summary_path = EVAL_ROOT / "comparison_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"汇总已保存：{summary_path}")
    if failures:
        print(f"失败 {len(failures)} 项，见 comparison_summary.json")
        return 1
    return 0


def _annotation_status() -> dict[str, str]:
    statuses: dict[str, str] = {}
    with METADATA.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            statuses[row["image_name"]] = row.get("annotation_status") or "unknown"
    return statuses


def _latest_run(algorithm: str, stem: str) -> Path | None:
    matches = [
        path
        for path in VISION_ROOT.iterdir()
        if path.is_dir() and path.name.startswith(f"{algorithm}_{stem}_")
    ]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def _rel(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT).as_posix()


def _fmt(value: float | None) -> str:
    return "None" if value is None else f"{value:6.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
