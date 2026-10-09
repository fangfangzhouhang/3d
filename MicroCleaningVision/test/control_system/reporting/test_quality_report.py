import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integration.test_workbench_flow import run_reviewed
from microcleaning.control_system.reporting import export_report, read_run, record_quality_review
from microcleaning.control_system.reporting.evidence_reader import resolve_asset


class QualityReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)/"任务含中文"
        run_reviewed(self.folder)

    def test_html_escaped_csv_bom_assets_and_revision_never_overwrite(self):
        manifest_path = self.folder/"task_manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["targets"]["S0003"]["note"] = "<script>alert(1)</script>"
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        report = export_report(self.folder)
        text = (report/"index.html").read_text(encoding="utf-8")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)
        self.assertNotIn("<script>alert(1)</script>", text)
        self.assertTrue((report/"targets.csv").read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertTrue((report/"assets/target_1_post.png").exists())
        self.assertNotEqual(report, export_report(self.folder))

    def test_missing_image_cannot_be_overridden_to_pass(self):
        (self.folder/"cycle_002/post.png").unlink()
        row = read_run(self.folder)["targets"][1]
        self.assertEqual(row["quality"], "UNCERTAIN")
        record_quality_review(self.folder, target_id="S0002", operator="Tester", conclusion="CLEANED",
            reason="not enough evidence", evidence_refs=["cycle_002/pre.png"])
        self.assertEqual(read_run(self.folder)["targets"][1]["quality"], "UNCERTAIN")
        export_report(self.folder)

    def test_corrupt_json_and_tampered_image_are_not_success(self):
        (self.folder/"cycle_002/post.png").write_bytes(b"different")
        (self.folder/"task_manifest.json").write_text("{invalid", encoding="utf-8")
        snapshot = read_run(self.folder)
        self.assertNotEqual(snapshot["quality_status"], "PASS")
        self.assertTrue(snapshot["issues"])
        self.assertIsNone(snapshot["attempts"][1]["post_image"])
        export_report(self.folder)

    def test_move_directory_preserves_identity_using_hashes(self):
        moved = self.folder.with_name("迁移后的任务")
        self.folder.rename(moved)
        snapshot = read_run(moved)
        self.assertEqual(snapshot["quality_status"], "FAIL")
        self.assertEqual(snapshot["attempts"][0]["pre_image"], "cycle_001/pre.png")
        self.assertEqual(snapshot["attempts"][0]["issues"], [])

    def test_report_failure_preserves_raw_task_and_can_retry(self):
        before = (self.folder/"summary.json").read_bytes()
        with patch("microcleaning.control_system.reporting.quality_report._csv", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                export_report(self.folder)
        self.assertEqual(before, (self.folder/"summary.json").read_bytes())
        self.assertTrue(list((self.folder/"reports").glob("*/report_error.json")))
        self.assertTrue((export_report(self.folder)/"index.html").exists())

    def test_historical_records_never_invent_operator_approval(self):
        (self.folder/"task_manifest.json").unlink()
        snapshot = read_run(self.folder)
        self.assertEqual({t["decision"] for t in snapshot["targets"]}, {"UNRECORDED"})
        self.assertNotEqual(snapshot["quality_status"], "PASS")

    def test_path_traversal_and_unsupported_format_rejected(self):
        relative, error = resolve_asset(self.folder, "../../private.png")
        self.assertIsNone(relative)
        self.assertIsNotNone(error)
        with self.assertRaises(ValueError):
            export_report(self.folder, formats=("fake",))

    def test_missing_summary_creates_incomplete_report(self):
        empty = Path(self.temp.name)/"empty"
        empty.mkdir()
        snapshot = read_run(empty)
        self.assertEqual(snapshot["quality_status"], "INCOMPLETE")
        self.assertIn("MISSING_JSON:summary.json", snapshot["issues"])
        self.assertTrue((export_report(empty)/"index.html").exists())

    def test_legacy_single_frame_reads_episode_without_inventing_approval(self):
        from demo.demo_pipeline import run_demo
        legacy = run_demo(generate_sample=True, mode="simulate", output_root=Path(self.temp.name)/"legacy")
        summary = json.loads((legacy/"summary.json").read_text(encoding="utf-8"))
        snapshot = read_run(legacy)
        self.assertEqual(snapshot["task_id"], summary["run_id"])
        self.assertEqual(snapshot["software_version"], summary["demo_version"])
        self.assertEqual(snapshot["workflow_status"], "UNRECORDED")
        self.assertEqual(snapshot["statistics"]["approved"], None)
        self.assertEqual(len(snapshot["targets"]), 1)
        self.assertEqual(snapshot["targets"][0]["target_id"], "UNRECORDED")
        self.assertEqual(snapshot["targets"][0]["decision"], "UNRECORDED")
        self.assertEqual(snapshot["attempts"][0]["episode_refs"], [summary["episode_file"]])
        self.assertEqual(snapshot["attempts"][0]["pre_image"], "input.png")
        self.assertEqual(snapshot["attempts"][0]["post_image"], "post_mask.png")
        self.assertNotIn("EPISODE_HASH_MISSING_OR_MISMATCH", snapshot["attempts"][0]["issues"])
        self.assertTrue((export_report(legacy)/"index.html").exists())
