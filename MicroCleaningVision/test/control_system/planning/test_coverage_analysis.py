"""P5 controlled comparisons with independent pixel-set checks and failure cases."""

import ast
import importlib.util
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from itertools import count
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

HAS_DEPS = all(importlib.util.find_spec(v) is not None for v in ("numpy", "cv2"))
if HAS_DEPS:
    import numpy as np
    from microcleaning.control_system.planning import coverage_analysis
    from microcleaning.control_system.planning.coverage_analysis import METHODS, AnalysisSpec, analyze_coverage
    from microcleaning.control_system.planning.coverage_model import Evidence, FootprintKernel, FootprintProfile, PixelScale, SprayConditions, build_kernel
    from microcleaning.control_system.planning.coverage_planner import CoverageConstraints, CoverageInput, PositionUncertainty


@unittest.skipUnless(HAS_DEPS, "需要 requirements/perception-opencv.txt")
class CoverageAnalysisTests(unittest.TestCase):
    def evidence(self):
        return Evidence("assumed", "test:explicit-analysis")

    def model(self, diameter=4, scale=None):
        return build_kernel(FootprintProfile("virtual", diameter, "px", self.evidence()), scale)

    def source(self, mask, kind="synthetic"):
        return CoverageInput(mask, "S0001", kind, "test:complete-target", frame_id="F001")

    def row(self, result, method="COVERAGE_AWARE", phase="comparison", value=None, budget=None):
        return next(r for r in result.runs if r.method == method and r.phase == phase
            and r.parameter_value == value and (budget is None or r.policy.max_stations == budget))

    def test_all_methods_use_same_full_mask_kernel_domain_goal_and_budget(self):
        mask = np.zeros((17, 23), bool)
        mask[5:10, 4:9] = True
        mask[12, 19] = True
        result = analyze_coverage(self.source(mask), self.model(), spec=AnalysisSpec(station_budgets=(1, 4), target_coverage=1))
        self.assertEqual(6, len(result.runs))
        for budget in (1, 4):
            rows = [r for r in result.runs if r.policy.max_stations == budget]
            self.assertEqual(set(METHODS), {r.method for r in rows})
            self.assertEqual(1, len({r.model.kernel.sha256 for r in rows}))
            self.assertEqual(1, len({r.constraints_json for r in rows}))
            self.assertEqual(1, len({r.candidate_count for r in rows}))
            for r in rows:
                self.assertEqual(26, r.metrics.target_area_px)
                self.assertEqual((0., 0.), r.policy.start_px)
                self.assertEqual(1, r.policy.target_coverage)
                self.assertLessEqual(len(r.points_px), budget)

    def test_center_baseline_has_one_center_per_component_not_global_centroid(self):
        mask = np.zeros((19, 29), bool)
        mask[3:6, 3:6] = True
        mask[3:6, 19:22] = True
        mask[13, 24] = True
        result = analyze_coverage(self.source(mask), self.model(0.1), spec=AnalysisSpec(station_budgets=(8,), target_coverage=1))
        center = self.row(result, "CENTER_POINT_COMPONENTS")
        self.assertEqual(((4, 4), (20, 4), (24, 13)), center.points_px)
        self.assertEqual(19, center.metrics.target_area_px)
        self.assertTrue(center.evaluation.covered_mask[13, 24])
        self.assertEqual(3, center.metrics.covered_area_px)

    def test_raster_is_forced_discrete_legacy_row_sampling(self):
        mask = np.zeros((50, 50), bool)
        mask[4:8, 4:10] = True
        result = analyze_coverage(self.source(mask), self.model(0.1), spec=AnalysisSpec(station_budgets=(16,), target_coverage=1, raster_step_px=2))
        raster = self.row(result, "RASTER_SCAN_DWELL")
        self.assertEqual("CENTER_POINT", raster.legacy_auto_strategy)
        self.assertEqual(((4, 4), (6, 4), (8, 4), (9, 4), (9, 6), (8, 6), (6, 6), (4, 6)), raster.points_px)
        self.assertEqual(8, raster.metrics.covered_area_px)
        self.assertEqual(16, raster.metrics.uncovered_area_px)
        self.assertEqual("forced_geometry_filtered_dwell", raster.to_dict()["baseline_semantics"])

    def test_union_matches_independent_stamped_coordinate_sets(self):
        mask = np.zeros((11, 13), bool)
        mask[3:9, 3:10] = True
        kernel = FootprintKernel(np.array([[1, 0, 1], [1, 1, 0]], bool), (1, 0), self.model().profile)
        result = analyze_coverage(self.source(mask), kernel, spec=AnalysisSpec(station_budgets=(4,), target_coverage=1, raster_step_px=2))
        target = {(x, y) for y, x in zip(*np.nonzero(mask))}
        offsets = {(x-1, y) for y, x in zip(*np.nonzero(kernel.mask))}
        for row in result.runs:
            union, counts = set(), {}
            for x, y in row.points_px:
                for dx, dy in offsets:
                    p = (x+dx, y+dy)
                    union.add(p)
                    counts[p] = counts.get(p, 0)+1
            covered = target & union
            self.assertEqual(len(covered), row.metrics.covered_area_px)
            self.assertEqual(len(target-union), row.metrics.uncovered_area_px)
            self.assertEqual(sum(counts[p] >= 2 for p in target if p in counts), row.metrics.overlap_target_area_px)
            self.assertEqual(covered, {(x, y) for y, x in zip(*np.nonzero(row.evaluation.covered_mask))})

    def test_illegal_center_is_reported_and_never_moved_to_a_better_point(self):
        mask = np.zeros((13, 13), bool)
        mask[4:9, 4:9] = True
        centers = np.ones_like(mask)
        centers[6, 6] = False
        constraints = CoverageConstraints(allowed_centers_mask=centers, evidence=self.evidence())
        result = analyze_coverage(self.source(mask), self.model(), constraints=constraints, spec=AnalysisSpec(station_budgets=(8,), target_coverage=1))
        center = self.row(result, "CENTER_POINT_COMPONENTS")
        self.assertEqual(((6, 6),), center.proposed_points_px)
        self.assertEqual((), center.points_px)
        self.assertEqual("FULL_FOOTPRINT_OR_CENTER_DOMAIN_VIOLATION", center.rejected_proposals[0].reason)
        self.assertEqual(0, center.metrics.covered_area_px)
        self.assertGreater(center.raw_prefix_metrics.covered_area_px, 0)
        self.assertTrue(center.to_dict()["raw_prefix_is_audit_not_ranked"])

    def test_candidate_grid_mismatch_is_distinguished_from_geometric_violation(self):
        mask = np.zeros((13, 13), bool)
        mask[5, 5] = True
        result = analyze_coverage(self.source(mask), self.model(0.1), spec=AnalysisSpec(station_budgets=(2,), candidate_stride_px=2))
        center = self.row(result, "CENTER_POINT_COMPONENTS")
        self.assertEqual("CANDIDATE_GRID_MISMATCH", center.rejected_proposals[0].reason)
        self.assertEqual(0, center.metrics.geometric_coverage_rate)

    def test_fixed_diameter_keeps_original_points_when_they_become_unsafe(self):
        mask = np.zeros((9, 9), bool)
        mask[4, 4] = True
        original = self.model(2)
        result = analyze_coverage(self.source(mask), original, spec=AnalysisSpec(station_budgets=(4,), diameters=(10,)))
        base = self.row(result)
        fixed = self.row(result, phase="diameter_fixed", value=10)
        replanned = self.row(result, phase="diameter_replan", value=10)
        self.assertEqual(base.points_px, fixed.points_px)
        self.assertEqual("CONSTRAINT_VIOLATION", fixed.status)
        self.assertFalse(fixed.qualifies_for_comparison)
        self.assertEqual(1, fixed.metrics.geometric_coverage_rate)
        self.assertEqual((), replanned.points_px)
        self.assertEqual(0, replanned.metrics.geometric_coverage_rate)
        self.assertEqual(base.to_dict()["run_id"], fixed.source_run_id)
        self.assertEqual(2, original.profile.diameter)
        self.assertEqual(original.profile.sha256, fixed.model.profile.parent_sha256)

    def test_explicit_non_circle_kernel_is_not_silently_replaced_for_diameter_sweep(self):
        kernel = FootprintKernel(np.array([[1, 1, 0], [0, 1, 1]], bool), (2, 0), self.model().profile)
        source = self.source(np.eye(11, dtype=bool))
        with self.assertRaisesRegex(ValueError, "DIAMETER_SWEEP_REQUIRES_CIRCLE_FACTORY_KERNEL"):
            analyze_coverage(source, kernel, spec=AnalysisSpec(station_budgets=(4,), diameters=(6,)))
        result = analyze_coverage(source, kernel, spec=AnalysisSpec(station_budgets=(4,), position_errors=(1,)))
        self.assertTrue(all(r.model.kernel.sha256 == kernel.sha256 for r in result.runs))

    def test_fixed_position_error_nominal_rate_stays_same_but_conservative_changes(self):
        mask = np.zeros((15, 15), bool)
        mask[5:10, 5:10] = True
        result = analyze_coverage(self.source(mask), self.model(4), spec=AnalysisSpec(station_budgets=(1,), position_errors=(0, 1)))
        zero = self.row(result, "CENTER_POINT_COMPONENTS", "position_error_fixed", 0)
        one = self.row(result, "CENTER_POINT_COMPONENTS", "position_error_fixed", 1)
        self.assertEqual(zero.points_px, one.points_px)
        self.assertEqual(13, zero.metrics.covered_area_px)
        self.assertEqual(13, one.metrics.covered_area_px)
        self.assertEqual(13, zero.metrics.conservative_covered_area_px)
        self.assertEqual(1, one.metrics.conservative_covered_area_px)
        self.assertFalse(one.constraint_violations)

    def test_mm_error_without_scale_remains_unavailable(self):
        result = analyze_coverage(self.source(np.eye(11, dtype=bool)), self.model(),
            spec=AnalysisSpec(station_budgets=(2,), position_errors=(1,), position_error_unit="mm"))
        for row in result.runs:
            if row.phase != "comparison":
                self.assertEqual("CONSTRAINTS_UNAVAILABLE", row.status)
                self.assertIn("POSITION_PIXEL_SCALE_MISSING", row.reason_codes)
                self.assertIsNone(row.metrics)
                if row.phase == "position_error_fixed":
                    self.assertEqual(self.row(result, row.method).points_px, row.points_px)

    def test_fixed_points_are_retained_when_new_kernel_is_unavailable(self):
        mask = np.zeros((15, 15), bool)
        mask[7, 7] = True
        result = analyze_coverage(self.source(mask), self.model(),
            spec=AnalysisSpec(station_budgets=(2,), diameters=(1e6,)))
        for method in METHODS:
            base = self.row(result, method)
            fixed = self.row(result, method, "diameter_fixed", 1e6)
            self.assertTrue(base.points_px)
            self.assertEqual(base.points_px, fixed.points_px)
            self.assertEqual(base.points_px, fixed.proposed_points_px)
            self.assertEqual("MODEL_UNAVAILABLE", fixed.status)
            self.assertIsNone(fixed.metrics)
            self.assertFalse(fixed.qualifies_for_comparison)

    def test_oversized_raster_is_rejected_before_legacy_route_generation(self):
        with patch.object(coverage_analysis, "plan_cleaning", side_effect=AssertionError("unbounded route")):
            result = analyze_coverage(self.source(np.ones((65, 65), bool)), self.model(0.1),
                spec=AnalysisSpec(station_budgets=(1,), raster_step_px=1))
        raster = self.row(result, "RASTER_SCAN_DWELL")
        self.assertEqual("RASTER_SCAN", raster.legacy_auto_strategy)
        self.assertEqual("RESOURCE_LIMIT", raster.status)
        self.assertEqual(("BASELINE_POINT_RESOURCE_LIMIT",), raster.reason_codes)
        self.assertIsNone(raster.metrics)
        self.assertEqual((), raster.proposed_points_px)

    def test_measured_profile_remains_measured_only_in_original_scenario(self):
        conditions = SprayConditions("N1", 4, "water", "PLA", 200, "fixed_supply")
        profile = FootprintProfile("wet", 4, "mm", Evidence("measured", "test:wet-footprint-record"), conditions, semantics="wet_footprint")
        scale = PixelScale(1, 2, self.evidence(), (15, 19))
        model = build_kernel(profile, scale)
        result = analyze_coverage(self.source(np.eye(15, 19, dtype=bool)), model,
            spec=AnalysisSpec(station_budgets=(4,), diameters=(6,)))
        self.assertEqual("measured", self.row(result).model.profile.evidence.status)
        scenario = self.row(result, phase="diameter_replan", value=6)
        self.assertEqual("assumed", scenario.model.profile.evidence.status)
        self.assertEqual(profile.sha256, scenario.model.profile.parent_sha256)
        self.assertEqual((5, 7), scenario.model.kernel.mask.shape)
        self.assertEqual("measured", profile.evidence.status)

    def test_unknown_model_and_unavailable_fixed_reference_have_no_fake_metrics(self):
        result = analyze_coverage(self.source(np.eye(11, dtype=bool)), self.model(None),
            spec=AnalysisSpec(station_budgets=(2,), diameters=(2,)))
        for row in result.runs:
            if row.phase == "comparison":
                self.assertEqual("MODEL_UNAVAILABLE", row.status)
                self.assertIsNone(row.metrics)
            elif row.phase == "diameter_fixed":
                self.assertEqual("REFERENCE_UNAVAILABLE", row.status)
                self.assertIsNone(row.metrics)
            else:
                self.assertIsNotNone(row.metrics)

    def test_empty_and_manual_region_semantics_are_preserved(self):
        for mask, kind in ((np.zeros((11, 11), bool), "synthetic"), (np.eye(11, dtype=bool), "manual_region")):
            result = analyze_coverage(self.source(mask, kind), self.model(), spec=AnalysisSpec(station_budgets=(2,)))
            self.assertEqual(kind, result.input.mask_kind)
            if kind == "synthetic":
                self.assertTrue(all(r.status == "NO_TARGET" and r.metrics.geometric_coverage_rate is None for r in result.runs))
            else:
                self.assertEqual("manual_region_px", result.to_dict()["input"]["area_kind"])

    def test_timing_is_excluded_from_ids_and_sources_remain_immutable(self):
        source, model = self.source(np.eye(11, dtype=bool)), self.model()
        original = source.sha256
        spec = AnalysisSpec(station_budgets=(2,))
        first = analyze_coverage(source, model, spec=spec)
        ticks = count()
        with patch.object(coverage_analysis.time, "perf_counter", side_effect=lambda: next(ticks) * 0.25):
            second = analyze_coverage(source, model, spec=spec)
        self.assertEqual(first.to_dict(include_timing=False), second.to_dict(include_timing=False))
        self.assertNotEqual(first.to_dict()["elapsed_seconds"], second.to_dict()["elapsed_seconds"])
        self.assertEqual(original, source.sha256)
        with self.assertRaises(ValueError):
            second.runs[0].evaluation.covered_mask.setflags(write=True)
        with self.assertRaises(FrozenInstanceError):
            second.executable = True
        with self.assertRaises((TypeError, ValueError)):
            replace(second.runs[0], executable=True)
        json.dumps(second.to_dict(), allow_nan=False)

    def test_invalid_scenarios_and_resource_limits_fail_before_planning(self):
        for kwargs in ({"station_budgets": ()}, {"station_budgets": (2, 1)}, {"station_budgets": (1, 1)},
                {"station_budgets": (True,)}, {"diameters": (0,)}, {"diameters": (4, 2)},
                {"position_errors": (-1,)}, {"position_error_unit": "steps"},
                {"station_budgets": tuple(range(1, 100)), "diameters": (1,)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                AnalysisSpec(**kwargs)
        with patch.object(coverage_analysis, "plan_coverage") as planner:
            with self.assertRaisesRegex(ValueError, "ANALYSIS_MASK_RESOURCE_LIMIT"):
                analyze_coverage(self.source(np.ones((501, 501), bool)), self.model(),
                    spec=AnalysisSpec(station_budgets=(1, 4, 8, 16, 32, 64), diameters=(2, 4, 6), position_errors=(0, 1, 2)))
            planner.assert_not_called()

    def test_core_cannot_open_camera_serial_or_write_files(self):
        serial = ModuleType("serial")
        serial.Serial = Mock(side_effect=AssertionError("offline"))
        with patch.dict("sys.modules", {"serial": serial}), patch("cv2.VideoCapture", side_effect=AssertionError("offline")), \
                patch.object(Path, "write_text", side_effect=AssertionError("pure algorithm")):
            result = analyze_coverage(self.source(np.eye(11, dtype=bool)), self.model(), spec=AnalysisSpec(station_budgets=(4,)))
            self.assertFalse(result.executable)
            serial.Serial.assert_not_called()
        tree = ast.parse(Path(coverage_analysis.__file__).read_text(encoding="utf-8"))
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(any(v in name for v in ("serial", "demo", "orchestration", "workbench", "firmware")) for name in imports))


if __name__ == "__main__":
    unittest.main()
