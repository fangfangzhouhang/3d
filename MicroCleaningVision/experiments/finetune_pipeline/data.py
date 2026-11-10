"""Read existing manifest/instance masks without rewriting, copying or resplitting."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image
from torch.utils.data import Dataset

from .augment import augment_pair
from .preprocess import prepare


class ClassSchema:
    def __init__(self, source):
        data = json.loads(Path(source).read_text(encoding="utf-8-sig")) if isinstance(source, (str, Path)) else source
        self.config = json.loads(json.dumps(data))
        self.classes = self.config["classes"]
        self.ignore = self.config["ignore"]
        self.all_classes = [*self.classes, self.ignore]
        if not self.classes:
            raise ValueError("at least one learnable class is required")
        if [item["index"] for item in self.classes] != list(range(len(self.classes))):
            raise ValueError("learnable class indices must be contiguous from zero")
        for item in self.all_classes:
            if not isinstance(item["name"], str) or not item["name"].strip():
                raise ValueError("class name must be nonempty")
            if type(item["index"]) is not int or not 0 <= item["index"] <= 255:
                raise ValueError("PNG class indices must be integers in 0..255")
            if type(item["priority"]) is not int:
                raise ValueError("class priority must be an integer")
            if len(item["color"]) != 3 or any(type(x) is not int or not 0 <= x <= 255 for x in item["color"]):
                raise ValueError("class color must be RGB byte values")
        for field in ("name", "index", "priority"):
            if len({item[field] for item in self.all_classes}) != len(self.all_classes):
                raise ValueError(f"class {field} values must be unique")
        if self.ignore["priority"] <= max(item["priority"] for item in self.classes):
            raise ValueError("ignore priority must exceed every learnable class")
        self.ignore_index = self.ignore["index"]
        self.num_classes = len(self.classes)
        self.by_name = {item["name"]: item for item in self.all_classes}


def _boolean(value, field):
    if str(value).lower() in ("true", "1"):
        return True
    if str(value).lower() in ("false", "0"):
        return False
    raise ValueError(f"{field}: expected explicit true/false")


def audit_splits(records):
    names, stems, groups = set(), set(), {}
    for row in records:
        filename = row["filename"]
        if Path(filename).name != filename or filename in (".", ".."):
            raise ValueError("manifest filename must be a basename")
        if filename in names or Path(filename).stem in stems:
            raise ValueError("duplicate image filename or stem")
        names.add(filename)
        stems.add(Path(filename).stem)
        if row["split"] not in ("train", "val", "test"):
            raise ValueError(f"invalid split: {row['split']}")
        for key in ("part_id", "capture_batch", "group_id"):
            value = row.get(key)
            if key == "group_id" and value is None:
                continue
            if not isinstance(value, str) or not value.strip() or value.lower() == "unknown":
                raise ValueError(f"missing explicit conservative group: {key}")
            group = (key, value)
            old = groups.setdefault(group, row["split"])
            if old != row["split"]:
                raise ValueError(f"group leakage: {key}={value!r} crosses {old}/{row['split']}")


def compose_semantic(shape, instances, schema):
    """Instances are (label, binary ndarray); uncovered remains unknown/ignore."""
    semantic = np.full(shape, schema.ignore_index, dtype=np.uint8)
    validated = []
    for label, mask in instances:
        if label not in schema.by_name:
            raise ValueError(f"unknown instance class: {label!r}")
        mask = np.asarray(mask)
        if mask.shape != tuple(shape) or mask.ndim != 2:
            raise ValueError("instance mask dimensions differ from image")
        validated.append((schema.by_name[label], mask != 0))
    for item, selected in sorted(validated, key=lambda pair: pair[0]["priority"]):
        semantic[selected] = item["index"]
    return semantic


class ManifestDataset(Dataset):
    def __init__(self, manifest_path, images_root, schema, split=None,
                 preprocess_config=None, augment_config=None, seed=0):
        self.manifest_path, self.images_root = Path(manifest_path).resolve(), Path(images_root).resolve()
        self.schema = schema if isinstance(schema, ClassSchema) else ClassSchema(schema)
        self.preprocess_config, self.augment_config = preprocess_config, augment_config
        self.seed, self.epoch = int(seed), 0
        with self.manifest_path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            required = {"filename", "part_id", "capture_batch", "width", "height", "split",
                        "annotation_status", "human_verified", "test_ground_truth_allowed"}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError(f"manifest missing columns: {sorted(required - set(reader.fieldnames or []))}")
            self.all_records = list(reader)
        audit_splits(self.all_records)
        for row in self.all_records:
            row["human_verified"] = _boolean(row["human_verified"], "human_verified")
            row["test_ground_truth_allowed"] = _boolean(row["test_ground_truth_allowed"], "test_ground_truth_allowed")
            row["width"], row["height"] = int(row["width"]), int(row["height"])
            if row["width"] <= 0 or row["height"] <= 0:
                raise ValueError("manifest dimensions must be positive")
        if split is not None and split not in ("train", "val", "test"):
            raise ValueError("unknown dataset split")
        self.records = [row for row in self.all_records if split is None or row["split"] == split]

    def __len__(self):
        return len(self.records)

    def set_epoch(self, epoch):
        self.epoch = int(epoch)

    def _raw_record(self, row):
        image_path = self.images_root / row["filename"]
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        if image.size != (row["width"], row["height"]):
            raise ValueError(f"manifest/image dimensions disagree: {row['filename']}")
        annotation_dir = self.manifest_path.parent / Path(row["filename"]).stem
        instances_path = annotation_dir / "instances.json"
        entries = json.loads(instances_path.read_text(encoding="utf-8-sig"))
        if not isinstance(entries, list):
            raise ValueError("instances.json must be a list")
        ids, instances = set(), []
        instance_counts = {item["name"]: 0 for item in self.schema.all_classes}
        for entry in entries:
            if entry["id"] in ids:
                raise ValueError("duplicate instance id")
            ids.add(entry["id"])
            label = entry["label"]
            if label not in instance_counts:
                raise ValueError(f"unknown instance class: {label!r}")
            mask_path = (annotation_dir / entry["mask"]).resolve()
            if not mask_path.is_relative_to(annotation_dir.resolve()):
                raise ValueError("instance mask path escapes its annotation directory")
            expected_hash = entry.get("mask_sha256")
            if expected_hash and hashlib.sha256(mask_path.read_bytes()).hexdigest() != expected_hash:
                raise ValueError(f"instance mask SHA256 mismatch: {mask_path}")
            with Image.open(mask_path) as source:
                if source.mode not in ("1", "L", "I", "I;16", "P"):
                    raise ValueError("instance PNG must be a single-channel mask")
                mask = np.array(source)
            instances.append((label, mask))
            instance_counts[label] += 1
        semantic = compose_semantic((image.height, image.width), instances, self.schema)
        metadata = dict(row)
        metadata.update({"image_path": str(image_path), "instances_path": str(instances_path),
                         "instance_counts": instance_counts,
                         "draft_unverified": row["annotation_status"] == "draft" or not row["human_verified"]})
        return image, semantic, metadata

    def raw_item(self, index):
        return self._raw_record(self.records[index])

    def __getitem__(self, index):
        image, mask, metadata = self.raw_item(index)
        if self.augment_config:
            # Hashing avoids Python's randomized hash and is stable across workers.
            identity = f"{self.seed}:{self.epoch}:{metadata['filename']}".encode("utf-8")
            seed = int.from_bytes(hashlib.sha256(identity).digest()[:8], "little")
            image, mask = augment_pair(image, mask, self.augment_config, seed, self.schema.ignore_index)
        if self.preprocess_config is None:
            raise ValueError("tensor output requires explicit preprocessing configuration")
        tensor, target, transform = prepare(image, mask, self.preprocess_config, self.schema.ignore_index)
        metadata["preprocess"] = transform
        return {"image": tensor, "mask": target, "ignore_mask": target == self.schema.ignore_index,
                "metadata": metadata}

    def audit(self):
        splits = {name: {"image_count": 0, "status": "无样本"} for name in ("train", "val", "test")}
        counts = {item["name"]: 0 for item in self.schema.all_classes}
        images = []
        for row in self.all_records:
            _, semantic, metadata = self._raw_record(row)
            splits[row["split"]]["image_count"] += 1
            splits[row["split"]]["status"] = "有样本"
            for name, value in metadata["instance_counts"].items():
                counts[name] += value
            images.append({"filename": row["filename"], "split": row["split"],
                           "draft_unverified": metadata["draft_unverified"],
                           "instance_counts": metadata["instance_counts"],
                           "class_pixels": {item["name"]: int(np.count_nonzero(semantic == item["index"]))
                                            for item in self.schema.all_classes}})
        return {"splits": splits, "images": images, "instance_counts": counts,
                "class_status": {name: "有样本" if value else "无样本" for name, value in counts.items()},
                "draft_unverified": any(image["draft_unverified"] for image in images),
                "manifest": str(self.manifest_path), "split_policy": "manifest_unchanged; groups_do_not_cross_splits"}
