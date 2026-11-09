"""Deterministic, leakage-resistant CSV manifest builder for the probe dataset.

Metadata is intentionally explicit: image dimensions are always read from the
image files, while part/batch/device facts are never inferred from filenames.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

FIELDS = ("filename", "part_id", "capture_batch", "device", "width", "height", "split",
          "split_role", "annotation_status", "human_verified", "test_ground_truth_allowed")
SPLITS = ("train", "val", "test")


class ManifestError(ValueError):
    """Invalid or unsafe metadata/manifest."""


class UnionFind:
    def __init__(self, items: Iterable[str]):
        values = tuple(items)
        self.parent = {item: item for item in values}
        self.rank = {item: 0 for item in values}

    def find(self, item: str) -> str:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            nxt = self.parent[item]
            self.parent[item] = root
            item = nxt
        return root

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a == b:
            return
        if self.rank[a] < self.rank[b]:
            a, b = b, a
        self.parent[b] = a
        if self.rank[a] == self.rank[b]:
            self.rank[a] += 1


def load_metadata(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read metadata {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("images"), list):
        raise ManifestError("metadata must be an object with an images list")
    return data


def _validate_records(metadata: dict[str, Any], images_root: Path) -> list[dict[str, str]]:
    records = metadata["images"]
    by_name: dict[str, dict[str, str]] = {}
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            raise ManifestError(f"images[{index}] must be an object")
        name = item.get("filename")
        if not isinstance(name, str) or not name.strip():
            raise ManifestError(f"images[{index}] needs a non-empty filename")
        if name in by_name:
            raise ManifestError(f"duplicate filename: {name}")
        # Restrict each record to a direct child; metadata cannot escape the image root.
        if Path(name).name != name or name in (".", ".."):
            raise ManifestError(f"filename must be a plain basename: {name}")
        part, batch = item.get("part_id"), item.get("capture_batch")
        device = item.get("device", "unknown")
        for key, value in (("part_id", part), ("capture_batch", batch), ("device", device)):
            if not isinstance(value, str) or not value.strip():
                raise ManifestError(f"{name}: {key} must be a non-empty string; use explicit unknown placeholder")
        for key, value in (("part_id", part), ("capture_batch", batch)):
            if value.strip().lower() == "unknown":
                raise ManifestError(f"{name}: generic unknown cannot be used as {key}; provide a verified ID or explicit conservative group ID")
        file_path = images_root / name
        if not file_path.is_file():
            raise ManifestError(f"missing image file: {file_path}")
        by_name[name] = {"filename": name, "part_id": part, "capture_batch": batch, "device": device}
    actual = {p.name for p in images_root.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}}
    unknown = sorted(actual - set(by_name))
    if unknown:
        raise ManifestError(f"image files missing metadata: {', '.join(unknown)}")
    return [by_name[name] for name in sorted(by_name)]


def _read_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise ManifestError("Pillow is required to read real image dimensions") from exc
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            return image.size
    except Exception as exc:
        raise ManifestError(f"invalid image or unreadable dimensions: {path}: {exc}") from exc


def _groups(records: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    uf = UnionFind(r["filename"] for r in records)
    for key in ("part_id", "capture_batch"):
        seen: dict[str, str] = {}
        for record in records:
            value = record[key]
            # Explicit unknown placeholders should be shared only when deliberately
            # named as a common conservative group (e.g. handoff_batch_unknown_001).
            if value.lower() == "unknown":
                continue
            if value in seen:
                uf.union(record["filename"], seen[value])
            else:
                seen[value] = record["filename"]
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    for record in records:
        result[uf.find(record["filename"])].append(record)
    return sorted((sorted(group, key=lambda x: x["filename"]) for group in result.values()), key=lambda g: g[0]["filename"])


def _assign_splits(groups: list[list[dict[str, str]]], seed: int, ratios: dict[str, float]) -> dict[str, str]:
    if set(ratios) != set(SPLITS) or any(not isinstance(v, (int, float)) or v < 0 for v in ratios.values()) or sum(ratios.values()) <= 0:
        raise ManifestError("split_ratios must contain non-negative train/val/test values with positive sum")
    total = sum(ratios.values())
    cuts = (ratios["train"] / total, (ratios["train"] + ratios["val"]) / total)
    order = list(range(len(groups)))
    random.Random(seed).shuffle(order)
    assignments = {}
    # Group-level assignment keeps all linked samples together. Ratios are targets;
    # with few groups some splits necessarily remain empty.
    for position, gi in enumerate(order):
        fraction = (position + 0.5) / max(len(order), 1)
        split = "train" if fraction < cuts[0] else "val" if fraction < cuts[1] else "test"
        for record in groups[gi]:
            assignments[record["filename"]] = split
    return assignments


def build_rows(metadata: dict[str, Any], images_root: Path, seed: int | None = None) -> tuple[list[dict[str, Any]], int]:
    records = _validate_records(metadata, images_root)
    groups = _groups(records)
    actual_seed = int(metadata.get("seed", 0) if seed is None else seed)
    ratios = metadata.get("split_ratios", {"train": 0.8, "val": 0.1, "test": 0.1})
    if not isinstance(ratios, dict):
        raise ManifestError("split_ratios must be an object")
    assignments = _assign_splits(groups, actual_seed, ratios)
    rows = []
    for r in records:
        width, height = _read_size(images_root / r["filename"])
        source = next(item for item in metadata["images"] if item["filename"] == r["filename"])
        rows.append({**r, "width": width, "height": height, "split": assignments[r["filename"]],
                     "split_role": source.get("split_role", "placeholder_demo"),
                     "annotation_status": source.get("annotation_status", "draft"),
                     "human_verified": source.get("human_verified", False),
                     "test_ground_truth_allowed": source.get("test_ground_truth_allowed", False)})
    audit_rows(rows)
    return rows, len(groups)


def audit_rows(rows: list[dict[str, Any]]) -> int:
    """Reject duplicate/invalid rows and any part or batch split leakage."""
    names = [row.get("filename") for row in rows]
    if len(names) != len(set(names)):
        raise ManifestError("manifest contains duplicate filenames")
    for row in rows:
        if row.get("split") not in SPLITS:
            raise ManifestError(f"{row.get('filename')}: invalid split {row.get('split')!r}")
        for key in ("part_id", "capture_batch"):
            value = row.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ManifestError(f"{row.get('filename')}: missing {key}")
            if value.strip().lower() == "unknown":
                raise ManifestError(f"{row.get('filename')}: generic unknown cannot be audited as {key}")
        if (row.get("split_role") != "placeholder_demo" or row.get("annotation_status") != "draft"
                or row.get("human_verified") is not False or row.get("test_ground_truth_allowed") is not False):
            raise ManifestError(f"{row.get('filename')}: placeholder manifest rows must remain draft, unverified, and disallowed as test ground truth")
    for key in ("part_id", "capture_batch"):
        split_by_id: dict[str, str] = {}
        for row in rows:
            value = row[key]
            if value.lower() == "unknown":
                continue
            old = split_by_id.setdefault(value, row["split"])
            if old != row["split"]:
                raise ManifestError(f"leakage: {key}={value!r} appears in {old} and {row['split']}")
    return len(_groups(rows))


def write_manifest(rows: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--images-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int)
    args = parser.parse_args(argv)
    try:
        rows, group_count = build_rows(load_metadata(args.metadata), args.images_root, args.seed)
        write_manifest(rows, args.output)
        print(f"rows={len(rows)} groups={group_count} leakage=none output={args.output}")
    except ManifestError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
