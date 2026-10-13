"""P3 pixel partitions, prefix semantics and original-coordinate rendering."""

import unittest
from dataclasses import replace

import numpy as np

from microcleaning.control_system.planning.coverage_model import Evidence, FootprintKernel, FootprintProfile, build_kernel
from microcleaning.control_system.planning.coverage_planner import CoverageConstraints, CoverageInput, CoveragePolicy, PositionUncertainty, plan_coverage
from microcleaning.control_system.planning.coverage_visualization import COLORS, RenderOptions, render_coverage, render_report


def sample_plan(*, diameter=8, constraints=None):
    mask = np.zeros((40, 52), bool)
    mask[6:25, 6:23] = True
    mask[28:33, 37:45] = True
    image = np.full((*mask.shape, 3), (45, 60, 75), np.uint8)
    profile = FootprintProfile("test-circle", diameter, "px", Evidence("assumed", "synthetic-test"))
    plan = plan_coverage(CoverageInput(mask, "split", "synthetic", "test-fixture"), build_kernel(profile),
        policy=CoveragePolicy(max_stations=6), constraints=constraints)
    return image, plan


class CoverageVisualizationTests(unittest.TestCase):
    def test_exact_covered_uncovered_partition_in_original_pixels(self):
        image, plan = sample_plan()
        view = render_coverage(image, plan, options=RenderOptions(target=False, footprints=False,
            stations=False, route=False, alpha=1))
        self.assertEqual(view.image_rgb.shape, image.shape)
        np.testing.assert_array_equal(view.image_rgb[plan.covered_mask], np.tile(COLORS["covered"], (plan.metrics.covered_area_px, 1)))
        np.testing.assert_array_equal(view.image_rgb[plan.uncovered_mask], np.tile(COLORS["uncovered"], (plan.metrics.uncovered_area_px, 1)))
        np.testing.assert_array_equal(view.image_rgb[~plan.input.mask], image[~plan.input.mask])

    def test_playback_prefix_matches_independent_coordinate_union(self):
        image, plan = sample_plan()
        view = render_coverage(image, plan, step_count=2)
        kernel = plan.model.kernel
        pixels = set()
        for x, y in plan.points_px[:2]:
            for ky, kx in zip(*np.nonzero(kernel.mask)):
                pixels.add((x + int(kx) - kernel.anchor_px[0], y + int(ky) - kernel.anchor_px[1]))
        expected = sum((int(x), int(y)) in pixels for y, x in zip(*np.nonzero(plan.input.mask)))
        self.assertEqual(expected, view.metrics.covered_area_px)
        self.assertEqual(2, view.metrics.station_count)
        self.assertEqual(6, len(plan.stations))
        self.assertEqual(0, render_coverage(image, plan, step_count=0).metrics.covered_area_px)

    def test_no_mutation_and_layer_changes_keep_full_plan_identity(self):
        image, plan = sample_plan()
        before, plan_before = image.copy(), plan.to_dict()
        options = RenderOptions(footprints=False, stations=False, route=False)
        first = render_coverage(image, plan, options=options)
        second = render_coverage(image, plan, options=replace(options, covered=False))
        self.assertEqual(first.to_dict()["plan_id"], second.to_dict()["plan_id"])
        self.assertFalse(np.array_equal(first.image_rgb, second.image_rgb))
        np.testing.assert_array_equal(before, image)
        self.assertEqual(plan_before, plan.to_dict())
        with self.assertRaises(ValueError):
            first.image_rgb.setflags(write=True)

    def test_model_unknown_does_not_paint_predicted_coverage(self):
        image, plan = sample_plan(diameter=None)
        view = render_coverage(image, plan, options=RenderOptions(target=False))
        np.testing.assert_array_equal(image, view.image_rgb)
        self.assertIsNone(view.metrics)
        self.assertFalse(view.to_dict()["executable"])

    def test_conservative_layer_only_available_for_full_plan(self):
        constraints = CoverageConstraints(position_uncertainty=PositionUncertainty(1, "px", Evidence("assumed", "test-error")))
        image, plan = sample_plan(constraints=constraints)
        self.assertTrue(render_coverage(image, plan).to_dict()["prefix_conservative_available"])
        self.assertFalse(render_coverage(image, plan, step_count=1).to_dict()["prefix_conservative_available"])

    def test_non_circular_explicit_kernel_outline_is_used(self):
        image, plan = sample_plan()
        mask = np.array([[1, 1, 1], [1, 0, 0]], bool)
        kernel = FootprintKernel(mask, (0, 0), plan.model.profile)
        plan = plan_coverage(plan.input, kernel, policy=CoveragePolicy(max_stations=1))
        view = render_coverage(image, plan, options=RenderOptions(target=False, covered=False,
            uncovered=False, stations=False, route=False))
        x, y = plan.points_px[0]
        np.testing.assert_array_equal(COLORS["footprints"], view.image_rgb[y, x + 2])
        np.testing.assert_array_equal(image[y + 1, x + 1], view.image_rgb[y + 1, x + 1])

    def test_invalid_image_step_and_layer_options_rejected(self):
        image, plan = sample_plan()
        for bad in (image.astype(float), image[:-1], image[..., 0]):
            with self.assertRaisesRegex(ValueError, "RGB"):
                render_coverage(bad, plan)
        for step in (-1, 7, True, 1.5):
            with self.assertRaises(ValueError):
                render_coverage(image, plan, step_count=step)
        for values in ({"alpha": float("nan")}, {"alpha": True}, {"covered": 1}):
            with self.assertRaises(ValueError):
                RenderOptions(**values)

    def test_report_rejects_mismatched_plan_and_empty_target_has_no_rate(self):
        image, plan = sample_plan()
        report = render_report(plan, render_coverage(image, plan))
        self.assertEqual("RGB", report.mode)
        self.assertEqual(1000, report.width)
        other = replace(plan, status="DIFFERENT")
        with self.assertRaisesRegex(ValueError, "MISMATCH"):
            render_report(other, render_coverage(image, plan))
        empty = plan_coverage(CoverageInput(np.zeros(plan.input.mask.shape, bool), "empty", "synthetic", "fixture"), plan.model)
        self.assertIsNone(render_coverage(image, empty).metrics.geometric_coverage_rate)
