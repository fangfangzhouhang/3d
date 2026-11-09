"""Artifact and occupancy safeguards; no model load and no CUDA calls."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

import run_stage0


class SmokeExportTest(unittest.TestCase):
    def test_overlapping_masks_preserve_individual_pixels(self):
        image = np.full((9, 11, 3), 80, dtype=np.uint8)
        large = np.zeros((9, 11), bool)
        large[1:8, 2:9] = True
        small = np.zeros_like(large)
        small[3:5, 4:7] = True
        proposals = [{"segmentation": m, "area": int(m.sum()),
                      "bbox": [0, 0, 11, 9], "predicted_iou": 0.9,
                      "point_coords": [[5.0, 4.0]], "stability_score": 0.99,
                      "crop_box": [0, 0, 11, 9]} for m in (small, large)]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "export"
            result = run_stage0.save_proposals(image, proposals, out)
            self.assertEqual(result["union_pixels"], 49)
            self.assertEqual(result["mask_count"], 2)
            for i, expected in enumerate((small, large), 1):
                actual = np.asarray(Image.open(out / "masks" / f"{i:04d}.png"))
                np.testing.assert_array_equal(actual, expected.astype(np.uint8) * 255)
            ids = np.asarray(Image.open(out / "proposal_ids.png"))
            self.assertTrue((ids[small] == 1).all())
            records = json.loads((out / "proposals.json").read_text(encoding="utf-8"))
            self.assertTrue(all(r["semantic_class"] is None for r in records))

    def test_zero_proposals_still_export_valid_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "empty"
            result = run_stage0.save_proposals(np.zeros((5, 7, 3), np.uint8), [], out)
            self.assertEqual(result["mask_count"], 0)
            self.assertFalse(np.asarray(Image.open(out / "proposal_union.png")).any())

    def test_foreign_gpu_process_stops_before_model(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "run_stage0.subprocess.check_output", side_effect=["snapshot", "6789, trainer.exe, 2048\n"]
        ):
            with self.assertRaisesRegex(RuntimeError, "GPU occupied"):
                run_stage0.gpu_guard(Path(tmp), "busy")
            self.assertTrue((Path(tmp) / "nvidia_smi_busy.txt").exists())

    def test_current_inference_pid_is_only_allowed_after_start(self):
        row = f"{os.getpid()}, python.exe, 1024\n"
        with tempfile.TemporaryDirectory() as tmp:
            with patch("run_stage0.subprocess.check_output", side_effect=["snapshot", row]):
                with self.assertRaises(RuntimeError):
                    run_stage0.gpu_guard(Path(tmp), "initial")
            with patch("run_stage0.subprocess.check_output", side_effect=["snapshot", row]):
                run_stage0.gpu_guard(Path(tmp), "self", allow_self=True)


if __name__ == "__main__":
    unittest.main()
