import tempfile
import unittest
from pathlib import Path

from PIL import Image

from annotate.manifest import ManifestError, audit_rows, build_rows


def record(filename, part, batch):
    return {"filename": filename, "part_id": part, "capture_batch": batch, "device": "unknown"}


class ManifestGroupingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name, size in (("a.png", (11, 13)), ("b.png", (17, 19)), ("c.png", (23, 29)), ("d.png", (31, 37))):
            Image.new("RGB", size).save(self.root / name)

    def tearDown(self):
        self.temp.cleanup()

    def test_shared_part_and_batch_form_transitive_group(self):
        meta = {"seed": 4, "split_ratios": {"train": 0.5, "val": 0.25, "test": 0.25},
                "images": [record("a.png", "part-A", "batch-1"), record("b.png", "part-A", "batch-2"),
                           record("c.png", "part-C", "batch-2"), record("d.png", "part-D", "batch-4")]}
        rows, count = build_rows(meta, self.root)
        by_name = {r["filename"]: r for r in rows}
        self.assertEqual(count, 2)
        self.assertEqual({by_name[n]["split"] for n in ("a.png", "b.png", "c.png")}, {by_name["a.png"]["split"]})
        self.assertNotEqual(by_name["a.png"]["split"], by_name["d.png"]["split"])
        self.assertEqual((by_name["a.png"]["width"], by_name["a.png"]["height"]), (11, 13))
        self.assertEqual((by_name["a.png"]["annotation_status"], by_name["a.png"]["human_verified"],
                          by_name["a.png"]["test_ground_truth_allowed"], by_name["a.png"]["split_role"]),
                         ("draft", False, False, "placeholder_demo"))
        self.assertEqual(audit_rows(rows), 2)

    def test_shared_batch_links_different_parts(self):
        meta = {"seed": 8, "split_ratios": {"train": 0.5, "val": 0.25, "test": 0.25},
                "images": [record("a.png", "part-A", "batch-x"), record("b.png", "part-B", "batch-x"),
                           record("c.png", "part-C", "batch-y"), record("d.png", "part-D", "batch-z")]}
        rows, count = build_rows(meta, self.root)
        by_name = {r["filename"]: r for r in rows}
        self.assertEqual(count, 3)
        self.assertEqual(by_name["a.png"]["split"], by_name["b.png"]["split"])

    def test_deterministic_independent_of_input_order_and_dimensions_are_real(self):
        records = [record("a.png", "p1", "b1"), record("b.png", "p2", "b2"), record("c.png", "p3", "b3"), record("d.png", "p4", "b4")]
        config = {"seed": 19, "split_ratios": {"train": 0.5, "val": 0.25, "test": 0.25}}
        rows1, groups1 = build_rows({**config, "images": records}, self.root)
        rows2, groups2 = build_rows({**config, "images": list(reversed(records))}, self.root)
        self.assertEqual(groups1, groups2)
        self.assertEqual({r["filename"]: r["split"] for r in rows1}, {r["filename"]: r["split"] for r in rows2})
        self.assertEqual({r["filename"]: (r["width"], r["height"]) for r in rows1}["c.png"], (23, 29))

    def test_duplicate_and_unknown_metadata_id_rejected(self):
        with self.assertRaisesRegex(ManifestError, "duplicate filename"):
            build_rows({"images": [record("a.png", "p", "b"), record("a.png", "p", "b")]}, self.root)
        with self.assertRaisesRegex(ManifestError, "missing metadata"):
            build_rows({"images": [record("a.png", "p", "b")]}, self.root)
        with self.assertRaisesRegex(ManifestError, "generic unknown"):
            build_rows({"images": [record("a.png", "unknown", "batch-1")]}, self.root)

    def test_audit_rejects_fabricated_cross_split(self):
        base = {"part_id": "part-A", "capture_batch": "batch-1", "device": "unknown", "width": 11, "height": 13,
                "split_role": "placeholder_demo", "annotation_status": "draft", "human_verified": False,
                "test_ground_truth_allowed": False}
        rows = [{**base, "filename": "a.png", "split": "train"},
                {**base, "filename": "b.png", "split": "test", "part_id": "part-B"}]
        with self.assertRaisesRegex(ManifestError, "leakage"):
            audit_rows(rows)


if __name__ == "__main__":
    unittest.main(verbosity=2)
