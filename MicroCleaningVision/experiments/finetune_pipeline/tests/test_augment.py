from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from experiments.finetune_pipeline.augment import augment_pair
from experiments.finetune_pipeline.preprocess import prepare, restore_prediction


class AugmentationTests(unittest.TestCase):
    def marker(self):
        mask = np.zeros((64, 80), np.uint8)
        mask[19:28, 47:56] = 1
        image = Image.fromarray(np.repeat((mask * 255)[..., None], 3, axis=2))
        return image, mask

    def assert_aligned(self, image, mask):
        pixels = np.asarray(image)[..., 0]
        expected = mask == 1
        actual = pixels >= 128
        disagreement = np.count_nonzero(actual != expected)
        self.assertLessEqual(disagreement, 4, "image and nearest-neighbor semantic mask disagree beyond edge interpolation")
        y1, x1 = np.nonzero(expected)
        y2, x2 = np.nonzero(actual)
        self.assertGreater(len(x1), 0)
        self.assertLess(abs(x1.mean() - x2.mean()), 0.5)
        self.assertLess(abs(y1.mean() - y2.mean()), 0.5)

    def test_flips_and_quarter_turns_have_exact_landmark_locations(self):
        image, mask = self.marker()
        cases = [({"hflip_p": 1}, np.fliplr(mask)), ({"vflip_p": 1}, np.flipud(mask))]
        cases += [({"rotate90_p": 1, "rotate90_choices": [k]}, np.rot90(mask, k)) for k in (1, 2, 3)]
        for config, expected in cases:
            with self.subTest(config=config):
                changed, labels = augment_pair(image, mask, config, 42, 255)
                np.testing.assert_array_equal(labels, expected)
                np.testing.assert_array_equal(np.asarray(changed)[..., 0], expected * 255)

    def test_small_rotation_alignment_expected_point_and_unknown_fill(self):
        image, mask = self.marker()
        angle = 13
        changed, labels = augment_pair(image, mask, {"rotate_degrees": [angle, angle]}, 42, 255)
        self.assert_aligned(changed, labels)
        y, x = np.nonzero(labels == 1)
        theta = np.deg2rad(angle)
        # Pillow rotates about pixel-grid center, with downward-positive image y.
        dx, dy = 51 - 39.5, 23 - 31.5
        self.assertLess(abs(x.mean() - (39.5 + dx * np.cos(theta) + dy * np.sin(theta))), 0.6)
        self.assertLess(abs(y.mean() - (31.5 - dx * np.sin(theta) + dy * np.cos(theta))), 0.6)
        self.assertTrue((labels == 255).any())
        self.assertTrue(set(np.unique(labels)).issubset({0, 1, 255}))

    def test_random_crop_alignment_and_changed_extent(self):
        image, mask = self.marker()
        changed, labels = augment_pair(image, mask, {"crop_fraction": [0.8, 0.8]}, 42, 255)
        self.assertEqual(changed.size, (64, 51))
        self.assert_aligned(changed, labels)
        self.assertEqual(int((labels == 1).sum()), 81)

    def test_scale_alignment_and_nearest_class_values(self):
        image, mask = self.marker()
        changed, labels = augment_pair(image, mask, {"scale_range": [2, 2]}, 42, 255)
        self.assertEqual(changed.size, (160, 128))
        self.assert_aligned(changed, labels)
        self.assertEqual(set(np.unique(labels)), {0, 1})
        self.assertEqual(int((labels == 1).sum()), 81 * 4)

    def test_full_configuration_is_reproducible_pixel_for_pixel(self):
        image, mask = self.marker()
        config = {"hflip_p": 0.5, "vflip_p": 0.5, "rotate90_p": 1,
                  "rotate_degrees": [-8, 8], "crop_fraction": [0.85, 1], "scale_range": [0.9, 1.1],
                  "brightness": [0.8, 1.2], "contrast": [0.8, 1.2], "gamma": [0.9, 1.1],
                  "blur_radius": [0, 0.7], "noise_std": [0, 0.025]}
        first = augment_pair(image, mask, config, 20261110, 255)
        second = augment_pair(image, mask, config, 20261110, 255)
        np.testing.assert_array_equal(np.asarray(first[0]), np.asarray(second[0]))
        np.testing.assert_array_equal(first[1], second[1])
        self.assertTrue(np.array_equal(np.asarray(image)[..., 0], mask * 255))

    def test_photometry_leaves_mask_unchanged(self):
        image, mask = self.marker()
        config = {"brightness": [0.8, 0.8], "contrast": [0.8, 0.8], "gamma": [1.2, 1.2],
                  "blur_radius": [0.5, 0.5], "noise_std": [0.03, 0.03]}
        changed, labels = augment_pair(image, mask, config, 12, 255)
        np.testing.assert_array_equal(labels, mask)
        self.assertFalse(np.array_equal(np.asarray(changed), np.asarray(image)))

    def test_invalid_parameters_fail_instead_of_silent_identity(self):
        image, mask = self.marker()
        for config in ({"hflip_p": 2}, {"gamma": [0, 1]}, {"crop_fraction": [1, 2]},
                       {"scale_range": [-1, 1]}, {"unrecognized": True}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                augment_pair(image, mask, config, 1, 255)


class PreprocessTests(unittest.TestCase):
    def test_resize_padding_normalization_and_inverse_coordinates(self):
        image = Image.new("RGB", (100, 50), (255, 128, 0))
        mask = np.zeros((50, 100), np.uint8)
        mask[:, 50:] = 1
        config = {"max_side": 64, "pad_multiple": 32, "mean": [0.5, 0.5, 0.5],
                  "std": [0.5, 0.25, 1], "image_fill": [0, 0, 0]}
        tensor, labels, metadata = prepare(image, mask, config, 255)
        self.assertEqual(tuple(tensor.shape), (3, 32, 64))
        self.assertEqual(metadata["resized_size"], [64, 32])
        np.testing.assert_allclose(tensor[:, 0, 0], [1, (128 / 255 - 0.5) / 0.25, -0.5], atol=1e-6)
        restored = restore_prediction(labels.numpy(), metadata)
        np.testing.assert_array_equal(restored, mask)

    def test_odd_size_padding_is_ignore_and_restored_shape_exact(self):
        image = Image.new("RGB", (37, 21))
        mask = np.ones((21, 37), np.uint8)
        config = {"max_side": None, "pad_multiple": 32, "mean": [0, 0, 0],
                  "std": [1, 1, 1], "image_fill": [0, 0, 0]}
        _, labels, metadata = prepare(image, mask, config, 255)
        self.assertEqual(tuple(labels.shape), (32, 64))
        self.assertTrue((labels[21:, :] == 255).all())
        self.assertTrue((labels[:, 37:] == 255).all())
        np.testing.assert_array_equal(restore_prediction(labels.numpy(), metadata), mask)

    def test_normalization_must_be_explicit(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            prepare(Image.new("RGB", (2, 2)), np.zeros((2, 2)), {}, 255)


if __name__ == "__main__":
    unittest.main()
