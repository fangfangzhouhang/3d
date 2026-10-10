"""冻结ROI分割的合成像素对抗检查。"""

import importlib.util
import unittest
from dataclasses import replace
from types import SimpleNamespace

from microcleaning.vision.roi_segmentation import FrozenRoiSegmenter


HAS_DEPS = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


@unittest.skipUnless(HAS_DEPS, "需要 requirements/perception-opencv.txt")
class FrozenRoiSegmentationTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np = np
        self.image = np.full((200, 240, 3), (155, 155, 155), np.uint8)
        self.target = np.zeros(self.image.shape[:2], np.uint8)
        self.target[80:100, 90:110] = 255
        self.roi = (72, 62, 56, 56)

    def segmenter(self, algorithm, **changes):
        from microcleaning.vision.local_contrast_baseline import LocalContrastPolicy
        from microcleaning.vision.hsv_baseline import HSVSegmentationPolicy
        from microcleaning.vision.otsu_baseline import OtsuSegmentationPolicy
        from microcleaning.vision.exg_baseline import ColorIndexPolicy
        policy = {
            "local": LocalContrastPolicy(),
            "hsv": HSVSegmentationPolicy(),
            "otsu": OtsuSegmentationPolicy(),
            "exg": ColorIndexPolicy(index="exg"),
            "exr": ColorIndexPolicy(index="exr"),
        }[algorithm]
        return SimpleNamespace(algorithm=algorithm, policy=replace(policy, **changes))

    def make_image(self, algorithm):
        image = self.image.copy()
        image[self.target > 0] = {
            "local": (25, 25, 25), "otsu": (25, 25, 25),
            "hsv": (0, 0, 210), "exr": (0, 0, 210), "exg": (0, 210, 0),
        }[algorithm]
        return image

    def test_all_algorithms_preserve_reference_target_and_output_shape(self):
        for algorithm in ("local", "hsv", "otsu", "exg", "exr"):
            with self.subTest(algorithm=algorithm):
                image = self.make_image(algorithm)
                frozen = FrozenRoiSegmenter(self.segmenter(algorithm), image, self.roi, self.target)
                mask = frozen.mask(image)
                self.assertEqual(self.target.shape, mask.shape)
                self.assertEqual(self.np.uint8, mask.dtype)
                self.assertGreater(self.np.count_nonzero(mask & self.target), 0)
                x, y, w, h = self.roi
                outside = mask.copy()
                outside[y:y+h, x:x+w] = 0
                self.assertEqual(0, self.np.count_nonzero(outside))
                self.np.testing.assert_array_equal(mask, frozen.mask(image))

    def test_other_target_changes_never_reestimate_local_otsu_peak_or_mask(self):
        for algorithm in ("local", "otsu", "exg", "exr"):
            with self.subTest(algorithm=algorithm):
                image = self.make_image(algorithm)
                image[30:60, 170:200] = (0, 0, 255)
                frozen = FrozenRoiSegmenter(self.segmenter(algorithm), image, self.roi, self.target)
                initial = frozen.mask(image)
                config = frozen.to_dict()
                changed = image.copy()
                changed[20:140, 150:230] = (0, 255, 0)
                self.np.testing.assert_array_equal(initial, frozen.mask(changed))
                self.assertEqual(config, frozen.to_dict())

    def test_adjacent_outside_roi_changes_cannot_change_gaussian_context(self):
        image = self.make_image("local")
        frozen = FrozenRoiSegmenter(self.segmenter("local"), image, self.roi, self.target)
        initial = frozen.mask(image)
        changed = image.copy()
        x, y, w, h = self.roi
        changed[y:y+h, x+w:x+w+15] = (0, 0, 0)
        self.np.testing.assert_array_equal(initial, frozen.mask(changed))

    def test_inside_roi_neighbor_context_is_excluded_and_frozen(self):
        image = self.make_image("local")
        excluded = self.np.zeros_like(self.target)
        excluded[80:100, 118:128] = 255
        image[excluded > 0] = (30, 30, 30)
        frozen = FrozenRoiSegmenter(self.segmenter("local"), image, self.roi, self.target, excluded_mask=excluded)
        initial = frozen.mask(image)
        changed = image.copy()
        changed[excluded > 0] = (155, 155, 155)
        self.np.testing.assert_array_equal(initial, frozen.mask(changed))
        self.assertIn("excluded_context_sha256", frozen.to_dict())

    def test_local_fixed_threshold_tracks_partial_residual(self):
        image = self.make_image("local")
        frozen = FrozenRoiSegmenter(self.segmenter("local"), image, self.roi, self.target)
        post = image.copy()
        post[80:100, 100:110] = self.image[80:100, 100:110]
        pre_area = self.np.count_nonzero(frozen.mask(image))
        post_area = self.np.count_nonzero(frozen.mask(post))
        self.assertGreater(post_area, 0)
        self.assertLess(post_area, pre_area)
        self.assertEqual(frozen.to_dict()["threshold"], frozen.threshold)

    def test_no_target_produces_empty_post_for_all_algorithms(self):
        for algorithm in ("local", "hsv", "otsu", "exg", "exr"):
            with self.subTest(algorithm=algorithm):
                image = self.make_image(algorithm)
                frozen = FrozenRoiSegmenter(self.segmenter(algorithm), image, self.roi, self.target)
                self.assertEqual(0, self.np.count_nonzero(frozen.mask(self.image)))

    def test_tiny_target_uses_original_frame_filter_scale_not_roi_area_ratio(self):
        target = self.np.zeros_like(self.target)
        target[80:84, 90:94] = 255
        image = self.image.copy()
        image[target > 0] = (0, 0, 210)
        roi = (88, 78, 8, 8)
        segmenter = self.segmenter("hsv", min_component_area_px=1, morphology_kernel_px=1)
        frozen = FrozenRoiSegmenter(segmenter, image, roi, target)
        self.assertEqual(16, self.np.count_nonzero(frozen.mask(image)))

    def test_growth_reaches_roi_boundary_and_is_visible_to_verification_guard(self):
        image = self.make_image("hsv")
        frozen = FrozenRoiSegmenter(self.segmenter("hsv", morphology_kernel_px=1), image, self.roi, self.target)
        larger = image.copy()
        larger[80:100, 90:150] = (0, 0, 210)
        mask = frozen.mask(larger)
        x, y, w, h = self.roi
        self.assertTrue(self.np.any(mask[y:y+h, x+w-1]))
        self.assertGreater(self.np.count_nonzero(mask), frozen.initial_area_px)

    def test_policy_and_reference_snapshot_cannot_drift(self):
        image = self.make_image("local")
        source = self.segmenter("local")
        frozen = FrozenRoiSegmenter(source, image, self.roi, self.target)
        initial = frozen.mask(image)
        source.policy = replace(source.policy, min_residual=200)
        source.algorithm = "hsv"
        image[:] = 0
        self.np.testing.assert_array_equal(initial, frozen.mask(frozen._reference))
        config = frozen.to_dict()
        config["policy"]["min_residual"] = 200
        self.assertEqual(8.0, frozen.to_dict()["policy"]["min_residual"])
        self.assertEqual("local", frozen.algorithm)
        with self.assertRaises(AttributeError):
            frozen.threshold = 200

    def test_initial_otsu_branch_remains_fixed_after_other_region_changes(self):
        image = self.make_image("otsu")
        frozen = FrozenRoiSegmenter(self.segmenter("otsu"), image, self.roi, self.target)
        self.assertEqual("frozen_initial_otsu", frozen.branch)
        initial = frozen.mask(image)
        changed = image.copy()
        changed[:50] = 0
        self.np.testing.assert_array_equal(initial, frozen.mask(changed))
        self.assertEqual("frozen_initial_otsu", frozen.branch)

    def test_otsu_adaptive_fallback_is_frozen_threshold_map(self):
        image = self.make_image("otsu")
        segmenter = self.segmenter("otsu", min_component_area_px=1000, fallback_min_component_area_px=1)
        frozen = FrozenRoiSegmenter(segmenter, image, self.roi, self.target)
        self.assertEqual("frozen_initial_adaptive_background", frozen.branch)
        self.assertIn("adaptive_threshold_map_sha256", frozen.to_dict())
        initial = frozen.mask(image)
        self.assertGreater(self.np.count_nonzero(initial), 0)
        changed = image.copy()
        changed[:30] = 0
        self.np.testing.assert_array_equal(initial, frozen.mask(changed))

    def test_no_initial_signal_stays_explicit_without_dynamic_fallback(self):
        frozen = FrozenRoiSegmenter(self.segmenter("local"), self.image, self.roi, self.target)
        self.assertEqual("no_initial_signal", frozen.branch)
        self.assertIsNone(frozen.threshold)
        self.assertEqual(0, self.np.count_nonzero(frozen.mask(self.make_image("local"))))

    def test_bad_roi_shape_algorithm_and_image_size_rejected(self):
        with self.assertRaises(ValueError):
            FrozenRoiSegmenter(self.segmenter("local"), self.image, (-1, 0, 10, 10), self.target)
        with self.assertRaises(ValueError):
            FrozenRoiSegmenter(SimpleNamespace(algorithm="neural"), self.image, self.roi, self.target)
        frozen = FrozenRoiSegmenter(self.segmenter("hsv"), self.make_image("hsv"), self.roi, self.target)
        with self.assertRaises(ValueError):
            frozen.mask(self.image[:100])

    def test_initial_frozen_areas_match_original_baselines(self):
        from microcleaning.vision.local_contrast_baseline import segment_contamination as local
        from microcleaning.vision.hsv_baseline import segment_contamination as hsv
        from microcleaning.vision.otsu_baseline import segment_contamination as otsu
        from microcleaning.vision.exg_baseline import segment_contamination as exg, segment_excess_red as exr
        for algorithm, function in (("local", local), ("hsv", hsv), ("otsu", otsu), ("exg", exg), ("exr", exr)):
            with self.subTest(algorithm=algorithm):
                image = self.make_image(algorithm)
                source = self.segmenter(algorithm)
                original = function(image, policy=source.policy).mask
                frozen = FrozenRoiSegmenter(source, image, self.roi, self.target)
                expected = self.np.zeros_like(original)
                x, y, w, h = self.roi
                expected[y:y+h, x:x+w] = original[y:y+h, x:x+w]
                self.np.testing.assert_array_equal(expected, frozen.mask(image))


if __name__ == "__main__":
    unittest.main()
