"""成员 A 训练入口：OpenCV 调参可用，深度学习后端必须拒绝。"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

HAS_PERCEPTION_DEPS = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


class TrainEntryBackendTests(unittest.TestCase):
    def test_yolo_and_torch_backends_are_rejected(self):
        from microcleaning.data_learning.train_entry import main

        for backend in ("yolo", "torch", "semantic-seg", "autodl"):
            with self.subTest(backend=backend), redirect_stderr(io.StringIO()) as err:
                code = main(["--backend", backend])
            self.assertEqual(2, code)
            self.assertTrue(err.getvalue())

    def test_unknown_backend_is_rejected(self):
        from microcleaning.data_learning.train_entry import main

        with redirect_stderr(io.StringIO()):
            self.assertEqual(2, main(["--backend", "magic"]))


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class TrainEntryOpencvTuneTests(unittest.TestCase):
    def _write_dataset(self, folder: Path) -> None:
        import cv2
        import numpy as np

        raw_dir = folder / "raw_images" / "batch"
        mask_dir = folder / "annotations" / "masks"
        raw_dir.mkdir(parents=True)
        mask_dir.mkdir(parents=True)
        image = np.full((160, 200, 3), (90, 170, 210), dtype=np.uint8)
        cv2.circle(image, (100, 80), 16, (25, 25, 25), -1)
        mask = np.zeros((160, 200), dtype=np.uint8)
        cv2.circle(mask, (100, 80), 16, 255, -1)
        self.assertTrue(cv2.imwrite(str(raw_dir / "public_001.png"), image))
        self.assertTrue(cv2.imwrite(str(mask_dir / "public_001.png"), mask))
        metadata = folder / "metadata.csv"
        with metadata.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["image_name", "annotation_status"],
            )
            writer.writeheader()
            writer.writerow({"image_name": "public_001.png", "annotation_status": "labeled"})

    def test_opencv_tune_writes_report_and_can_apply_without_holdout(self):
        from microcleaning.data_learning.train_entry import main
        from microcleaning.vision.local_contrast_baseline import (
            LocalContrastPolicy,
            load_local_contrast_policy,
            save_local_contrast_policy,
        )

        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            self._write_dataset(folder)
            start = folder / "start.json"
            save_local_contrast_policy(LocalContrastPolicy(min_residual=80.0), start)
            output_dir = folder / "tuning"
            apply_path = folder / "models" / "local_contrast_policy.json"
            code = main(
                [
                    "--workflow",
                    "search",
                    "--backend",
                    "opencv-tune",
                    "--raw-root",
                    str(folder / "raw_images"),
                    "--mask-dir",
                    str(folder / "annotations" / "masks"),
                    "--metadata",
                    str(folder / "metadata.csv"),
                    "--output-dir",
                    str(output_dir),
                    "--start-policy",
                    str(start),
                    "--max-rounds",
                    "1",
                    "--apply",
                    "--apply-path",
                    str(apply_path),
                ]
            )
            self.assertEqual(0, code)
            report = json.loads((output_dir / "tune_report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["feeds_action_request"])
            self.assertGreater(report["develop_best"]["mean_iou"], report["develop_baseline"]["mean_iou"])
            self.assertTrue(apply_path.is_file())
            loaded = load_local_contrast_policy(apply_path)
            self.assertLess(loaded.min_residual, 80.0)


if __name__ == "__main__":
    unittest.main()
