"""CPU scientific checks for the DINO probe; no model download or GPU required."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from probes.base import ImageInput
from probes.dino import DinoProbe
from probes.feature_utils import cluster_features, draft_statistics, pca_rgb


class FeatureStatisticsTests(unittest.TestCase):
    def test_ignore_and_conflicts_cannot_become_class_examples(self):
        features = np.array([[[1., 0., 0.], [0., 1., 0.]], [[0., 0., 1.], [1., 1., 0.]]])
        structure = np.array([[True, True], [True, False]])
        residue = np.array([[False, True], [False, False]])
        ignore = np.array([[True, False], [False, False]])
        stats, weights = draft_statistics(features, {"structure": structure, "residue": residue, "ignore": ignore}, (2, 2), 1)
        self.assertEqual(stats["ignore_pixels"], 1)
        self.assertEqual(stats["conflicting_nonignore_pixels"], 1)
        self.assertEqual(stats["unknown_pixels"], 1)
        self.assertEqual(stats["classes"]["structure"]["valid_draft_pixels"], 1)
        self.assertEqual(stats["classes"]["residue"]["valid_draft_pixels"], 0)
        self.assertIsNone(stats["classes"]["substrate"]["center"])
        self.assertEqual(stats["three_class_separability_status"], "not_assessable")
        self.assertFalse(stats["unlabelled_is_substrate"])
        np.testing.assert_array_equal(weights["structure"], [[0, 0], [1, 0]])

    def test_orthogonal_class_centres_have_distance_one_without_accuracy(self):
        features = np.array([[[1., 0., 0.], [1., 0., 0.]], [[0., 1., 0.], [0., 1., 0.]]])
        top = np.array([[True, True], [False, False]])
        stats, _ = draft_statistics(features, {"structure": top, "residue": ~top}, (2, 2), 1)
        pair = stats["comparisons"][0]
        self.assertAlmostEqual(pair["center_cosine_distance"], 1)
        self.assertAlmostEqual(pair["mean_within_cosine_dispersion"], 0)
        self.assertIsNone(pair["between_to_within_ratio"])
        self.assertIsNone(stats["comparisons"][1]["center_cosine_distance"])
        self.assertFalse(stats["test_ground_truth_allowed"])

    def test_padding_is_not_semantic_data_and_shared_patches_are_visible(self):
        features = np.array([[[1., 0., 0.], [0., 1., 0.]], [[1., 0., 0.], [0., 1., 0.]]])
        structure = np.zeros((3, 3), bool)
        residue = structure.copy()
        structure[0, 0] = True
        residue[0, 1] = True
        stats, weights = draft_statistics(features, {"structure": structure, "residue": residue}, (3, 3), 2)
        self.assertEqual(weights["structure"].sum(), 1)
        self.assertEqual(stats["comparisons"][0]["shared_contributing_patches"], 1)
        self.assertAlmostEqual(stats["classes"]["residue"]["pixel_weighted_patch_class_fraction"], 0.25)
        self.assertEqual(stats["unknown_pixels"], 7)

    def test_pca_artifact_can_reproduce_display(self):
        features = np.random.default_rng(5).normal(size=(4, 5, 6)).astype(np.float32)
        rgb, projection = pca_rgb(features)
        projected = (features.reshape(-1, 6) - projection["mean"]) @ projection["components"].T
        reproduced = np.rint(np.clip((projected - projection["display_low"]) / (projection["display_high"] - projection["display_low"]), 0, 1) * 255).astype(np.uint8).reshape(4, 5, 3)
        np.testing.assert_array_equal(rgb, reproduced)
        self.assertLessEqual(projection["explained_variance_ratio"].sum(), 1 + 1e-10)

    def test_clustering_reproducible_for_separated_groups(self):
        features = np.array([[[1., 0., 0.], [1., 0., 0.]], [[0., 1., 0.], [0., 1., 0.]]])
        labels, centers, info = cluster_features(features, k=2)
        labels_again, centers_again, _ = cluster_features(features, k=2)
        np.testing.assert_array_equal(labels, labels_again)
        np.testing.assert_array_equal(centers, centers_again)
        self.assertEqual(labels[0, 0], labels[0, 1])
        self.assertNotEqual(labels[0, 0], labels[1, 0])
        self.assertEqual(info["cluster_patch_counts"], [2, 2])
        self.assertIsNone(info["semantic_assignment"])


class ProbeArtifactTests(unittest.TestCase):
    def test_cpu_stub_exercises_artifact_geometry_and_draft_provenance(self):
        import torch

        class FrozenFeatureFixture:
            def forward_features(self, tensor):
                self.shape = tuple(tensor.shape)
                values = np.random.default_rng(17).normal(size=(1, 6, 384)).astype(np.float32)
                return {"x_norm_patchtokens": torch.from_numpy(values)}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "input.png"
            Image.fromarray(np.full((17, 29, 3), 128, np.uint8)).save(image_path)
            seed = root / "seed"
            seed.mkdir()
            mask = np.zeros((17, 29), np.uint8)
            mask[:12, :12] = 255
            mask_path = seed / "mask.png"
            Image.fromarray(mask).save(mask_path)
            (seed / "instances.json").write_text(json.dumps([{"id": 1, "label": "structure", "mask": "mask.png", "mask_sha256": hashlib.sha256(mask_path.read_bytes()).hexdigest()}]))
            (seed / "points.json").write_text(json.dumps({"image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest()}))
            output = root / "output"
            output.mkdir()
            probe = DinoProbe({"clusters": 2, "device": "cpu"}, root)
            probe.model = FrozenFeatureFixture()
            probe.torch = torch
            probe.device = "cpu"
            probe.parameter_count = 0
            result = probe.infer(ImageInput("image", image_path, {"seed_dir": "seed"}), output)
            self.assertEqual(probe.model.shape, (1, 3, 28, 42))
            self.assertEqual(result.metrics["feature_shape"], [2, 3, 384])
            with Image.open(output / "overlay.png") as opened:
                self.assertEqual(opened.size, (29, 17))
            with Image.open(output / "original_pca_clusters.png") as opened:
                self.assertEqual(opened.size, (87, 17))
            self.assertEqual(np.load(output / "features.npy", allow_pickle=False).shape, (2, 3, 384))
            self.assertIn("missing-draft-substrate", result.reason_codes)
            self.assertIn("missing-draft-residue", result.reason_codes)
            self.assertTrue(all((output / file).is_file() for file in result.output_files))
            # Reject a changed mask before producing semantic statistics.
            mask[0, 0] = 0
            Image.fromarray(mask).save(mask_path)
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                probe._draft_masks(ImageInput("image", image_path, {"seed_dir": "seed"}), (17, 29))


if __name__ == "__main__":
    unittest.main()
