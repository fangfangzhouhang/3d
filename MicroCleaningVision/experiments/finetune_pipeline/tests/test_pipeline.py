"""CPU synthetic integration, checkpoint prediction, and evidence/safety boundaries."""
import contextlib
import csv
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import torch

from finetune_pipeline.data import ClassSchema
from finetune_pipeline.predict import main as predict_main
from finetune_pipeline.runtime import ROOT, DISCLAIMER, gpu_guard, new_output
from finetune_pipeline.train import collate_samples, evidence_policy, run, validate_config


class PipelineTests(unittest.TestCase):
    def test_gpu_external_process_including_gui_is_blocked(self):
        temporary_root = ROOT / ".tmp"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as folder:
            result = subprocess.CompletedProcess([], 0, "999999, ChatGPT.exe, [N/A]\n", "")
            with patch("finetune_pipeline.runtime.subprocess.run", return_value=result):
                with self.assertRaisesRegex(RuntimeError, "GPU_BLOCKED"):
                    gpu_guard(Path(folder))
            recorded = json.loads((Path(folder) / "gpu_before_training.json").read_text(encoding="utf-8"))
            self.assertEqual(recorded["external_processes"][0]["pid"], 999999)

    def test_formal_refuses_unreviewed_and_placeholder(self):
        for row in ({"human_verified": False}, {"human_verified": True, "split_role": "placeholder_demo"},
                    {"human_verified": True, "split": "test", "test_ground_truth_allowed": False}):
            with self.subTest(row=row), self.assertRaises(ValueError):
                evidence_policy([row], "formal")
        self.assertTrue(evidence_policy([{"human_verified": False}], "smoke"))

    def test_formal_template_fails_pending_values(self):
        config = json.loads((ROOT / "configs/formal.template.json").read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "pending"):
            validate_config(config)

    def test_output_cannot_escape_or_overwrite(self):
        with self.assertRaises(ValueError):
            new_output(ROOT.parent / "forbidden_test_output")
        with self.assertRaises(ValueError):
            new_output(ROOT)

    def test_variable_shape_batch_pads_only_ignore_targets(self):
        schema = ClassSchema(ROOT / "configs/classes.json")
        samples = [{"image": torch.ones(3, 32, 64), "mask": torch.zeros(32, 64, dtype=torch.long)},
                   {"image": torch.ones(3, 64, 32), "mask": torch.zeros(64, 32, dtype=torch.long)}]
        images, masks = collate_samples(samples, schema.ignore_index)
        self.assertEqual(tuple(images.shape), (2, 3, 64, 64))
        self.assertTrue(torch.all(masks[0, 32:] == schema.ignore_index))
        self.assertTrue(torch.all(masks[1, :, 32:] == schema.ignore_index))

    def test_synthetic_cpu_complete_chain_and_standalone_predict(self):
        temporary_root = ROOT / ".tmp"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as folder:
            base = Path(folder)
            dataset, images = base / "dataset", base / "images"
            dataset.mkdir()
            images.mkdir()
            schema = ClassSchema(ROOT / "configs/classes.json")
            rows = []
            for number, split in enumerate(("train", "val", "test")):
                name = f"synthetic_{number}"
                array = np.zeros((32, 48, 3), np.uint8)
                array[8:24, 8:24] = (240, 100, 20)
                Image.fromarray(array).save(images / f"{name}.png")
                annotations = dataset / name
                annotations.mkdir()
                mask = np.zeros((32, 48), np.uint8)
                mask[8:24, 8:24] = 255
                Image.fromarray(mask).save(annotations / "mask.png")
                (annotations / "instances.json").write_text(json.dumps([{"id": 1, "label": schema.classes[0]["name"], "mask": "mask.png"}]), encoding="utf-8")
                rows.append({"filename": f"{name}.png", "width": 48, "height": 32, "part_id": name,
                             "capture_batch": name, "split": split, "split_role": "synthetic_test_fixture",
                             "annotation_status": "draft", "human_verified": "False", "test_ground_truth_allowed": "False"})
            manifest = dataset / "manifest.csv"
            with manifest.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0])
                writer.writeheader()
                writer.writerows(rows)
            config = json.loads((ROOT / "configs/smoke.json").read_text(encoding="utf-8"))
            config.update({"manifest": str(manifest), "images_root": str(images), "device": "cpu", "epochs": 2,
                           "augmentation": {"hflip_p": 0.5}, "augmentation_preview_count": 1})
            config["preprocess"]["max_side"] = 64
            config["model"]["params"]["base_channels"] = 4
            blocked_config = {**config, "device": "cuda"}
            with contextlib.redirect_stdout(io.StringIO()), patch("finetune_pipeline.train.gpu_guard", side_effect=RuntimeError("GPU_BLOCKED: test external PID")):
                with self.assertRaisesRegex(RuntimeError, "GPU_BLOCKED"):
                    run(blocked_config, base / "blocked")
            blocked_metrics = json.loads((base / "blocked/metrics.json").read_text(encoding="utf-8"))
            self.assertIsNone(blocked_metrics["metrics"])
            self.assertEqual(blocked_metrics["training"], "NOT_PERFORMED")
            self.assertFalse((base / "blocked/checkpoints").exists())
            with contextlib.redirect_stdout(io.StringIO()):
                output = run(config, base / "run")
                result = predict_main(["--checkpoint", str(output / "checkpoints/best.pt"), "--input", str(images / rows[0]["filename"]),
                                       "--output", str(base / "standalone"), "--device", "cpu"])
            self.assertEqual(result, 0)
            status = json.loads((output / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "completed")
            self.assertEqual(status["device"], "cpu")
            self.assertTrue(status["draft_unverified"])
            self.assertEqual(status["disclaimer"], DISCLAIMER)
            metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
            self.assertFalse(metrics["independent_test_available"])
            self.assertEqual(metrics["splits"]["test"]["classes"][schema.classes[1]["name"]]["status"], "无样本")
            for filename in ("latest.pt", "best.pt"):
                self.assertGreater((output / "checkpoints" / filename).stat().st_size, 0)
            self.assertTrue((output / "augmentation_grid.png").is_file())
            with Image.open(base / "standalone/prediction/semantic.png") as predicted:
                self.assertEqual(predicted.size, (48, 32))
            areas = json.loads((base / "standalone/prediction/areas.json").read_text(encoding="utf-8"))
            self.assertEqual(sum(c["area_px"] for c in areas["classes"].values()), 48 * 32)
            for row in rows:
                self.assertTrue((output / "predictions" / Path(row["filename"]).stem / "overlay.png").is_file())

    def test_empty_manifest_reports_no_samples_without_gpu_or_crash(self):
        temporary_root = ROOT / ".tmp"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as folder:
            base = Path(folder)
            manifest = base / "manifest.csv"
            manifest.write_text("filename,part_id,capture_batch,width,height,split,split_role,annotation_status,human_verified,test_ground_truth_allowed\n", encoding="utf-8")
            config = json.loads((ROOT / "configs/smoke.json").read_text(encoding="utf-8"))
            config.update({"manifest": str(manifest), "images_root": str(base)})
            with contextlib.redirect_stdout(io.StringIO()), patch("finetune_pipeline.train.gpu_guard", side_effect=AssertionError("must not acquire GPU")):
                run(config, base / "empty")
            result = json.loads((base / "empty/status.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "no_training_samples")


if __name__ == "__main__":
    unittest.main()
