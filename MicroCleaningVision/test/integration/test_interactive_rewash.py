"""人工复检路由的贯通/对抗测试；仅合成图及协议替身。"""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from demo.closed_loop_fixture import MockFrames, MockF103Serial, mock_offset
from demo.image_ops import FrozenSegmenter
from microcleaning.control_system.orchestration.cleaning_loop import CleaningLoop, LoopConfig
from microcleaning.control_system.orchestration.hardware_executor import HardwareExecutor
from microcleaning.control_system.orchestration.workbench_events import Cancellation
from microcleaning.control_system.planning.path_preview import load_path_placeholders
from microcleaning.control_system.planning.stage2_position import set_zero
from microcleaning.control_system.serial.f103_session import F103SerialSession
from microcleaning.control_system.serial.stage2_link import Stage2SerialLink
from microcleaning.control_system.serial.stm32_serial import STM32SerialController
from microcleaning.control_system.reporting import read_run, export_report


def execute(folder, *, decide, target_count=1, scenario="success", confirm=lambda _: True, frame_type=MockFrames):
    frames = frame_type(scenario, roi_mode=True)
    raw = MockF103Serial(frames, scenario)
    position = folder / "position.json"
    set_zero(position)
    cancel = Cancellation()
    session = F103SerialSession(serial_factory=raw.factory, cancellation=cancel, timeout=.08)
    executor = HardwareExecutor(session=session, link=Stage2SerialLink(session=session, armed=True),
        controller=STM32SerialController(session=session, arm_pump=True), position_path=position, confirm=confirm)
    def review(task, pre, prepare):
        for index, stable in enumerate(task.targets):
            task.decide(stable, "APPROVED" if index < target_count else "EXCLUDED", "模拟审核")
        task.lock()
        task.begin()
    loop = CleaningLoop(output_dir=folder, source=frames, segmenter=FrozenSegmenter("hsv"),
        executor=executor, placeholders=load_path_placeholders(None), calibration=None, offset=mock_offset(),
        config=LoopConfig(max_cycles=1, max_retries_per_target=0), compare=lambda _: True,
        review=review, recheck_decision=decide, metadata={"operator": "模拟操作员"})
    result = loop.run()
    return result, raw, frames, loop


class InteractiveRewashTests(unittest.TestCase):
    def test_twelve_rewashes_ignore_old_cycle_and_retry_caps_keep_same_target(self):
        seen = []
        def decide(f):
            seen.append(f)
            return {"choice": "rewash" if len(seen) < 13 else "next", "reason": "逐轮核对后人工认可"}
        with tempfile.TemporaryDirectory() as tmp:
            result, raw, frames, loop = execute(Path(tmp), decide=decide)
            self.assertEqual(result["status"], "SUCCESS", result["reasons"])
            self.assertEqual(raw.pump_count, 13)
            self.assertTrue(result["report_ready"])
            self.assertEqual({c["target_id"] for c in result["cycles"]}, {"S0001"})
            self.assertEqual(loop.task.targets["S0001"].retry_count, 12)
            self.assertEqual(len({c["pre_observation"]["frame_id"] for c in result["cycles"]}), 13)
            actions = [c["execution"]["session"]["receipt"]["action_id"] for c in result["cycles"]]
            self.assertEqual(len(set(actions)), 13)
            self.assertEqual(frames.capture_phases, ["pre", "post"] * 13)
            snapshot = read_run(tmp)
            self.assertEqual(len(snapshot["targets"][0]["attempts"]), 13)
            report = export_report(tmp)
            import csv
            with (report / "attempts.csv").open(encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 13)
            self.assertIn("cycle_013", (report / "index.html").read_text(encoding="utf-8"))

    def test_retake_keeps_each_recheck_without_second_pump(self):
        choices = iter(("retake", "next"))
        with tempfile.TemporaryDirectory() as tmp:
            result, raw, frames, _ = execute(Path(tmp), decide=lambda _: {"choice": next(choices), "reason": "复核"})
            self.assertEqual(result["status"], "SUCCESS", result["reasons"])
            self.assertEqual(raw.pump_count, 1)
            self.assertEqual(frames.capture_phases, ["pre", "post", "post"])
            self.assertEqual(len(result["cycles"][0]["rechecks"]), 2)
            self.assertTrue((Path(tmp) / "cycle_001/recheck_001/post.png").is_file())
            self.assertTrue((Path(tmp) / "cycle_001/recheck_002/post.png").is_file())

    def test_pause_saves_unfinished_and_no_following_motion(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, raw, _, loop = execute(Path(tmp), decide=lambda _: {"choice": "pause"}, target_count=2)
            self.assertEqual(raw.pump_count, 1)
            self.assertEqual(result["status"], "HUMAN")
            self.assertFalse(result["report_ready"])
            self.assertFalse(loop.task.targets["S0001"].finished)
            self.assertFalse(loop.task.targets["S0002"].finished)

    def test_ordinary_motion_rejection_never_means_rewash(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, raw, _, _ = execute(Path(tmp), decide=lambda _: self.fail("未复检不得出现复洗选择"),
                confirm=lambda f: f.get("phase") != "move")
            self.assertEqual(raw.pump_count, 0)
            self.assertFalse(any(line.startswith(b"MOVEXY") for line in raw.writes))
            self.assertFalse(result["report_ready"])

    def test_missing_post_detection_is_zero_invalid_and_can_be_human_reviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, raw, _, _ = execute(Path(tmp), scenario="workbench-unmatched",
                decide=lambda f: {"choice": "next", "reason": "人工确认新图中区域已清洁"})
            self.assertEqual(result["status"], "SUCCESS", result["reasons"])
            roi = result["cycles"][0]["roi_analysis"]
            self.assertFalse(roi["valid"])
            self.assertEqual(roi["removal_rate"], 0.0)
            self.assertTrue(result["report_ready"])
            self.assertEqual(result["cycles"][0]["quality_evidence"]["effective_source"], "operator_review")

    def test_shifted_post_disallows_final_acceptance_and_has_no_valid_rate(self):
        class Shifted(MockFrames):
            def _capture(self, phase, *, after=None):
                from dataclasses import replace
                frame = super()._capture(phase, after=after)
                if phase == "post":
                    return replace(frame, image=self.np.roll(frame.image, 3, axis=1))
                return frame
        def decide(f):
            self.assertNotIn("next", f["choices"])
            self.assertFalse(f["manual_accept_allowed"])
            return {"choice": "pause"}
        with tempfile.TemporaryDirectory() as tmp:
            result, raw, _, _ = execute(Path(tmp), frame_type=Shifted, decide=decide)
            self.assertEqual(raw.pump_count, 1)
            self.assertFalse(result["report_ready"])
            self.assertIsNone(result["cycles"][0]["roi_analysis"]["removal_rate"])

    def test_invalid_choice_is_saved_and_does_not_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, raw, _, _ = execute(Path(tmp), decide=lambda _: {"choice": "spray"})
            self.assertEqual(result["status"], "ERROR")
            self.assertEqual(raw.pump_count, 1)
            self.assertFalse(result["report_ready"])

    def test_next_advances_and_preserves_earlier_residue(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, raw, _, _ = execute(Path(tmp), target_count=2, scenario="workbench-residual",
                decide=lambda f: {"choice": "next", "reason": "末目标人工复核认可"})
            self.assertEqual([c["target_id"] for c in result["cycles"]], ["S0001", "S0002"])
            self.assertEqual(raw.pump_count, 2)
            self.assertEqual(result["quality_status"], "FAIL")
            self.assertTrue(result["report_ready"])

