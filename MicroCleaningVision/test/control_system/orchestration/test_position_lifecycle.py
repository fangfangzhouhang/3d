"""位置记录与物理发令之间的故障边界；全部采用串口替身。"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from demo.closed_loop_fixture import MockF103Serial, MockFrames, mock_offset
from microcleaning.contracts import Observation
from microcleaning.control_system.orchestration.hardware_executor import HardwareExecutor
from microcleaning.control_system.planning.cleaning_plan import CleaningPlan, CleaningStrategy
from microcleaning.control_system.planning.path_preview import PathPlaceholderConfig
from microcleaning.control_system.planning.stage2_geometry import build_cycle_geometry
from microcleaning.control_system.planning import stage2_position as ledger
from microcleaning.control_system.serial.f103_session import F103SerialSession
from microcleaning.control_system.serial.stage2_link import Stage2SerialLink
from microcleaning.control_system.serial.stm32_serial import STM32SerialController
from microcleaning.vision.contamination import ContaminationMeasurement


TEMP_ROOT = Path("D:/大创/tmp/mcv_impl_20261010")


class _PositionFixture(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "position.json"
        ledger.set_zero(self.path)
        self.lines = ("MOVEXY 20 FWD 7 REV", "MOVEXY 0 FWD 2 FWD")


class PositionLifecycleTests(_PositionFixture):
    def test_restart_with_pending_refuses_old_position(self):
        ledger.begin_motion(self.path, self.lines, run_id="r", motion_id="m", expected_before=(0, 0))
        restarted = ledger.load_position(self.path)
        self.assertFalse(restarted.known)
        self.assertIsNone(restarted.xy())
        self.assertEqual("MOVING", restarted.status)
        self.assertEqual([20, -5], restarted.pending_motion["expected_steps"])
        with self.assertRaisesRegex(ledger.PositionLifecycleError, "POSITION_UNKNOWN_BEFORE_MOTION"):
            ledger.begin_motion(self.path, self.lines, run_id="r2", motion_id="m2")

    def test_complete_pending_exactly_once(self):
        epoch = ledger.load_position(self.path).reference_epoch
        ledger.begin_motion(self.path, self.lines, run_id="r", motion_id="m")
        completed = ledger.record_completed(self.path, self.lines, run_id="r", motion_id="m")
        self.assertEqual((20, -5), completed.xy())
        self.assertEqual(epoch, completed.reference_epoch)
        self.assertEqual("READY", completed.status)
        self.assertIsNone(completed.pending_motion)
        with self.assertRaisesRegex(ledger.PositionLifecycleError, "POSITION_PENDING_MISSING"):
            ledger.record_completed(self.path, self.lines, run_id="r", motion_id="m")
        self.assertEqual((20, -5), ledger.load_position(self.path).xy())

    def test_partial_foreign_or_tampered_completion_stays_pending(self):
        ledger.begin_motion(self.path, self.lines, run_id="r", motion_id="m")
        for lines, run_id, motion_id in ((self.lines[:1], "r", "m"),
                                        (self.lines, "foreign", "m"),
                                        (self.lines, "r", "foreign")):
            with self.subTest(lines=lines, run_id=run_id, motion_id=motion_id):
                with self.assertRaises(ledger.PositionLifecycleError):
                    ledger.record_completed(self.path, lines, run_id=run_id, motion_id=motion_id)
                self.assertIsNone(ledger.load_position(self.path).xy())

    def test_atomic_commit_failure_keeps_pending_for_restart(self):
        ledger.begin_motion(self.path, self.lines, run_id="r", motion_id="m")
        with patch.object(ledger.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(ledger.PositionPersistenceError):
                ledger.record_completed(self.path, self.lines, run_id="r", motion_id="m")
        self.assertEqual("MOVING", ledger.load_position(self.path).status)
        self.assertIsNone(ledger.load_position(self.path).xy())

    def test_only_proven_not_started_can_restore_and_manual_zero_clears_pending(self):
        ledger.begin_motion(self.path, self.lines, run_id="r", motion_id="m")
        with self.assertRaises(ledger.PositionLifecycleError):
            ledger.record_not_started(self.path, run_id="r", motion_id="wrong")
        self.assertEqual((0, 0), ledger.record_not_started(self.path, run_id="r", motion_id="m").xy())
        epoch = ledger.load_position(self.path).reference_epoch
        ledger.begin_motion(self.path, self.lines, run_id="r", motion_id="again")
        reset = ledger.set_zero(self.path)
        self.assertEqual((0, 0), reset.xy())
        self.assertNotEqual(epoch, reset.reference_epoch)
        self.assertIsNone(reset.pending_motion)

    def test_legacy_valid_ready_ledger_remains_readable(self):
        self.path.write_text(json.dumps({"format": ledger.POSITION_FORMAT, "known": True,
                                        "x_steps": 12, "y_steps": -3, "zero_set_at": "legacy"}), encoding="utf-8")
        position = ledger.load_position(self.path)
        self.assertEqual((12, -3), position.xy())
        self.assertEqual("legacy", position.reference_epoch)
        ledger.begin_motion(self.path, self.lines, run_id="r", motion_id="m")
        self.assertIsNone(ledger.load_position(self.path).xy())


class ExecutorPositionLifecycleTests(_PositionFixture):

    def make_executor(self, *, pump=True, scenario="success", confirm=None):
        self.frames = MockFrames(scenario)
        self.frames.selected = 0
        self.raw = MockF103Serial(self.frames, scenario)
        self.events = []
        self.session = F103SerialSession(serial_factory=self.raw.factory)
        self.executor = HardwareExecutor(session=self.session,
            link=Stage2SerialLink(session=self.session, armed=True),
            controller=STM32SerialController(session=self.session, arm_pump=pump),
            position_path=self.path, confirm=confirm or (lambda facts: True), on_position=self.events.append)
        self.addCleanup(self.executor.close)
        plan = CleaningPlan(CleaningStrategy.CENTER_POINT, "image_px", (320, 240), 100,
                            ((120.0, 90.0),), (0,), "synthetic")
        self.geometry = build_cycle_geometry(plan, base=PathPlaceholderConfig(), calibration=None,
            offset=mock_offset(), observation_position=(0, 0), task_id="task")
        self.observation = Observation("pre", "task", datetime.now(timezone.utc).isoformat(), "f", "pre.png",
                                       0.9, 0.9, 0.9, (), "test")
        self.preview = {"target_id": "S0001", "position_zero_set_at": ledger.load_position(self.path).zero_set_at}
        self.measurement = ContaminationMeasurement(100, (120.0, 90.0), 1.0, 0.9, component_count=1)

    def execute(self):
        return self.executor.execute(geometry=self.geometry, observation=self.observation,
            measurement=self.measurement, preview=self.preview, stage=lambda phase: None)

    def test_every_outbound_and_return_byte_has_durable_pending(self):
        for pump in (True, False):
            with self.subTest(pump=pump):
                ledger.set_zero(self.path)
                self.make_executor(pump=pump)
                original = self.raw.write
                recorded = []
                def inspect(data):
                    if data.startswith(b"MOVEXY "):
                        current = ledger.load_position(self.path)
                        self.assertEqual("MOVING", current.status)
                        self.assertIsNone(current.xy())
                        recorded.append(current.pending_motion["motion_id"])
                    return original(data)
                self.raw.write = inspect
                result = self.execute()
                self.assertEqual("RETURNED" if pump else "MOVED", result.status)
                self.assertEqual((0, 0), ledger.load_position(self.path).xy())
                self.assertIn(self.geometry.outbound_request.request_id, recorded)
                self.assertIn(self.geometry.return_request.request_id, recorded)
                self.assertEqual(["pending", "committed", "pending", "committed"],
                                 [entry["phase"] for entry in result.position_lifecycle])
                self.executor.close()

    def test_start_persistence_failure_sends_no_motion_or_pump(self):
        self.make_executor()
        with patch.object(ledger.os, "replace", side_effect=OSError("disk unavailable")):
            result = self.execute()
        self.assertEqual("ERROR", result.status)
        self.assertIn("POSITION_PERSISTENCE_FAILED", result.reasons)
        self.assertFalse(any(line.startswith(b"MOVEXY ") for line in self.raw.writes))
        self.assertEqual(0, self.raw.pump_count)

    def test_commit_failure_stops_preserves_receipt_and_restart_unknown(self):
        self.make_executor()
        original = ledger.os.replace
        def fail_ready_or_unknown(source, target):
            payload = json.loads(Path(source).read_text(encoding="utf-8"))
            if payload["status"] != "MOVING":
                raise OSError("persistent storage fault")
            return original(source, target)
        with patch.object(ledger.os, "replace", fail_ready_or_unknown):
            result = self.execute()
        self.assertEqual("ERROR", result.status)
        self.assertEqual(0, self.raw.pump_count)
        self.assertIsNotNone(result.outbound_result)
        self.assertIn(b"MCV1|STOP\n", self.raw.writes)
        self.assertIn("position_persistence_error", result.stop_evidence)
        self.assertEqual("MOVING", ledger.load_position(self.path).status)
        self.assertIsNone(ledger.load_position(self.path).xy())
        self.assertEqual("POSITION_UNCERTAIN", self.executor.position_snapshot["status"])
        self.assertIsNone(self.executor.position_snapshot["estimated_xy_steps"])

    def test_return_commit_failure_blocks_post_and_keeps_return_receipt(self):
        self.make_executor()
        original = ledger.os.replace
        calls = {"commits": 0}
        def fail_second_commit(source, target):
            payload = json.loads(Path(source).read_text(encoding="utf-8"))
            if payload["status"] == "READY":
                calls["commits"] += 1
                if calls["commits"] == 2:
                    raise OSError("return commit fault")
            return original(source, target)
        with patch.object(ledger.os, "replace", fail_second_commit):
            result = self.execute()
        self.assertEqual("ERROR", result.status)
        self.assertEqual(1, self.raw.pump_count)
        self.assertIsNotNone(result.return_result)
        self.assertIsNone(result.returned_at)
        self.assertIsNone(ledger.load_position(self.path).xy())

    def test_declined_spray_uses_tracked_return_without_pump(self):
        self.make_executor(confirm=lambda facts: facts.get("phase") != "align")
        result = self.execute()
        self.assertEqual("HUMAN", result.status)
        self.assertEqual(0, self.raw.pump_count)
        self.assertEqual((0, 0), ledger.load_position(self.path).xy())
        self.assertEqual(2, sum(item["phase"] == "committed" for item in result.position_lifecycle))

    def test_unknown_restart_never_asks_or_opens_serial(self):
        self.make_executor()
        ledger.begin_motion(self.path, self.geometry.outbound.lines, run_id="old", motion_id="old")
        self.executor.confirm = lambda facts: self.fail("unknown position must not be authorized")
        result = self.execute()
        self.assertEqual("HUMAN", result.status)
        self.assertEqual([], self.raw.writes)
        self.assertEqual(0, self.raw.opens)

    def test_estimates_use_segment_origin_not_sum_of_repeated_counts(self):
        self.make_executor()
        ledger.begin_motion(self.path, self.lines, run_id="task", motion_id="m")
        self.executor._active_motion = {"pending": ledger.load_position(self.path).pending_motion,
            "segment_index": None, "segment_line": None, "estimated_xy": (0, 0)}
        event = {"motion_id": "m", "segment_index": 0, "segment_line": self.lines[0], "x_sent": 5, "y_sent": 3}
        self.executor._motion_progress(event)
        self.executor._motion_progress(event)
        self.assertEqual((5, -3), self.executor.position_snapshot["estimated_xy_steps"])
        self.executor._motion_progress({**event, "segment_index": 1, "segment_line": self.lines[1],
                                        "x_sent": 20, "y_sent": 1})
        self.assertEqual((20, -6), self.executor.position_snapshot["estimated_xy_steps"])
        self.assertEqual((0, 0), self.executor.position_snapshot["confirmed_xy_steps"])
        self.executor._motion_progress({**event, "x_sent": 20000})
        self.assertIsNone(self.executor.position_snapshot["estimated_xy_steps"])


class StandaloneMotionLifecycleTests(_PositionFixture):
    def run_motion(self, *, scenario="success"):
        from demo.motion_mode import _stage2_move
        from microcleaning.control_system.planning.stage2_axes import single_move_dispatch
        from microcleaning.control_system.planning.work_frame import MotorCalibration
        raw = MockF103Serial(MockFrames(), scenario)
        self.raw = raw
        return _stage2_move(run_id="standalone", run_dir=Path(self.folder.name),
            dispatch=single_move_dispatch(20, "FWD", 7, "REV"), start_reference="image_center",
            calibration=MotorCalibration("mock://calibration", "0" * 64, 0.01, 0.01, None, ()),
            position_path=self.path, motion_confirm=lambda request, plan: True, arm_stage2_xy=True,
            serial_port=None, baudrate=115200, serial_timeout=0.1, serial_factory=raw.factory)

    def test_standalone_success_commits_before_closing_same_connection(self):
        result = self.run_motion()
        self.assertEqual("sent", result.status)
        self.assertEqual((20, -7), result.position_after.xy())
        receipt = json.loads((Path(self.folder.name) / "stage2_receipt.json").read_text(encoding="utf-8"))
        self.assertEqual("READY", receipt["position_after"]["status"])

    def test_standalone_commit_failure_stops_on_its_original_connection(self):
        original = ledger.os.replace
        def fail_after_pending(source, target):
            if json.loads(Path(source).read_text(encoding="utf-8"))["status"] != "MOVING":
                raise OSError("persistent failure")
            return original(source, target)
        with patch.object(ledger.os, "replace", fail_after_pending):
            result = self.run_motion()
        self.assertEqual("failed", result.status)
        self.assertEqual("POSITION_PERSISTENCE_FAILED", result.error.reason_code)
        self.assertTrue(result.error.stopped)
        self.assertIsNotNone(result.transmit)
        self.assertEqual(1, self.raw.opens)
        self.assertEqual(b"STOP\r\n", self.raw.writes[-1])
        self.assertIsNone(result.position_after.xy())
        receipt = json.loads((Path(self.folder.name) / "stage2_receipt.json").read_text(encoding="utf-8"))
        self.assertFalse(receipt["position_after"]["known"])
        self.assertEqual("MOVING", receipt["position_after"]["status"])

    def test_standalone_start_persistence_failure_never_opens_link(self):
        with patch.object(ledger.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(ledger.PositionPersistenceError):
                self.run_motion()
        self.assertEqual(0, self.raw.opens)
        self.assertEqual([], self.raw.writes)
        receipt = json.loads((Path(self.folder.name) / "stage2_receipt.json").read_text(encoding="utf-8"))
        self.assertEqual("aborted", receipt["status"])
        self.assertIsNone(receipt["transmit"])


if __name__ == "__main__":
    unittest.main()
