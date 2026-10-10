"""维护隔离和进程锁验收；不打开 COM/相机。"""
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch
from microcleaning.control_system.serial.resource_lease import ResourceLease, device_resources
from microcleaning.control_system.safety.maintenance import authorize_maintenance_move
from microcleaning.control_system.planning.stage2_position import set_zero, load_position


class MaintenanceIsolationTests(unittest.TestCase):
    def test_cancellation_during_confirmation_leaves_position_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "position.json"
            set_zero(path)
            cancelled = [False]
            def confirm(_):
                cancelled[0] = True
                return "YES"
            with self.assertRaises(PermissionError):
                authorize_maintenance_move("MOVEXY 100 FWD 0 FWD", path,
                    enabled=True, confirm=confirm, cancelled=lambda: cancelled[0])
            self.assertEqual(load_position(path).xy(), (0, 0))

    def test_raw_command_allowlist_blocks_injection_and_old_paths(self):
        from scripts.send_stage2_cmd import validated_command
        for command in ("HELLO\r\nMOVEXY 10 FWD 0 FWD", " STOP\n", "PUMP ON", "MCV1|PUMP|x|100",
            "TO_NEEDLE", "MOVE 1 FWD", "ARM", "CLEAR", "movexy 10 FWD 0 FWD", "SPEED 20001",
            "MOVEXY 1001 FWD 0 FWD", "MOVEXY 1 FWD 0 FWD junk", "HELLO\t", "HELLO\x00"):
            with self.subTest(command=command), self.assertRaises(ValueError):
                validated_command(command, maintenance_mode=True)
        for command in ("HELLO", "READXY", "STOP", "SPEED 100", "MOVEXY 1000 REV 0 FWD"):
            self.assertEqual(validated_command(command, maintenance_mode=True), command)
        with self.assertRaises(ValueError):
            validated_command("MOVEXY 1 FWD 0 FWD", maintenance_mode=False)

    def test_missing_mode_and_denial_leave_position_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "position.json"
            set_zero(path)
            for enabled, answer in ((False, "YES"), (True, "no")):
                with self.assertRaises(PermissionError):
                    authorize_maintenance_move("MOVEXY 100 FWD 0 FWD", path, enabled=enabled, confirm=lambda _: answer)
                self.assertEqual(load_position(path).xy(), (0, 0))

    def test_invalidates_before_any_caller_transmission(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "position.json"
            set_zero(path)
            authorize_maintenance_move("MOVEXY 100 REV 800 FWD", path, enabled=True, confirm=lambda _: "YES")
            self.assertIsNone(load_position(path).xy())
            self.assertEqual(load_position(path).status, "POSITION_UNCERTAIN")

    def test_cap_cannot_be_overridden_by_yes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "position.json"
            set_zero(path)
            with self.assertRaises(PermissionError):
                authorize_maintenance_move("MOVEXY 1001 REV 0 FWD", path, enabled=True, confirm=lambda _: "YES")
            self.assertTrue(load_position(path).known)

    def test_write_failure_propagates_before_motion(self):
        with patch("microcleaning.control_system.safety.maintenance.mark_unknown", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                authorize_maintenance_move("MOVEXY 100 FWD 0 FWD", "not_used", enabled=True, confirm=lambda _: "YES")

    def test_cross_process_lock_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = ResourceLease(("com:mock-only",), directory=tmp).acquire()
            code = "from microcleaning.control_system.serial.resource_lease import ResourceLease; ResourceLease(('COM:MOCK-ONLY',),directory=" + repr(tmp) + ").acquire()"
            child = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
            self.assertNotEqual(child.returncode, 0)
            self.assertIn("DEVICE_OR_POSITION_FILE_BUSY", child.stderr)
            lock.close()
            child = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
            self.assertEqual(child.returncode, 0, child.stderr)

    def test_partial_lease_acquisition_releases_prior_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            with ResourceLease(("b",), directory=tmp):
                with self.assertRaises(PermissionError):
                    ResourceLease(("a", "b"), directory=tmp).acquire()
                with ResourceLease(("a",), directory=tmp):
                    pass

