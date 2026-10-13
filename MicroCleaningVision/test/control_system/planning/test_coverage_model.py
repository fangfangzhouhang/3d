"""Footprint units, evidence and immutable scenario snapshots."""

import importlib.util
import unittest
from dataclasses import FrozenInstanceError

HAS_DEPS = all(importlib.util.find_spec(name) is not None for name in ("numpy", "cv2"))
if HAS_DEPS:
    import numpy as np
    from microcleaning.control_system.planning.coverage_model import (
        Evidence, FootprintKernel, FootprintProfile, KernelResult, PixelScale,
        SprayConditions, build_kernel,
    )


@unittest.skipUnless(HAS_DEPS, "需要 requirements/perception-opencv.txt")
class CoverageModelTests(unittest.TestCase):
    def profile(self, diameter=2, unit="px", **kwargs):
        return FootprintProfile("virtual-circle", diameter, unit, Evidence("assumed", "test:explicit"), **kwargs)

    def test_no_implicit_diameter(self):
        with self.assertRaises(TypeError):
            FootprintProfile(profile_id="missing", unit="px", evidence=Evidence("assumed", "test"))
        result = build_kernel(self.profile(None))
        self.assertFalse(result.available)
        self.assertEqual(("FOOTPRINT_DIAMETER_MISSING",), result.reason_codes)
        self.assertIsNone(result.profile.radius)

    def test_invalid_diameters(self):
        for value in (True, 0, -1, float("nan"), float("inf"), "18"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.profile(value)

    def test_invalid_units_evidence_and_conditions(self):
        with self.assertRaises(ValueError):
            self.profile(unit="steps")
        for status, source in (("calibrated", "test"), ("assumed", "")):
            with self.subTest(status=status, source=source), self.assertRaises(ValueError):
                Evidence(status, source)
        for value in (False, -1, float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SprayConditions(height_mm=value)
        with self.assertRaises(ValueError):
            SprayConditions(duration_ms=0)

    def test_mm_requires_explicit_scale(self):
        result = build_kernel(self.profile(4, "mm"))
        self.assertEqual(("PIXEL_SCALE_MISSING",), result.reason_codes)
        self.assertIsNone(result.kernel)

    def test_closed_disk_counts_pixel_centers(self):
        kernel = build_kernel(self.profile()).kernel
        np.testing.assert_array_equal(kernel.mask, [[0, 1, 0], [1, 1, 1], [0, 1, 0]])
        self.assertEqual((1, 1), kernel.anchor_px)
        self.assertEqual(5, kernel.to_dict()["area_px"])
        self.assertEqual("integer_pixel_centers_closed_disk", kernel.rasterization)
        tiny = build_kernel(self.profile(0.1)).kernel
        self.assertEqual(1, np.count_nonzero(tiny.mask))

    def test_physical_circle_uses_anisotropic_scale(self):
        scale = PixelScale(1, 2, Evidence("assumed", "synthetic:ruler"), (9, 11))
        kernel = build_kernel(self.profile(4, "mm"), scale).kernel
        np.testing.assert_array_equal(kernel.mask, [[0, 0, 1, 0, 0], [1, 1, 1, 1, 1], [0, 0, 1, 0, 0]])
        self.assertEqual((2, 1), kernel.anchor_px)
        self.assertEqual((9, 11), kernel.scale.image_shape_hw)

    def test_scale_rejects_nonpositive_nonfinite_and_invalid_shape(self):
        for value in (0, -1, True, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PixelScale(value, 1, Evidence("assumed", "test"))
        with self.assertRaises(ValueError):
            PixelScale(1e-300, 1e-300, Evidence("assumed", "test"))
        with self.assertRaises(ValueError):
            PixelScale(1, 1, Evidence("assumed", "test"), (0, 10))

    def measured_profile(self):
        return FootprintProfile("measured-wet", 4, "mm", Evidence("measured", "experiment:wet-001"),
            SprayConditions("N1", 3, "water", "PLA", 500, "pressure:explicit-test"), semantics="wet_footprint")

    def test_measured_wet_footprint_requires_conditions(self):
        with self.assertRaises(ValueError):
            FootprintProfile("bad", 4, "mm", Evidence("measured", "experiment"))
        profile = self.measured_profile()
        self.assertEqual("wet_footprint", profile.semantics)
        self.assertEqual("measured", profile.evidence.status)

    def test_resize_is_assumed_scenario_and_keeps_original(self):
        original = self.measured_profile()
        resized = original.with_diameter(8)
        self.assertEqual(4, original.diameter)
        self.assertEqual("measured", original.evidence.status)
        self.assertEqual("assumed", resized.evidence.status)
        self.assertEqual(original.sha256, resized.parent_sha256)
        self.assertEqual(original.conditions, resized.conditions)
        self.assertNotEqual(original.sha256, resized.sha256)
        with self.assertRaises(FrozenInstanceError):
            original.diameter = 99

    def test_footprint_and_scale_evidence_stay_independent(self):
        scale = PixelScale(0.5, 0.5, Evidence("assumed", "synthetic-scale"))
        kernel = build_kernel(self.measured_profile(), scale).kernel
        self.assertEqual("measured", kernel.profile.evidence.status)
        self.assertEqual("assumed", kernel.scale.evidence.status)

    def test_kernel_snapshot_is_readonly_even_if_source_changes(self):
        source = np.ones((2, 3), dtype=np.uint8)
        kernel = FootprintKernel(source, (0, 1), self.profile())
        source[:] = 0
        self.assertEqual(6, np.count_nonzero(kernel.mask))
        with self.assertRaises(ValueError):
            kernel.mask[0, 0] = False
        with self.assertRaises(ValueError):
            kernel.mask.setflags(write=True)
        self.assertEqual("explicit_binary_mask", kernel.rasterization)

    def test_mask_and_anchor_validation(self):
        for mask in (np.zeros((2, 2)), np.array([[2]], dtype=np.uint8), np.zeros((2, 2, 3), dtype=np.uint8),
                np.zeros((0, 2), dtype=bool), np.zeros((2, 2), dtype=bool)):
            with self.subTest(shape=mask.shape, dtype=mask.dtype), self.assertRaises(ValueError):
                FootprintKernel(mask, (0, 0), self.profile())
        for anchor in ((-1, 0), (2, 0), (True, 0)):
            with self.subTest(anchor=anchor), self.assertRaises(ValueError):
                FootprintKernel(np.ones((2, 2), dtype=bool), anchor, self.profile())

    def test_unavailable_and_available_results_cannot_disagree(self):
        profile = self.profile()
        with self.assertRaises(ValueError):
            KernelResult(profile, None)
        with self.assertRaises(ValueError):
            KernelResult(self.profile(4), build_kernel(profile).kernel)
        with self.assertRaises(ValueError):
            KernelResult(profile, build_kernel(profile).kernel, ("FAIL",))

    def test_shape_and_allocation_limits_return_reasons(self):
        self.assertEqual(("FOOTPRINT_SHAPE_UNSUPPORTED",), build_kernel(self.profile(shape="ellipse")).reason_codes)
        self.assertEqual(("KERNEL_RESOURCE_LIMIT",), build_kernel(self.profile(1e20)).reason_codes)
        self.assertEqual(("FOOTPRINT_CONVERSION_OUT_OF_RANGE",), build_kernel(self.profile(5e-324)).reason_codes)

    def test_hashes_are_repeatable_and_include_scale_and_mask(self):
        first = build_kernel(self.profile()).kernel
        second = build_kernel(self.profile()).kernel
        self.assertEqual(first.sha256, second.sha256)
        self.assertNotEqual(first.sha256, build_kernel(self.profile(4)).kernel.sha256)
        scaled = build_kernel(self.profile(), PixelScale(1, 1, Evidence("assumed", "test"))).kernel
        self.assertNotEqual(first.sha256, scaled.sha256)


if __name__ == "__main__":
    unittest.main()
