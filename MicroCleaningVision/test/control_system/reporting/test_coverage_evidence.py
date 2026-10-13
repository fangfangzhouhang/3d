"""Bound simulation archives, optional report compatibility and tamper isolation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_workbench_coverage import source_fixture
from demo.coverage_session import CoverageRequest, compute_request
from microcleaning.control_system.planning.coverage_model import Evidence, FootprintProfile
from microcleaning.control_system.planning.coverage_planner import CoverageConstraints, CoveragePolicy
from microcleaning.control_system.planning.coverage_visualization import RenderOptions
from microcleaning.control_system.reporting.coverage_evidence import export_bound_preview, read_coverage_evidence
from microcleaning.control_system.reporting import read_run, export_report


class CoverageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = source_fixture(self.root)
        self.constraints = CoverageConstraints()
        profile = FootprintProfile("test-virtual", 12, "px", Evidence("assumed", "explicit-test"))
        self.plan = compute_request(CoverageRequest(1, self.source.dataset, profile,
            policy=CoveragePolicy(candidate_stride_px=2), constraints=self.constraints))

    def save(self, **overrides):
        values = dict(generation=2, constraints=self.constraints, options=RenderOptions(), step_count=1)
        values.update(overrides)
        return export_bound_preview(self.source.dataset.image_rgb, self.plan, self.root, self.source.binding, **values)

    def read(self):
        return read_coverage_evidence(self.root, task_id="test-task", targets=("S0001",))

    def test_complete_four_file_archive_plus_binding_and_prefix_metrics(self):
        folder = self.save()
        self.assertEqual({p.name for p in (folder / "archive").iterdir()},
            {"coverage_preview.json", "coverage_overlay.png", "source_image.png", "coverage_masks.npz"})
        previews, issues = self.read()
        self.assertFalse(issues, issues)
        self.assertEqual(len(previews), 1)
        preview = previews[0]
        self.assertEqual(preview["step_count"], 1)
        self.assertLess(preview["view_metrics"]["geometric_coverage_rate"], preview["metrics"]["geometric_coverage_rate"])
        self.assertEqual(preview["comparison_status"], "hypothetical_plan_was_not_executed")
        self.assertFalse(preview["executable"])
        self.assertNotEqual(folder, self.save())

    def test_report_reader_never_runs_planning_and_optional_damage_cannot_change_quality(self):
        baseline = read_run(self.root)
        folder = self.save()
        with patch("microcleaning.control_system.reporting.coverage_evidence.load_coverage_preview", side_effect=AssertionError("no replan on report reader")):
            snapshot = read_run(self.root)
            self.assertEqual(len(snapshot["coverage_previews"]), 1)
        (folder / "archive/coverage_overlay.png").write_bytes(b"tampered")
        damaged = read_run(self.root)
        self.assertFalse(damaged["coverage_previews"])
        self.assertTrue(damaged["coverage_preview_issues"])
        for key in ("quality_status", "quality_reasons", "report_ready", "evidence_issues"):
            self.assertEqual(damaged[key], baseline[key], key)

    def test_wrong_task_frame_target_source_path_and_hardware_flags_are_quarantined(self):
        folder = self.save()
        path = folder / "binding.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        for key, value in (("task_id", "foreign"), ("target_id", "S0002"), ("frame_id", "old-frame"),
                ("cycle", 2), ("image_ref", "../outside.png"), ("source_ref", "../outside.json"),
                ("executable", True), ("feeds_action_request", True), ("send_to_controller", True)):
            with self.subTest(key=key):
                path.write_text(json.dumps({**original, key: value}), encoding="utf-8")
                previews, issues = self.read()
                self.assertFalse(previews)
                self.assertTrue(issues)
        path.write_text(json.dumps(original), encoding="utf-8")
        self.assertFalse(self.read()[1])

    def test_tampered_manifest_and_incomplete_archive_do_not_look_complete(self):
        folder = self.save()
        (folder / "archive/coverage_preview.json").write_text("{}", encoding="utf-8")
        unfinished = self.root / "coverage_previews/incomplete/archive"
        unfinished.mkdir(parents=True)
        (unfinished / "source_image.png").write_bytes(b"partial")
        previews, issues = self.read()
        self.assertFalse(previews)
        self.assertEqual(len(issues), 2)

    def test_failed_deterministic_export_does_not_publish_binding(self):
        with patch("microcleaning.control_system.reporting.coverage_evidence.load_coverage_preview", side_effect=ValueError("bad replay")):
            with self.assertRaisesRegex(ValueError, "bad replay"):
                self.save()
        self.assertFalse(list(self.root.glob("coverage_previews/*/binding.json")))
        self.assertTrue(self.read()[1])

    def test_export_rejects_wrong_pixels_and_wrong_plan_identity_before_archive(self):
        with self.assertRaisesRegex(ValueError, "PIXELS_MISMATCH"):
            export_bound_preview(self.source.dataset.image_rgb ^ 1, self.plan, self.root, self.source.binding,
                generation=1, constraints=self.constraints, options=RenderOptions(), step_count=1)
        with self.assertRaisesRegex(ValueError, "BINDING_MISMATCH"):
            export_bound_preview(self.source.dataset.image_rgb, self.plan, self.root,
                {**self.source.binding, "mask_sha256": "c" * 64}, generation=1,
                constraints=self.constraints, options=RenderOptions(), step_count=1)
        self.assertFalse((self.root / "coverage_previews").exists())

    def test_legacy_missing_preview_is_normal_and_new_csv_is_separate(self):
        self.assertEqual(self.read(), ([], []))
        old_report = export_report(self.root)
        self.assertFalse((old_report / "coverage_previews.csv").exists())
        self.save()
        report = export_report(self.root)
        self.assertTrue((report / "coverage_previews.csv").is_file())
        self.assertEqual((old_report / "targets.csv").read_bytes(), (report / "targets.csv").read_bytes())
        text = (report / "index.html").read_text(encoding="utf-8")
        self.assertIn("多驻点计划未执行", text)
        self.assertIn("不是清除率", text)

    def test_unknown_diameter_keeps_metrics_unknown_in_archive_and_report(self):
        self.plan = compute_request(CoverageRequest(1, self.source.dataset,
            FootprintProfile("unknown", None, "px", Evidence("assumed", "explicit-unknown"))))
        self.save(step_count=0)
        previews, issues = self.read()
        self.assertFalse(issues, issues)
        self.assertIsNone(previews[0]["metrics"])
        self.assertEqual(previews[0]["station_count"], 0)


if __name__ == "__main__":
    unittest.main()
