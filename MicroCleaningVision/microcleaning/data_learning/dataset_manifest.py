"""MicroCleaning Dataset v0.1 的初始化、导入与清单检查工具。

数据集不是一个装满图片的文件夹。每张图片还必须有稳定编号、文件哈希、采集批次
和来源，团队才能知道训练或实验到底用了什么。本模块不做训练，也不替人填写未知的
倍率、光照等事实。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from microcleaning.data_learning.image_quality import inspect_image_file


DATASET_VERSION = "microcleaning-dataset-v0.1"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
METADATA_FIELDS = (
    "image_id",
    "relative_path",
    "sha256",
    "source",
    "sample_id",
    "capture_session",
    "contamination_type",
    "illumination",
    "magnification",
    "annotation_ref",
    "label_status",
)


@dataclass(frozen=True)
class DatasetRecord:
    image_id: str
    relative_path: str
    sha256: str
    source: str
    sample_id: str
    capture_session: str
    contamination_type: str
    illumination: str
    magnification: str
    annotation_ref: str = ""
    label_status: str = "unlabelled"


def initialize_dataset(root: str | Path) -> Path:
    """建立可复现的数据集骨架；重复运行不会覆盖已有清单。"""

    dataset_root = Path(root)
    (dataset_root / "raw").mkdir(parents=True, exist_ok=True)
    (dataset_root / "annotations").mkdir(parents=True, exist_ok=True)
    metadata = dataset_root / "metadata.csv"
    if not metadata.exists():
        _write_records(metadata, [])
    readme = dataset_root / "README.md"
    if not readme.exists():
        readme.write_text(
            "# MicroCleaning Dataset v0.1\n\n"
            "`raw/` 保存原始图片，`annotations/` 保存人工标注，"
            "`metadata.csv` 保存每张图的来源和采集条件。\n\n"
            "原始图片默认不进入 Git；团队通过清单、共享盘或数据版本工具同步。\n",
            encoding="utf-8",
        )
    return dataset_root


def import_image(
    image_path: str | Path,
    dataset_root: str | Path,
    *,
    source: str,
    sample_id: str,
    capture_session: str,
    contamination_type: str = "unknown",
    illumination: str = "unknown",
    magnification: str = "unknown",
) -> DatasetRecord:
    """复制一张图片并原子更新清单；相同内容不会重复导入。"""

    root = initialize_dataset(dataset_root)
    source_path = Path(image_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"待导入图片不存在：{source_path}")
    extension = source_path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("数据集v0.1只接受 jpg/jpeg/png 图片")
    for name, value in {
        "source": source,
        "sample_id": sample_id,
        "capture_session": capture_session,
    }.items():
        if not value.strip():
            raise ValueError(f"{name} 不能为空；未知信息必须明确写 unknown")

    inspection = inspect_image_file(source_path)
    digest = inspection.sha256
    records = read_records(root)
    duplicate = next((item for item in records if item.sha256 == digest), None)
    if duplicate is not None:
        return duplicate

    image_id = f"img_{digest[:12]}"
    destination = root / "raw" / f"{image_id}{extension}"
    if destination.exists():
        raise FileExistsError(f"目标文件已存在但清单中没有对应记录：{destination}")
    shutil.copy2(source_path, destination)
    record = DatasetRecord(
        image_id=image_id,
        relative_path=destination.relative_to(root).as_posix(),
        sha256=digest,
        source=source.strip(),
        sample_id=sample_id.strip(),
        capture_session=capture_session.strip(),
        contamination_type=contamination_type.strip() or "unknown",
        illumination=illumination.strip() or "unknown",
        magnification=magnification.strip() or "unknown",
    )
    try:
        _write_records(root / "metadata.csv", [*records, record])
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return record


def read_records(dataset_root: str | Path) -> list[DatasetRecord]:
    metadata = Path(dataset_root) / "metadata.csv"
    if not metadata.exists():
        return []
    with metadata.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != METADATA_FIELDS:
            raise ValueError("metadata.csv 字段与 MicroCleaning Dataset v0.1 不一致")
        return [DatasetRecord(**{field: row.get(field, "") for field in METADATA_FIELDS}) for row in reader]


def validate_dataset(dataset_root: str | Path) -> list[str]:
    """返回可读问题列表；空列表表示结构和哈希检查通过。"""

    root = Path(dataset_root)
    issues: list[str] = []
    try:
        records = read_records(root)
    except (OSError, ValueError) as exc:
        return [str(exc)]
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for record in records:
        if record.image_id in seen_ids:
            issues.append(f"重复 image_id：{record.image_id}")
        seen_ids.add(record.image_id)
        if record.sha256 in seen_hashes:
            issues.append(f"重复 sha256：{record.sha256}")
        seen_hashes.add(record.sha256)
        image_path = root / record.relative_path
        if not image_path.is_file():
            issues.append(f"图片缺失：{record.relative_path}")
            continue
        actual = hashlib.sha256(image_path.read_bytes()).hexdigest()
        if actual != record.sha256:
            issues.append(f"图片哈希不一致：{record.relative_path}")
        if record.annotation_ref and not (root / record.annotation_ref).is_file():
            issues.append(f"标注缺失：{record.annotation_ref}")
    return issues


def _write_records(metadata_path: Path, records: list[DatasetRecord]) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8-sig",
            newline="",
            dir=metadata_path.parent,
            delete=False,
        ) as handle:
            temporary_name = handle.name
            writer = csv.DictWriter(handle, fieldnames=METADATA_FIELDS)
            writer.writeheader()
            for record in records:
                writer.writerow(asdict(record))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, metadata_path)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="建立并检查 MicroCleaning Dataset v0.1")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", help="建立 raw/annotations/metadata.csv")
    init_parser.add_argument("root")

    import_parser = subparsers.add_parser("import", help="导入一张或多张图片")
    import_parser.add_argument("root")
    import_parser.add_argument("images", nargs="+")
    import_parser.add_argument("--source", required=True)
    import_parser.add_argument("--sample-id", required=True)
    import_parser.add_argument("--session", required=True)
    import_parser.add_argument("--contamination-type", default="unknown")
    import_parser.add_argument("--illumination", default="unknown")
    import_parser.add_argument("--magnification", default="unknown")

    check_parser = subparsers.add_parser("check", help="检查文件、编号和哈希")
    check_parser.add_argument("root")
    args = parser.parse_args(argv)

    if args.command == "init":
        print(initialize_dataset(args.root))
        return 0
    if args.command == "import":
        for image in args.images:
            record = import_image(
                image,
                args.root,
                source=args.source,
                sample_id=args.sample_id,
                capture_session=args.session,
                contamination_type=args.contamination_type,
                illumination=args.illumination,
                magnification=args.magnification,
            )
            print(json.dumps(asdict(record), ensure_ascii=False))
        return 0
    issues = validate_dataset(args.root)
    if issues:
        print(json.dumps({"status": "FAIL", "issues": issues}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "PASS", "version": DATASET_VERSION}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
