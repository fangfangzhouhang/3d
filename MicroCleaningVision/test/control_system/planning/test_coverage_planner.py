"""Offline planning: complete masks, safety footprints and an independent greedy oracle."""

import ast
import importlib.util
import json
import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

HAS_DEPS = all(importlib.util.find_spec(name) is not None for name in ("numpy", "cv2"))
if HAS_DEPS:
    import numpy as np
    from microcleaning.control_system.planning import coverage_model, coverage_metrics, coverage_planner
    from microcleaning.control_system.planning.coverage_model import Evidence, FootprintKernel, FootprintProfile, PixelScale, build_kernel
    from microcleaning.control_system.planning.coverage_planner import (
        CoverageConstraints, CoverageInput, CoveragePolicy, PositionUncertainty, plan_coverage, prepare_coverage_domain,
    )


def greedy_oracle(target, kernel, policy, *, centers=None, spray=None, forbidden=None, error_box=None):
    """Pure coordinate sets. No convolution, erosion, planner helper or component selection."""
    h, w = target.shape
    remaining = {(int(x), int(y)) for y, x in zip(*np.nonzero(target))}
    footprint = {(int(x)-kernel.anchor_px[0], int(y)-kernel.anchor_px[1]) for y, x in zip(*np.nonzero(kernel.mask))}
    outer = footprint
    inner = footprint
    if error_box is not None:
        bx, by = error_box
        shifted = [{(x+dx, y+dy) for x, y in footprint}
            for dy in range(-by, by+1) for dx in range(-bx, bx+1)]
        outer = set.union(*shifted)
        inner = set.intersection(*shifted)
    candidates = set()
    for y in range(0, h, policy.candidate_stride_px):
        for x in range(0, w, policy.candidate_stride_px):
            if centers is not None and not centers[y, x]:
                continue
            if all(0 <= x+dx < w and 0 <= y+dy < h
                    and (spray is None or spray[y+dy, x+dx])
                    and (forbidden is None or not forbidden[y+dy, x+dx]) for dx, dy in outer):
                candidates.add((x, y))
    objective = footprint if policy.coverage_basis == "nominal" else inner
    total = len(remaining)
    points = []
    previous = policy.start_px
    while candidates and len(points) < policy.max_stations:
        scored = []
        for x, y in candidates:
            gain = remaining & {(x+dx, y+dy) for dx, dy in objective}
            scored.append((-len(gain), (x-previous[0])**2+(y-previous[1])**2, y, x, gain))
        best = min(scored, key=lambda item: item[:4])
        if best[0] == 0:
            break
        point = (best[3], best[2])
        points.append(point)
        candidates.remove(point)
        remaining -= best[4]
        previous = point
        if 1 - len(remaining)/total >= policy.target_coverage:
            break
    return tuple(points)


@unittest.skipUnless(HAS_DEPS, "需要 requirements/perception-opencv.txt")
class CoveragePlannerTests(unittest.TestCase):
    def evidence(self):
        return Evidence("assumed", "test:explicit-simulation")

    def model(self, diameter=2, scale=None):
        return build_kernel(FootprintProfile("virtual-circle", diameter, "px", self.evidence()), scale)

    def input(self, mask, kind="synthetic"):
        return CoverageInput(mask, "T001", kind, "test:full-mask", frame_id="F001")

    def test_shape_fixtures_include_all_disconnected_residuals(self):
        circle = np.zeros((17, 19), dtype=bool)
        yy, xx = np.ogrid[:17, :19]
        circle[:] = (xx-9)**2 + (yy-8)**2 <= 9
        strip = np.zeros_like(circle)
        strip[8, 3:16] = True
        concave = np.zeros_like(circle)
        concave[4:12, 4] = True
        concave[11, 4:14] = True
        split = np.zeros_like(circle)
        split[4:8, 4:8] = True
        split[13, 16] = True  # A much smaller residual must not disappear.
        for name, target in (("circle", circle), ("strip", strip), ("concave", concave), ("split", split)):
            with self.subTest(name=name):
                plan = plan_coverage(self.input(target), self.model(), policy=CoveragePolicy(target_coverage=1))
                self.assertEqual("COMPLETE", plan.status)
                self.assertEqual(int(target.sum()), plan.metrics.target_area_px)
                np.testing.assert_array_equal(target, plan.covered_mask)
                self.assertEqual(0, plan.metrics.uncovered_area_px)
                if name == "split":
                    self.assertTrue(plan.covered_mask[13, 16])

    def test_edge_mask_can_be_explicitly_unreachable(self):
        target = np.zeros((7, 7), dtype=bool)
        target[0, 0] = True
        plan = plan_coverage(self.input(target), self.model())
        self.assertEqual("PARTIAL", plan.status)
        self.assertEqual(("NO_ADDITIONAL_COVERAGE",), plan.reason_codes)
        self.assertEqual(0, plan.metrics.geometric_coverage_rate)
        self.assertTrue(plan.uncovered_mask[0, 0])

    def test_center_outside_stain_can_be_optimal(self):
        target = np.zeros((5, 5), dtype=bool)
        target[2, 1] = target[2, 3] = True
        plan = plan_coverage(self.input(target), self.model(), policy=CoveragePolicy(target_coverage=1))
        self.assertEqual(((2, 2),), plan.points_px)
        self.assertFalse(target[2, 2])

    def test_full_footprint_must_fit_spray_domain(self):
        target = np.zeros((7, 7), dtype=bool)
        target[3, 3] = True
        spray = np.zeros_like(target)
        spray[3, 3] = True
        plan = plan_coverage(self.input(target), self.model(),
            constraints=CoverageConstraints(allowed_spray_mask=spray, evidence=self.evidence()))
        self.assertEqual(("NO_FEASIBLE_CANDIDATE",), plan.reason_codes)
        self.assertEqual(0, plan.candidate_count)
        self.assertEqual(0, plan.metrics.covered_area_px)

    def test_forbidden_and_center_constraints_both_apply(self):
        target = np.zeros((9, 9), dtype=bool)
        target[4, 4] = True
        centers = np.zeros_like(target)
        centers[4, 4] = True
        forbidden = np.zeros_like(target)
        forbidden[4, 5] = True  # Center is allowed but its spray touches a forbidden pixel.
        constraints = CoverageConstraints(centers, np.ones_like(target), forbidden, self.evidence())
        blocked = plan_coverage(self.input(target), self.model(), constraints=constraints)
        self.assertEqual(0, blocked.candidate_count)
        forbidden[:] = False
        unblocked = plan_coverage(self.input(target), self.model(),
            constraints=CoverageConstraints(centers, np.ones_like(target), forbidden, self.evidence()))
        self.assertEqual(((4, 4),), unblocked.points_px)
        self.assertEqual(1, unblocked.candidate_count)

    def test_position_envelope_reduces_candidates_and_conservative_coverage(self):
        target = np.ones((11, 11), dtype=bool)
        policy = CoveragePolicy(max_stations=1)
        nominal = plan_coverage(self.input(target), self.model(6), policy=policy)
        constraints = CoverageConstraints(position_uncertainty=PositionUncertainty(0.2, "px", self.evidence()))
        robust = plan_coverage(self.input(target), self.model(6), policy=policy, constraints=constraints)
        self.assertLess(robust.candidate_count, nominal.candidate_count)
        self.assertLess(robust.metrics.conservative_coverage_rate, robust.metrics.geometric_coverage_rate)
        self.assertIsNone(nominal.metrics.conservative_coverage_rate)
        self.assertIn("ASSUMED_POSITION_UNCERTAINTY", robust.warnings)

    def test_conservative_goal_does_not_stop_on_nominal_coverage(self):
        target = np.zeros((15, 15), dtype=bool)
        target[5:10, 5:10] = True
        constraints = CoverageConstraints(position_uncertainty=PositionUncertainty(1, "px", self.evidence()))
        nominal = plan_coverage(self.input(target), self.model(6),
            policy=CoveragePolicy(target_coverage=1), constraints=constraints)
        robust = plan_coverage(self.input(target), self.model(6),
            policy=CoveragePolicy(target_coverage=1, coverage_basis="conservative"), constraints=constraints)
        self.assertGreater(len(robust.stations), len(nominal.stations))
        self.assertEqual(1, robust.metrics.conservative_coverage_rate)
        self.assertEqual(robust.points_px, greedy_oracle(target, self.model(6).kernel,
            robust.policy, error_box=(1, 1)))

    def test_zero_explicit_error_equals_nominal_coverage(self):
        target = np.ones((7, 7), dtype=bool)
        result = plan_coverage(self.input(target), self.model(),
            constraints=CoverageConstraints(position_uncertainty=PositionUncertainty(0, "px", self.evidence())))
        self.assertEqual(result.metrics.geometric_coverage_rate, result.metrics.conservative_coverage_rate)

    def test_conservative_or_mm_error_without_scale_is_unavailable(self):
        target = np.ones((7, 7), dtype=bool)
        conservative = plan_coverage(self.input(target), self.model(), policy=CoveragePolicy(coverage_basis="conservative"))
        self.assertEqual("CONSTRAINTS_UNAVAILABLE", conservative.status)
        self.assertIsNone(conservative.metrics)
        mm_error = plan_coverage(self.input(target), self.model(),
            constraints=CoverageConstraints(position_uncertainty=PositionUncertainty(1, "mm", self.evidence())))
        self.assertEqual(("POSITION_PIXEL_SCALE_MISSING",), mm_error.reason_codes)
        self.assertIsNone(mm_error.metrics)

    def test_mm_error_is_converted_on_both_axes(self):
        target = np.zeros((17, 19), dtype=bool)
        target[5:12, 6:13] = True
        scale = PixelScale(1, 2, self.evidence(), target.shape)
        model = self.model(6, scale)
        policy = CoveragePolicy(target_coverage=1, coverage_basis="conservative")
        plan = plan_coverage(self.input(target), model, policy=policy,
            constraints=CoverageConstraints(position_uncertainty=PositionUncertainty(1.2, "mm", self.evidence())))
        self.assertEqual(plan.points_px, greedy_oracle(target, model.kernel, policy, error_box=(2, 1)))

    def test_greedy_and_asymmetric_anchor_match_independent_oracle(self):
        rng = np.random.default_rng(1009)
        kernel = FootprintKernel(np.array([[1, 0, 1], [1, 1, 0]], dtype=bool), (1, 0), self.model().profile)
        policy = CoveragePolicy(target_coverage=0.9, max_stations=8, start_px=(3.5, 4))
        for index in range(25):
            target = rng.random((8, 9)) > 0.7
            centers = rng.random(target.shape) > 0.2
            forbidden = rng.random(target.shape) > 0.97
            constraints = CoverageConstraints(allowed_centers_mask=centers, forbidden_mask=forbidden, evidence=self.evidence())
            expected = greedy_oracle(target, kernel, policy, centers=centers, forbidden=forbidden)
            with self.subTest(index=index):
                plan = plan_coverage(self.input(target), kernel, policy=policy, constraints=constraints)
                self.assertEqual(expected, plan.points_px)
                self.assertEqual(sum(s.newly_covered_area_px for s in plan.stations), plan.metrics.covered_area_px)

    def test_ties_choose_nearest_then_row_then_column(self):
        target = np.zeros((7, 7), dtype=bool)
        target[2, 2] = target[2, 4] = target[4, 2] = target[4, 4] = True
        policy = CoveragePolicy(target_coverage=1, start_px=(3, 3))
        plan = plan_coverage(self.input(target), self.model(0.1), policy=policy)
        self.assertEqual(((2, 2), (4, 2), (4, 4), (2, 4)), plan.points_px)
        self.assertAlmostEqual(2**0.5 + 6, plan.route_length_px)

    def test_asymmetric_error_envelope_matches_coordinate_oracle(self):
        rng = np.random.default_rng(823)
        policy = CoveragePolicy(target_coverage=0.85, max_stations=8, coverage_basis="conservative")
        for index in range(16):
            kmask = rng.random((4, 5)) > 0.15
            kmask[1:3, 1:4] = True
            kernel = FootprintKernel(kmask, (index % 5, index % 4), self.model().profile)
            target = rng.random((11, 12)) > 0.6
            constraints = CoverageConstraints(position_uncertainty=PositionUncertainty(1, "px", self.evidence()))
            with self.subTest(index=index):
                plan = plan_coverage(self.input(target), kernel, policy=policy, constraints=constraints)
                self.assertEqual(greedy_oracle(target, kernel, policy, error_box=(1, 1)), plan.points_px)
                self.assertLessEqual(plan.metrics.conservative_coverage_rate, plan.metrics.geometric_coverage_rate)

    def test_partial_reasons_distinguish_budget_and_candidate_exhaustion(self):
        target = np.zeros((7, 7), dtype=bool)
        target[2, 2] = target[4, 4] = True
        budget = plan_coverage(self.input(target), self.model(0.1), policy=CoveragePolicy(target_coverage=1, max_stations=1))
        self.assertEqual(("MAX_STATIONS_REACHED",), budget.reason_codes)
        centers = np.zeros_like(target)
        centers[2, 2] = True
        exhausted = plan_coverage(self.input(target), self.model(0.1), policy=CoveragePolicy(target_coverage=1),
            constraints=CoverageConstraints(allowed_centers_mask=centers, evidence=self.evidence()))
        self.assertEqual(("CANDIDATES_EXHAUSTED",), exhausted.reason_codes)
        self.assertEqual(0.5, exhausted.metrics.geometric_coverage_rate)

    def test_empty_and_unavailable_models_do_not_fabricate_metrics(self):
        empty = plan_coverage(self.input(np.zeros((5, 5), dtype=bool)), self.model())
        self.assertEqual("NO_TARGET", empty.status)
        self.assertIsNone(empty.metrics.geometric_coverage_rate)
        unknown = plan_coverage(self.input(np.ones((5, 5), dtype=bool)), self.model(None))
        self.assertEqual("MODEL_UNAVAILABLE", unknown.status)
        self.assertIsNone(unknown.metrics)
        self.assertIsNone(unknown.covered_mask)
        self.assertEqual(("FOOTPRINT_DIAMETER_MISSING",), unknown.reason_codes)

    def test_manual_roi_semantics_are_not_stain_area(self):
        mask = np.ones((7, 7), dtype=bool)
        for kind, area_kind in (("manual_region", "manual_region_px"), ("manual_roi_segmented", "target_mask_px"),
                ("segmented_stain", "target_mask_px")):
            with self.subTest(kind=kind):
                result = plan_coverage(self.input(mask, kind), self.model())
                self.assertEqual(kind, result.to_dict()["input"]["mask_kind"])
                self.assertEqual(area_kind, result.to_dict()["input"]["area_kind"])

    def test_snapshot_and_result_do_not_mutate_inputs(self):
        mask = np.zeros((7, 7), dtype=np.uint8)
        mask[2:5, 2:5] = 255
        source = self.input(mask)
        original = source.sha256
        mask[:] = 0
        plan = plan_coverage(source, self.model())
        self.assertEqual(9, plan.metrics.target_area_px)
        self.assertEqual(original, source.sha256)
        with self.assertRaises(ValueError):
            source.mask.setflags(write=True)
        with self.assertRaises(FrozenInstanceError):
            plan.executable = True
        with self.assertRaises((TypeError, ValueError)):
            replace(plan, executable=True)

    def test_json_is_deterministic_and_always_nonexecutable(self):
        target = np.eye(9, dtype=bool)
        first = plan_coverage(self.input(target), self.model())
        second = plan_coverage(self.input(target.astype(np.uint8)*255), self.model())
        self.assertEqual(first.to_dict(), second.to_dict())
        serialized = json.dumps(first.to_dict(), allow_nan=False)
        result = json.loads(serialized)
        for name in ("executable", "feeds_action_request", "send_to_controller"):
            self.assertIs(result[name], False)
        resized = plan_coverage(self.input(target), self.model(4))
        self.assertNotEqual(first.to_dict()["plan_id"], resized.to_dict()["plan_id"])

    def test_default_domains_remain_assumed_with_other_measured_evidence(self):
        result = plan_coverage(self.input(np.eye(7, dtype=bool)), self.model(),
            constraints=CoverageConstraints(evidence=Evidence("measured", "other-mask-evidence")))
        self.assertIn("SPRAY_DOMAIN_IMAGE_EXTENT_ASSUMED", result.warnings)
        self.assertIn("CENTER_DOMAIN_IMAGE_EXTENT_ASSUMED", result.warnings)
        self.assertIn("SAFETY_NOT_MEASURED", result.warnings)
        self.assertIn("POSITION_UNCERTAINTY_UNKNOWN", result.warnings)

    def test_policy_masks_and_shape_errors_fail_explicitly(self):
        for kwargs in ({"target_coverage": 0}, {"target_coverage": 1.1}, {"max_stations": True},
                {"max_stations": 257}, {"candidate_stride_px": 0}, {"start_px": (-1, 0)}, {"coverage_basis": "actual"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CoveragePolicy(**kwargs)
        with self.assertRaises(ValueError):
            self.input(np.full((5, 5), 2, dtype=np.uint8))
        with self.assertRaises(ValueError):
            CoverageConstraints(allowed_spray_mask=np.ones((5, 5), dtype=bool))
        with self.assertRaisesRegex(ValueError, "CONSTRAINT_IMAGE_SHAPE_MISMATCH"):
            plan_coverage(self.input(np.eye(7, dtype=bool)), self.model(),
                constraints=CoverageConstraints(forbidden_mask=np.eye(5, dtype=bool), evidence=self.evidence()))
        with self.assertRaisesRegex(ValueError, "SCALE_IMAGE_SHAPE_MISMATCH"):
            plan_coverage(self.input(np.eye(7, dtype=bool)), self.model(scale=PixelScale(1, 1, self.evidence(), (5, 5))))
        with self.assertRaisesRegex(ValueError, "START_OUTSIDE_IMAGE"):
            plan_coverage(self.input(np.eye(7, dtype=bool)), self.model(), policy=CoveragePolicy(start_px=(1e308, 0)))

    def test_resource_limits_return_unknown_coverage(self):
        target = np.ones((501, 501), dtype=bool)
        limited = plan_coverage(self.input(target), self.model(0.1))
        self.assertEqual(("CANDIDATE_RESOURCE_LIMIT",), limited.reason_codes)
        self.assertIsNone(limited.metrics)
        coarse = plan_coverage(self.input(target), self.model(0.1), policy=CoveragePolicy(candidate_stride_px=5, max_stations=1))
        self.assertEqual("PARTIAL", coarse.status)
        self.assertIsNotNone(coarse.metrics)
        envelope = plan_coverage(self.input(np.eye(7, dtype=bool)), self.model(),
            constraints=CoverageConstraints(position_uncertainty=PositionUncertainty(1e20, "px", self.evidence())))
        self.assertEqual(("ERROR_ENVELOPE_RESOURCE_LIMIT",), envelope.reason_codes)
        self.assertIsNone(envelope.metrics)
        with self.assertRaisesRegex(ValueError, "INPUT_IMAGE_RESOURCE_LIMIT"):
            self.input(np.zeros((1001, 1000), dtype=bool))

    def test_no_hardware_io_and_legacy_plan_remains_unchanged(self):
        from microcleaning.control_system.planning.cleaning_plan import plan_cleaning, cleaning_plan_to_dict
        target = np.zeros((100, 100), dtype=np.uint8)
        target[40:45, 40:45] = 255
        before = cleaning_plan_to_dict(plan_cleaning(target))
        serial_stub = ModuleType("serial")
        serial_stub.Serial = Mock(side_effect=AssertionError("offline must not open serial"))
        with patch.dict(sys.modules, {"serial": serial_stub}), \
                patch("cv2.VideoCapture", side_effect=AssertionError("offline must not open camera")):
            result = plan_coverage(self.input(target), self.model())
            self.assertFalse(result.executable)
            serial_stub.Serial.assert_not_called()
        self.assertEqual(before, cleaning_plan_to_dict(plan_cleaning(target)))
        for module in (coverage_model, coverage_metrics, coverage_planner):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
            self.assertFalse(any(any(term in name for term in ("serial", "orchestration", "workbench", "firmware")) for name in imports))

    def test_shared_domain_matches_independent_full_footprint_error_enumeration(self):
        kernel = FootprintKernel(np.array([[1, 0, 1], [1, 1, 0]], bool), (2, 0), self.model().profile)
        target = np.ones((9, 11), bool)
        forbidden = np.zeros_like(target)
        forbidden[4, 5] = True
        constraints = CoverageConstraints(forbidden_mask=forbidden, evidence=self.evidence(),
            position_uncertainty=PositionUncertainty(1, "px", self.evidence()))
        policy = CoveragePolicy(candidate_stride_px=2)
        domain = prepare_coverage_domain(self.input(target), kernel, policy=policy, constraints=constraints)
        offsets = {(x-2+dx, y+dy) for y, x in zip(*np.nonzero(kernel.mask))
            for dy in (-1, 0, 1) for dx in (-1, 0, 1)}
        expected = np.zeros_like(target)
        for y in range(9):
            for x in range(11):
                expected[y, x] = all(0 <= x+dx < 11 and 0 <= y+dy < 9 and not forbidden[y+dy, x+dx]
                    for dx, dy in offsets)
        np.testing.assert_array_equal(expected, domain.feasible_centers_mask)
        grid = np.zeros_like(target)
        grid[::2, ::2] = True
        np.testing.assert_array_equal(expected & grid, domain.candidate_centers_mask)
        plan = plan_coverage(self.input(target), kernel, policy=policy, constraints=constraints)
        self.assertEqual(domain.candidate_count, plan.candidate_count)
        for array in (domain.feasible_centers_mask, domain.candidate_centers_mask, domain.conservative_kernel_mask):
            with self.assertRaises(ValueError):
                array.setflags(write=True)

    def test_shared_domain_unknown_and_resource_limits_do_not_invent_masks(self):
        target = self.input(np.eye(9, dtype=bool))
        for model, policy, constraints, status in (
                (self.model(None), CoveragePolicy(), None, "MODEL_UNAVAILABLE"),
                (self.model(), CoveragePolicy(coverage_basis="conservative"), None, "CONSTRAINTS_UNAVAILABLE"),
                (self.model(), CoveragePolicy(), CoverageConstraints(position_uncertainty=PositionUncertainty(1e20, "px", self.evidence())), "RESOURCE_LIMIT")):
            with self.subTest(status=status):
                domain = prepare_coverage_domain(target, model, policy=policy, constraints=constraints)
                self.assertEqual(status, domain.status)
                self.assertIsNone(domain.candidate_centers_mask)
                self.assertIsNone(domain.feasible_centers_mask)
                self.assertIsNone(domain.conservative_kernel_mask)


if __name__ == "__main__":
    unittest.main()
