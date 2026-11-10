from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from experiments.finetune_pipeline.data import ClassSchema, ManifestDataset, audit_splits, compose_semantic


def schema():
    # Synthetic names deliberately differ from the project taxonomy.
    return ClassSchema({"classes": [
        {"name": "a", "index": 0, "priority": 1, "color": [255, 0, 0]},
        {"name": "b", "index": 1, "priority": 2, "color": [0, 255, 0]},
        {"name": "c", "index": 2, "priority": 0, "color": [0, 0, 255]}],
        "ignore": {"name": "void", "index": 255, "priority": 3, "color": [80, 80, 80]}})


PREPROCESS = {"max_side": None, "pad_multiple": 32, "mean": [0, 0, 0],
              "std": [1, 1, 1], "image_fill": [0, 0, 0]}


class CompositionTests(unittest.TestCase):
    def test_overlap_priority_is_independent_of_instance_order(self):
        masks = [("c", np.array([[1, 1, 1, 1, 0]], np.uint8)),
                 ("a", np.array([[0, 1, 1, 1, 0]], np.uint8)),
                 ("b", np.array([[0, 0, 1, 1, 0]], np.uint8)),
                 ("void", np.array([[0, 0, 0, 1, 0]], np.uint8))]
        expected = np.array([[2, 0, 1, 255, 255]], np.uint8)
        np.testing.assert_array_equal(compose_semantic((1, 5), masks, schema()), expected)
        np.testing.assert_array_equal(compose_semantic((1, 5), masks[::-1], schema()), expected)

    def test_uncovered_is_ignore_even_if_lowest_priority_class_exists(self):
        result = compose_semantic((2, 2), [("c", np.array([[1, 0], [0, 0]]))], schema())
        self.assertEqual(result[0, 0], 2)
        self.assertTrue(np.all(result[[0, 1, 1], [1, 0, 1]] == schema().ignore_index))
        self.assertTrue(np.all(compose_semantic((2, 2), [], schema()) == schema().ignore_index))

    def test_unknown_label_and_bad_shape_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown instance class"):
            compose_semantic((2, 2), [("not_configured", np.ones((2, 2)))], schema())
        with self.assertRaisesRegex(ValueError, "dimensions"):
            compose_semantic((2, 2), [("a", np.ones((1, 2)))], schema())

    def test_conflicting_schema_is_rejected(self):
        config = schema().config
        config["ignore"]["priority"] = -1
        with self.assertRaisesRegex(ValueError, "ignore priority"):
            ClassSchema(config)


class SplitTests(unittest.TestCase):
    def rows(self):
        return [{"filename": "one.png", "part_id": "p1", "capture_batch": "b1", "split": "train"},
                {"filename": "two.png", "part_id": "p2", "capture_batch": "b2", "split": "val"}]

    def test_either_part_batch_or_explicit_group_cannot_cross_split(self):
        for key in ("part_id", "capture_batch", "group_id"):
            with self.subTest(key=key):
                rows = self.rows()
                rows[0][key] = rows[1][key] = "same"
                with self.assertRaisesRegex(ValueError, "group leakage"):
                    audit_splits(rows)

    def test_distinct_groups_and_same_split_linked_groups_are_accepted(self):
        rows = self.rows()
        audit_splits(rows)
        rows[1]["split"] = "train"
        rows[1]["part_id"] = rows[0]["part_id"]
        audit_splits(rows)

    def test_missing_group_is_rejected(self):
        rows = self.rows()
        rows[0]["part_id"] = "unknown"
        with self.assertRaisesRegex(ValueError, "conservative group"):
            audit_splits(rows)


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="data_test_", dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.images = self.root / "images"
        self.images.mkdir()
        self.annotation = self.root / "one"
        self.annotation.mkdir()
        Image.new("RGB", (8, 6), (100, 50, 30)).save(self.images / "one.png")
        mask = np.zeros((6, 8), np.uint8)
        mask[1:4, 2:5] = 255
        self.mask_path = self.annotation / "mask.png"
        Image.fromarray(mask).save(self.mask_path)
        self.entries = [{"id": 1, "label": "a", "mask": "mask.png",
                         "mask_sha256": hashlib.sha256(self.mask_path.read_bytes()).hexdigest()}]
        self.write_entries()
        self.manifest = self.root / "manifest.csv"
        row = {"filename": "one.png", "part_id": "p1", "capture_batch": "b1", "width": 8,
               "height": 6, "split": "train", "split_role": "placeholder_demo", "annotation_status": "draft",
               "human_verified": False, "test_ground_truth_allowed": False}
        with self.manifest.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)

    def write_entries(self):
        (self.annotation / "instances.json").write_text(json.dumps(self.entries), encoding="utf-8")

    def dataset(self, **kwargs):
        return ManifestDataset(self.manifest, self.images, schema(), preprocess_config=PREPROCESS, **kwargs)

    def test_missing_class_empty_split_and_audit(self):
        dataset = self.dataset()
        sample = dataset[0]
        self.assertEqual(tuple(sample["image"].shape), (3, 32, 32))
        self.assertEqual(int((sample["mask"] == 0).sum()), 9)
        self.assertEqual(int(sample["ignore_mask"].sum()), 32 * 32 - 9)
        self.assertTrue(sample["metadata"]["draft_unverified"])
        self.assertEqual(len(self.dataset(split="val")), 0)
        report = dataset.audit()
        self.assertEqual(report["instance_counts"]["b"], 0)
        self.assertEqual(report["class_status"]["c"], "无样本")
        self.assertEqual(report["splits"]["val"], {"image_count": 0, "status": "无样本"})
        self.assertEqual(report["images"][0]["class_pixels"]["void"], 39)

    def test_mask_hash_mismatch_is_rejected(self):
        self.entries[0]["mask_sha256"] = "0" * 64
        self.write_entries()
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            self.dataset().raw_item(0)

    def test_mask_path_cannot_escape_annotation_directory(self):
        self.entries[0]["mask"] = "../other.png"
        self.write_entries()
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.dataset().raw_item(0)

    def test_same_epoch_is_reproducible_and_does_not_mutate_inputs(self):
        before = self.mask_path.read_bytes()
        dataset = self.dataset(augment_config={"noise_std": [0.1, 0.1]}, seed=47)
        dataset.set_epoch(2)
        first, second = dataset[0], dataset[0]
        np.testing.assert_array_equal(first["image"].numpy(), second["image"].numpy())
        dataset.set_epoch(3)
        self.assertFalse(np.array_equal(first["image"].numpy(), dataset[0]["image"].numpy()))
        self.assertEqual(before, self.mask_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
