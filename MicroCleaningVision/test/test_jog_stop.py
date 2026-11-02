"""Deterministic host STOP tests. No physical devices or timing sleeps."""

import json
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock, patch

from demo import motion_mode
from demo.manual_jog import JogController
from microcleaning.control_system.planning import stage2_position as ledger
from microcleaning.control_system.planning.stage2_axes import Stage2Dispatch, single_move_dispatch
from microcleaning.control_system.safety.motion_gate import (
    MotionRequest, approve_motion_gate, evaluate_motion, new_motion_request_id,
)
from microcleaning.control_system.serial.stage2_link import Stage2SerialLink, Stage2TransmitError
from microcleaning.control_system.serial.stage2_protocol import Stage2ProtocolError, parse_stage2_reply


class EventSerial:
    """Pause a chosen response until the test explicitly releases its reader."""

    def __init__(self, block_on=None):
        self.block_on = block_on
        self.read_entered = threading.Event()
        self.release_read = threading.Event()
        self.stop_written = threading.Event()
        self.open_entered = threading.Event()
        self.release_open = threading.Event()
        self.flush_entered = threading.Event()
        self.release_flush = threading.Event()
        self.block_open = False
        self.block_flush = None
        self.stop_error = None
        self.stop_reply = b"STEP_STOPPED\r\n"
        self.read_error = None
        self.prefixes = {}
        self.writes, self.operations = [], []
        self.opens = self.closes = self.reads = 0
        self._queue = deque()
        self._last = None
        self._blocked = False
        self._pending_flush = None
        self._counts = (0, 0)

    def factory(self):
        self.opens += 1
        self.open_entered.set()
        if self.block_open and not self.release_open.wait(5):
            raise TimeoutError("test did not release factory")
        return self

    def reset_input_buffer(self):
        self._queue.clear()

    def write(self, data):
        command = data.decode("ascii").strip()
        if self._pending_flush is not None:
            raise AssertionError("another write interleaved before flush")
        self._pending_flush = command
        self._last = command.split()[0]
        self.writes.append(data)
        self.operations.append(("write", command))
        self._queue.extend(self.prefixes.get(self._last, ()))
        if command == "STOP":
            self.stop_written.set()
            if self.stop_error == "write":
                self._pending_flush = None
                raise OSError("STOP write failed")
            self._queue.append(self.stop_reply)
        elif command == "HELLO":
            self._queue.append(b"STEP_OK v0.3\r\n")
        elif command.startswith("MOVEXY "):
            _, nx, dx, ny, dy = command.split()
            self._counts = int(nx), int(ny)
            self._queue.append(f"STEP2_START X={nx} {dx} Y={ny} {dy}\r\n".encode())
        elif command == "READXY":
            x, y = self._counts
            self._queue.append(f"STEP2 X={x} BX=0 Y={y} BY=0\r\n".encode())
        else:
            raise AssertionError(command)
        return len(data)

    def flush(self):
        command = self._pending_flush
        if command and command.split()[0] == self.block_flush:
            self.block_flush = None
            self.flush_entered.set()
            if not self.release_flush.wait(5):
                raise TimeoutError("test did not release flush")
        self.operations.append(("flush", command))
        self._pending_flush = None
        if command == "STOP" and self.stop_error == "flush":
            raise OSError("STOP flush failed")

    def readline(self):
        self.reads += 1
        if self._last == self.block_on and not self._blocked:
            self._blocked = True
            self.read_entered.set()
            if not self.release_read.wait(5):
                raise TimeoutError("test did not release reader")
            if self.read_error is not None:
                raise self.read_error
        return self._queue.popleft() if self._queue else b""

    def close(self):
        self.closes += 1

    def release(self):
        self.release_read.set()
        self.release_open.set()
        self.release_flush.set()


class JogStopTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.position = self.root / "position.json"
        self.calibration = self.root / "calibration.json"
        self.calibration.write_text(json.dumps({"mm_per_px_x": .0187,
            "mm_per_px_y": .0181, "warnings": []}), encoding="utf-8")
        self.raw = EventSerial()
        self.threads = []
        self.addCleanup(self.finish_threads)

    def finish_threads(self):
        self.raw.release()
        for thread in self.threads:
            thread.join(5)
            self.assertFalse(thread.is_alive(), "test worker did not exit")

    def start(self, callback):
        box = {}
        def run():
            try:
                box["result"] = callback()
            except BaseException as exc:
                box["error"] = exc
        thread = threading.Thread(target=run, daemon=True)
        self.threads.append(thread)
        thread.start()
        return thread, box

    def joined(self, thread, box):
        thread.join(5)
        self.assertFalse(thread.is_alive())
        if "error" in box:
            raise box["error"]
        return box["result"]

    def wait(self, event):
        self.assertTrue(event.wait(5), "expected synchronization point not reached")

    def controller(self, **kwargs):
        options = dict(stage2_calibration=self.calibration, position_path=self.position,
            output_root=self.root / "runs", serial_factory=self.raw.factory,
            serial_timeout=.1, confirm=lambda request, plan: True)
        options.update(kwargs)
        controller = JogController(**options)
        controller.set_zero(confirmed=True)
        controller.arm()
        return controller

    def send(self, link, dispatch=None, **kwargs):
        dispatch = dispatch or single_move_dispatch(320, "FWD", 0, "FWD")
        request = MotionRequest(request_id=new_motion_request_id(), task_id="stop-test",
            lines=dispatch.lines, position_before_steps=(0, 0), start_reference="image_center",
            calibration_ref="synthetic.json", calibration_sha256="0" * 64)
        decision = approve_motion_gate(request, evaluate_motion(request), confirmed=True)
        return link.transmit(dispatch, request=request, decision=decision, **kwargs)

    def blocked_jog(self, command):
        self.raw.block_on = command
        controller = self.controller()
        thread, box = self.start(lambda: controller.jog("FWD", None, 320))
        self.wait(self.raw.read_entered)
        return controller, thread, box

    def stopped_result(self, controller, thread, box, *, opens=1):
        self.raw.release_read.set()
        result = self.joined(thread, box)
        self.assertEqual("failed", result.status, result.message)
        self.assertEqual(("OPERATOR_STOPPED",), result.reason_codes)
        self.assertFalse(controller.armed)
        self.assertEqual(opens, self.raw.opens)
        self.assertEqual(opens, self.raw.closes)
        receipt = json.loads((result.run_dir / "stage2_receipt.json").read_text(encoding="utf-8"))
        self.assertEqual("OPERATOR_STOPPED", receipt["error"]["reason_code"])
        return result

    def test_idle_stop_never_opens_port_or_zeros(self):
        controller = self.controller()
        before = self.position.read_bytes()
        for _ in range(3):
            self.assertEqual((0, 0), controller.stop().xy())
        self.assertFalse(controller.armed)
        self.assertEqual(before, self.position.read_bytes())
        self.assertEqual([], self.raw.writes)
        self.assertEqual(0, self.raw.opens)

    def test_link_stop_before_transmit_latches_without_opening(self):
        link = Stage2SerialLink(armed=True, serial_factory=self.raw.factory)
        self.assertFalse(link.request_stop())
        with self.assertRaises(Stage2TransmitError) as caught:
            self.send(link)
        self.assertEqual("OPERATOR_STOPPED", caught.exception.reason_code)
        self.assertFalse(caught.exception.motion_attempted)
        self.assertFalse(caught.exception.stopped)
        self.assertEqual(0, self.raw.opens)
        self.assertEqual([], self.raw.writes)

    def test_stop_preempts_blocked_move_ack_before_reader_returns(self):
        controller, thread, box = self.blocked_jog("MOVEXY")
        position = controller.stop()
        self.wait(self.raw.stop_written)
        self.assertFalse(self.raw.release_read.is_set())
        self.assertTrue(thread.is_alive())
        self.assertIsNone(position.xy())  # Pending snapshot, no premature READY.
        result = self.stopped_result(controller, thread, box)
        self.assertTrue(result.motion.error.motion_attempted)
        self.assertTrue(result.motion.error.stopped)
        self.assertEqual(2, self.raw.writes.count(b"STOP\r\n"))
        self.assertNotIn(b"READXY\r\n", self.raw.writes)
        self.assertIsNone(result.position.xy())

    def test_stop_preempts_readxy_and_full_counts_do_not_commit(self):
        controller, thread, box = self.blocked_jog("READXY")
        controller.stop()
        self.wait(self.raw.stop_written)
        result = self.stopped_result(controller, thread, box)
        self.assertEqual((), result.motion.error.sent_lines)
        self.assertIsNone(result.position.xy())

    def test_handshake_stop_restores_reference_without_attempting_motion(self):
        controller, thread, box = self.blocked_jog("HELLO")
        controller.stop()
        self.wait(self.raw.stop_written)
        result = self.stopped_result(controller, thread, box)
        self.assertFalse(result.motion.error.motion_attempted)
        self.assertEqual((0, 0), result.position.xy())
        self.assertIsNone(result.position.pending_motion)
        self.assertFalse(any(line.startswith(b"MOVEXY") for line in self.raw.writes))

    def test_repeated_stops_have_one_immediate_write_and_one_cleanup(self):
        controller, thread, box = self.blocked_jog("MOVEXY")
        for _ in range(5):
            controller.stop()
        self.assertEqual(1, self.raw.writes.count(b"STOP\r\n"))
        self.stopped_result(controller, thread, box)
        self.assertEqual(2, self.raw.writes.count(b"STOP\r\n"))
        controller.stop()
        self.assertEqual(2, self.raw.writes.count(b"STOP\r\n"))

    def test_stop_before_link_registration_prevents_open(self):
        entered, release = threading.Event(), threading.Event()
        controller = self.controller()
        self.addCleanup(release.set)
        original = controller._register_link
        def register(link):
            if link is not None:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("registration not released")
            original(link)
        with patch.object(controller, "_register_link", side_effect=register):
            thread, box = self.start(lambda: controller.jog("FWD", None, 320))
            self.wait(entered)
            controller.stop()
            release.set()
            result = self.joined(thread, box)
        self.assertEqual(("OPERATOR_STOPPED",), result.reason_codes)
        self.assertEqual((0, 0), result.position.xy())
        self.assertFalse(result.motion.error.motion_attempted)
        self.assertEqual(0, self.raw.opens)

    def test_stop_during_factory_open_is_latched_and_no_movexy_follows(self):
        self.raw.block_open = True
        controller = self.controller()
        thread, box = self.start(lambda: controller.jog("FWD", None, 320))
        self.wait(self.raw.open_entered)
        controller.stop()  # Must return without waiting for open/handshake.
        self.assertEqual([], self.raw.writes)
        self.raw.release_open.set()
        result = self.joined(thread, box)
        self.assertEqual(("OPERATOR_STOPPED",), result.reason_codes)
        self.assertFalse(result.motion.error.motion_attempted)
        self.assertEqual((0, 0), result.position.xy())
        self.assertEqual([b"STOP\r\n"], self.raw.writes)

    def test_stop_cancels_pending_confirm_even_if_callback_returns_true(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def confirm(request, plan):
            entered.set()
            self.assertTrue(release.wait(5))
            return True
        canceller = Mock(side_effect=release.set)
        controller = self.controller(confirm=confirm, confirm_canceller=canceller)
        thread, box = self.start(lambda: controller.jog("FWD", None, 320))
        self.wait(entered)
        controller.stop()
        result = self.joined(thread, box)
        self.assertEqual("declined", result.status)
        canceller.assert_called_once_with()
        self.assertEqual(0, self.raw.opens)
        self.assertEqual((0, 0), result.position.xy())

    def test_canceller_error_does_not_skip_serial_stop(self):
        controller, thread, box = self.blocked_jog("MOVEXY")
        controller._confirm_canceller = Mock(side_effect=RuntimeError("cancel failed"))
        with self.assertRaisesRegex(RuntimeError, "cancel failed"):
            controller.stop()
        self.wait(self.raw.stop_written)
        self.stopped_result(controller, thread, box)

    def test_controller_lock_is_free_during_serial_stop_write(self):
        controller, thread, box = self.blocked_jog("MOVEXY")
        self.raw.block_flush = "STOP"
        stopper, stop_box = self.start(controller.stop)
        self.wait(self.raw.flush_entered)
        # These calls acquire the state lock; both must finish before STOP flush.
        inspector, inspected = self.start(lambda: (controller.armed, controller.arm()))
        self.assertEqual((False, False), self.joined(inspector, inspected))
        self.raw.release_flush.set()
        self.joined(stopper, stop_box)
        self.stopped_result(controller, thread, box)

    def test_write_flush_pairs_are_serialized_with_stop(self):
        self.raw.block_flush = "MOVEXY"
        link = Stage2SerialLink(armed=True, serial_factory=self.raw.factory)
        thread, box = self.start(lambda: self.send(link))
        self.wait(self.raw.flush_entered)
        requested = threading.Event()
        original = link._stop_requested.set
        with patch.object(link._stop_requested, "set", side_effect=lambda: (original(), requested.set())):
            stopper, stop_box = self.start(link.request_stop)
            self.wait(requested)
            self.raw.release_flush.set()
            self.joined(stopper, stop_box)
        thread.join(5)
        self.assertIsInstance(box.get("error"), Stage2TransmitError)
        self.assertEqual("OPERATOR_STOPPED", box["error"].reason_code)
        for write, flush in zip(self.raw.operations[::2], self.raw.operations[1::2]):
            self.assertEqual(("flush", write[1]), flush)
        stop_index = self.raw.writes.index(b"STOP\r\n")
        self.assertFalse(any(line.startswith(b"MOVEXY") for line in self.raw.writes[stop_index + 1:]))

    def test_failed_stop_write_preserves_operator_reason(self):
        self.check_stop_io_error("write")

    def test_failed_stop_flush_preserves_operator_reason(self):
        self.check_stop_io_error("flush")

    def check_stop_io_error(self, phase):
        controller, thread, box = self.blocked_jog("MOVEXY")
        self.raw.stop_error = phase
        controller.stop()
        result = self.stopped_result(controller, thread, box)
        self.assertFalse(result.motion.error.stopped)
        self.assertIsNone(result.position.xy())
        self.assertEqual(2, self.raw.writes.count(b"STOP\r\n"))

    def test_failed_stop_ack_does_not_replace_operator_reason(self):
        controller, thread, box = self.blocked_jog("READXY")
        self.raw.stop_reply = b"ERR: STOP_REJECTED\r\n"
        controller.stop()
        result = self.stopped_result(controller, thread, box)
        self.assertFalse(result.motion.error.stopped)

    def test_serial_read_error_after_stop_keeps_operator_reason(self):
        controller, thread, box = self.blocked_jog("READXY")
        self.raw.read_error = OSError("disconnected")
        controller.stop()
        result = self.stopped_result(controller, thread, box)
        self.assertTrue(result.motion.error.stopped)

    def test_serial_error_without_operator_stop_keeps_original_code(self):
        controller, thread, box = self.blocked_jog("READXY")
        self.raw.read_error = OSError("disconnected")
        self.raw.release_read.set()
        result = self.joined(thread, box)
        self.assertEqual(("SERIAL_IO_ERROR",), result.reason_codes)
        self.assertTrue(result.motion.error.stopped)
        self.assertIsNone(result.position.xy())

    def test_unknown_blocks_retry_until_explicit_zero_then_new_link_succeeds(self):
        controller, thread, box = self.blocked_jog("MOVEXY")
        controller.stop()
        self.stopped_result(controller, thread, box)
        controller.arm()
        self.assertEqual(("POSITION_UNKNOWN",), controller.jog("FWD", None, 32).reason_codes)
        self.assertEqual(1, self.raw.opens)
        with self.assertRaises(PermissionError):
            controller.set_zero()
        controller.set_zero(confirmed=True)
        controller.arm()
        result = controller.jog("REV", None, 32)
        self.assertEqual("sent", result.status, result.message)
        self.assertEqual((-32, 0), result.position.xy())
        self.assertEqual(2, self.raw.opens)

    def test_stop_between_segments_suppresses_later_movexy(self):
        link = Stage2SerialLink(armed=True, serial_factory=self.raw.factory)
        dispatch = Stage2Dispatch(("MOVEXY 32 FWD 0 FWD", "MOVEXY 32 REV 0 FWD"),
            64, 0, 64, 0, 20000, False)
        link.on_motion_progress = lambda event: link.request_stop() if event["phase"] == "segment_completed" else None
        with self.assertRaises(Stage2TransmitError) as caught:
            self.send(link, dispatch)
        self.assertEqual("OPERATOR_STOPPED", caught.exception.reason_code)
        self.assertEqual((dispatch.lines[0],), caught.exception.sent_lines)
        self.assertEqual(1, sum(line.startswith(b"MOVEXY") for line in self.raw.writes))

    def test_stop_before_movexy_write_does_not_mark_motion_attempted(self):
        link = Stage2SerialLink(armed=True, serial_factory=self.raw.factory)
        link.on_motion_progress = lambda event: link.request_stop() if event["phase"] == "segment_start" else None
        with self.assertRaises(Stage2TransmitError) as caught:
            self.send(link)
        self.assertFalse(caught.exception.motion_attempted)
        self.assertEqual("OPERATOR_STOPPED", caught.exception.reason_code)
        self.assertFalse(any(line.startswith(b"MOVEXY") for line in self.raw.writes))

    def test_link_registration_is_cleared_after_success_and_failure(self):
        controller = self.controller()
        original = controller._register_link
        with patch.object(controller, "_register_link", wraps=original) as register:
            self.assertEqual("sent", controller.jog("FWD", None, 32).status)
            self.assertIsInstance(register.call_args_list[0].args[0], Stage2SerialLink)
            self.assertEqual((None,), register.call_args_list[-1].args)
        self.assertIsNone(controller._active_link)
        self.raw.block_on = "HELLO"
        controller.arm()
        thread, box = self.start(lambda: controller.jog("FWD", None, 32))
        self.wait(self.raw.read_entered)
        controller.stop()
        self.stopped_result(controller, thread, box, opens=2)
        self.assertIsNone(controller._active_link)

    def test_mcv1_interleaving_is_tolerated_in_link_but_not_parser(self):
        self.raw.prefixes = {name: (b"MCV1|ERR|old|STOPPED\r\n", b"MCV1|DONE|old\r\n")
            for name in ("HELLO", "MOVEXY", "READXY")}
        link = Stage2SerialLink(armed=True, serial_factory=self.raw.factory, read_limit=3)
        events = []
        link.on_progress = events.append
        self.assertEqual(1, len(self.send(link).sent_lines))
        self.assertEqual(6, sum(event["message"].startswith("MCV1|") for event in events))
        with self.assertRaises(Stage2ProtocolError) as caught:
            parse_stage2_reply(b"MCV1|DONE|old\r\n")
        self.assertEqual("WRONG_PROTOCOL", caught.exception.reason_code)
        self.assertFalse(any(b"MCV1" in line or b"PUMP" in line for line in self.raw.writes))

    def test_stop_confirmation_tolerates_mcv1_and_queued_step_replies(self):
        controller, thread, box = self.blocked_jog("MOVEXY")
        self.raw.prefixes["STOP"] = (b"MCV1|ERR|old|STOPPED\r\n", b"MCV1|DONE|old\r\n")
        controller.stop()
        result = self.stopped_result(controller, thread, box)
        self.assertTrue(result.motion.error.stopped)

    def test_foreign_reply_flood_hits_read_limit_without_motion(self):
        self.raw.prefixes["HELLO"] = (b"MCV1|PONG\r\n",) * 6
        link = Stage2SerialLink(armed=True, serial_factory=self.raw.factory, read_limit=3)
        with self.assertRaises(Stage2TransmitError) as caught:
            self.send(link)
        self.assertEqual("WRONG_PROTOCOL", caught.exception.reason_code)
        self.assertFalse(caught.exception.motion_attempted)
        self.assertFalse(caught.exception.stopped)
        self.assertEqual(6, self.raw.reads)  # 3 HELLO + 3 cleanup, no unbounded loop.
        self.assertFalse(any(line.startswith(b"MOVEXY") for line in self.raw.writes))

    def test_stop_confirmation_read_limit_keeps_operator_reason(self):
        controller, thread, box = self.blocked_jog("MOVEXY")
        self.raw.prefixes["STOP"] = (b"MCV1|ERR|old|STOPPED\r\n",) * 50
        controller.stop()
        result = self.stopped_result(controller, thread, box)
        self.assertFalse(result.motion.error.stopped)
        self.assertEqual(42, self.raw.reads)  # HELLO, interrupted START, 40 STOP lines.

    def test_closed_link_stop_does_not_reconnect_or_write(self):
        link = Stage2SerialLink(armed=True, serial_factory=self.raw.factory)
        self.send(link)
        before = list(self.raw.writes)
        self.assertFalse(link.request_stop())
        self.assertEqual(1, self.raw.opens)
        self.assertEqual(before, self.raw.writes)


if __name__ == "__main__":
    unittest.main()
