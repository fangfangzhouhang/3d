"""真实编排/分割/授权/协议替身/报告贯通，绝不连接实物。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from demo.closed_loop_fixture import MockF103Serial, MockFrames, mock_offset
from demo.image_ops import FrozenSegmenter
from demo.workbench_runtime import WorkbenchRuntime
from microcleaning.control_system.orchestration.cleaning_loop import CleaningLoop, LoopConfig
from microcleaning.control_system.orchestration.hardware_executor import HardwareExecutor
from microcleaning.control_system.orchestration.workbench_events import Cancellation
from microcleaning.control_system.planning.path_preview import load_path_placeholders
from microcleaning.control_system.planning.stage2_position import set_zero
from microcleaning.control_system.serial.f103_session import F103SerialSession
from microcleaning.control_system.serial.stage2_link import Stage2SerialLink
from microcleaning.control_system.serial.stm32_serial import STM32SerialController
from microcleaning.control_system.reporting import export_report, read_run, record_quality_review


def make_runtime(folder: Path, scenario="workbench-mixed"):
    args = SimpleNamespace(output_root=folder, real=False, mock_scenario=scenario, camera_index=None,
        camera_width=None, camera_height=None, camera_backend=None, warmup_frames=1, baudrate=115200,
        serial_timeout=0.15, serial_port=None, arm_stage2_xy=False, arm_pump=False, confirm_pump=False,
        pump_duration_ms=500, position_path=None, stage2_set_zero=False)
    return WorkbenchRuntime(args=args, config=LoopConfig(max_cycles=4), segmenter=FrozenSegmenter("local", use_tuned_policy=False),
        placeholders=load_path_placeholders(None), calibration=None, offset=mock_offset())


def run_reviewed(folder: Path, *, scenario="workbench-mixed", cancel_phase=None, decisions=None,
                 raw_type=MockF103Serial, cancellation=None, frames_type=MockFrames):
    folder.mkdir(parents=True, exist_ok=True)
    frames = frames_type(scenario)
    raw = raw_type(frames, scenario)
    cancellation = cancellation or Cancellation()
    path = folder / "mock_position.json"
    set_zero(path)
    session = F103SerialSession(serial_factory=raw.factory, cancellation=cancellation, timeout=.08)
    def confirm(facts):
        if facts.get("phase") == cancel_phase:
            cancellation.cancel()
            cancellation.check()
        return True
    executor = HardwareExecutor(session=session, link=Stage2SerialLink(session=session, armed=True),
        controller=STM32SerialController(session=session, arm_pump=True), position_path=path, confirm=confirm)
    def review(task, pre, prepare):
        for stable, decision in (decisions or {"S0001": "APPROVED", "S0002": "APPROVED", "S0003": "EXCLUDED"}).items():
            task.decide(stable, decision, "合成测试中的人工决策")
        task.lock()
        task.begin()
    loop = CleaningLoop(output_dir=folder, source=frames, segmenter=FrozenSegmenter("local", use_tuned_policy=False),
        executor=executor, placeholders=load_path_placeholders(None), calibration=None, offset=mock_offset(),
        config=LoopConfig(), compare=lambda _: True, review=review, metadata={"operator": "Mock reviewer", "sample_id": "DEMO-3-2"})
    result = loop.run()
    return result, raw, loop


class WorkbenchFlowTests(unittest.TestCase):
    def test_new_region_after_first_pump_never_enters_locked_list(self):
        class NewRegionFrames(MockFrames):
            def pump_finished(self):
                super().pump_finished()
                if self.pumps == 1:
                    self.centers = (*self.centers, (55, 45))
                    self.remaining.add(3)
        with tempfile.TemporaryDirectory() as temp:
            result, raw, _ = run_reviewed(Path(temp), scenario="success", frames_type=NewRegionFrames)
            self.assertEqual(result["status"], "SUCCESS")
            self.assertEqual(result["execution_target_ids"], ["S0001", "S0002"])
            self.assertEqual(raw.pump_count, 2)
            self.assertGreater(result["ignored_new_components"], 0)
            self.assertEqual(len(read_run(temp)["targets"]), 3)

    def test_changed_current_location_blocks_second_target_before_move(self):
        class ShiftedFrames(MockFrames):
            def pump_finished(self):
                super().pump_finished()
                if self.pumps == 1:
                    self.centers = (self.centers[0], (212, 110), self.centers[2])
        with tempfile.TemporaryDirectory() as temp:
            result, raw, _ = run_reviewed(Path(temp), scenario="success", frames_type=ShiftedFrames)
            self.assertEqual(result["status"], "HUMAN")
            self.assertIn("TARGET_IDENTITY_UNCERTAIN", result["reasons"])
            self.assertIn("S0002:CURRENT_LOCATION_NOT_VERIFIED", result["reasons"])
            self.assertEqual(raw.pump_count, 1)
            self.assertEqual(sum(line.startswith(b"MOVEXY") for line in raw.writes), 3, "第一目标两段去程和一段回程；第二目标未发运动")
            self.assertEqual(read_run(temp)["quality_status"], "INCOMPLETE")

    def test_startup_failure_and_cleanup_failure_are_saved_and_release_task(self):
        class BrokenSource:
            def __init__(self, **kwargs):
                pass
            def close(self):
                raise RuntimeError("camera close failed")
        with tempfile.TemporaryDirectory() as temp:
            runtime = make_runtime(Path(temp))
            with patch("demo.workbench_runtime.CameraBroker", BrokenSource), patch("demo.workbench_runtime.CleaningLoop", side_effect=RuntimeError("startup failed")):
                runtime.start({"operator": "Mock reviewer"})
                runtime.worker.join(timeout=3)
            self.assertFalse(runtime.active)
            summary = json.loads((runtime.folder / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["workflow_status"], "FAILED")
            self.assertIn("FRAME_SOURCE_CLOSE_FAILED", summary["reasons"])
            self.assertEqual(summary["cleanup_errors"][0]["message"], "camera close failed")
            self.assertTrue((runtime.folder / "serial.json").exists())

    def test_three_candidates_two_approved_one_excluded_actual_two_pumps(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            result, raw, loop = run_reviewed(folder)
            self.assertEqual(result["status"], "SUCCESS")
            self.assertEqual(result["initial_target_ids"], ["S0001", "S0002", "S0003"])
            self.assertEqual(result["execution_target_ids"], ["S0001", "S0002"])
            self.assertEqual(raw.pump_count, 2)
            self.assertEqual([cycle["target_id"] for cycle in result["cycles"]], ["S0001", "S0002"])
            snapshot = read_run(folder)
            self.assertEqual(snapshot["quality_status"], "FAIL")
            self.assertEqual(snapshot["statistics"]["executed_targets"], 2)
            self.assertEqual([row["quality"] for row in snapshot["targets"]], ["UNCERTAIN", "NOT_CLEANED", "NOT_ASSESSED"])
            self.assertFalse((folder / "reports").exists(), "原始记录保存不能自动触发报告导出")
            report = export_report(folder)
            self.assertTrue((report / "index.html").is_file())
            self.assertEqual(len((report / "targets.csv").read_text(encoding="utf-8-sig").splitlines()), 4)
            self.assertIn("S0003", (report / "index.html").read_text(encoding="utf-8"))

    def test_cancel_after_done_preserves_receipt_and_leaves_no_post(self):
        with tempfile.TemporaryDirectory() as temp:
            result, raw, _ = run_reviewed(Path(temp), cancel_phase="return")
            self.assertEqual(result["status"], "CANCELLED")
            self.assertEqual(result["workflow_status"], "CANCELLED")
            self.assertEqual(result["quality_status"], "INCOMPLETE")
            self.assertEqual(raw.pump_count, 1)
            execution = result["cycles"][0]["execution"]
            self.assertTrue(execution["session"]["receipt"]["success"])
            self.assertTrue(execution["stop_evidence"]["pump_stop_confirmed"])
            self.assertFalse((Path(temp) / "cycle_001/post.png").exists())
            snapshot = read_run(temp)
            self.assertEqual(snapshot["statistics"]["pump_done_attempts"], 1)

    def test_pending_candidate_not_queued_and_quality_requires_review(self):
        with tempfile.TemporaryDirectory() as temp:
            result, raw, _ = run_reviewed(Path(temp), decisions={"S0001": "APPROVED", "S0002": "PENDING_REVIEW", "S0003": "EXCLUDED"})
            self.assertEqual(raw.pump_count, 1)
            self.assertEqual(result["quality_status"], "REVIEW_REQUIRED")
            self.assertEqual(len(read_run(temp)["targets"]), 3)

    def test_manual_reviews_can_be_revoked_without_overwriting_auto(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            run_reviewed(folder)
            before = (folder / "cycle_001/verification.json").read_bytes()
            snapshot = read_run(folder)
            for row in snapshot["targets"][:2]:
                last = row["attempts"][-1]
                record_quality_review(folder, target_id=row["target_id"], operator="Test reviewer", conclusion="CLEANED",
                    reason="合成测试：人工核对同一视野及目标区域", evidence_refs=[last["pre_image"], last["post_image"]])
            self.assertEqual(read_run(folder)["quality_status"], "PASS")
            self.assertEqual(before, (folder / "cycle_001/verification.json").read_bytes())
            record_quality_review(folder, target_id="S0001", operator="Test reviewer", conclusion="REVOKED", reason="撤销测试", evidence_refs=[])
            self.assertEqual(read_run(folder)["quality_status"], "REVIEW_REQUIRED")


if __name__ == "__main__":
    unittest.main()
