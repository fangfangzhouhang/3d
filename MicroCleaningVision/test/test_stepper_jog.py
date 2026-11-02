"""No physical devices: keyboard/state tests, protocol substitutes and own Tk."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from demo.stepper_jog import (JogConfig, JogController, JogDevice, JogRequest,
                              JogWindow, parse_args)
from microcleaning.control_system.planning.stage2_position import load_position, set_zero
from microcleaning.control_system.serial.resource_lease import ResourceLease, device_resources


def wait_for(predicate, seconds=2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("state wait timed out")


class FakeDevice:
    def __init__(self):
        self.opened = 0
        self.moves = []
        self.stops = 0
        self.closed = 0
        self.block = False
        self.entered = threading.Event()
        self.fail = False

    def open(self, cancel):
        self.opened += 1

    def move(self, request, cancel):
        self.moves.append(request)
        self.entered.set()
        if self.fail:
            raise OSError("disconnected")
        if self.block:
            cancel.wait(2)
        if cancel.is_set():
            raise InterruptedError("cancelled")

    def stop(self):
        self.stops += 1

    def close(self):
        self.closed += 1


class ProtocolSerial:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.sent = []
        self.replies = []
        self.closed = False
        self.short = False
        self.bad_start = False
        self.short_count = False
        self.partial = False
        self.silent = False
        self.move = (0, "FWD", 0, "FWD")

    def reset_input_buffer(self):
        self.replies.clear()

    def write(self, data):
        self.sent.append(data)
        if self.short:
            return 0
        if self.silent:
            return len(data)
        if data == b"HELLO\r\n":
            self.replies.append(b"STEP_OK v0.3\n" if not self.partial else b"STEP_OK v0.3")
        elif data.startswith(b"MOVEXY "):
            _, x, dx, y, dy = data.decode().split()
            self.move = (int(x), dx, int(y), dy)
            self.replies.append(f"STEP2_START X={int(x) + int(self.bad_start)} {dx} Y={y} {dy}\n".encode())
        elif data == b"READXY\r\n":
            x, _, y, _ = self.move
            self.replies.append(f"STEP2 X={max(0, x-int(self.short_count))} BX=0 Y={y} BY=0\n".encode())
        elif data == b"STOP\r\n":
            self.replies.append(b"STEP_STOPPED\n")
        else:
            raise AssertionError("unapproved command " + repr(data))
        return len(data)

    def readline(self, limit):
        if self.replies:
            return self.replies.pop(0)
        time.sleep(0.002)
        return b""

    def close(self):
        self.closed = True


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.device = FakeDevice()
        self.ctrl = JogController(JogConfig(), self.device)

    def tearDown(self):
        self.ctrl.close()
        self.ctrl.thread.join(3)
        self.assertFalse(self.ctrl.thread.is_alive())

    def arm(self):
        self.assertTrue(self.ctrl.arm())
        wait_for(lambda: self.ctrl.state == "READY")

    def test_locked_keys_send_nothing_and_block_arm_until_release(self):
        self.assertFalse(self.ctrl.press("w"))
        self.assertFalse(self.ctrl.arm())
        self.ctrl.release("w")
        self.arm()
        self.assertEqual([], self.device.moves)

    def test_all_directions_and_case(self):
        self.arm()
        for key, xy in (("W", (20, 0)), ("s", (-20, 0)), ("a", (0, -20)), ("D", (0, 20))):
            self.assertTrue(self.ctrl.press(key))
            wait_for(lambda: self.ctrl.state == "READY")
            self.ctrl.release(key)
            self.assertEqual(xy, (self.device.moves[-1].x, self.device.moves[-1].y))

    def test_held_key_never_repeats_even_after_completed_move(self):
        self.arm()
        self.assertTrue(self.ctrl.press("w"))
        wait_for(lambda: self.ctrl.state == "READY")
        for _ in range(30):
            self.assertFalse(self.ctrl.press("w"))
        self.assertEqual(1, len(self.device.moves))

    def test_busy_keys_discarded_no_queue(self):
        self.device.block = True
        self.arm()
        self.ctrl.press("w")
        self.assertTrue(self.device.entered.wait(1))
        self.assertFalse(self.ctrl.press("d"))
        self.ctrl.stop()
        wait_for(lambda: self.ctrl.state == "LOCKED")
        self.assertEqual(["w"], [r.key for r in self.device.moves])
        self.assertFalse(self.ctrl.arm())  # held keys survive STOP

    def test_stop_closes_and_requires_manual_reenable(self):
        self.arm()
        self.ctrl.press("space")
        wait_for(lambda: self.ctrl.state == "LOCKED")
        self.assertGreaterEqual(self.device.stops, 1)
        self.assertGreaterEqual(self.device.closed, 1)
        self.assertFalse(self.ctrl.press("a"))

    def test_disconnect_locks_and_stops_no_retry(self):
        self.device.fail = True
        self.arm()
        self.ctrl.press("a")
        wait_for(lambda: self.ctrl.state == "LOCKED")
        self.assertEqual(1, len(self.device.moves))
        self.assertEqual(1, self.device.opened)
        self.assertEqual(1, self.device.stops)

    def test_quit_during_busy_stops_same_owner(self):
        self.device.block = True
        self.arm()
        self.ctrl.press("w")
        self.assertTrue(self.device.entered.wait(1))
        self.ctrl.press("q")
        self.ctrl.thread.join(2)
        self.assertEqual("CLOSED", self.ctrl.state)
        self.assertEqual(1, self.device.stops)

    def test_real_missing_human_gate_never_opens(self):
        other = JogController(JogConfig(real=True, port="COM_TEST"), self.device)
        try:
            self.assertFalse(other.arm())
            self.assertFalse(other.arm(operator_authorized="YES"))
            self.assertEqual(0, self.device.opened)
        finally:
            other.close()
            other.thread.join(2)

    def test_stdout_failure_before_dispatch_stops_without_move(self):
        self.arm()
        with patch("builtins.print", side_effect=BrokenPipeError("closed log")):
            self.assertFalse(self.ctrl.press("w"))
            wait_for(lambda: self.ctrl.state == "LOCKED")
        self.assertEqual([], self.device.moves)
        self.assertGreaterEqual(self.device.stops, 1)
        self.assertFalse(self.ctrl.arm())
        self.assertTrue(self.ctrl.thread.is_alive())

    def test_stdout_failure_during_move_still_stops_and_closes(self):
        self.device.block = True
        self.arm()
        self.ctrl.press("w")
        self.assertTrue(self.device.entered.wait(1))
        with patch("builtins.print", side_effect=ValueError("I/O operation on closed file")):
            self.ctrl._emit("in-flight log")
            wait_for(lambda: self.ctrl.state == "LOCKED")
        self.assertEqual(1, self.device.stops)
        self.assertEqual(1, self.device.closed)
        self.assertFalse(self.ctrl.arm())


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.position = root / "position.json"
        self.lockdir = root / "locks"
        self.config = JogConfig(real=True, port="COM_TEST", timeout=0.2, position_path=self.position)
        self.serial = ProtocolSerial()
        self.device = JogDevice(self.config, serial_factory=lambda **kw: self.serial, lock_directory=self.lockdir)
        self.cancel = threading.Event()

    def tearDown(self):
        self.device.close()
        self.temp.cleanup()

    def test_full_protocol_and_position_invalid_before_move(self):
        set_zero(self.position)
        self.device.open(self.cancel)
        original_write = self.serial.write
        def checked(data):
            if data.startswith(b"MOVEXY"):
                self.assertFalse(load_position(self.position).known)
            return original_write(data)
        self.serial.write = checked
        self.device.move(JogRequest("w", 20, 0), self.cancel)
        self.device.stop()
        self.assertFalse(load_position(self.position).known)
        self.assertEqual([b"HELLO\r\n", b"READXY\r\n", b"MOVEXY 20 FWD 0 FWD\r\n", b"READXY\r\n", b"STOP\r\n"], self.serial.sent)

    def test_cancel_before_send_has_zero_motion(self):
        self.device.open(self.cancel)
        self.cancel.set()
        with self.assertRaises(InterruptedError):
            self.device.move(JogRequest("w", 20, 0), self.cancel)
        self.assertFalse(any(x.startswith(b"MOVEXY") for x in self.serial.sent))

    def test_position_write_failure_prevents_motion(self):
        self.device.open(self.cancel)
        with patch("demo.stepper_jog.mark_unknown", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.device.move(JogRequest("w", 20, 0), self.cancel)
        self.assertFalse(any(x.startswith(b"MOVEXY") for x in self.serial.sent))

    def test_cancel_raised_by_tx_logging_prevents_motion_bytes(self):
        self.device.open(self.cancel)
        def log(message):
            if message.startswith("TX_PENDING MOVEXY"):
                self.cancel.set()
        self.device.log = log
        with self.assertRaises(InterruptedError):
            self.device.move(JogRequest("w", 20, 0), self.cancel)
        self.assertFalse(any(x.startswith(b"MOVEXY") for x in self.serial.sent))

    def test_forged_or_multiaxis_request_denied(self):
        for request in (JogRequest("PUMP", 0, 20), JogRequest("w", 20, 20), JogRequest("a", 0, -1001),
                        JogRequest("d", 20, 0), JogRequest("w", 0, 20)):
            with self.assertRaises(PermissionError):
                self.device.move(request, self.cancel)
        self.assertEqual([], self.serial.sent)

    def test_wrong_start_and_short_count_fail_keep_unknown(self):
        self.device.open(self.cancel)
        self.serial.bad_start = True
        with self.assertRaises(OSError):
            self.device.move(JogRequest("w", 20, 0), self.cancel)
        self.serial.bad_start = False
        self.serial.short_count = True
        with self.assertRaises(OSError):
            self.device.move(JogRequest("w", 20, 0), self.cancel)
        self.assertFalse(load_position(self.position).known)

    def test_incomplete_handshake_closes_releases_lease(self):
        self.serial.partial = True
        with self.assertRaises(OSError):
            self.device.open(self.cancel)
        self.assertTrue(self.serial.closed)
        with ResourceLease(device_resources("COM_TEST", self.position), directory=self.lockdir):
            pass

    def test_conflict_before_serial_open(self):
        with ResourceLease(device_resources("COM_TEST", self.position), directory=self.lockdir):
            with self.assertRaises(PermissionError):
                self.device.open(self.cancel)
        self.assertEqual([], self.serial.sent)

    def test_short_write_and_timeout(self):
        self.serial.short = True
        with self.assertRaises(OSError):
            self.device.open(self.cancel)
        self.serial.short = False
        self.serial.silent = True
        with self.assertRaises(TimeoutError):
            self.device.open(self.cancel)

    def test_stop_drains_pending_move_reply(self):
        self.device.open(self.cancel)
        self.serial.replies.append(b"STEP2_START X=20 FWD Y=0 FWD\n")
        self.device.stop()
        self.assertEqual(b"STOP\r\n", self.serial.sent[-1])

    def test_simulation_never_opens_serial_or_writes_position(self):
        with patch("serial.Serial", side_effect=AssertionError("COM opened")), patch("demo.stepper_jog.mark_unknown", side_effect=AssertionError("position changed")):
            device = JogDevice(JogConfig())
            device.open(self.cancel)
            device.move(JogRequest("s", -20, 0), self.cancel)
            device.stop()
            device.close()

    def test_config_rejects_invalid_args(self):
        for change in ({"steps": 0}, {"steps": True}, {"steps": 1001}, {"timeout": float("nan")}, {"timeout": float("inf")}, {"timeout": 0}, {"real": True}, {"port": "COM5"}):
            with self.assertRaises(ValueError):
                JogConfig(**change)
        self.assertFalse(parse_args([]).real)
        self.assertTrue(parse_args(["--real", "--port", "COM_TEST"]).real)


class TkTests(unittest.TestCase):
    def test_modified_shortcuts_do_not_reach_motion_controller(self):
        import tkinter as tk
        root = tk.Tk()
        device = FakeDevice()
        controller = JogController(JogConfig(), device)
        try:
            view = JogWindow(root, controller)
            controller.arm()
            wait_for(lambda: controller.state == "READY")
            with patch.object(controller, "press", wraps=controller.press) as press:
                for modifier in (0x4, 0x8, 0x40, 0x80, 0x20000):
                    for key in "wasd":
                        view._press(SimpleNamespace(keysym=key, state=modifier))
                press.assert_not_called()
            self.assertEqual([], device.moves)
        finally:
            controller.close()
            controller.thread.join(2)
            root.destroy()

    def test_own_tk_key_events_repeat_stop_and_close(self):
        import tkinter as tk
        root = tk.Tk()
        device = FakeDevice()
        controller = JogController(JogConfig(), device)
        try:
            view = JogWindow(root, controller)
            root.update()
            root.focus_force()
            root.update()
            view.button.invoke()
            wait_for(lambda: controller.state == "READY")
            root.event_generate("<KeyPress-w>")
            root.update()
            wait_for(lambda: len(device.moves) == 1 and controller.state == "READY")
            root.event_generate("<KeyPress-w>")
            root.update()
            self.assertEqual(1, len(device.moves))
            root.event_generate("<KeyRelease-w>")
            deadline = time.monotonic() + .1
            while time.monotonic() < deadline:
                root.update()
                time.sleep(.005)
            self.assertNotIn("w", controller.held)
            root.event_generate("<KeyPress-space>")
            root.update()
            wait_for(lambda: controller.state == "LOCKED")
            view._close()
            controller.thread.join(2)
            self.assertEqual("CLOSED", controller.state)
        finally:
            controller.close()
            controller.thread.join(2)
            try:
                root.destroy()
            except tk.TclError:
                pass

    def test_real_window_requires_explicit_enable_and_never_claims_hardware_acceptance(self):
        import tkinter as tk
        root = tk.Tk()
        device = FakeDevice()
        controller = JogController(JogConfig(real=True, port="COM_TEST"), device)
        try:
            view = JogWindow(root, controller)
            self.assertEqual("LOCKED", controller.state)
            self.assertEqual(0, device.opened)
            self.assertFalse(hasattr(view, "checks"))
            view.button.invoke()
            wait_for(lambda: controller.state == "READY")
            self.assertEqual(1, device.opened)
            self.assertEqual([], device.moves)
        finally:
            controller.close()
            controller.thread.join(2)
            root.destroy()

    def test_explicit_startup_enable_only_connects_never_moves(self):
        import tkinter as tk
        root = tk.Tk()
        device = FakeDevice()
        controller = JogController(JogConfig(real=True, port="COM_TEST", enable=True), device)
        try:
            JogWindow(root, controller)
            root.update_idletasks()
            wait_for(lambda: controller.state == "READY")
            self.assertEqual(1, device.opened)
            self.assertEqual([], device.moves)
            controller.stop()
            wait_for(lambda: controller.state == "LOCKED")
            root.update_idletasks()
            self.assertEqual("LOCKED", controller.state)
            self.assertEqual(1, device.opened)
        finally:
            controller.close()
            controller.thread.join(2)
            root.destroy()

    def test_focus_loss_stops_and_retains_held_key(self):
        import tkinter as tk
        root = tk.Tk()
        device = FakeDevice()
        controller = JogController(JogConfig(), device)
        try:
            view = JogWindow(root, controller)
            controller.arm()
            wait_for(lambda: controller.state == "READY")
            controller.press("w")
            wait_for(lambda: controller.state == "READY")
            with patch.object(root, "focus_displayof", return_value=None):
                view._check_focus()
            wait_for(lambda: controller.state == "LOCKED")
            self.assertIn("w", controller.held)
            self.assertFalse(controller.arm())
            controller.release("w")
            self.assertTrue(controller.arm())
            wait_for(lambda: controller.state == "READY")
            self.assertEqual(1, len(device.moves))  # no automatic resumption
        finally:
            controller.close()
            controller.thread.join(2)
            root.destroy()


if __name__ == "__main__":
    unittest.main()
