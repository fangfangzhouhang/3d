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
from microcleaning.control_system.safety.motion_gate import DEFAULT_SOFT_LIMIT_STEPS, STAGE2_RUN_STEP_CAP
from microcleaning.control_system.serial.f103_session import F103SerialSession
from microcleaning.control_system.serial.stage2_link import Stage2SerialLink
from microcleaning.control_system.serial.stm32_serial import STM32SerialController


def station(folder, scenario="success", *, cycles=3, retries=0, budget=1600, offset=None, armed=True, pump_armed=None, frames=None, compare=None, confirm=None, strategy="nearest_neighbor"):
    folder = Path(folder)
    frames = frames or MockFrames(scenario)
    raw = MockF103Serial(frames, scenario)
    position = folder / "mock_position.json"
    set_zero(position)
    session = F103SerialSession(serial_factory=raw.factory)
    executor = HardwareExecutor(session=session, link=Stage2SerialLink(session=session, armed=armed),
        controller=STM32SerialController(session=session, arm_pump=armed if pump_armed is None else pump_armed), position_path=position,
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
        self.assertEqual(["T1", "T2", "T3"], [cycle["frame_target_id"] for cycle in result["cycles"]])
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
        decline_next = lambda preview: preview.get("phase") != "next_incomplete"
        for scenario in ("post-quality", "noncomparable"):
            with self.subTest(scenario=scenario):
                result, raw, _, _, _ = self.run_case(scenario, confirm=decline_next)
                self.assertEqual("HUMAN", result["status"])
                self.assertEqual(1, raw.pump_count)
                self.assertIn("PRE_POST_NOT_COMPARABLE", result["reasons"])

    def test_stale_post_is_an_error_even_if_pixels_look_clean(self):
        result, raw, frames, _, _ = self.run_case("stale-post")
        self.assertEqual("ERROR", result["status"])
        self.assertIn("POST_CAPTURE_NOT_AFTER_RETURN", result["reasons"])
        self.assertFalse(any(entry["completed"] for entry in result["targets"].values()))

    def test_motion_without_pump_returns_and_does_not_spray(self):
        result, raw, _, executor, _ = self.run_case(pump_armed=False, cycles=1)
        self.assertEqual("SUCCESS", result["status"])
        self.assertEqual(("MOTION_COMPLETED_NO_PUMP",), result["reasons"])
        self.assertEqual(0, raw.pump_count)
        self.assertTrue(any(line.startswith(b"MOVEXY ") for line in raw.writes))
        self.assertFalse(any(line.startswith(b"MCV1|PUMP|") for line in raw.writes))
        self.assertEqual((0, 0), load_position(executor.position_path).xy())
        self.assertEqual("MOVED", result["cycles"][0]["execution"]["status"])
        self.assertNotIn("verification", result["cycles"][0])

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

    def test_one_move_over_budget_is_refused_without_adding_the_return(self):
        too_long = replace(mock_offset(), scope_to_nozzle_delta_steps=(0, 301))
        result, raw, _, _, _ = self.run_case(budget=300, offset=too_long)
        self.assertEqual("HUMAN", result["status"])
        self.assertEqual(0, raw.pump_count)
        self.assertEqual(0, raw.opens)
        self.assertIn("INCOMPLETE_DISPATCH", result["reasons"])
        result, raw, _, _, _ = self.run_case(budget=300)
        self.assertEqual("SUCCESS", result["status"])
        self.assertGreater(raw.pump_count, 0)

    def test_move_past_the_old_fence_is_not_stopped(self):
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result, raw, _, _, _ = self.run_case(
                budget=STAGE2_RUN_STEP_CAP,
                offset=replace(mock_offset(), scope_to_nozzle_delta_steps=(0, DEFAULT_SOFT_LIMIT_STEPS + 200)))
        text = buffer.getvalue()
        self.assertIsNone(result["boundary_notice"])
        self.assertNotIn("已拦住", text)
        self.assertGreater(raw.opens, 0)

    def test_truncated_offset_and_raster_are_not_executed(self):
        for kwargs in ({"offset": replace(mock_offset(), scope_to_nozzle_delta_steps=(0, DEFAULT_SOFT_LIMIT_STEPS + 200))}, {"scenario": "raster"}):
            with self.subTest(kwargs=kwargs):
                result, raw, _, _, folder = self.run_case(**kwargs)
                self.assertEqual("HUMAN", result["status"])
                self.assertEqual(0, raw.pump_count)
                self.assertEqual(0, raw.opens)
                self.assertTrue((folder / "cycle_001/path_overlay.png").exists())

    def test_pair_decline_prevents_success_and_next_target(self):
        result, raw, _, _, _ = self.run_case(compare=lambda _: False, confirm=lambda preview: preview.get("phase") != "next_incomplete")
        self.assertEqual("HUMAN", result["status"])
        self.assertEqual(1, raw.pump_count)
        self.assertFalse(result["cycles"][0]["comparability"]["pair_confirmed"])

    def test_yes_after_incomparable_recheck_starts_the_next_stain_even_if_pixels_remain(self):
        frames = MockFrames()
        frames.pump_finished = lambda: setattr(frames, "pumps", frames.pumps + 1)
        result, raw, _, _, _ = self.run_case(frames=frames, compare=lambda _: False, cycles=2)
        self.assertEqual(2, raw.pump_count)
        self.assertEqual(["pre", "post", "pre", "post"], frames.capture_phases)
        self.assertEqual("HUMAN", result["status"])
        self.assertEqual(("MAX_CYCLES_REACHED",), result["reasons"])
        completed = [item for item in result["targets"].values() if item["completed"]]
        self.assertEqual(2, len(completed))
        self.assertNotEqual(result["cycles"][0]["target_id"], result["cycles"][1]["target_id"])

    def test_later_frame_cannot_add_a_third_clean_beyond_the_first_picture(self):
        import io
        from contextlib import redirect_stdout

        class ExtraBlob(MockFrames):
            def __init__(self):
                super().__init__()
                self.centers = ((120, 90), (200, 110))
                self.remaining = {0, 1}

            def capture(self, phase, **kwargs):
                frame = super().capture(phase, **kwargs)
                if self.pumps and phase == "pre":
                    self.cv2.circle(frame.image, (30, 210), 7, (18, 18, 205), -1)
                return frame

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result, raw, _, _, folder = self.run_case(frames=ExtraBlob(), cycles=3)
        text = buffer.getvalue()
        self.assertEqual("SUCCESS", result["status"])
        self.assertEqual(("ALL_OBSERVED_TARGETS_COMPLETED",), result["reasons"])
        self.assertEqual(2, raw.pump_count)
        self.assertEqual(["S0001", "S0002"], result["initial_target_ids"])
        self.assertEqual(2, len(result["targets"]))
        self.assertGreaterEqual(result["ignored_new_components"], 1)
        self.assertIn("第一次画面共 2 块", text)
        self.assertNotIn("现在进行第三个", text)
        self.assertTrue((folder / "initial_roster.png").exists())

    def test_numbered_order_survives_an_unmatched_recheck(self):
        class Speck(MockFrames):
            def __init__(self):
                super().__init__()
                self.centers = ((30, 30), (160, 120))
                self.remaining = {0, 1}

            def capture(self, phase, **kwargs):
                frame = super().capture(phase, **kwargs)
                if self.pumps:
                    self.cv2.circle(frame.image, (42, 30), 2, (18, 18, 205), -1)
                return frame

        result, raw, _, _, _ = self.run_case(frames=Speck(), cycles=3)
        self.assertEqual(["S0001", "S0002"], [cycle["target_id"] for cycle in result["cycles"]])
        self.assertEqual((30.0, 30.0), tuple(round(value) for value in result["cycles"][0]["centroid_px"]))
        self.assertEqual(2, raw.pump_count)
        self.assertEqual("SUCCESS", result["status"])
        self.assertEqual(("ALL_OBSERVED_TARGETS_COMPLETED",), result["reasons"])

    def test_each_spray_cycle_stops_for_move_coincidence_return_recheck_and_next(self):
        phases = []

        def confirm(preview):
            phases.append(preview.get("phase"))
            return True

        result, raw, frames, _, _ = self.run_case(confirm=confirm, cycles=2)
        self.assertEqual("HUMAN", result["status"])
        self.assertEqual(("MAX_CYCLES_REACHED",), result["reasons"])
        self.assertEqual(2, raw.pump_count)
        self.assertEqual(
            ["move", "align", "return", "recheck", "next", "move", "align", "return", "recheck", "next"],
            phases,
        )
        self.assertEqual(["pre", "post", "pre", "post"], frames.capture_phases)

    def test_accepting_one_stain_names_the_next_and_does_not_run_it_without_a_new_yes(self):
        import io
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result, raw, _, _, _ = self.run_case(cycles=2)
        text = buffer.getvalue()
        self.assertIn("第一个污渍已清洗完成，现在进行第二个", text)
        self.assertIn("复检结果：", text)
        self.assertNotIn("引导", text)
        self.assertEqual(2, raw.pump_count)
        self.assertEqual("HUMAN", result["status"])
        self.assertNotIn("现在进行第三个", text)

    def test_coincidence_no_does_not_spray_and_can_still_return(self):
        result, raw, frames, executor, _ = self.run_case(
            cycles=1, confirm=lambda preview: preview.get("phase") != "align")
        self.assertEqual("HUMAN", result["status"])
        self.assertIn("HUMAN_DECLINED_SPRAY", result["reasons"])
        self.assertIn("RETURNED_WITHOUT_SPRAY", result["reasons"])
        self.assertEqual(0, raw.pump_count)
        self.assertFalse(any(line.startswith(b"MCV1|PUMP|") for line in raw.writes))
        self.assertTrue(any(line.startswith(b"MOVEXY ") for line in raw.writes))
        self.assertEqual((0, 0), load_position(executor.position_path).xy())
        self.assertEqual(["pre"], frames.capture_phases)

    def test_return_no_after_spray_does_not_recheck(self):
        result, raw, frames, executor, _ = self.run_case(
            cycles=1, confirm=lambda preview: preview.get("phase") != "return")
        self.assertEqual("HUMAN", result["status"])
        self.assertEqual(("HUMAN_DECLINED_RETURN",), result["reasons"])
        self.assertEqual(1, raw.pump_count)
        self.assertEqual(["pre"], frames.capture_phases)
        self.assertNotEqual((0, 0), load_position(executor.position_path).xy())
        self.assertTrue(load_position(executor.position_path).known)

    def test_recheck_no_does_not_capture_post(self):
        result, raw, frames, executor, _ = self.run_case(
            cycles=1, confirm=lambda preview: preview.get("phase") != "recheck")
        self.assertEqual("HUMAN", result["status"])
        self.assertEqual(("HUMAN_DECLINED_RECHECK",), result["reasons"])
        self.assertEqual(1, raw.pump_count)
        self.assertEqual(["pre"], frames.capture_phases)
        self.assertEqual((0, 0), load_position(executor.position_path).xy())

    def test_next_no_does_not_start_the_second_stain(self):
        result, raw, frames, _, _ = self.run_case(
            cycles=3, confirm=lambda preview: preview.get("phase") != "next")
        self.assertEqual("HUMAN", result["status"])
        self.assertIn("HUMAN_STOPPED_AFTER_RECHECK", result["reasons"])
        self.assertEqual(1, raw.pump_count)
        self.assertEqual(["pre", "post"], frames.capture_phases)
        self.assertFalse(any(entry["completed"] for entry in result["targets"].values()))

    def test_stage_overlay_tracks_scope_and_nozzle_without_using_a_zero_axis_count(self):
        from demo.closed_loop_station import StageOverlay
        overlay = StageOverlay()
        overlay.plan((0, 0), (42, 7601), (320.0, 240.0))
        overlay.feed("MOVEXY 42 FWD 42 FWD")
        overlay.feed("STEP2 X=42 BX=0 Y=42 BY=0")
        self.assertEqual((42, 42), overlay.current_xy)
        overlay.feed("MOVEXY 0 FWD 7559 FWD")
        overlay.feed("STEP2 X=42 BX=0 Y=100 BY=1")
        self.assertEqual((42, 142), overlay.current_xy)
        self.assertFalse(overlay.at_nozzle)
        overlay.feed("STEP2 X=42 BX=0 Y=7559 BY=0")
        self.assertEqual((42, 7601), overlay.current_xy)
        self.assertTrue(overlay.at_nozzle)

    def test_microscope_guides_keep_scope_center_and_both_inset_points(self):
        import numpy as np
        from demo.camera_preview import draw_stage_guides
        cv2 = __import__("cv2")
        image = np.zeros((480, 640, 3), np.uint8)
        draw_stage_guides(image, cv2, {"scope_xy": (0, 0), "nozzle_xy": (0, 7559), "current_xy": (0, 3000), "stain_px": None})
        self.assertEqual([0, 255, 255], image[240, 320].tolist())
        inset = image[480 - 148:480, 640 - 192:640]
        self.assertTrue(np.any(np.all(inset == (0, 255, 255), axis=2)))
        self.assertTrue(np.any(np.all(inset == (0, 0, 255), axis=2)))

    def test_prompt_text_separates_motion_from_spray(self):
        from unittest.mock import patch
        from demo import closed_loop_station as station
        preview = {
            "phase": "move", "cycle": 1, "target_id": "S0001", "frame_target_id": "T1",
            "area_px": 20, "centroid_px": (10, 12), "path_overlay": "missing.png",
            "include_pump": True, "pump_request": {"duration_ms": 200},
            "offset": {"setup_id": "rig", "version": "scope-nozzle-v1", "uncertainty_steps": [0, 0]},
            "geometry": {
                "target_delta_steps": [1, 2], "offset_delta_steps": [0, 3],
                "execution_position": [1, 5], "observation_position": [0, 0],
                "outbound": {"lines": ["MOVEXY 1 FWD 5 FWD"]},
                "returning": {"lines": ["MOVEXY 1 REV 5 REV"]},
                "outbound_request": {"calibration_ref": "cal"},
            },
        }
        station._LIVE_PREVIEW = None
        answers = iter(["yes", "no"])
        import io
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with patch("builtins.input", lambda: next(answers)), redirect_stdout(buffer):
            move = station.confirm_cycle(preview)
            align = station.confirm_cycle({**preview, "phase": "align"})
        text = buffer.getvalue()
        self.assertTrue(move)
        self.assertFalse(align)
        self.assertEqual((1, 5), station._OVERLAY.current_xy)
        self.assertTrue(station._OVERLAY.at_nozzle)
        self.assertNotIn("引导", text)
        self.assertIn("这一次 yes 不喷水", text)
        self.assertIn("输入 yes 才喷水", text)
        self.assertIn("输入 no 不喷水", text)

    def test_wrong_protocol_hello_does_not_start_motion_or_pump(self):
        result, raw, _, executor, _ = self.run_case("wrong-protocol")
        self.assertEqual("ERROR", result["status"])
        self.assertEqual(0, raw.pump_count)
        self.assertFalse(any(line.startswith(b"MOVEXY ") for line in raw.writes))
        self.assertTrue(load_position(executor.position_path).known)
