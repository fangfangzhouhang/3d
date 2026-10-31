"""Connection-only safety boundaries; all devices below are replacements."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from demo.device_connection import QueryOnlyConnection, QUERY_REPLIES
from demo.image_ops import FrozenSegmenter
from demo.workbench_runtime import WorkbenchRuntime
from microcleaning.control_system.orchestration.cleaning_loop import CapturedFrame
from microcleaning.control_system.orchestration.workbench_events import Cancellation, WorkbenchEvent
from microcleaning.control_system.serial.resource_lease import ResourceLease

REPLIES = {
    "HELLO": b"STEP_OK v0.3\r\n",
    "READXY": b"STEP2 X=0 BX=0 Y=0 BY=0\r\n",
    "MCV1|PING": b"MCV1|PONG\n",
    "MCV1|STATUS": b"MCV1|STATUS|ESTOP=0|PUMP=0\n",
}


class QuerySerial:
    def __init__(self):
        self._queue, self.writes = [], []
        self.timeout = .05
        self.is_open = True
        self.partial = False
        self.override = None

    def write(self, value):
        self.writes.append(bytes(value))
        command = value.decode("ascii").strip()
        self._queue.append(self.override if self.override is not None else REPLIES[command])
        return len(value) - 1 if self.partial else len(value)

    def readline(self):
        return self._queue.pop(0) if self._queue else b""

    def reset_input_buffer(self):
        self._queue.clear()

    def flush(self):
        pass

    def close(self):
        self.is_open = False


class TestQueryConnection(unittest.TestCase):
    def make_connection(self, raw=None, **kwargs):
        raw = raw or QuerySerial()
        calls = []
        def factory():
            calls.append(True)
            return raw
        connection = QueryOnlyConnection(port="COM-TEST-CONNECTION", timeout=.03,
            serial_factory=factory, **kwargs)
        self.addCleanup(connection.close)
        return connection, raw, calls

    def test_only_four_queries_and_close_emits_no_action(self):
        connection, raw, calls = self.make_connection()
        for command in REPLIES:
            self.assertEqual(REPLIES[command].decode().strip(), connection.query(command))
        connection.close()
        connection.close()
        self.assertEqual(1, len(calls))
        self.assertEqual([(key + "\r\n").encode() for key in REPLIES], raw.writes)
        self.assertFalse(raw.is_open)
        self.assertEqual(4, sum(item["direction"] == "tx" for item in connection.events))

    def test_invalid_or_injected_queries_never_open_transport(self):
        connection, raw, calls = self.make_connection()
        for command in ("MOVEXY 1 FWD 0 FWD", "PUMP ON", "MCV1|PUMP|x|100", "STOP",
                "CLEAR", "ARM", "HELLO\nPUMP ON", "HELLO\r", " HELLO", "", None, [], 1):
            with self.subTest(command=command), self.assertRaises(PermissionError):
                connection.query(command)
        self.assertFalse(calls)
        self.assertFalse(raw.writes)

    def test_wrong_protocol_and_incomplete_replies_cannot_claim_connected(self):
        for reply in (b"STEP_OK v0.2\n", b"MCV1|PONG\n", b"STEP_OK v0.3"):
            with self.subTest(reply=reply):
                raw = QuerySerial()
                raw.override = reply
                connection, raw, _ = self.make_connection(raw)
                with self.assertRaises((TimeoutError, ValueError)):
                    connection.query("HELLO")
                self.assertFalse(raw.is_open)
                self.assertEqual([b"HELLO\r\n"], raw.writes)

    def test_partial_write_closes_without_stop_or_retry(self):
        raw = QuerySerial()
        raw.partial = True
        connection, _, _ = self.make_connection(raw)
        with self.assertRaises(OSError):
            connection.query("HELLO")
        self.assertFalse(raw.is_open)
        self.assertEqual([b"HELLO\r\n"], raw.writes)

    def test_cancel_before_query_never_opens(self):
        cancel = Cancellation()
        cancel.cancel()
        connection, _, calls = self.make_connection(cancellation=cancel)
        with self.assertRaises(RuntimeError):
            connection.query("HELLO")
        self.assertFalse(calls)

    def test_long_reply_is_rejected(self):
        raw = QuerySerial()
        raw.override = b"x" * 513 + b"\n"
        connection, _, _ = self.make_connection(raw)
        with self.assertRaises(ValueError):
            connection.query("HELLO")
        self.assertFalse(raw.is_open)

    def test_nonfinite_or_invalid_connection_parameters_are_rejected(self):
        for parameters in ({"port": ""}, {"port": None}, {"timeout": float("nan")},
                {"timeout": float("inf")}, {"timeout": 0}, {"timeout": True},
                {"baudrate": True}, {"baudrate": 0}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                QueryOnlyConnection(**{"port": "COM-TEST", **parameters})


def args_for(folder):
    return SimpleNamespace(connect_only=True, real=False, output_root=Path(folder),
        serial_port="COM-TEST-CONNECTION", baudrate=115200, serial_timeout=.03,
        camera_index=1, camera_backend=1400, camera_width=640, camera_height=480,
        warmup_frames=0, algorithm="local")


class CameraReplacement:
    def __init__(self, *, args, segmenter, bus, cancel, **kwargs):
        self.ready = threading.Event()
        self.ready.set()
        self.failure = None
        self.capture_key = threading.Event()
        self.cancel, self.bus, self.closed = cancel, bus, False
        bus.emit("connection_device", camera="替身帧已连接")

    def capture(self, phase, **kwargs):
        self.capture_key.clear()
        self.bus.emit("capture_ready", phase=phase)
        while not self.capture_key.wait(.01):
            self.cancel.check()
        self.cancel.check()
        self.bus.emit("capture_closed", phase=phase)
        image = np.full((120, 160, 3), 210, dtype=np.uint8)
        image[35:65, 60:90] = 20
        return CapturedFrame(image, "replacement-frame", "2026-10-30T00:00:00+00:00",
            "synthetic:test", {"actual_size": [120, 160]})

    def close(self):
        self.closed = True


class ConnectionRuntimeTests(unittest.TestCase):
    def prepare(self, folder):
        raw = QuerySerial()
        constructor = QueryOnlyConnection
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("demo.workbench_runtime.CameraBroker", CameraReplacement))
        stack.enter_context(patch("demo.device_connection.QueryOnlyConnection",
            side_effect=lambda **kw: constructor(**kw, serial_factory=lambda: raw)))
        stack.enter_context(patch("demo.device_connection.ResourceLease",
            side_effect=lambda resources: ResourceLease(resources, directory=Path(folder) / "locks")))
        for name in ("CleaningLoop", "HardwareExecutor", "Stage2SerialLink", "STM32SerialController", "set_zero", "load_position"):
            stack.enter_context(patch("demo.workbench_runtime." + name, side_effect=AssertionError("Actuator API forbidden: " + name)))
        runtime = WorkbenchRuntime(args=args_for(folder), config=None, segmenter=FrozenSegmenter(),
            placeholders=None, calibration=None, offset=None, coverage_enabled=False)
        self.addCleanup(self.finish, runtime)
        return runtime, raw

    @staticmethod
    def finish(runtime):
        runtime.submit("cancel")
        if runtime.worker:
            runtime.worker.join(4)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail("Connection fixture timed out")

    def test_runtime_connect_capture_cancel_has_no_execution_or_position(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime, raw = self.prepare(temp)
            self.assertTrue(runtime.start({"sample_id": "replacement-only"}))
            self.wait_for(lambda: runtime.source is not None)
            self.wait_for(lambda: not runtime.bus.events.empty() and runtime.source.ready.is_set())
            self.assertFalse(runtime.start({}))
            for action in ("begin", "lock", "confirm", "approve_all", "manual_add", "decision", "rewash", "pump"):
                self.assertFalse(runtime.submit(action, answer=True))
            # Wait for the capture request before setting its key.
            self.wait_for(lambda: any(e.kind == "capture_ready" for e in runtime.bus.drain()))
            connected = json.loads((runtime.folder / "connection.json").read_text(encoding="utf-8"))
            self.assertEqual(4, sum(e["direction"] == "tx" for e in connected["serial_events"]))
            self.assertIn("demo/device_connection.py", connected["source_files_sha256"])
            runtime.submit("capture")
            self.wait_for(lambda: list(Path(temp).rglob("capture_0001.json")))
            self.finish(runtime)
            self.assertFalse(runtime.active)
            self.assertEqual("SUCCESS", runtime.last_status)
            record = json.loads((runtime.folder / "connection.json").read_text(encoding="utf-8"))
            capture = json.loads((runtime.folder / "capture_0001.json").read_text(encoding="utf-8"))
            self.assertEqual("DISCONNECTED", record["status"])
            self.assertEqual(1, len(record["captures"]))
            self.assertFalse(record["errors"])
            self.assertFalse(record["position_reference_established"])
            self.assertEqual("synthetic:test", capture["source_id"])
            self.assertFalse(capture["executable"])
            self.assertFalse(list(Path(temp).rglob("*position*")))
            self.assertFalse(list(Path(temp).rglob("summary.json")))
            self.assertEqual([(key + "\r\n").encode() for key in REPLIES], raw.writes)
            self.assertFalse(raw.is_open)
            self.assertTrue(runtime.source.closed)

    def test_query_failure_is_error_not_connected_and_releases_resources(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime, raw = self.prepare(temp)
            raw.override = b"WRONG\n"
            runtime.start({})
            runtime.worker.join(4)
            self.assertFalse(runtime.active)
            self.assertEqual("ERROR", runtime.last_status)
            self.assertFalse(raw.is_open)
            record = json.loads((runtime.folder / "connection.json").read_text(encoding="utf-8"))
            self.assertEqual("ERROR", record["status"])
            self.assertFalse(record["queries"])
            self.assertIsNone(runtime.source)
            lease = ResourceLease(("com:COM-TEST-CONNECTION",), directory=Path(temp) / "locks").acquire()
            lease.close()

    def test_camera_failure_is_error_and_closes_serial(self):
        class FailedCamera(CameraReplacement):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.failure = RuntimeError("TEST_CAMERA_FAILED")
        with tempfile.TemporaryDirectory() as temp:
            runtime, raw = self.prepare(temp)
            with patch("demo.workbench_runtime.CameraBroker", FailedCamera):
                runtime.start({})
                runtime.worker.join(4)
            self.assertEqual("ERROR", runtime.last_status)
            self.assertFalse(raw.is_open)
            self.assertTrue(runtime.source.closed)

    def test_busy_resource_prevents_serial_open(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime, raw = self.prepare(temp)
            with ResourceLease(("com:COM-TEST-CONNECTION",), directory=Path(temp) / "locks"):
                runtime.start({})
                runtime.worker.join(4)
            self.assertEqual("ERROR", runtime.last_status)
            self.assertFalse(raw.writes)
            self.assertIsNone(runtime.source)

    def test_actual_tk_navigation_and_execution_controls_are_disabled(self):
        import tkinter as tk
        from demo.station_window import WorkbenchWindow
        with tempfile.TemporaryDirectory() as temp:
            runtime, raw = self.prepare(temp)
            try:
                window = WorkbenchWindow(runtime)
            except tk.TclError as exc:
                self.skipTest(str(exc))
            window.root.withdraw()
            try:
                window.start_task()
                deadline = time.monotonic() + 5
                while not window.capture_ready and time.monotonic() < deadline:
                    window.root.update()
                    time.sleep(.01)
                self.assertTrue(window.capture_ready)
                self.assertIn("仅连接", window.root.title())
                self.assertIn("禁用", window.device.cget("text"))
                for page in ("review", "monitor", "results"):
                    self.assertTrue(window.nav[page].instate(["disabled"]))
                    window.navigate(page)
                    self.assertEqual("detection", window.current_page)
                self.assertTrue(window.views["review"].lock.instate(["disabled"]))
                self.assertTrue(window.views["review"].begin.instate(["disabled"]))
                window.begin()
                window._event(WorkbenchEvent("motion", {"phase": "tx", "message": "MOVEXY 1 FWD 0 FWD"}))
                window.views["detection"].capture.invoke()
                deadline = time.monotonic() + 4
                while not list(Path(temp).rglob("capture_0001.json")) and time.monotonic() < deadline:
                    window.root.update()
                    time.sleep(.01)
                self.assertTrue(list(Path(temp).rglob("capture_0001.json")))
                self.assertIsNone(window.task)
                self.assertGreater(window.heartbeat_count, 1)
            finally:
                window.close()
                deadline = time.monotonic() + 4
                while not window.destroyed and time.monotonic() < deadline:
                    window.root.update()
                    time.sleep(.01)
                if not window.destroyed:
                    self.finish(runtime)
                    window._destroy()
            self.assertFalse(raw.is_open)
            self.assertFalse(runtime.active)


class ConnectionCLITests(unittest.TestCase):
    def test_minimum_explicit_devices_selects_connection_runtime_without_calibration(self):
        from demo.closed_loop_station import main
        with patch("demo.workbench_runtime.WorkbenchRuntime") as runtime:
            runtime.return_value.run.return_value = 0
            code = main(["--connect-only", "--serial-port", "COM5", "--camera-index", "1"])
        self.assertEqual(0, code)
        call = runtime.call_args.kwargs
        self.assertIsNone(call["calibration"])
        self.assertIsNone(call["offset"])
        self.assertIsNone(call["config"])
        self.assertFalse(call["args"].arm_stage2_xy)
        self.assertFalse(call["args"].arm_pump)

    def test_arming_zeroing_motion_configuration_and_missing_devices_are_rejected(self):
        from demo.closed_loop_station import main
        base = ["--connect-only", "--serial-port", "COM5", "--camera-index", "1"]
        options = [
            ["--arm-stage2-xy"], ["--arm-pump"], ["--confirm-pump"], ["--stage2-set-zero"],
            ["--position-path", "other.json"], ["--stage2-calibration", "old.json"],
            ["--nozzle-offset", "old.json"], ["--path-placeholders", "old.json"],
            ["--serial-timeout", "nan"], ["--serial-timeout", "inf"],
            ["--serial-timeout", "0"], ["--baudrate", "0"], ["--camera-index", "-1"],
            ["--real"], ["--mock"],
        ]
        with patch("demo.workbench_runtime.WorkbenchRuntime") as runtime, redirect_stderr(StringIO()):
            for option in options:
                with self.subTest(option=option), self.assertRaises(SystemExit):
                    main(base + option)
            for arguments in (["--connect-only"], ["--connect-only", "--camera-index", "1"],
                    ["--connect-only", "--serial-port", "COM5"]):
                with self.subTest(arguments=arguments), self.assertRaises(SystemExit):
                    main(arguments)
            runtime.assert_not_called()

    def test_existing_real_mode_still_requires_motion_arming_and_configuration(self):
        from demo.closed_loop_station import main
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            main(["--real", "--serial-port", "COM5", "--camera-index", "1"])
