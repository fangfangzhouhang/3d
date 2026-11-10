"""CPU-only checks for model/loss contracts and the real mainline evaluator."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch

from experiments.finetune_pipeline.evaluation import evaluate_predictions, load_mainline_evaluator
from experiments.finetune_pipeline.export import export_onnx
from experiments.finetune_pipeline.losses import build_loss, register_loss, LOSS_REGISTRY
from experiments.finetune_pipeline.models import build_model, register_model, MODEL_REGISTRY, TinyUNet


def schema():
    # Generic synthetic identities, not a second copy of the project class table.
    return SimpleNamespace(classes=[{"name": f"class_{i}", "index": i} for i in range(3)], ignore_index=255)


class ModelLossTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_threads = torch.get_num_threads()
        torch.set_num_threads(1)

    @classmethod
    def tearDownClass(cls):
        torch.set_num_threads(cls.original_threads)

    def test_unet_shape_and_real_backward_on_odd_size(self):
        torch.manual_seed(10)
        model = build_model({"name": "tiny_unet", "params": {"base_channels": 2}}, 3)
        image = torch.rand(2, 3, 33, 47)
        target = torch.randint(0, 3, (2, 33, 47))
        target[:, :3] = 255
        logits = model(image)
        self.assertEqual(tuple(logits.shape), (2, 3, 33, 47))
        loss = build_loss({"name": "ce_dice"}, 255)(logits, target)
        loss.backward()
        self.assertTrue(torch.isfinite(loss))
        self.assertTrue(all(p.grad is not None and torch.isfinite(p.grad).all() for p in model.parameters()))
        self.assertTrue(any(torch.count_nonzero(p.grad) > 0 for p in model.parameters()))

    def test_model_registry_allows_new_factory_without_training_branch(self):
        name = "test_linear_classifier"
        try:
            @register_model(name)
            def factory(num_classes, input_channels):
                return torch.nn.Conv2d(input_channels, num_classes, 1)
            model = build_model({"name": name, "params": {"input_channels": 3}}, 2)
            self.assertEqual(tuple(model(torch.ones(1, 3, 8, 8)).shape), (1, 2, 8, 8))
            with self.assertRaises(ValueError):
                register_model(name)(factory)
        finally:
            MODEL_REGISTRY.pop(name, None)

    def test_decoder_upsamples_by_exact_nearest_replication(self):
        deep = torch.tensor([[[[1., 2.], [3., 4.]]]], requires_grad=True)
        skip = torch.full((1, 1, 4, 4), 9.)
        joined = TinyUNet._join(deep, skip)
        expected = deep.detach().repeat_interleave(2, dim=2).repeat_interleave(2, dim=3)
        self.assertTrue(torch.equal(joined[:, :1], expected))
        self.assertTrue(torch.equal(joined[:, 1:], skip))
        joined.sum().backward()
        self.assertTrue(torch.equal(deep.grad, torch.full_like(deep, 4.)))

    def test_pending_backbone_is_explicit_and_no_model_loaded(self):
        with self.assertRaisesRegex(NotImplementedError, "TODO"):
            build_model({"name": "dino"}, 3)
        with self.assertRaises(ValueError):
            build_model({"name": "not_registered"}, 3)

    def test_ignore_logits_do_not_change_loss_or_receive_gradient(self):
        target = torch.tensor([[[0, 255], [1, 255]]])
        reference = torch.tensor([[[[2., 1.], [0., 4.]], [[0., 5.], [2., 2.]], [[-1., 9.], [-1., 3.]]]])
        for name in ("cross_entropy", "dice", "ce_dice"):
            with self.subTest(name=name):
                loss_fn = build_loss({"name": name}, 255)
                x = reference.clone().requires_grad_()
                modified = reference.clone()
                modified[:, :, :, 1] = torch.tensor([100., -100., 23.]).view(1, 3, 1)
                self.assertEqual(float(loss_fn(x, target)), float(loss_fn(modified, target)))
                loss_fn(x, target).backward()
                self.assertTrue(torch.equal(x.grad[:, :, :, 1], torch.zeros_like(x.grad[:, :, :, 1])))

    def test_all_ignore_is_finite_zero_with_valid_backward(self):
        for name in ("cross_entropy", "dice", "ce_dice"):
            with self.subTest(name=name):
                logits = torch.randn(1, 3, 2, 2, requires_grad=True)
                loss = build_loss({"name": name}, 255)(logits, torch.full((1, 2, 2), 255))
                self.assertEqual(float(loss.detach()), 0.)
                loss.backward()
                self.assertTrue(torch.equal(logits.grad, torch.zeros_like(logits)))

    def test_default_loss_is_requested_unweighted_sum(self):
        logits = torch.tensor([[[[2., 0.]], [[0., 2.]], [[-2., -2.]]]])
        target = torch.tensor([[[0, 1]]])
        ce = build_loss({"name": "cross_entropy"}, 255)(logits, target)
        dice = build_loss({"name": "dice"}, 255)(logits, target)
        combined = build_loss({"name": "ce_dice"}, 255)(logits, target)
        self.assertTrue(torch.equal(combined, ce + dice))

    def test_loss_registry_is_replaceable(self):
        name = "test_zero"
        try:
            @register_loss(name)
            class CustomLoss(torch.nn.Module):
                def __init__(self, ignore_index):
                    super().__init__()
                    self.ignore_index = ignore_index
                def forward(self, logits, target):
                    return logits.sum() * 0
            self.assertIsInstance(build_loss({"name": name}, 255), CustomLoss)
            with self.assertRaises(ValueError):
                register_loss(name)(CustomLoss)
        finally:
            LOSS_REGISTRY.pop(name, None)

    def test_invalid_class_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown class"):
            build_loss({"name": "ce_dice"}, 255)(torch.randn(1, 3, 1, 1), torch.tensor([[[9]]]))

    def test_export_is_only_a_pending_interface(self):
        with self.assertRaisesRegex(NotImplementedError, "i3-5005U"):
            export_onnx(None, None, example_input=None)


class MainlineEvaluationTests(unittest.TestCase):
    def test_import_is_original_source_and_does_not_install_global_cv2(self):
        prior_cv2 = sys.modules.get("cv2")
        module = load_mainline_evaluator()
        expected = Path(__file__).resolve().parents[3] / "microcleaning/data_learning/mask_evaluation.py"
        self.assertEqual(Path(module.evaluate_masks.__code__.co_filename).resolve(), expected)
        self.assertFalse(module.finetune_provenance["source_modified"])
        self.assertEqual(len(module.finetune_provenance["sha256"]), 64)
        if module.finetune_provenance["dependency_adapter"] == "scoped_unavailable_cv2":
            self.assertIs(sys.modules.get("cv2"), prior_cv2)
            with self.assertRaisesRegex(ImportError, "OpenCV is not installed"):
                _ = module.cv2.imdecode

    def test_known_values_and_ignore_pixels_excluded(self):
        gt = np.array([[0, 0, 1, 255], [1, 1, 255, 255]], dtype=np.uint8)
        pred = np.array([[0, 1, 1, 0], [1, 0, 2, 0]], dtype=np.uint8)
        result = evaluate_predictions([({"filename": "synthetic"}, gt, pred)], schema())
        self.assertEqual(result["known_pixels"], 5)
        self.assertEqual(result["ignored_pixels"], 3)
        first = result["classes"]["class_0"]
        self.assertAlmostEqual(first["iou"], 1 / 3)
        self.assertAlmostEqual(first["precision"], .5)
        self.assertAlmostEqual(first["recall"], .5)
        self.assertAlmostEqual(first["f1"], .5)
        second = result["classes"]["class_1"]
        self.assertAlmostEqual(second["iou"], .5)
        self.assertAlmostEqual(second["precision"], 2 / 3)
        self.assertAlmostEqual(second["recall"], 2 / 3)
        self.assertAlmostEqual(second["f1"], 2 / 3)

    def test_absent_class_is_no_samples_and_false_positives_remain(self):
        gt = np.zeros((2, 3), dtype=np.uint8)
        pred = np.array([[2, 0, 0], [0, 0, 0]], dtype=np.uint8)
        absent = evaluate_predictions([({}, gt, pred)], schema())["classes"]["class_2"]
        self.assertEqual(absent["status"], "无样本")
        self.assertEqual(absent["false_positive_px"], 1)
        self.assertEqual(absent["ground_truth_area_px"], 0)
        for key in ("iou", "precision", "recall", "f1"):
            self.assertIsNone(absent[key])

    def test_micro_aggregation_includes_false_positive_from_absent_image(self):
        pairs = [({}, np.array([[0, 1]], dtype=np.uint8), np.array([[0, 1]], dtype=np.uint8)),
                 ({}, np.array([[1, 1]], dtype=np.uint8), np.array([[0, 1]], dtype=np.uint8))]
        item = evaluate_predictions(pairs, schema())["classes"]["class_0"]
        self.assertAlmostEqual(item["iou"], .5)
        self.assertAlmostEqual(item["precision"], .5)
        self.assertAlmostEqual(item["recall"], 1.)
        self.assertEqual(item["images_with_ground_truth"], 1)

    def test_empty_split_and_all_ignore_are_no_samples(self):
        for pairs in ([], [({}, np.full((2, 3), 255, dtype=np.uint8), np.zeros((2, 3), dtype=np.uint8))]):
            with self.subTest(pair_count=len(pairs)):
                result = evaluate_predictions(pairs, schema())
                self.assertEqual(result["status"], "无样本")
                self.assertEqual(result["known_pixels"], 0)
                self.assertTrue(all(row["iou"] is None for row in result["classes"].values()))
                self.assertTrue(all(row["false_positive_px"] == 0 for row in result["classes"].values()))

    def test_bad_shape_and_unknown_labels_fail(self):
        with self.assertRaises(ValueError):
            evaluate_predictions([({}, np.zeros((2, 2)), np.zeros((3, 3)))], schema())
        with self.assertRaisesRegex(ValueError, "class index"):
            evaluate_predictions([({}, np.zeros((2, 2)), np.full((2, 2), 99))], schema())


if __name__ == "__main__":
    unittest.main()
