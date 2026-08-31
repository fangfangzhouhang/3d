"""审计A当前图片、质量报告和分类metadata之间是否一致。

本工具不移动图片、不猜类别、不补写设备或倍率，只报告事实：文件能否解码、是否重复、
哪些尚未登记、metadata哪些必填字段缺失，以及metadata是否引用了不存在的图片。
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from microcleaning.data_learning.inspect_images import inspect_directory


REQUIRED_METADATA_FIELDS = (
    "image_name",
    "category",
    "source",
    "capture_date",
    "device",
    "resolution",
    "annotation_status",
)


def audit_data(raw_root: Path, metadata_csv: Path) -> dict[str, object]:
    """返回当前数据状态；未知字段保持原样，不对图片内容做语义判断。"""

    quality_report = inspect_directory(raw_root, recursive=True)
    metadata_rows, metadata_schema_issues = _read_metadata(metadata_csv)
    registered_names = {_text(row.get("image_name")) for row in metadata_rows if _text(row.get("image_name"))}
    image_items = [item for item in quality_report if item.get("status") == "OK"]
    error_items = [item for item in quality_report if item.get("status") == "ERROR"]

    names_to_files: dict[str, list[str]] = defaultdict(list)
    hashes_to_files: dict[str, list[str]] = defaultdict(list)
    for item in quality_report:
        relative_path = str(item["file"])
        names_to_files[Path(relative_path).name].append(relative_path)
        if item.get("status") == "OK":
            hashes_to_files[str(item["sha256"])].append(relative_path)

    missing_field_rows: list[dict[str, object]] = []
    for row_number, row in enumerate(metadata_rows, start=2):
        missing = [field for field in REQUIRED_METADATA_FIELDS if not _text(row.get(field))]
        if missing:
            missing_field_rows.append(
                {"row": row_number, "image_name": _text(row.get("image_name")), "missing_fields": missing}
            )

    metadata_missing_images = sorted(
        name for name in registered_names
        if name not in names_to_files and not _find_in_dataset(metadata_csv.parent, name)
    )
    duplicate_groups = [files for files in hashes_to_files.values() if len(files) > 1]
    ambiguous_names = {name: files for name, files in names_to_files.items() if len(files) > 1}
    unregistered = sorted(
        relative_path
        for files in names_to_files.values()
        for relative_path in files
        if Path(relative_path).name not in registered_names
    )
    extension_counts = Counter(Path(str(item["file"])).suffix.lower() for item in quality_report)

    return {
        "raw_root": str(raw_root),
        "metadata_csv": str(metadata_csv),
        "summary": {
            "image_count": len(quality_report),
            "decodable_count": len(image_items),
            "corrupt_count": len(error_items),
            "metadata_record_count": len(metadata_rows),
            "registered_raw_image_count": len(quality_report) - len(unregistered),
            "unregistered_raw_image_count": len(unregistered),
            "duplicate_group_count": len(duplicate_groups),
            "metadata_rows_with_missing_required_fields": len(missing_field_rows),
            "extension_counts": dict(sorted(extension_counts.items())),
        },
        "unregistered_images": unregistered,
        "duplicate_groups": duplicate_groups,
        "corrupt_images": error_items,
        "metadata_schema_issues": metadata_schema_issues,
        "metadata_missing_required_fields": missing_field_rows,
        "metadata_references_missing_images": metadata_missing_images,
        "ambiguous_basenames": ambiguous_names,
    }


def _read_metadata(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.is_file():
        return [], [f"metadata不存在：{path}"]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        missing_columns = [field for field in REQUIRED_METADATA_FIELDS if field not in fields]
        issues = [f"metadata缺少列：{field}" for field in missing_columns]
        rows = [
            dict(row)
            for row in reader
            if any(_text(value) for value in row.values())
        ]
        return rows, issues


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _find_in_dataset(data_root: Path, image_name: str) -> bool:
    dataset = data_root / "dataset"
    return dataset.is_dir() and any(path.is_file() for path in dataset.rglob(image_name))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="审计A的原始图片与metadata登记状态")
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw_images"))
    parser.add_argument("--metadata", type=Path, default=Path("data/metadata.csv"))
    parser.add_argument("--output", type=Path, default=Path("output/data_learning/data_audit.json"))
    args = parser.parse_args(argv)
    report = audit_data(args.raw_root, args.metadata)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"数据审计已保存：{args.output}")
    summary = report["summary"]
    print(
        f"图片{summary['image_count']}张；可解码{summary['decodable_count']}张；"
        f"已登记{summary['registered_raw_image_count']}张；重复组{summary['duplicate_group_count']}组"
    )
    return 1 if summary["corrupt_count"] or report["metadata_schema_issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
