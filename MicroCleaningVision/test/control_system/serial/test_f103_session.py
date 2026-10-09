"""单串口会话：步进成功后才喷水。只用脚本串口，不打开真实 COM。"""

import sys
import threading
import types
import unittest

from microcleaning.control_system.planning.stage2_axes import dispatch_motion, stage2_stepper
from microcleaning.control_system.planning.stepper_preview import preview_motion
from microcleaning.control_system.safety.fixed_rule import propose_pump_in_place
from microcleaning.control_system.safety.governor import approve_human_gate, evaluate_action
from microcleaning.control_system.safety.motion_gate import (
    MotionRequest,
    approve_motion_gate,
    evaluate_motion,
    new_motion_request_id,
)
from microcleaning.control_system.serial.f103_session import (
    F103SerialSession,
    F103SessionBusy,
    com_occupied_by_session,
)
from microcleaning.control_system.serial.stage2_link import Stage2SerialLink
from microcleaning.control_system.serial.stage2_protocol import Stage2ProtocolError, parse_stage2_reply
from microcleaning.control_system.serial.stm32_protocol import STM32ProtocolError, encode_pump, parse_response
from microcleaning.control_system.serial.stm32_serial import STM32SerialController
from microcleaning.data_learning.image_quality import ImageQuality, build_observation
from microcleaning.vision.contamination import ContaminationMeasurement
from microcleaning.vision.state_estimator import estimate_state


_SERIAL_MODULE = None
_SERIAL_ORIGINAL = None
_SERIAL_INSERTED = False


def setUpModule() -> None:
    """挡住 serial.Serial，避免哪条路径不小心打开真实 COM。"""

    global _SERIAL_MODULE, _SERIAL_ORIGINAL, _SERIAL_INSERTED
    serial_mod = sys.modules.get("serial")
    if serial_mod is None:
        serial_mod = types.ModuleType("serial")
        sys.modules["serial"] = serial_mod
        _SERIAL_INSERTED = True
        _SERIAL_ORIGINAL = None
    else:
        _SERIAL_ORIGINAL = getattr(serial_mod, "Serial", None)
        _SERIAL_INSERTED = False

    def _boom(*args, **kwargs):
        raise AssertionError("测试不应打开真实串口")

    serial_mod.Serial = _boom
    _SERIAL_MODULE = serial_mod


def tearDownModule() -> None:
    if _SERIAL_INSERTED:
        sys.modules.pop("serial", None)
        return
    if _SERIAL_MODULE is not None and _SERIAL_ORIGINAL is not None:
        _SERIAL_MODULE.Serial = _SERIAL_ORIGINAL


class DualProtocolSerial:
    """同一条脚本串口，按写入分别回答 STEP 或 MCV1。reset 会清空队列。"""

    def __init__(
        self,
        *,
        hello: bytes = b"STEP_OK v0.3\r\n",
        timeout_readxy: bool = False,
        timeout_pump: bool = False,
        raise_on_pump: bool = False,
        status_reply: bytes | None = None,
        ack_only: bool = False,
        tail_on_readxy: int = 0,
    ) -> None:
        self.hello = hello
        self.timeout_readxy = timeout_readxy
        self.timeout_pump = timeout_pump
        self.raise_on_pump = raise_on_pump
        self.status_reply = status_reply
        self.ack_only = ack_only
        self.tail_on_readxy = tail_on_readxy
        self.writes: list[bytes] = []
        self._queue: list[bytes] = []
        self.readxy_count = 0
        self.reset_calls = 0
        self.close_count = 0
        self.closed = False

    def preload(self, line: bytes) -> None:
        self._queue.append(line)

    def reset_input_buffer(self) -> None:
        self.reset_calls += 1
        self._queue.clear()

    def write(self, data: bytes) -> int:
        text = data.decode("ascii").strip()
        if self.raise_on_pump and text.startswith("MCV1|PUMP|"):
            self.writes.append(data)
            raise RuntimeError("pump link lost")
        self.writes.append(data)
        if text == "HELLO":
            self._queue.append(self.hello)
        elif text.startswith("MOVEXY "):
            _cmd, nx, dx, ny, dy = text.split()
            old_x, old_y = getattr(self, "_counts", (0, 0))
            self._counts = (int(nx) or old_x, int(ny) or old_y)
            self._queue.append(f"STEP2_START X={nx} {dx} Y={ny} {dy}\r\n".encode("ascii"))
        elif text == "READXY":
            self.readxy_count += 1
            if not self.timeout_readxy:
                self._queue.append(f"STEP2 X={self._counts[0]} BX=0 Y={self._counts[1]} BY=0\r\n".encode("ascii"))
                if self.readxy_count == self.tail_on_readxy:
                    self._queue.append(b"MCV1|ERR|tail|STOPPED\n")
                    self._queue.append(b"ERR: TAIL\r\n")
        elif text == "STOP":
            self._queue.append(b"STEP_STOPPED\r\n")
        elif text.startswith("MCV1|"):
            parts = text.split("|")
            kind = parts[1] if len(parts) > 1 else ""
            if kind == "STATUS":
                if self.status_reply is not None:
                    self._queue.append(self.status_reply)
                else:
                    self._queue.append(b"MCV1|STATUS|ESTOP=0|PUMP=0\n")
            elif kind == "STOP":
                self._queue.append(b"MCV1|ACK|STOP\n")
                self._queue.append(b"MCV1|DONE|STOP\n")
            elif kind == "PUMP":
                if not self.timeout_pump:
                    action_id = parts[2]
                    self._queue.append(f"MCV1|ACK|{action_id}\n".encode("ascii"))
                    if self.ack_only:
                        self._queue.append(f"MCV1|ACK|{action_id}\n".encode("ascii"))
                    else:
                        self._queue.append(f"MCV1|DONE|{action_id}\n".encode("ascii"))
            elif kind == "PING":
                self._queue.append(b"MCV1|PONG\n")
        else:
            self._queue.append(b"ERR: BAD_CMD\r\n")
        return len(data)

    def flush(self) -> None:
        return None

    def readline(self) -> bytes:
        if not self._queue:
            return b""
        return self._queue.pop(0)

    def close(self) -> None:
        self.close_count += 1
        self.closed = True


def _motion():
    return preview_motion(((2.0, 1.0), (1.0, 1.0)), (0,), stage2_stepper())


def _approved(dispatch):
    request = MotionRequest(
        request_id=new_motion_request_id(),
        task_id="test-task",
        lines=dispatch.lines,
        position_before_steps=(0, 0),
        start_reference="image_center",
        calibration_ref="cal/mm_per_px.json",
        calibration_sha256="0" * 64,
    )
    decision = approve_motion_gate(request, evaluate_motion(request), confirmed=True)
    return request, decision


class ProtocolBoundaryTests(unittest.TestCase):
    def test_each_parser_rejects_the_other_protocol(self):
        with self.assertRaises(Stage2ProtocolError) as step_error:
            parse_stage2_reply(b"MCV1|DONE|A001\n")
        self.assertEqual("WRONG_PROTOCOL", step_error.exception.reason_code)

        with self.assertRaises(STM32ProtocolError) as pump_error:
            parse_response(b"STEP2 X=1 BX=0 Y=1 BY=0\r\n")
        self.assertEqual("UNSUPPORTED_VERSION", pump_error.exception.reason_code)
        with self.assertRaises(STM32ProtocolError):
            parse_response(b"STEP_OK v0.3\r\n")


class F103SessionTests(unittest.TestCase):
    def test_drain_io_failure_releases_registry_and_closes_owned_port(self):
        from unittest.mock import patch
        port = DualProtocolSerial()
        session, _, _, _ = self._owned_session("COM-drain-failure", port)
        lease = object()
        guard = session.acquire("mcv1", lease)
        with patch.object(guard, "finish", side_effect=OSError("rx buffer lost")):
            with self.assertRaises(F103SessionBusy):
                session.release(lease)
        self.assertFalse(session.is_open)
        self.assertTrue(session.failed)
        self.assertFalse(com_occupied_by_session("COM-drain-failure"))
        self.assertEqual(1, port.close_count)
        with self.assertRaises(F103SessionBusy):
            session.acquire("stage2", object())
    def test_persistent_success_keeps_one_connection_for_next_cycle(self):
        port = DualProtocolSerial()
        session, link, controller, opens = self._owned_session("COM-persistent", port)
        for _ in range(2):
            dispatch, request, decision = self._motion_parts()
            pump_request, pump_decision = self._pump_parts()
            result = session.run_step_then_pump(link, controller, dispatch=dispatch,
                motion_request=request, motion_decision=decision,
                pump_request=pump_request, pump_decision=pump_decision, close_after=False)
            self.assertTrue(result.output_finished)
            self.assertEqual(dispatch.lines, result.motion_result.sent_lines)
            self.assertTrue(session.is_open)
        self.assertEqual(1, opens["n"])
        self.assertEqual(0, port.close_count)
        session.close()
        self.assertEqual(1, port.close_count)

    def test_wrong_pump_request_is_rejected_before_motion(self):
        from dataclasses import replace
        port = DualProtocolSerial()
        session, link, controller, opens = self._owned_session("COM-preflight", port)
        request, decision = self._pump_parts()
        result = self._run(session, link, controller, self._motion_parts(),
                           (replace(request, duration_ms=request.duration_ms + 1), decision))
        self.assertEqual(0, opens["n"])
        self.assertEqual([], port.writes)
        self.assertFalse(result.pump_called)
        self.assertTrue(session.failed)

    def test_pump_failure_retains_completed_motion_and_latches_session(self):
        port = DualProtocolSerial(timeout_pump=True)
        session, link, controller, _ = self._owned_session("COM-latch", port)
        motion = self._motion_parts()
        result = self._run(session, link, controller, motion, self._pump_parts())
        self.assertEqual(motion[0].lines, result.motion_result.sent_lines)
        self.assertFalse(result.output_finished)
        self.assertTrue(session.failed)
        with self.assertRaises(F103SessionBusy):
            self._run(session, link, controller, self._motion_parts(), self._pump_parts())

    def test_truncated_dispatch_never_opens_or_calls_pump(self):
        from dataclasses import replace
        port = DualProtocolSerial()
        session, link, controller, opens = self._owned_session("COM-truncated", port)
        dispatch, request, decision = self._motion_parts()
        result = self._run(session, link, controller, (replace(dispatch, truncated=True), request, decision), self._pump_parts())
        self.assertEqual(0, opens["n"])
        self.assertFalse(result.pump_called)
        self.assertEqual("INCOMPLETE_DISPATCH", result.motion_reason)

    def test_keyboard_interrupt_during_pump_stops_and_retains_motion(self):
        port = DualProtocolSerial()
        session, link, controller, _ = self._owned_session("COM-interrupt", port)
        def interrupt(*_args, **_kwargs):
            raise KeyboardInterrupt()
        controller.execute = interrupt
        result = self._run(session, link, controller, self._motion_parts(), self._pump_parts())
        self.assertIsNotNone(result.motion_result)
        self.assertTrue(result.stopped)
        self.assertEqual("INTERRUPTED", result.pump_reason)
        self.assertFalse(session.is_open)
    def setUp(self):
        quality = ImageQuality(0.95, 0.95, 0.95)
        pre = build_observation(
            task_id="serial",
            frame_id="pre",
            raw_image_ref="replay://pre.png",
            quality=quality,
        )
        self.state = estimate_state(
            pre,
            ContaminationMeasurement(80.0, (10.0, 12.0), 0.2, 0.95),
            device_state={"controller_connected": True, "interlock_ok": True},
        )

    def _motion_parts(self):
        dispatch = dispatch_motion(_motion(), budget=1600)
        request, decision = _approved(dispatch)
        return dispatch, request, decision

    def _pump_parts(self, *, confirmed: bool = True):
        request = propose_pump_in_place(self.state)
        self.assertIsNotNone(request)
        assert request is not None
        human = evaluate_action(self.state, request)
        if not confirmed:
            return request, human
        return request, approve_human_gate(self.state, request, human, confirmed=True)

    def _run(self, session, link, controller, motion, pump):
        dispatch, motion_request, motion_decision = motion
        pump_request, pump_decision = pump
        return session.run_step_then_pump(
            link,
            controller,
            dispatch=dispatch,
            motion_request=motion_request,
            motion_decision=motion_decision,
            pump_request=pump_request,
            pump_decision=pump_decision,
        )

    def _owned_session(self, port_name: str, port: DualProtocolSerial):
        opens = {"n": 0}

        def factory():
            opens["n"] += 1
            return port

        session = F103SerialSession(port=port_name, serial_factory=factory)
        self.addCleanup(session.close)
        link = Stage2SerialLink(armed=True, session=session, speed_hz=20000)
        controller = STM32SerialController(arm_pump=True, session=session)
        return session, link, controller, opens

    def test_step_then_mcv1_done_uses_one_connection(self):
        port = DualProtocolSerial()
        session, link, controller, opens = self._owned_session("COM71", port)
        calls = {"n": 0}
        original = controller.execute

        def execute(*args, **kwargs):
            calls["n"] += 1
            return original(*args, **kwargs)

        controller.execute = execute
        pump = self._pump_parts()
        result = self._run(session, link, controller, self._motion_parts(), pump)
        blob = b"".join(port.writes)
        pump_writes = [item for item in port.writes if item.startswith(b"MCV1|PUMP|")]

        self.assertEqual(1, opens["n"])
        self.assertEqual(1, port.close_count)
        self.assertEqual(1, calls["n"])
        self.assertTrue(result.serial_opened)
        self.assertTrue(result.pump_called)
        self.assertEqual(1, len(pump_writes))
        self.assertEqual(len(pump_writes[0]), result.pump_bytes)
        self.assertLess(blob.index(b"HELLO\r\n"), blob.index(b"MOVEXY"))
        self.assertLess(blob.index(b"READXY\r\n"), blob.index(b"MCV1|STATUS"))
        self.assertLess(blob.index(b"MCV1|STATUS"), blob.index(b"MCV1|PUMP|"))
        self.assertNotIn(b"MCV1|STOP", blob)
        self.assertNotIn(b"TO_NEEDLE", blob)
        self.assertNotIn(b"TO_SCOPE", blob)
        self.assertIsNotNone(result.receipt)
        assert result.receipt is not None
        self.assertEqual("DONE", result.receipt.controller_state)
        self.assertTrue(result.receipt.success)
        self.assertTrue(result.output_finished)
        self.assertFalse(result.stopped)
        self.assertNotEqual("ACK", result.receipt.controller_state)
        pump_request, _decision = pump
        self.assertIn(
            encode_pump(result.receipt.action_id, pump_request.duration_ms),
            port.writes,
        )

    def test_already_open_connection_is_not_opened_again(self):
        port = DualProtocolSerial()
        opened = {"n": 0}

        def factory():
            opened["n"] += 1
            raise AssertionError("已经打开的连接不应再开第二口")

        session = F103SerialSession(port="COM72", serial_factory=factory)
        self.addCleanup(session.close)
        link = Stage2SerialLink(armed=True, connection=port, speed_hz=20000)
        controller = STM32SerialController(arm_pump=True, connection=port)
        result = self._run(session, link, controller, self._motion_parts(), self._pump_parts())

        self.assertEqual(0, opened["n"])
        self.assertFalse(port.closed)
        self.assertEqual(0, port.close_count)
        self.assertTrue(result.output_finished)
        self.assertGreater(result.pump_bytes, 0)
        self.assertIn(b"HELLO\r\n", port.writes)
        self.assertTrue(any(item.startswith(b"MCV1|PUMP|") for item in port.writes))

    def test_step_failure_does_not_call_pump(self):
        port = DualProtocolSerial(timeout_readxy=True)
        session, link, controller, opens = self._owned_session("COM73", port)
        calls = {"n": 0}

        def execute(*args, **kwargs):
            calls["n"] += 1
            raise AssertionError("步进失败后不应调用喷水")

        controller.execute = execute
        result = self._run(session, link, controller, self._motion_parts(), self._pump_parts())

        self.assertEqual(1, opens["n"])
        self.assertEqual(0, calls["n"])
        self.assertFalse(result.pump_called)
        self.assertEqual(0, result.pump_bytes)
        self.assertEqual("TIMEOUT", result.motion_reason)
        self.assertTrue(result.stopped)
        self.assertFalse(result.output_finished)
        self.assertIn(b"STOP\r\n", port.writes)
        self.assertFalse(any(item.startswith(b"MCV1|PUMP|") for item in port.writes))

    def test_unauthorized_does_not_open_or_send_pump(self):
        cases = (
            "motion_unarmed",
            "pump_unarmed",
            "motion_human",
            "pump_human",
        )
        for index, name in enumerate(cases):
            with self.subTest(name=name):
                port = DualProtocolSerial()
                opens = {"n": 0}

                def factory(port=port, opens=opens):
                    opens["n"] += 1
                    return port

                session = F103SerialSession(port=f"COM8{index}", serial_factory=factory)
                self.addCleanup(session.close)
                link = Stage2SerialLink(armed=name != "motion_unarmed", session=session, speed_hz=20000)
                controller = STM32SerialController(arm_pump=name != "pump_unarmed", session=session)
                calls = {"n": 0}
                original = controller.execute

                def execute(*args, **kwargs):
                    calls["n"] += 1
                    return original(*args, **kwargs)

                controller.execute = execute
                if name == "motion_human":
                    dispatch = dispatch_motion(_motion(), budget=1600)
                    motion_request, _allow = _approved(dispatch)
                    motion = (dispatch, motion_request, evaluate_motion(motion_request))
                else:
                    motion = self._motion_parts()
                pump = self._pump_parts(confirmed=name != "pump_human")
                result = self._run(session, link, controller, motion, pump)

                self.assertEqual(0, opens["n"])
                self.assertEqual(0, calls["n"])
                self.assertFalse(result.serial_opened)
                self.assertFalse(result.pump_called)
                self.assertEqual(0, result.pump_bytes)
                self.assertEqual([], port.writes)

    def test_pump_timeout_stops_and_does_not_pump_again(self):
        port = DualProtocolSerial(timeout_pump=True)
        session, link, controller, _opens = self._owned_session("COM74", port)
        result = self._run(session, link, controller, self._motion_parts(), self._pump_parts())
        pump_at = [index for index, item in enumerate(port.writes) if item.startswith(b"MCV1|PUMP|")]
        stop_at = [index for index, item in enumerate(port.writes) if item.startswith(b"MCV1|STOP")]

        self.assertEqual([pump_at[0]], pump_at)
        self.assertEqual(1, len(stop_at))
        self.assertLess(pump_at[0], stop_at[0])
        self.assertFalse(any(item.startswith(b"MCV1|PUMP|") for item in port.writes[stop_at[0] :]))
        self.assertTrue(result.pump_called)
        self.assertGreater(result.pump_bytes, 0)
        self.assertFalse(result.output_finished)
        self.assertTrue(result.stopped)
        self.assertIsNotNone(result.receipt)
        assert result.receipt is not None
        self.assertFalse(result.receipt.success)
        self.assertEqual("RESPONSE_TIMEOUT", result.receipt.error_code)

    def test_pump_exception_stops_and_does_not_pump_again(self):
        port = DualProtocolSerial(raise_on_pump=True)
        session, link, controller, _opens = self._owned_session("COM75", port)
        result = self._run(session, link, controller, self._motion_parts(), self._pump_parts())
        pump_writes = [item for item in port.writes if item.startswith(b"MCV1|PUMP|")]

        self.assertEqual(1, len(pump_writes))
        self.assertTrue(any(item.startswith(b"MCV1|STOP") for item in port.writes))
        self.assertGreater(result.pump_bytes, 0)
        self.assertFalse(result.output_finished)
        self.assertTrue(result.stopped)
        self.assertIsNone(result.receipt)

    def test_ack_without_done_is_not_output_finished(self):
        port = DualProtocolSerial(ack_only=True)
        session, link, controller, _opens = self._owned_session("COM76", port)
        result = self._run(session, link, controller, self._motion_parts(), self._pump_parts())

        self.assertIsNotNone(result.receipt)
        assert result.receipt is not None
        self.assertEqual("ACK", result.receipt.controller_state)
        self.assertFalse(result.receipt.success)
        self.assertFalse(result.output_finished)
        self.assertTrue(result.stopped)
        self.assertTrue(any(item.startswith(b"MCV1|STOP") for item in port.writes))

    def test_wrong_protocol_reply_is_not_success(self):
        step_port = DualProtocolSerial(hello=b"MCV1|DONE|A001\n")
        step_session, step_link, step_controller, _opens = self._owned_session("COM77", step_port)
        calls = {"n": 0}

        def execute(*args, **kwargs):
            calls["n"] += 1
            raise AssertionError("错误协议不应进入喷水")

        step_controller.execute = execute
        step_result = self._run(
            step_session,
            step_link,
            step_controller,
            self._motion_parts(),
            self._pump_parts(),
        )
        self.assertEqual("WRONG_PROTOCOL", step_result.motion_reason)
        self.assertEqual(0, calls["n"])
        self.assertEqual(0, step_result.pump_bytes)
        self.assertNotIn(b"MOVEXY", b"".join(step_port.writes))
        self.assertFalse(any(item.startswith(b"MCV1|PUMP|") for item in step_port.writes))

        pump_port = DualProtocolSerial(status_reply=b"STEP_OK v0.3\r\n")
        pump_session, pump_link, pump_controller, _opens = self._owned_session("COM78", pump_port)
        pump_result = self._run(
            pump_session,
            pump_link,
            pump_controller,
            self._motion_parts(),
            self._pump_parts(),
        )
        self.assertIsNotNone(pump_result.receipt)
        assert pump_result.receipt is not None
        self.assertFalse(pump_result.receipt.success)
        self.assertEqual("UNSUPPORTED_VERSION", pump_result.receipt.error_code)
        self.assertFalse(pump_result.output_finished)
        self.assertFalse(any(item.startswith(b"MCV1|PUMP|") for item in pump_port.writes))
        self.assertTrue(any(item.startswith(b"MCV1|STOP") for item in pump_port.writes))

    def test_reset_keeps_unread_foreign_reply_until_protocol_switch(self):
        port = DualProtocolSerial(tail_on_readxy=2)
        port.preload(b"MCV1|ERR|pre|STOPPED\n")
        port.preload(b"*** boot banner ***\r\n")
        session, link, controller, _opens = self._owned_session("COM79", port)
        result = self._run(session, link, controller, self._motion_parts(), self._pump_parts())

        self.assertTrue(result.output_finished)
        self.assertEqual(2, port.readxy_count)
        self.assertIn(b"MCV1|ERR|pre|STOPPED\n", session.preserved_replies)
        self.assertIn(b"MCV1|ERR|tail|STOPPED\n", session.preserved_replies)
        self.assertIn(b"ERR: TAIL\r\n", session.finished_replies)
        self.assertFalse(any(b"boot banner" in line for line in session.preserved_replies))
        self.assertFalse(any(b"boot banner" in line for line in session.finished_replies))
        for line in session.preserved_replies:
            with self.assertRaises(Stage2ProtocolError):
                parse_stage2_reply(line)
        self.assertGreater(port.reset_calls, 0)

    def test_one_session_rejects_a_second_owner_and_a_second_open(self):
        port = DualProtocolSerial()
        session = F103SerialSession(port="COM80", connection=port)
        self.addCleanup(session.close)
        lease = object()
        session.acquire("stage2", lease)
        self.assertTrue(com_occupied_by_session("COM80"))

        errors: list[BaseException] = []

        def other_owner() -> None:
            try:
                session.acquire("mcv1", object())
            except F103SessionBusy as exc:
                errors.append(exc)

        thread = threading.Thread(target=other_owner)
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(errors))

        opened = {"n": 0}

        def factory():
            opened["n"] += 1
            return DualProtocolSerial()

        dispatch, motion_request, motion_decision = self._motion_parts()
        other_link = Stage2SerialLink(
            port="COM80",
            armed=True,
            serial_factory=factory,
            speed_hz=20000,
        )
        with self.assertRaises(Exception) as caught:
            other_link.transmit(dispatch, request=motion_request, decision=motion_decision)
        self.assertEqual(0, opened["n"])
        self.assertIsInstance(caught.exception.__cause__, F103SessionBusy)

        pump_request, pump_decision = self._pump_parts()
        other_controller = STM32SerialController(port="COM80", arm_pump=True, serial_factory=factory)
        with self.assertRaises(F103SessionBusy):
            other_controller.execute(pump_request, pump_decision)
        self.assertEqual(0, opened["n"])
        self.assertFalse(any(item.startswith(b"MCV1|PUMP|") for item in port.writes))


if __name__ == "__main__":
    unittest.main()
