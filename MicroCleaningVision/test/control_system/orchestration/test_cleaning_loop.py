"""从真实分割到串口替身的状态/失败矩阵。"""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from demo.closed_loop_fixture import MockF103Serial, MockFrames, mock_offset
from demo.image_ops import FrozenSegmenter
from microcleaning.control_system.orchestration.cleaning_loop import CleaningLoop, LoopConfig
from microcleaning.control_system.orchestration.hardware_executor import HardwareExecutor
from microcleaning.control_system.planning.path_preview import PathPlaceholderConfig
from microcleaning.control_system.planning.stage2_position import load_position, set_zero
from microcleaning.control_system.serial.f103_session import F103SerialSession
from microcleaning.control_system.serial.stage2_link import Stage2SerialLink
from microcleaning.control_system.serial.stm32_serial import STM32SerialController


def station(folder, scenario="success", *, cycles=3, retries=0, budget=1600, offset=None, armed=True, frames=None, compare=None, confirm=None, strategy="nearest_neighbor"):
    folder = Path(folder)
    frames = frames or MockFrames(scenario)
    raw = MockF103Serial(frames, scenario)
    position = folder / "mock_position.json"
    set_zero(position)
    session = F103SerialSession(serial_factory=raw.factory)
    executor = HardwareExecutor(session=session, link=Stage2SerialLink(session=session, armed=armed),
        controller=STM32SerialController(session=session, arm_pump=armed), position_path=position,
        confirm=confirm or (lambda _: scenario != "decline"))
    loop = CleaningLoop(output_dir=folder, source=frames, segmenter=FrozenSegmenter("hsv"),
        executor=executor, placeholders=PathPlaceholderConfig(), calibration=None, offset=offset or mock_offset(),
        config=LoopConfig(cycles, retries, strategy, budget), compare=compare or (lambda _: True))
    return loop, raw, frames, executor


class CleaningLoopTests(unittest.TestCase):
    def test_no_target_never_opens_serial(self):
        result, raw, _, _, _ = self.run_case("no-target")
        self.assertEqual("SUCCESS", result["status"])
        self.assertEqual(("NO_TARGET",), result["reasons"])
        self.assertEqual(0, raw.opens)

    def test_zero_motion_is_not_replaced_with_a_dummy_move(self):
        frames = MockFrames()
        frames.centers, frames.remaining = ((160, 120),), {0}
        result, raw, _, _, _ = self.run_case(frames=frames, offset=replace(mock_offset(), scope_to_nozzle_delta_steps=(0, 0)))
        self.assertEqual("HUMAN", result["status"])
        self.assertIn("NO_MOTION", result["reasons"])
        self.assertEqual([], raw.writes)

    def test_post_capture_interrupt_preserves_receipt_and_failure_episode(self):
        class InterruptedFrames(MockFrames):
            def capture(self, phase, **kwargs):
                if phase == "post":
                    raise KeyboardInterrupt()
                return super().capture(phase, **kwargs)
        result, raw, frames, executor, folder = self.run_case(frames=InterruptedFrames())
        self.assertEqual("ERROR", result["status"])
        self.assertEqual(("INTERRUPTED",), result["reasons"])
        self.assertEqual(1, raw.pump_count)
        self.assertTrue(raw.closed)
        self.assertTrue(frames.closed)
        self.assertEqual((0, 0), load_position(executor.position_path).xy())
        import json
        episode = json.loads(next(folder.rglob("episode_*.json")).read_text(encoding="utf-8"))
        self.assertTrue(episode["execution_receipt"]["success"])
        self.assertEqual("HUMAN", episode["verification"]["next_route"])
        self.assertEqual("INTERRUPTED", episode["failures"][0]["reason_codes"][0])

    def test_changed_settings_before_next_action_stop_identity_mapping(self):
        class ChangedFrames(MockFrames):
            def capture(self, phase, **kwargs):
                frame = super().capture(phase, **kwargs)
                if phase == "pre" and self.pumps:
                    frame.settings["camera_moved"] = True
                return frame
        result, raw, _, _, _ = self.run_case(frames=ChangedFrames())
        self.assertEqual("HUMAN", result["status"])
        self.assertEqual(1, raw.pump_count)
        self.assertIn("PRE_CAPTURE_SETTINGS_CHANGED", result["reasons"][1])

    def test_cleanup_failure_keeps_interrupt_receipt_and_closes_other_resource(self):
        import json
        class InterruptedFrames(MockFrames):
            def capture(self, phase, **kwargs):
                if phase == "post":
                    raise KeyboardInterrupt()
                return super().capture(phase, **kwargs)
        with TemporaryDirectory() as folder:
            loop, raw, frames, executor = station(folder, frames=InterruptedFrames())
            original = executor.close
            def failing_close():
                original()
                raise OSError("serial cleanup fault")
            executor.close = failing_close
            result = loop.run()
            self.assertEqual("ERROR", result["status"])
            self.assertEqual(("INTERRUPTED", "EXECUTOR_CLOSE_FAILED"), result["reasons"])
            self.assertTrue(raw.closed)
            self.assertTrue(frames.closed)
            self.assertEqual(1, raw.pump_count)
            stored = json.loads((Path(folder) / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual("OSError", stored["cleanup_errors"][0]["type"])
            self.assertTrue((Path(folder) / "serial.json").exists())
            self.assertTrue((Path(folder) / "failure.json").exists())
            episode = json.loads(next(Path(folder).rglob("episode_*.json")).read_text(encoding="utf-8"))
            self.assertTrue(episode["execution_receipt"]["success"])
            self.assertIn("EXECUTOR_CLOSE_FAILED", episode["failures"][0]["reason_codes"])

    def test_source_cleanup_failure_changes_task_success_to_recorded_error(self):
        class FailingCloseFrames(MockFrames):
            def close(self):
                super().close()
                raise OSError("camera cleanup fault")
        result, raw, frames, _, folder = self.run_case(frames=FailingCloseFrames())
        self.assertEqual("ERROR", result["status"])
        self.assertEqual(("FRAME_SOURCE_CLOSE_FAILED",), result["reasons"])
        self.assertEqual(3, len(result["cycles"]))
        self.assertEqual(3, raw.pump_count)
        self.assertTrue(raw.closed)
        self.assertTrue(frames.closed)
        self.assertTrue((folder / "summary.json").exists())
        self.assertTrue((folder / "failure.json").exists())

    def test_return_approval_expiring_after_move_blocks_pump(self):
        from unittest.mock import patch
        from datetime import datetime, timedelta, timezone
        from microcleaning.control_system.orchestration import hardware_executor
        original = hardware_executor.approve_motion_gate
        calls = {"n": 0}
        def approve(*args, **kwargs):
            calls["n"] += 1
            decision = original(*args, **kwargs)
            if calls["n"] == 2:
                decision = replace(decision, expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
            return decision
        with patch.object(hardware_executor, "approve_motion_gate", approve):
            result, raw, _, executor, _ = self.run_case()
        self.assertEqual("ERROR", result["status"])
        self.assertEqual(0, raw.pump_count)
        self.assertIn("RETURN_APPROVAL_EXPIRED_BEFORE_PUMP", result["reasons"])
        self.assertTrue(load_position(executor.position_path).known)

    def test_estop_reported_by_actual_status_prevents_move(self):
        from unittest.mock import patch
        original = MockF103Serial.write
        def estop(raw, data):
            count = original(raw, data)
            if data.strip() == b"MCV1|STATUS":
                raw._queue[-1] = b"MCV1|STATUS|ESTOP=1|PUMP=0\n"
            return count
        with patch.object(MockF103Serial, "write", estop):
            result, raw, _, _, _ = self.run_case()
        self.assertEqual("HUMAN", result["status"])
        self.assertEqual(0, raw.pump_count)
        self.assertFalse(any(line.startswith(b"MOVEXY ") for line in raw.writes))
        self.assertIn("ESTOP_ACTIVE", result["reasons"])
    def run_case(self, scenario="success", **kwargs):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        loop, raw, frames, executor = station(temporary.name, scenario, **kwargs)
        result = loop.run()
        return result, raw, frames, executor, Path(temporary.name)

    def test_two_targets_use_real_modules_and_keep_one_com_for_both_returns(self):
        frames = MockFrames()
        frames.remaining = {0, 1}
        result, raw, frames, executor, folder = self.run_case(frames=frames)
        self.assertEqual("SUCCESS", result["status"])
        self.assertEqual(2, len(result["cycles"]))
        self.assertEqual(2, raw.pump_count)
        self.assertEqual(1, raw.opens)
        self.assertEqual(1, raw.close_count)
        self.assertEqual((0, 0), raw.position)
        self.assertEqual((0, 0), load_position(executor.position_path).xy())
        self.assertEqual(["pre", "post", "pre", "post"], frames.capture_phases)
        self.assertTrue(all(cycle["execution"]["return_result"] for cycle in result["cycles"]))
        self.assertEqual(2, len(list(folder.rglob("episode_*.json"))))

    def test_t1_removal_relabels_original_t2_without_losing_identity(self):
        result, raw, _, _, _ = self.run_case(strategy="area_desc")
        self.assertEqual("SUCCESS", result["status"])
        self.assertEqual(["S0001", "S0002", "S0003"], [cycle["target_id"] for cycle in result["cycles"]])
        self.assertEqual(["T1", "T1", "T1"], [cycle["frame_target_id"] for cycle in result["cycles"]])
        self.assertEqual(3, raw.pump_count)

    def test_motion_failure_never_calls_pump_and_marks_unknown(self):
        for scenario in ("motion-short", "motion-timeout"):
            with self.subTest(scenario=scenario):
                result, raw, _, executor, folder = self.run_case(scenario)
                self.assertEqual("ERROR", result["status"])
                self.assertEqual(0, raw.pump_count)
                self.assertFalse(load_position(executor.position_path).known)
                self.assertFalse(any(folder.rglob("post.png")))
                self.assertTrue((folder / "cycle_001/execution.json").exists())

    def test_pump_failure_preserves_completed_motion_but_cannot_mark_clean(self):
        result, raw, _, executor, _ = self.run_case("pump-timeout")
        self.assertEqual("ERROR", result["status"])
        self.assertEqual(1, raw.pump_count)
        execution = result["cycles"][0]["execution"]
        self.assertIsNotNone(execution["session"]["motion_result"])
        self.assertTrue(execution["session"]["stopped"])
        self.assertTrue(load_position(executor.position_path).known)
        self.assertFalse(any(entry["completed"] for entry in result["targets"].values()))

    def test_return_failure_has_no_normal_post_verification(self):
        result, raw, frames, executor, folder = self.run_case("return-timeout")
        self.assertEqual("ERROR", result["status"])
        self.assertEqual(1, raw.pump_count)
        self.assertEqual(["pre"], frames.capture_phases)
        self.assertFalse(load_position(executor.position_path).known)
        self.assertFalse(any(folder.rglob("verification.json")))

    def test_bad_post_or_incomparable_pair_stops_without_success(self):
        for scenario in ("post-quality", "noncomparable"):
            with self.subTest(scenario=scenario):
                result, raw, _, _, _ = self.run_case(scenario)
                self.assertEqual("HUMAN", result["status"])
                self.assertEqual(1, raw.pump_count)
                self.assertIn("PRE_POST_NOT_COMPARABLE", result["reasons"])

    def test_stale_post_is_an_error_even_if_pixels_look_clean(self):
        result, raw, frames, _, _ = self.run_case("stale-post")
        self.assertEqual("ERROR", result["status"])
        self.assertIn("POST_CAPTURE_NOT_AFTER_RETURN", result["reasons"])
        self.assertFalse(any(entry["completed"] for entry in result["targets"].values()))

    def test_no_confirmation_or_arm_never_opens_a_serial_port(self):
        for kwargs in ({"scenario": "decline"}, {"armed": False}):
            with self.subTest(kwargs=kwargs):
                result, raw, _, _, _ = self.run_case(**kwargs)
                self.assertEqual("HUMAN", result["status"])
                self.assertEqual(0, raw.opens)
                self.assertEqual([], raw.writes)

    def test_retry_has_new_capture_action_and_decisions(self):
        result, raw, frames, _, _ = self.run_case("retry", retries=1, cycles=4)
        self.assertEqual("SUCCESS", result["status"])
        a, b = result["cycles"][:2]
        self.assertEqual(a["target_id"], b["target_id"])
        self.assertNotEqual(a["pre_observation"]["frame_id"], b["pre_observation"]["frame_id"])
        self.assertNotEqual(a["execution"]["pump_request"]["action_id"], b["execution"]["pump_request"]["action_id"])
        self.assertNotEqual(a["execution"]["pump_decision"]["approval_token"], b["execution"]["pump_decision"]["approval_token"])
        self.assertEqual(1, b["retry_count"])
        self.assertEqual(4, raw.pump_count)

    def test_retry_default_and_cycle_limit_are_bounded(self):
        result, raw, _, _, _ = self.run_case("retry")
        self.assertEqual("HUMAN", result["status"])
        self.assertIn("MAX_RETRIES_PER_TARGET_REACHED", result["reasons"])
        self.assertEqual(1, raw.pump_count)
        result, raw, _, _, _ = self.run_case(cycles=1)
        self.assertEqual("HUMAN", result["status"])
        self.assertEqual(("MAX_CYCLES_REACHED",), result["reasons"])
        self.assertEqual(1, raw.pump_count)

    def test_budget_reserves_return_and_accumulates_across_targets(self):
        result, raw, _, _, _ = self.run_case(budget=300)
        self.assertEqual("HUMAN", result["status"])
        self.assertEqual(0, raw.pump_count)
        self.assertEqual(0, raw.opens)
        result, raw, _, _, _ = self.run_case(budget=500)
        self.assertEqual("HUMAN", result["status"])
        self.assertEqual(1, raw.pump_count)
        self.assertIn("TASK_BUDGET_INCLUDES_RETURN_EXCEEDED", result["reasons"])

    def test_truncated_offset_and_raster_are_not_executed(self):
        for kwargs in ({"offset": replace(mock_offset(), scope_to_nozzle_delta_steps=(0, 24 * 320))}, {"scenario": "raster"}):
            with self.subTest(kwargs=kwargs):
                result, raw, _, _, folder = self.run_case(**kwargs)
                self.assertEqual("HUMAN", result["status"])
                self.assertEqual(0, raw.pump_count)
                self.assertEqual(0, raw.opens)
                self.assertTrue((folder / "cycle_001/path_overlay.png").exists())

    def test_pair_decline_prevents_success_and_next_target(self):
        result, raw, _, _, _ = self.run_case(compare=lambda _: False)
        self.assertEqual("HUMAN", result["status"])
        self.assertEqual(1, raw.pump_count)
        self.assertFalse(result["cycles"][0]["comparability"]["pair_confirmed"])

    def test_wrong_protocol_hello_does_not_start_motion_or_pump(self):
        result, raw, _, executor, _ = self.run_case("wrong-protocol")
        self.assertEqual("ERROR", result["status"])
        self.assertEqual(0, raw.pump_count)
        self.assertFalse(any(line.startswith(b"MOVEXY ") for line in raw.writes))
        self.assertTrue(load_position(executor.position_path).known)
