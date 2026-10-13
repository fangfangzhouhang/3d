"""Exact union checks against an independent coordinate-set oracle."""

import importlib.util
import unittest

HAS_DEPS = all(importlib.util.find_spec(name) is not None for name in ("numpy", "cv2"))
if HAS_DEPS:
    import numpy as np
    from microcleaning.control_system.planning.coverage_model import Evidence, FootprintKernel, FootprintProfile, PixelScale, build_kernel
    from microcleaning.control_system.planning.coverage_metrics import evaluate_coverage


def coordinate_counts(kernel, points):
    """Enumerate each actual kernel pixel, including negative/off-image locations."""
    counts = {}
    for x, y in points:
        for ky, kx in zip(*np.nonzero(kernel.mask)):
            location = (x + int(kx) - kernel.anchor_px[0], y + int(ky) - kernel.anchor_px[1])
            counts[location] = counts.get(location, 0) + 1
    return counts


@unittest.skipUnless(HAS_DEPS, "需要 requirements/perception-opencv.txt")
class CoverageMetricsTests(unittest.TestCase):
    def kernel(self, scale=None):
        return build_kernel(FootprintProfile("virtual", 2, "px", Evidence("assumed", "test")), scale).kernel

    def test_union_overlap_spill_have_hand_counted_values(self):
        mask = np.ones((3, 3), dtype=bool)
        result = evaluate_coverage(mask, self.kernel(), [(0, 0), (1, 0)])
        self.assertEqual(8, result.metrics.footprint_area_px)
        self.assertEqual(5, result.metrics.covered_area_px)
        self.assertEqual(4, result.metrics.uncovered_area_px)
        self.assertEqual(3, result.metrics.outside_image_area_px)
        self.assertEqual(3, result.metrics.outside_target_area_px)
        self.assertEqual(2, result.metrics.overlap_target_area_px)
        self.assertAlmostEqual(5 / 9, result.metrics.geometric_coverage_rate)

    def test_duplicate_dwell_counts_union_once(self):
        result = evaluate_coverage(np.ones((5, 5), dtype=bool), self.kernel(), [(2, 2), (2, 2)])
        self.assertEqual(5, result.metrics.covered_area_px)
        self.assertEqual(5, result.metrics.overlap_target_area_px)
        self.assertEqual(2, result.metrics.station_count)

    def test_empty_target_does_not_claim_one_hundred_percent(self):
        result = evaluate_coverage(np.zeros((5, 5), dtype=bool), self.kernel(), [])
        self.assertIsNone(result.metrics.geometric_coverage_rate)
        self.assertEqual(0, result.metrics.target_area_px)
        self.assertIsNone(result.metrics.conservative_coverage_rate)

    def test_covered_and_uncovered_are_exact_partition(self):
        mask = np.eye(7, dtype=bool)
        result = evaluate_coverage(mask, self.kernel(), [(1, 1), (4, 4)])
        np.testing.assert_array_equal(mask, result.covered_mask | result.uncovered_mask)
        self.assertFalse(np.any(result.covered_mask & result.uncovered_mask))
        self.assertEqual(result.metrics.target_area_px,
            result.metrics.covered_area_px + result.metrics.uncovered_area_px)

    def test_unknown_and_explicit_conservative_coverage_are_distinct(self):
        mask = np.ones((5, 5), dtype=bool)
        kernel = self.kernel()
        nominal = evaluate_coverage(mask, kernel, [(2, 2)])
        self.assertIsNone(nominal.conservative_covered_mask)
        inner = np.zeros(kernel.mask.shape, dtype=bool)
        inner[1, 1] = True
        result = evaluate_coverage(mask, kernel, [(2, 2)], conservative_kernel_mask=inner)
        self.assertEqual(1, result.metrics.conservative_covered_area_px)
        self.assertAlmostEqual(1 / 25, result.metrics.conservative_coverage_rate)
        with self.assertRaises(ValueError):
            evaluate_coverage(mask, kernel, [], conservative_kernel_mask=np.ones((3, 3), dtype=bool))

    def test_area_units_require_explicit_scale(self):
        mask = np.ones((3, 3), dtype=bool)
        self.assertIsNone(evaluate_coverage(mask, self.kernel(), []).metrics.target_area_mm2)
        scale = PixelScale(0.5, 2, Evidence("assumed", "test-scale"), (3, 3))
        result = evaluate_coverage(mask, self.kernel(scale), [(1, 1)])
        self.assertEqual(9, result.metrics.target_area_mm2)
        self.assertEqual(4, result.metrics.uncovered_area_mm2)
        with self.assertRaises(ValueError):
            evaluate_coverage(np.ones((4, 3), dtype=bool), self.kernel(scale), [])

    def test_random_and_asymmetric_kernels_match_coordinate_oracle(self):
        rng = np.random.default_rng(774)
        for index in range(20):
            target = rng.random((7, 8)) > 0.5
            kmask = rng.random((4, 5)) > 0.4
            kmask[0, 0] = True
            anchor = (index % 5, index % 4)
            kernel = FootprintKernel(kmask, anchor, self.kernel().profile)
            points = [tuple(int(v) for v in rng.integers([0, 0], [8, 7])) for _ in range(4)]
            expected = coordinate_counts(kernel, points)
            result = evaluate_coverage(target, kernel, points)
            with self.subTest(index=index, anchor=anchor):
                counts = np.array([[expected.get((x, y), 0) for x in range(8)] for y in range(7)], dtype=np.uint32)
                np.testing.assert_array_equal(counts, result.coverage_counts)
                np.testing.assert_array_equal(target & (counts > 0), result.covered_mask)
                self.assertEqual(len(expected), result.metrics.footprint_area_px)
                self.assertEqual(sum(not (0 <= x < 8 and 0 <= y < 7) for x, y in expected),
                    result.metrics.outside_image_area_px)

    def test_evaluation_arrays_cannot_be_mutated(self):
        result = evaluate_coverage(np.ones((5, 5), dtype=bool), self.kernel(), [(2, 2)])
        for array in (result.footprint_mask, result.covered_mask, result.uncovered_mask, result.overlap_mask, result.coverage_counts):
            with self.subTest(dtype=array.dtype), self.assertRaises(ValueError):
                array.setflags(write=True)

    def test_invalid_stations_and_allocation_are_rejected(self):
        for point in ((-1, 0), (5, 1), (1.1, 1), (True, 1), (1,)):
            with self.subTest(point=point), self.assertRaises(ValueError):
                evaluate_coverage(np.ones((5, 5), dtype=bool), self.kernel(), [point])
        with self.assertRaisesRegex(ValueError, "CANVAS_RESOURCE_LIMIT"):
            evaluate_coverage(np.zeros((2001, 2001), dtype=bool), self.kernel(), [])
        with self.assertRaisesRegex(ValueError, "POINT_RESOURCE_LIMIT"):
            evaluate_coverage(np.ones((5, 5), dtype=bool), self.kernel(), [(2, 2)] * 4097)

    def test_metadata_is_repeatable_and_tracks_coverage_changes(self):
        mask = np.ones((5, 5), dtype=bool)
        first = evaluate_coverage(mask, self.kernel(), [(1, 1)])
        second = evaluate_coverage(mask, self.kernel(), [(1, 1)])
        third = evaluate_coverage(mask, self.kernel(), [(3, 3)])
        self.assertEqual(first.masks_to_dict(), second.masks_to_dict())
        self.assertNotEqual(first.masks_to_dict(), third.masks_to_dict())


if __name__ == "__main__":
    unittest.main()
