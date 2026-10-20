"""路径/覆盖模仿：无 GPU 也可跑。缺少 torch 时跳过网络部分，几何部分仍检查。"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

HAS_CV = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None
HAS_TORCH = importlib.util.find_spec("torch") is not None
PACKAGE = Path(__file__).resolve().parents[2] / "microcleaning" / "data_learning" / "plan_imitation"


class PlanImitationBoundaryTests(unittest.TestCase):
    def test_sources_do_not_touch_hardware_or_yolo(self):
        banned = (
            "control_system.serial",
            "control_system.orchestration",
            "control_system.safety",
            "import ultralytics",
            "import yolo",
            "serial.write",
            "F103SerialSession",
        )
        files = list(PACKAGE.glob("*.py"))
        self.assertGreaterEqual(len(files), 8)
        for path in files:
            text = path.read_text(encoding="utf-8")
            for token in banned:
                self.assertNotIn(token, text, f"{path.name} contains {token}")

    def test_segmentation_entry_does_not_import_plan_model(self):
        text = (PACKAGE.parents[0] / "train_entry.py").read_text(encoding="utf-8")
        self.assertNotIn("import torch", text)
        self.assertIn("plan_imitation.train", text)
        self.assertIn("AGPL", text)


@unittest.skipUnless(HAS_CV, "需要 requirements/perception-opencv.txt")
class PlanImitationGeometryTests(unittest.TestCase):
    def test_synthetic_kinds_are_reproducible_binary_masks(self):
        from microcleaning.data_learning.plan_imitation.synthetic import KINDS, generate_masks, save_mask_archive, load_mask_archive

        first = generate_masks(8, (32, 32), 7)
        second = generate_masks(8, (32, 32), 7)
        self.assertTrue({sample.kind for sample in first} <= set(KINDS))
        self.assertEqual(set(KINDS), {sample.kind for sample in generate_masks(40, (32, 32), 1)})
        for left, right in zip(first, second):
            self.assertEqual(left.kind, right.kind)
            np.testing.assert_array_equal(left.mask, right.mask)
            self.assertEqual({0, 1}, set(np.unique(left.mask).tolist()))
            self.assertGreaterEqual(int(left.mask.sum()), 8)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "masks.npz"
            save_mask_archive(path, first)
            loaded = load_mask_archive(path)
        self.assertEqual([sample.kind for sample in first], [sample.kind for sample in loaded])

    def test_teacher_plan_reuses_greedy_and_stays_nonexecutable(self):
        from microcleaning.data_learning.plan_imitation.config import ImitationStudy
        from microcleaning.data_learning.plan_imitation.synthetic import generate_mask
        from microcleaning.data_learning.plan_imitation.teacher import teacher_plan

        study = ImitationStudy()
        mask = generate_mask("blob", study.shape_hw, np.random.default_rng(3))
        plan = teacher_plan(mask, study)
        self.assertFalse(plan.executable)
        self.assertIn("executable", plan.to_dict())
        self.assertFalse(plan.to_dict()["executable"])
        self.assertFalse(plan.to_dict()["feeds_action_request"])
        self.assertFalse(plan.to_dict()["send_to_controller"])
        self.assertEqual("greedy-discrete-coverage-v1", plan.to_dict()["algorithm"])
        self.assertGreater(len(plan.points_px), 0)

    def test_nearest_neighbor_orders_from_start(self):
        from microcleaning.data_learning.plan_imitation.decode import nearest_neighbor_order, route_length_px

        ordered = nearest_neighbor_order([(10, 4), (2, 2), (10, 4)], (0.0, 0.0))
        self.assertEqual(((2, 2), (10, 4), (10, 4)), ordered)
        self.assertAlmostEqual(route_length_px(ordered, (0.0, 0.0)), (8 ** 0.5) + (68 ** 0.5))

    def test_teacher_heatmap_decodes_back_to_greedy_points(self):
        from microcleaning.data_learning.plan_imitation.config import ImitationStudy
        from microcleaning.data_learning.plan_imitation.evaluate import compare_sample
        from microcleaning.data_learning.plan_imitation.synthetic import generate_mask
        from microcleaning.data_learning.plan_imitation.teacher import stations_to_heatmap, teacher_example

        study = ImitationStudy(heatmap_sigma_px=0.55, peak_threshold=0.6, min_peak_distance_px=0.5, max_stations=6)
        mask = generate_mask("split", study.shape_hw, np.random.default_rng(11))
        example = teacher_example(mask, study, kind="split")
        heatmap = stations_to_heatmap(mask.shape, example.points_px, study.heatmap_sigma_px)
        report = compare_sample(mask, heatmap, study, kind="split", source_ref="roundtrip", target_id="roundtrip")
        self.assertTrue(report["teacher_points_match_greedy"])
        self.assertFalse(report["executable"])
        self.assertFalse(report["learned"]["executable"])
        self.assertEqual(example.station_count, report["learned"]["station_count"])
        self.assertAlmostEqual(example.coverage_rate, report["learned"]["geometric_coverage_rate"])
        for name in ("COVERAGE_AWARE", "CENTER_POINT_COMPONENTS", "RASTER_SCAN_DWELL"):
            self.assertFalse(report["baselines"][name]["executable"])
            self.assertFalse(report["baselines"][name]["send_to_controller"])

    def test_infeasible_peaks_are_dropped(self):
        from microcleaning.data_learning.plan_imitation.config import ImitationStudy
        from microcleaning.data_learning.plan_imitation.decode import decode_and_order

        study = ImitationStudy(peak_threshold=0.4, min_peak_distance_px=1, max_stations=3)
        heat = np.zeros(study.shape_hw, dtype=np.float32)
        heat[1, 1] = 1.0
        heat[8, 8] = 0.8
        feasible = np.zeros(study.shape_hw, dtype=bool)
        feasible[8, 8] = True
        self.assertEqual(((8, 8),), decode_and_order(heat, feasible, study))

    def test_proposal_rejects_executable_constructor_argument(self):
        from microcleaning.data_learning.plan_imitation.proposal import LearnedCoverageProposal

        with self.assertRaises(TypeError):
            LearnedCoverageProposal(
                points_px=(),
                route_length_px=0.0,
                status="PARTIAL",
                reason_codes=(),
                teacher_points_px=(),
                teacher_route_length_px=0.0,
                teacher_coverage_rate=None,
                teacher_status="NO_TARGET",
                evaluation=None,
                kind="blob",
                source_ref="unit",
                executable=True,
            )


@unittest.skipUnless(HAS_CV and HAS_TORCH, "需要 requirements/learning-torch.txt")
class PlanImitationTorchTests(unittest.TestCase):
    def test_forward_and_loss_step_stay_finite(self):
        import torch

        from microcleaning.data_learning.plan_imitation.config import ImitationStudy
        from microcleaning.data_learning.plan_imitation.dataset import HeatmapPlanDataset
        from microcleaning.data_learning.plan_imitation.model import build_model
        from microcleaning.data_learning.plan_imitation.train import build_examples

        study = ImitationStudy(channels=4, seed=2)
        examples = build_examples(2, study, seed=2)
        dataset = HeatmapPlanDataset(examples)
        mask, heat = dataset[0]
        self.assertEqual((1, 32, 32), tuple(mask.shape))
        self.assertEqual(mask.shape, heat.shape)
        model = build_model(study.channels)
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)
        loss_fn = torch.nn.MSELoss()
        first = None
        last = None
        batch_mask = torch.stack([dataset[i][0] for i in range(len(dataset))])
        batch_heat = torch.stack([dataset[i][1] for i in range(len(dataset))])
        for _ in range(25):
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(torch.sigmoid(model(batch_mask)), batch_heat)
            loss.backward()
            optimizer.step()
            last = float(loss.detach())
            if first is None:
                first = last
        self.assertTrue(np.isfinite(last))
        self.assertLess(last, first)

    def test_tiny_cli_train_and_eval(self):
        from microcleaning.data_learning.plan_imitation.evaluate import main as evaluate_main
        from microcleaning.data_learning.plan_imitation.train import main as train_main

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "run"
            code = train_main([
                "--tiny",
                "--output-dir",
                str(output),
                "--epochs",
                "1",
                "--train-samples",
                "2",
                "--val-samples",
                "1",
                "--batch-size",
                "2",
            ])
            self.assertEqual(0, code)
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            self.assertFalse(summary["executable"])
            self.assertFalse(summary["send_to_controller"])
            self.assertEqual("synthetic", summary["data_source"])
            self.assertEqual("existing_greedy_plan_coverage", summary["label_source"])
            self.assertTrue((output / "checkpoint.pt").is_file())
            self.assertEqual(1, len(summary["epochs"]))
            self.assertTrue(np.isfinite(summary["epochs"][0]["train_loss"]))
            report = Path(folder) / "eval.json"
            eval_code = evaluate_main([
                "--checkpoint",
                str(output / "checkpoint.pt"),
                "--config",
                str(output / "config.json"),
                "--samples",
                "2",
                "--output",
                str(report),
            ])
            self.assertEqual(0, eval_code)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertFalse(payload["executable"])
            self.assertEqual(2, payload["sample_count"])
            self.assertIn("learned_coverage", payload["means"])
            self.assertIn("greedy_coverage", payload["means"])
            self.assertTrue(all(row["teacher_points_match_greedy"] for row in payload["samples"]))


if __name__ == "__main__":
    unittest.main()
