"""报告追溯验收：重复清洗、重复复检及中间证据损坏均不能被最后一轮掩盖。"""

from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from microcleaning.control_system.reporting import export_report, read_run


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def image(path, tone=100):
    path.parent.mkdir(parents=True, exist_ok=True)
    okay, payload = cv2.imencode(".png", np.full((32, 40, 3), tone, np.uint8))
    assert okay
    path.write_bytes(payload.tobytes())
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_history(root):
    records, serial = [], []
    comparable = {key: True for key in ("same_size", "same_source", "same_settings", "same_policy", "returned_to_overview", "quality_ok", "fresh_after_return", "pair_confirmed")}
    for number, stable in ((1, "S0001"), (2, "S0001"), (3, "S0002")):
        folder = root / f"cycle_{number:03d}"
        pre = {"raw_sha256": image(folder / "pre.png", 80 + number), "observation": {"raw_image_ref": str(folder / "pre.png")}}
        write_json(folder / "pre.json", pre)
        action = f"action_{number}"
        result = {"match_status": "matched", "pre_area_px": 100, "post_area_px": 5 if number > 1 else 50,
                  "removal_rate": .95 if number > 1 else .5, "result": {"reason_codes": []}}
        checks = []
        for index in range(1, 3 if number == 1 else 2):
            check_dir = folder / f"recheck_{index:03d}"
            post = {"raw_sha256": image(check_dir / "post.png", 150 + index), "observation": {"raw_image_ref": str(check_dir / "post.png")}}
            refs, hashes = {}, {}
            for key in ("pre_crop", "post_crop", "overlay", "contact_sheet"):
                path = check_dir / "roi" / f"{key}.png"
                hashes[key] = image(path, 100 + index)
                refs[key] = path.relative_to(root).as_posix()
            analysis = {"valid": True, "pre_area_px": 100, "post_area_px": result["post_area_px"],
                "initial_area_px": 100, "removal_rate": result["removal_rate"], "cumulative_removal_rate": result["removal_rate"],
                "iou": .05 if number > 1 else .5, "dice": .095 if number > 1 else .66,
                "intersection_px": result["post_area_px"], "union_px": 100, "roi": [10, 8, 12, 10],
                "alignment": {"valid": True, "shift_px": [0.02, -0.03], "confidence": .99}, "reason_codes": []}
            metadata = {**analysis, "artifacts": refs, "artifact_sha256": hashes}
            path = check_dir / "roi" / "roi_verification.json"
            write_json(path, metadata)
            refs["metadata"] = path.relative_to(root).as_posix()
            hashes["metadata"] = hashlib.sha256(path.read_bytes()).hexdigest()
            checks.append({"index": index, "pre": pre, "post": post, "verification": result, "comparability": comparable,
                "roi_analysis": analysis, "roi_evidence": refs, "roi_evidence_sha256": hashes,
                "decision": {"choice": "retake" if index == 1 and number == 1 else "rewash" if number == 1 else "next",
                             "operator": "检查员", "reason": "逐次确认", "at": "2026-10-10T02:00:00+00:00"}})
        write_json(folder / "post.json", checks[-1]["post"])
        execution = {"status": "RETURNED", "pump_request": {"action_id": action, "duration_ms": 500},
            "session": {"receipt": {"success": True, "controller_state": "DONE", "action_id": action},
                        "motion_result": {"sent_lines": ["MOVEXY 40 FWD 0 FWD"], "replies": ["STEP2 X=40 BX=0 Y=0 BY=0"]}},
            "return_result": {"sent_lines": ["MOVEXY 40 REV 0 FWD"], "replies": ["STEP2 X=40 BX=0 Y=0 BY=0"]}}
        record = {"cycle": number, "target_id": stable, "execution": execution, "verification": result,
            "comparability": comparable, "geometry": {"outbound": {"lines": ["MOVEXY 40 FWD 0 FWD"]},
                "returning": {"lines": ["MOVEXY 40 REV 0 FWD"]}}, "rechecks": checks,
            "roi_analysis": checks[-1]["roi_analysis"], "recheck_decision": checks[-1]["decision"]}
        write_json(folder / "cycle.json", record)
        write_json(folder / "execution.json", execution)
        episode_path = folder / "episodes" / "episode.json"
        write_json(episode_path, {"task_id": "history-task"})
        episode_path.with_suffix(".sha256").write_text(hashlib.sha256(episode_path.read_bytes()).hexdigest(), encoding="ascii")
        records.append(record)
        serial.extend([{"direction": "tx", "line": f"MCV1|PUMP|{action}|500"},
                       {"direction": "rx", "line": f"MCV1|ACK|{action}"}, {"direction": "rx", "line": f"MCV1|DONE|{action}"}])
    targets = {stable: {"source": "algorithm", "decision": "APPROVED", "finished": True,
        "instance": {"area_px": 100, "bbox": [10, 8, 12, 10], "centroid_px": [16, 13]}}
        for stable in ("S0001", "S0002")}
    write_json(root / "summary.json", {"task_id": "history-task", "status": "SUCCESS", "workflow_status": "COMPLETED", "mode": "mock", "cycles": records, "targets": targets})
    write_json(root / "task_manifest.json", {"task_id": "history-task", "state": "COMPLETED", "metadata": {"operator": "检查员", "mode": "mock"}, "targets": targets})
    write_json(root / "serial.json", {"events": serial})
    return records


class ReportHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.records = make_history(self.root)

    def test_every_cleaning_and_retake_is_exported_with_chinese_metrics(self):
        snapshot = read_run(self.root)
        self.assertEqual([len(row["attempts"]) for row in snapshot["targets"]], [2, 1])
        self.assertEqual(len(snapshot["attempts"][0]["rechecks"]), 2)
        self.assertEqual(snapshot["quality_status"], "PASS")
        report = export_report(self.root)
        html = (report / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('class="attempt"'), 4)
        self.assertIn("第2次清洗", html)
        self.assertIn("第2次复检", html)
        self.assertIn("掩膜重合", html)
        self.assertIn("X +40 步", html)
        self.assertIn("X已发40步", html)
        self.assertTrue((report / "assets/target_1_attempt_1_recheck_1_overlay.png").is_file())
        with (report / "attempts.csv").open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 4)
        self.assertEqual([row["污渍编号"] for row in rows], ["S0001", "S0001", "S0001", "S0002"])
        self.assertEqual(rows[0]["复检后人工选择"], "重拍后图并再次复检")
        self.assertEqual(float(rows[2]["本轮清洗率"]), .95)

    def test_damaged_earlier_overlay_is_retained_and_prevents_false_pass(self):
        path = self.root / self.records[0]["rechecks"][0]["roi_evidence"]["overlay"]
        path.write_bytes(b"tampered image")
        snapshot = read_run(self.root)
        first = snapshot["attempts"][0]["rechecks"][0]
        self.assertIsNone(first["roi_evidence"]["overlay"])
        self.assertIn("ROI_OVERLAY_IMAGE_HASH_MISMATCH", first["issues"])
        self.assertNotEqual(snapshot["quality_status"], "PASS")
        report = export_report(self.root)
        html = (report / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('class="attempt"'), 4)
        self.assertIn("ROI_OVERLAY_IMAGE_HASH_MISMATCH", html)
        self.assertFalse((report / "assets/target_1_attempt_1_recheck_1_overlay.png").exists())

    def test_missing_intermediate_original_image_does_not_disappear(self):
        path = Path(self.records[0]["rechecks"][0]["post"]["observation"]["raw_image_ref"])
        path.unlink()
        snapshot = read_run(self.root)
        self.assertEqual(len(snapshot["attempts"][0]["rechecks"]), 2)
        self.assertIsNone(snapshot["attempts"][0]["rechecks"][0]["post_image"])
        self.assertNotEqual(snapshot["quality_status"], "PASS")
        self.assertIn("POST_IMAGE_FILE_MISSING", (export_report(self.root) / "index.html").read_text(encoding="utf-8"))

    def test_thousandth_cycle_fallback_uses_numeric_order(self):
        empty = self.root / "long-history"
        for number in (1000, 999, 2):
            write_json(empty / f"cycle_{number:03d}" / "cycle.json", {"cycle": number, "target_id": "S0001"})
        write_json(empty / "summary.json", {"task_id": "long-task", "cycles": []})
        self.assertEqual([attempt["cycle"] for attempt in read_run(empty)["attempts"]], [2, 999, 1000])

    def test_original_metadata_conflict_and_path_escape_are_visible(self):
        summary = json.loads((self.root / "summary.json").read_text(encoding="utf-8"))
        check = summary["cycles"][0]["rechecks"][0]
        check["roi_analysis"]["removal_rate"] = .99
        check["roi_evidence"]["overlay"] = "../../private.png"
        check["roi_evidence_sha256"].pop("overlay")
        write_json(self.root / "summary.json", summary)
        snapshot = read_run(self.root)
        issues = snapshot["attempts"][0]["rechecks"][0]["issues"]
        self.assertIn("ROI_ANALYSIS_METADATA_CONFLICT:removal_rate", issues)
        self.assertIn("ROI_OVERLAY_IMAGE_OUTSIDE_RUN_OR_UNVERIFIED_MOVE", issues)
        self.assertNotEqual(snapshot["quality_status"], "PASS")

    def test_inline_final_acceptance_keeps_zero_and_invalid_automatic_result(self):
        summary = json.loads((self.root / "summary.json").read_text(encoding="utf-8"))
        record = summary["cycles"][-1]
        check = record["rechecks"][-1]
        check["roi_analysis"].update(valid=False, post_area_px=0, removal_rate=0.0, iou=0.0,
            dice=0.0, reason_codes=["ROI_POST_EMPTY"], reference_alignment={"valid": True})
        check["verification"].update(post_area_px=0, removal_rate=0.0, match_status="unmatched")
        record["verification"] = check["verification"]
        review = {"operator": "检查员", "reason": "已逐图核对原视野和区域，人工认可末目标",
                  "conclusion": "CLEANED", "evidence_refs": list(check["roi_evidence"].values())}
        check["quality_evidence"] = {"manual_review": review}
        metadata_path = self.root / check["roi_evidence"]["metadata"]
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata.update(check["roi_analysis"])
        write_json(metadata_path, metadata)
        check["roi_evidence_sha256"]["metadata"] = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
        summary["report_ready"] = True
        write_json(self.root / "summary.json", summary)
        snapshot = read_run(self.root)
        self.assertTrue(snapshot["report_ready"])
        self.assertEqual(snapshot["targets"][-1]["quality"], "CLEANED")
        self.assertEqual(snapshot["targets"][-1]["quality_evidence"]["effective_source"], "operator_review")
        self.assertEqual(snapshot["attempts"][-1]["rechecks"][-1]["roi_analysis"]["removal_rate"], 0.0)
        html = (export_report(self.root) / "index.html").read_text(encoding="utf-8")
        self.assertIn("数值不可作为合格依据", html)
        self.assertIn("人工复核", html)
        self.assertIn("window.print()", html)
        original = Path(self.records[0]["rechecks"][0]["post"]["observation"]["raw_image_ref"])
        original.unlink()
        broken = read_run(self.root)
        self.assertTrue(broken["recorded_report_ready"])
        self.assertFalse(broken["report_ready"])
        self.assertTrue(broken["evidence_issues"])
        self.assertNotIn("window.print()", (export_report(self.root) / "index.html").read_text(encoding="utf-8"))

    def test_incomplete_archive_exports_data_without_print_permission(self):
        summary = json.loads((self.root / "summary.json").read_text(encoding="utf-8"))
        summary.update(workflow_status="FAILED", report_ready=True)
        write_json(self.root / "summary.json", summary)
        snapshot = read_run(self.root)
        self.assertFalse(snapshot["report_ready"])
        report = export_report(self.root)
        html = (report / "index.html").read_text(encoding="utf-8")
        self.assertIn("档案查看", html)
        self.assertEqual(html.count('class="attempt"'), 4)
        self.assertNotIn("window.print()", html)


if __name__ == "__main__":
    unittest.main()
