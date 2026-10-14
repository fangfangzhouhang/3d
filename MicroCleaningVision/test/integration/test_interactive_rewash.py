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


def execute(folder, *, decide, target_count=1, scenario="success", confirm=lambda _: True, frame_type=MockFrames, coverage_parameters=None):
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
        review=review, recheck_decision=decide, metadata={"operator": "模拟操作员"},
        coverage_parameters=coverage_parameters)
    result = loop.run()
    return result, raw, frames, loop


class InteractiveRewashTests(unittest.TestCase):
    def test_explicit_diameter_visits_each_station_and_applies_the_offset_once(self):
        def parameters():
            return {"diameter": 8, "unit": "px", "candidate_stride_px": 4, "max_stations": 6, "target_coverage": 0.95}
        with tempfile.TemporaryDirectory() as tmp:
            result, raw, _, _ = execute(Path(tmp), decide=lambda _: {"choice": "next", "reason": "继续"}, coverage_parameters=parameters)
            self.assertEqual(result["status"], "SUCCESS", result["reasons"])
            geometry = json.loads((Path(tmp) / "cycle_001" / "geometry.json").read_text(encoding="utf-8"))
            self.assertEqual("coverage_stations", geometry["motion_source"])
            self.assertGreaterEqual(len(geometry["dwells"]), 2)
            self.assertEqual(1, geometry["offset_application_count"])
            moves = [item.decode("ascii").strip() for item in raw.writes if item.startswith(b"MOVEXY")]
            sent = []
            for hop in geometry["dwells"]:
                sent.extend(hop["lines"])
            sent.extend(geometry["returning"]["lines"])
            self.assertEqual(sent, moves)
            self.assertEqual(raw.pump_count, len(geometry["dwells"]))
            later = [line for hop in geometry["dwells"][1:] for line in hop["lines"]]
            self.assertNotIn(geometry["dwells"][0]["lines"][-1], later)

    def test_failed_location_can_retake_without_aborting_the_task(self):
        from dataclasses import replace as replace_location
        from microcleaning.control_system.orchestration import cleaning_loop
        real = cleaning_loop.validate_roi_location
        calls = {"n": 0}
        def wrapped(*args, **kwargs):
            calls["n"] += 1
            result = real(*args, **kwargs)
            if calls["n"] == 1:
                return replace_location(result, valid=False, reason_codes=("ROI_POSITION_SHIFTED", "ROI_ALIGNMENT_LOW_CONFIDENCE"))
            return result
        def decide(facts):
            if facts.get("hold") == "location":
                return {"choice": "retake", "reason": "重拍前图"}
            return {"choice": "next", "reason": "继续"}
        with tempfile.TemporaryDirectory() as tmp, patch.object(cleaning_loop, "validate_roi_location", wrapped):
            result, raw, frames, _ = execute(Path(tmp), decide=decide)
            self.assertEqual(result["status"], "SUCCESS", result["reasons"])
            self.assertEqual(raw.pump_count, 1)
            self.assertEqual(frames.capture_phases[:2], ["pre", "pre"])
            self.assertTrue((Path(tmp) / "cycle_001" / "rejected_pre_001" / "location_check.json").is_file())

    def test_failed_location_can_skip_to_the_next_stain_without_moving(self):
        from dataclasses import replace as replace_location
        from microcleaning.control_system.orchestration import cleaning_loop
        real = cleaning_loop.validate_roi_location
        calls = {"n": 0}
        def wrapped(*args, **kwargs):
            calls["n"] += 1
            result = real(*args, **kwargs)
            if calls["n"] == 1:
                return replace_location(result, valid=False, reason_codes=("ROI_POSITION_SHIFTED",))
            return result
        def decide(facts):
            if facts.get("hold") == "location":
                self.assertIn("next", facts["choices"])
                return {"choice": "next", "reason": "改洗下一块"}
            return {"choice": "next", "reason": "继续"}
        with tempfile.TemporaryDirectory() as tmp, patch.object(cleaning_loop, "validate_roi_location", wrapped):
            result, raw, frames, loop = execute(Path(tmp), decide=decide, target_count=2)
            self.assertEqual(result["status"], "SUCCESS", result["reasons"])
            self.assertEqual(raw.pump_count, 1)
            self.assertTrue(loop.task.targets["S0001"].finished)
            self.assertEqual("pre", frames.capture_phases[0])
            self.assertTrue((Path(tmp) / "cycle_001" / "location_hold.json").is_file())

    def test_failed_location_pause_does_not_move_or_spray(self):
        from dataclasses import replace as replace_location
        from microcleaning.control_system.orchestration import cleaning_loop
        real = cleaning_loop.validate_roi_location
        def wrapped(*args, **kwargs):
            result = real(*args, **kwargs)
            return replace_location(result, valid=False, reason_codes=("ROI_ALIGNMENT_LOW_CONFIDENCE",))
        with tempfile.TemporaryDirectory() as tmp, patch.object(cleaning_loop, "validate_roi_location", wrapped):
            result, raw, _, _ = execute(Path(tmp), decide=lambda facts: {"choice": "pause", "reason": "先停下"})
            self.assertEqual(result["status"], "HUMAN", result["reasons"])
            self.assertIn("HUMAN_PAUSED_AFTER_LOCATION_HOLD", result["reasons"])
            self.assertEqual(raw.pump_count, 0)
            self.assertEqual(raw.writes, [])
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

    def test_rewash_stays_on_the_same_stain_then_next_opens_the_report(self):
        def decide(facts):
            if facts.get("hold") == "location":
                return {"choice": "retake", "reason": "重拍"}
            if facts["target_id"] == "S0001" and facts["cycle"] == 1:
                return {"choice": "rewash", "reason": "再洗这一块"}
            return {"choice": "next", "reason": "转到下一块或结束"}
        with tempfile.TemporaryDirectory() as tmp:
            result, raw, _, _ = execute(Path(tmp), decide=decide, target_count=2)
            self.assertEqual(result["status"], "SUCCESS", result["reasons"])
            self.assertEqual([c["target_id"] for c in result["cycles"]], ["S0001", "S0001", "S0002"])
            self.assertEqual(raw.pump_count, 3)
            self.assertTrue(result["report_ready"])
            events = [json.loads(line) for line in (Path(tmp) / "stage_events.jsonl").read_text(encoding="utf-8").splitlines()]
            captures = [event for event in events if event["phase"] == "CAPTURE_PRE"]
            self.assertEqual([None, "S0001", "S0002"], [event["current_target_id"] for event in captures])
            self.assertEqual([None, "rewash", "next"], [event["route"] for event in captures])
            recorded = [event for event in events if event["phase"] == "STAIN_RECORDED"]
            self.assertEqual(["recorded", "report"], [event["route"] for event in recorded])
            self.assertEqual("S0002", recorded[0]["following_id"])
            snapshot = read_run(tmp)
            by_id = {row["target_id"]: row for row in snapshot["targets"]}
            self.assertEqual(len(by_id["S0001"]["attempts"]), 2)
            self.assertEqual(len(by_id["S0002"]["attempts"]), 1)
            report = export_report(tmp)
            text = (report / "index.html").read_text(encoding="utf-8")
            self.assertIn("S0001", text)
            self.assertIn("S0002", text)
            import csv
            with (report / "attempts.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual({"S0001", "S0002"}, {row["污渍编号"] for row in rows})

