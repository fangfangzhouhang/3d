"""为原始图片半自动补齐 metadata.csv 的可证明字段。

程序只填写能从文件直接得到的事实：文件名和分辨率。类别、来源、拍摄日期、设备、
倍率等默认保持 unknown；已有人工记录永不覆盖。默认只预览，只有 ``--apply`` 才写入。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from microcleaning.data_learning.image_quality import inspect_image_file
from microcleaning.data_learning.inspect_images import IMAGE_SUFFIXES


METADATA_FIELDS = (
    "image_name",
    "category",
    "source",
    "capture_date",
    "device",
    "resolution",
    "magnification",
    "annotation_status",
    "remark",
)


@dataclass(frozen=True)
class MetadataBuildResult:
    raw_image_count: int
    existing_record_count: int
    new_record_count: int
    unchanged_record_count: int
    mode: str
    new_rows: tuple[dict[str, str], ...]


def build_metadata(
    raw_root: Path,
    metadata_csv: Path,
    *,
    apply: bool = False,
    category: str = "unknown",
    source: str = "unknown",
    capture_date: str = "unknown",
    device: str = "unknown",
    magnification: str = "unknown",
) -> MetadataBuildResult:
    """扫描原图并生成缺失行；默认 dry-run，已有行保持原样。"""

    images = _find_images(raw_root)
    existing_rows = _read_metadata(metadata_csv)
    existing_by_name = _index_existing_rows(existing_rows)
    _reject_ambiguous_basenames(images)

    defaults = {
        "category": _nonempty_or_unknown(category),
        "source": _nonempty_or_unknown(source),
        "capture_date": _nonempty_or_unknown(capture_date),
        "device": _nonempty_or_unknown(device),
        "magnification": _nonempty_or_unknown(magnification),
    }
    new_rows: list[dict[str, str]] = []
    for image_path in images:
        image_name = image_path.name
        if image_name in existing_by_name:
            continue
        inspection = inspect_image_file(image_path)
        new_rows.append(
            {
                "image_name": image_name,
                "category": defaults["category"],
                "source": defaults["source"],
                "capture_date": defaults["capture_date"],
                "device": defaults["device"],
                "resolution": f"{inspection.metrics.width_px}x{inspection.metrics.height_px}",
                "magnification": defaults["magnification"],
                "annotation_status": "unlabeled",
                "remark": "",
            }
        )

    if apply and new_rows:
        _write_metadata_atomically(metadata_csv, [*existing_rows, *new_rows])

    return MetadataBuildResult(
        raw_image_count=len(images),
        existing_record_count=len(existing_rows),
        new_record_count=len(new_rows),
        unchanged_record_count=len(existing_rows),
        mode="APPLY" if apply else "DRY_RUN",
        new_rows=tuple(new_rows),
    )


def _find_images(raw_root: Path) -> list[Path]:
    if not raw_root.is_dir():
        raise FileNotFoundError(f"原图目录不存在：{raw_root}")
    images = sorted(
        path for path in raw_root.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if not images:
        raise ValueError(f"原图目录中没有支持的图片：{raw_root}")
    return images


def _read_metadata(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        if fields != METADATA_FIELDS:
            raise ValueError(
                "metadata表头与当前协议不一致："
                f"期望={METADATA_FIELDS}，实际={fields}"
            )
        rows: list[dict[str, str]] = []
        for row_number, raw_row in enumerate(reader, start=2):
            row = {field: (raw_row.get(field) or "").strip() for field in METADATA_FIELDS}
            if not any(row.values()):
                continue
            if not row["image_name"]:
                raise ValueError(f"metadata第 {row_number} 行缺少 image_name")
            rows.append(row)
        return rows


def _index_existing_rows(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for row in rows:
        image_name = row["image_name"]
        if image_name in indexed:
            raise ValueError(f"metadata存在重复 image_name：{image_name}")
        indexed[image_name] = row
    return indexed


def _reject_ambiguous_basenames(images: list[Path]) -> None:
    paths_by_name: dict[str, list[Path]] = {}
    for path in images:
        paths_by_name.setdefault(path.name, []).append(path)
    ambiguous = {name: paths for name, paths in paths_by_name.items() if len(paths) > 1}
    if ambiguous:
        detail = "; ".join(
            f"{name}: {', '.join(str(path) for path in paths)}"
            for name, paths in sorted(ambiguous.items())
        )
        raise ValueError(f"原图存在同名文件，metadata无法唯一对应：{detail}")


def _nonempty_or_unknown(value: str) -> str:
    stripped = value.strip()
    return stripped if stripped else "unknown"


def _write_metadata_atomically(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}-",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=METADATA_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        temporary_path.replace(path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="半自动生成metadata缺失行；默认只预览")
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw_images"))
    parser.add_argument("--metadata", type=Path, default=Path("data/metadata.csv"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只预览，不写入（默认）")
    mode.add_argument("--apply", action="store_true", help="确认后把缺失行写入metadata")
    parser.add_argument("--category", default="unknown")
    parser.add_argument("--source", default="unknown")
    parser.add_argument("--capture-date", default="unknown")
    parser.add_argument("--device", default="unknown")
    parser.add_argument("--magnification", default="unknown")
    args = parser.parse_args(argv)

    result = build_metadata(
        args.raw_root,
        args.metadata,
        apply=args.apply,
        category=args.category,
        source=args.source,
        capture_date=args.capture_date,
        device=args.device,
        magnification=args.magnification,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    if not args.apply and result.new_record_count:
        print("当前仅为预览；确认内容后加 --apply 才会写入metadata。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
