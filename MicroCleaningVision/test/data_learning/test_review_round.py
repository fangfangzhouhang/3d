"""一轮对照流程：终端提示、只改一个字段、改完即停。"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

HAS_PERCEPTION_DEPS = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class ReviewRoundTests(unittest.TestCase):
    def _write_pair(self, folder: Path, stem: str = "public_001") -> None:
        import cv2
        import numpy as np

        raw_dir = folder / "raw_images" / "batch"
        mask_dir = folder / "annotations" / "masks"
        raw_dir.mkdir(parents=True, exist_ok=True)
        mask_dir.mkdir(parents=True, exist_ok=True)
        image = np.full((160, 200, 3), (90, 170, 210), dtype=np.uint8)
        cv2.circle(image, (100, 80), 16, (25, 25, 25), -1)
        mask = np.zeros((160, 200), dtype=np.uint8)
        cv2.circle(mask, (100, 80), 16, 255, -1)
        self.assertTrue(cv2.imwrite(str(raw_dir / f"{stem}.png"), image))
        self.assertTrue(cv2.imwrite(str(mask_dir / f"{stem}.png"), mask))

    def test_review_round_prints_steps_changes_one_field_and_stops(self):
        from microcleaning.data_learning.train_entry import load_labeled_examples
        from microcleaning.data_learning.review_round import run_review_round
        from microcleaning.vision.local_contrast_baseline import LocalContrastPolicy

        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            self._write_pair(folder)
            examples = load_labeled_examples(
                raw_root=folder / "raw_images",
                mask_dir=folder / "annotations" / "masks",
                metadata_path=folder / "missing.csv",
            )
            stream = io.StringIO()
            report = run_review_round(
                examples,
                output_dir=folder / "review",
                start_policy=LocalContrastPolicy(min_residual=80.0),
                apply=False,
                stream=stream,
                allow_single_image=True,
            )
            text = stream.getvalue()
            self.assertIn("第 1 步 / 7", text)
            self.assertIn("正在用 B 的邻域差异算法跑图", text)
            self.assertIn("对比中", text)
            self.assertIn("简要分析", text)
            self.assertIn("正在修改对应的算法", text)
            self.assertIn("修改完成", text)
            self.assertIn("min_residual", text)
            self.assertIn("带着修改后的算法再跑一次", text)
            self.assertIn("本轮结束，请你检查", text)
            self.assertIn("没有写入全局生效文件", text)
            self.assertEqual(1, len(report["changes"]))
            self.assertEqual("min_residual", report["changes"][0]["field"])
            self.assertLess(report["changes"][0]["to"], 80.0)
            self.assertGreater(report["develop_after"]["mean_iou"], report["develop_before"]["mean_iou"])
            self.assertFalse(report["applied"])
            self.assertTrue((folder / "review" / "before" / "public_001" / "contamination_overlay.png").is_file())
            self.assertTrue((folder / "review" / "after" / "public_001" / "contamination_overlay.png").is_file())

    def test_default_cli_is_review_and_does_not_apply(self):
        from microcleaning.data_learning.train_entry import main

        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            self._write_pair(folder)
            metadata = folder / "metadata.csv"
            with metadata.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["image_name", "annotation_status"])
                writer.writeheader()
                writer.writerow({"image_name": "public_001.png", "annotation_status": "labeled"})
            apply_path = folder / "models" / "local_contrast_policy.json"
            stream = io.StringIO()
            import contextlib

            with contextlib.redirect_stdout(stream):
                code = main(
                    [
                        "--raw-root",
                        str(folder / "raw_images"),
                        "--mask-dir",
                        str(folder / "annotations" / "masks"),
                        "--metadata",
                        str(metadata),
                        "--output-dir",
                        str(folder / "out"),
                        "--apply-path",
                        str(apply_path),
                    ]
                )
            self.assertEqual(0, code)
            self.assertFalse(apply_path.is_file())
            self.assertIn("本轮结束，请你检查", stream.getvalue())
            self.assertIn("只对照", stream.getvalue())
            summary = json.loads((folder / "out" / "review_round.json").read_text(encoding="utf-8"))
            self.assertFalse(summary["applied"])
            self.assertTrue(summary["inspect_only"])

    def test_holdout_stem_pairs_but_does_not_tune(self):
        from microcleaning.data_learning.train_entry import load_labeled_examples
        from microcleaning.data_learning.review_round import run_review_round

        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            self._write_pair(folder, stem="public_002")
            examples = load_labeled_examples(
                raw_root=folder / "raw_images",
                mask_dir=folder / "annotations" / "masks",
                metadata_path=folder / "missing.csv",
            )
            stream = io.StringIO()
            report = run_review_round(
                examples,
                output_dir=folder / "review",
                apply=False,
                stream=stream,
            )
            text = stream.getvalue()
            self.assertIn("配对检查", text)
            self.assertIn("public_002", text)
            self.assertIn("冻结留出", text)
            self.assertIn("跳过改参", text)
            self.assertTrue(report["inspect_only"])
            self.assertEqual([], report["changes"])
            self.assertFalse(report["applied"])


if __name__ == "__main__":
    unittest.main()
