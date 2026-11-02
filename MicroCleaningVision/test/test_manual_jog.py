"""Manual jog core against the existing Stage 2 gate and scripted board."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from demo import motion_mode
from demo.closed_loop_fixture import MockF103Serial, MockFrames
from demo.manual_jog import JogController, STEP_LABELS, STEP_PRESETS
from microcleaning.control_system.planning import stage2_position as ledger
from microcleaning.control_system.serial.resource_lease import ResourceLease, device_resources


class JogControllerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.position_path = self.root / "position.json"
        self.calibration = self.root / "mm_per_px.json"
        self.calibration.write_text(json.dumps({"mm_per_px_x": .0187, "mm_per_px_y": .0181,
            "warnings": [], "feeds_action_request": False}), encoding="utf-8")
        self.raw = MockF103Serial(MockFrames())
        self.confirm = Mock(return_value=True)
        self.controller = self.make_controller()

    def make_controller(self, **overrides):
        options = dict(stage2_calibration=self.calibration, position_path=self.position_path,
            output_root=self.root / "runs", serial_timeout=.08,
            serial_factory=self.raw.factory, confirm=self.confirm)
        options.update(overrides)
        return JogController(**options)

    def zero(self):
        self.controller.set_zero(confirmed=True)

    def assert_no_serial(self):
        self.assertEqual(0, self.raw.opens)
        self.assertEqual([], self.raw.writes)

    def assert_preset(self, steps, label):
        self.zero()
        with patch.object(motion_mode, "_stage2_move", wraps=motion_mode._stage2_move) as execute:
            self.assertTrue(self.controller.arm())
            result = self.controller.jog("FWD", None, steps)
        self.assertEqual("sent", result.status, result.message)
        self.assertEqual((steps, 0), result.position.xy())
        self.assertEqual(1, execute.call_count)
        args = execute.call_args.kwargs
        self.assertEqual((f"MOVEXY {steps} FWD 0 FWD",), args["dispatch"].lines)
        self.assertFalse(args["dispatch"].truncated)
        self.assertEqual("image_center", args["start_reference"])
        self.assertEqual(115200, args["baudrate"])
        self.assertEqual(.08, args["serial_timeout"])
        calibration = args["calibration"]
        self.assertEqual(hashlib.sha256(self.calibration.read_bytes()).hexdigest(), calibration.sha256)
        self.assertEqual(self.calibration.as_posix(), calibration.ref)
        self.assertEqual(label, STEP_LABELS[STEP_PRESETS.index(steps)])
        self.assertEqual(1, self.raw.opens)
        self.assertTrue(self.raw.closed)
        self.assertFalse(self.controller.armed)
        written = b"".join(self.raw.writes)
        self.assertNotIn(b"MCV1", written)
        self.assertNotIn(b"PUMP", written)
        self.assertEqual(1, written.count(b"MOVEXY "))
        intent = json.loads((result.run_dir / "stage2_intent.json").read_text(encoding="utf-8"))
        receipt = json.loads((result.run_dir / "stage2_receipt.json").read_text(encoding="utf-8"))
        self.assertEqual("HUMAN", intent["gate_decision"]["outcome"])
        self.assertEqual("ALLOW", intent["final_decision"]["outcome"])
        self.assertEqual("sent", receipt["status"])
        self.assertEqual("READY", receipt["position_after"]["status"])
        self.assertIsNone(receipt["position_after"]["pending_motion"])

    def test_preset_32_steps_01_mm(self):
        self.assert_preset(32, "0.1 mm")

    def test_preset_160_steps_05_mm(self):
        self.assert_preset(160, "0.5 mm")

    def test_preset_320_steps_1_mm(self):
        self.assert_preset(320, "1 mm")

    def test_preset_1600_steps_5_mm(self):
        self.assert_preset(1600, "5 mm")

    def test_preset_3200_steps_10_mm(self):
        self.assert_preset(3200, "10 mm")

    def test_preset_6400_steps_20_mm(self):
        self.assert_preset(6400, "20 mm")

    def test_arbitrary_steps_above_old_panel_cap_are_sent(self):
        # Panel cap of 1600 was removed at the lead's request (2026-11-02):
        # any positive count up to the firmware per-line limit may be jogged.
        self.zero()
        for steps in (2000, 20000):
            with self.subTest(steps=steps):
                self.controller.arm()
                result = self.controller.jog("FWD", None, steps)
                self.assertEqual("sent", result.status, result.message)

    def test_all_single_axis_directions_and_diagonals_update_same_ledger(self):
        self.zero()
        expected = [0, 0]
        folders = set()
        for dx, dy in (("FWD", None), ("REV", None), (None, "FWD"), (None, "REV"),
                       ("FWD", "FWD"), ("FWD", "REV"), ("REV", "FWD"), ("REV", "REV")):
            with self.subTest(dx=dx, dy=dy):
                self.controller.arm()
                result = self.controller.jog(dx, dy, 1600)
                self.assertEqual("sent", result.status, result.message)
                expected = [value + (1600 if direction == "FWD" else -1600 if direction == "REV" else 0)
                            for value, direction in zip(expected, (dx, dy))]
                self.assertEqual(tuple(expected), ledger.load_position(self.position_path).xy())
                folders.add(result.run_dir)
        self.assertEqual(8, len(folders))
        self.assertEqual(8, self.raw.opens)
        self.assertEqual(0, self.raw.pump_count)

    def test_over_firmware_cap_is_rejected_whole_without_dispatch_or_confirmation(self):
        self.zero()
        before = self.position_path.read_bytes()
        for steps in (20001, 10**40):
            for axes in (("FWD", None), (None, "REV"), ("FWD", "REV")):
                with self.subTest(steps=steps, axes=axes):
                    self.controller.arm()
                    with patch("demo.manual_jog.single_move_dispatch") as dispatch:
                        result = self.controller.jog(*axes, steps)
                    self.assertEqual("denied", result.status)
                    self.assertEqual(("JOG_STEPS_OVER_FIRMWARE_CAP",), result.reason_codes)
                    self.assertIn("20000", result.message)
                    dispatch.assert_not_called()
                    self.assertFalse(self.controller.armed)
        self.confirm.assert_not_called()
        self.assert_no_serial()
        self.assertEqual(before, self.position_path.read_bytes())

    def test_invalid_presets_axes_and_injection_consume_arm(self):
        self.zero()
        for dx, dy, steps in (("FWD", None, 0),
                ("FWD", None, -32), ("FWD", None, True), ("FWD", None, 32.0),
                ("FWD", None, "32"), (None, None, 32), ("fwd", None, 32),
                ("FWD\nPUMP ON", None, 32), (None, "MCV1|PUMP", 32), (0, "FWD", 32)):
            with self.subTest(dx=dx, dy=dy, steps=steps):
                self.controller.arm()
                result = self.controller.jog(dx, dy, steps)
                self.assertEqual("denied", result.status)
                self.assertFalse(self.controller.armed)
        self.confirm.assert_not_called()
        self.assert_no_serial()

    def test_unknown_corrupt_and_pending_position_are_denied(self):
        for state in ("missing", "corrupt", "pending", "unknown"):
            with self.subTest(state=state):
                if state == "corrupt":
                    self.position_path.write_text("broken JSON", encoding="utf-8")
                elif state == "pending":
                    ledger.set_zero(self.position_path)
                    ledger.begin_motion(self.position_path, ("MOVEXY 32 FWD 0 FWD",), run_id="old", motion_id="old")
                elif state == "unknown":
                    ledger.mark_unknown(self.position_path, run_id="old", reason="power lost")
                self.controller.arm()
                result = self.controller.jog("FWD", None, 32)
                self.assertEqual("denied", result.status)
                self.assertEqual(("POSITION_UNKNOWN",), result.reason_codes)
                self.assertFalse(self.controller.armed)
        self.confirm.assert_not_called()
        self.assert_no_serial()

    def test_constructor_has_no_zero_arm_or_serial_side_effect(self):
        self.assertFalse(self.position_path.exists())
        self.assertIsNone(self.controller.get_position().xy())
        self.assertFalse(self.controller.armed)
        self.assert_no_serial()

    def test_set_zero_requires_explicit_boolean_human_confirmation(self):
        for confirmation in (False, None, "yes", 1):
            self.controller.arm()
            with self.assertRaisesRegex(PermissionError, "POSITION_REFERENCE_NOT_CONFIRMED"):
                self.controller.set_zero(confirmed=confirmation)
            self.assertFalse(self.controller.armed)
            self.assertFalse(self.position_path.exists())
        with patch.object(ledger, "set_zero", wraps=ledger.set_zero) as zero:
            result = self.controller.set_zero(confirmed=True)
        zero.assert_called_once_with(self.position_path)
        self.assertEqual((0, 0), result.xy())
        self.assert_no_serial()

    def test_arm_allows_only_one_jog_and_explicit_disarm_blocks(self):
        self.zero()
        result = self.controller.jog("FWD", None, 32)
        self.assertIn("JOG_NOT_ARMED", result.reason_codes)
        self.assert_no_serial()
        self.controller.arm()
        self.assertEqual("sent", self.controller.jog("FWD", None, 32).status)
        self.assertEqual("denied", self.controller.jog("FWD", None, 32).status)
        self.assertEqual(1, self.raw.opens)
        self.assertEqual((32, 0), self.controller.get_position().xy())
        self.controller.arm()
        self.controller.disarm()
        self.assertEqual("denied", self.controller.jog("FWD", None, 32).status)
        self.assertEqual(1, self.raw.opens)

    def test_decline_never_opens_port_and_next_rearmed_jog_succeeds(self):
        self.zero()
        self.confirm.return_value = False
        self.controller.arm()
        first = self.controller.jog("FWD", "REV", 160)
        self.assertEqual("declined", first.status)
        self.assertEqual("human_declined", first.motion.status)
        self.assertFalse(self.controller.armed)
        self.assert_no_serial()
        self.assertFalse((first.run_dir / "stage2_receipt.json").exists())
        self.confirm.return_value = True
        self.assertEqual("denied", self.controller.jog("FWD", "REV", 160).status)
        self.controller.arm()
        second = self.controller.jog("FWD", "REV", 160)
        self.assertEqual("sent", second.status, second.message)
        self.assertEqual((160, -160), second.position.xy())
        self.assertEqual((160, -160), self.raw.position)
        self.assertEqual(1, self.raw.opens)

    def test_missing_callback_never_self_approves(self):
        self.zero()
        controller = self.make_controller(confirm=None)
        controller.arm()
        result = controller.jog("FWD", None, 32)
        self.assertEqual("denied", result.status)
        self.assertEqual("human_pending", result.motion.status)
        self.assertFalse(controller.armed)
        self.assert_no_serial()

    def test_non_boolean_confirmation_is_not_approval(self):
        self.zero()
        for answer in ("yes", "rewash", 1, object()):
            with self.subTest(answer=answer):
                self.confirm.return_value = answer
                self.controller.arm()
                self.assertEqual("declined", self.controller.jog("FWD", None, 32).status)
                self.assertFalse(self.controller.armed)
        self.assert_no_serial()

    def test_existing_gate_denies_missing_or_warned_calibration(self):
        self.zero()
        warned = self.root / "warned.json"
        payload = json.loads(self.calibration.read_text(encoding="utf-8"))
        payload["warnings"] = ["direction not verified"]
        warned.write_text(json.dumps(payload), encoding="utf-8")
        for calibration, code in ((None, "CALIBRATION_MISSING"), (warned, "CALIBRATION_HAS_WARNINGS")):
            controller = self.make_controller(stage2_calibration=calibration)
            controller.arm()
            result = controller.jog(None, "REV", 32)
            self.assertEqual("denied", result.status)
            self.assertIn(code, result.reason_codes)
            self.assertFalse(controller.armed)
        self.confirm.assert_not_called()
        self.assert_no_serial()

    def test_missing_serial_configuration_is_denied_without_scanning(self):
        self.zero()
        controller = self.make_controller(serial_factory=None)
        controller.arm()
        result = controller.jog("FWD", None, 32)
        self.assertEqual("denied", result.status)
        self.assertIn("JOG_SERIAL_PORT_REQUIRED", result.reason_codes)
        self.assertFalse(controller.armed)
        self.confirm.assert_not_called()

    def test_incomplete_motion_stops_invalidates_ledger_and_disarms(self):
        self.zero()
        self.raw.scenario = "motion-short"
        self.controller.arm()
        result = self.controller.jog("FWD", None, 32)
        self.assertEqual("failed", result.status, result.message)
        self.assertIn("INCOMPLETE_MOTION", result.reason_codes)
        self.assertTrue(result.motion.error.stopped)
        self.assertEqual(b"STOP\r\n", self.raw.writes[-1])
        self.assertIsNone(self.controller.get_position().xy())
        self.assertFalse(self.controller.armed)
        self.controller.arm()
        retry = self.controller.jog("FWD", None, 32)
        self.assertIn("POSITION_UNKNOWN", retry.reason_codes)
        self.assertEqual(1, self.raw.opens)
        self.assertEqual(0, self.raw.pump_count)

    def test_callback_error_and_interrupt_disarm_without_opening_port(self):
        self.zero()
        self.confirm.side_effect = RuntimeError("confirmation failed")
        self.controller.arm()
        result = self.controller.jog("FWD", None, 32)
        self.assertEqual("failed", result.status)
        self.assertIn("confirmation failed", result.message)
        self.assertFalse(self.controller.armed)
        self.confirm.side_effect = KeyboardInterrupt()
        self.controller.arm()
        with self.assertRaises(KeyboardInterrupt):
            self.controller.jog("FWD", None, 32)
        self.assertFalse(self.controller.armed)
        self.assert_no_serial()

    def test_callback_cannot_reenter_jog_rearm_or_zero(self):
        self.zero()
        def confirm(request, plan):
            self.assertFalse(self.controller.arm())
            self.assertEqual("denied", self.controller.jog("REV", None, 32).status)
            with self.assertRaisesRegex(PermissionError, "JOG_BUSY"):
                self.controller.set_zero(confirmed=True)
            self.assertEqual(("MOVEXY 32 FWD 0 FWD",), request.lines)
            self.assertEqual([32, 0], plan["position_after"])
            return True
        self.confirm.side_effect = confirm
        self.controller.arm()
        result = self.controller.jog("FWD", None, 32)
        self.assertEqual("sent", result.status, result.message)
        self.assertFalse(self.controller.armed)
        self.assertEqual(1, self.raw.opens)

    def test_resource_busy_blocks_jog_and_zero_without_port(self):
        self.zero()
        before = self.position_path.read_bytes()
        with ResourceLease(device_resources(None, self.position_path)):
            self.controller.arm()
            result = self.controller.jog("FWD", None, 32)
            self.assertEqual("denied", result.status)
            self.assertIn("DEVICE_OR_POSITION_FILE_BUSY", result.reason_codes)
            self.assertFalse(self.controller.armed)
            with self.assertRaisesRegex(PermissionError, "DEVICE_OR_POSITION_FILE_BUSY"):
                self.controller.set_zero(confirmed=True)
        self.assertEqual(before, self.position_path.read_bytes())
        self.assert_no_serial()
        self.controller.arm()
        self.assertEqual("sent", self.controller.jog("FWD", None, 32).status)

    def test_bad_serial_parameters_and_calibration_use_existing_validation(self):
        for options in ({"baudrate": 0}, {"baudrate": True}, {"serial_timeout": 0},
                        {"serial_timeout": float("nan")}, {"serial_timeout": float("inf")}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.make_controller(**options)
        self.calibration.write_text('{"mm_per_px_x": 0}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.make_controller()
        self.assert_no_serial()

    def test_import_and_constructor_work_when_gui_imports_are_blocked(self):
        code = """
import importlib.abc
import sys
class NoGui(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('tkinter', '_tkinter'):
            raise ImportError('GUI imports blocked')
sys.meta_path.insert(0, NoGui())
from demo.manual_jog import JogController
controller = JogController()
assert not controller.armed
assert 'tkinter' not in sys.modules
"""
        completed = subprocess.run([sys.executable, "-B", "-c", code],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
        self.assertEqual(0, completed.returncode, completed.stderr)


if __name__ == "__main__":
    unittest.main()
