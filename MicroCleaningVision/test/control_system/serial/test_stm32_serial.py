"""STM32SerialController 无硬件测试：未武装拒绝 PUMP，武装路径使用脚本串口。"""

from dataclasses import replace
import unittest

from microcleaning.contracts import SafetyOutcome
from microcleaning.control_system.serial.fake_serial import FakeSerialController
from microcleaning.control_system.safety.fixed_rule import propose_pump_in_place
from microcleaning.control_system.safety.governor import approve_human_gate, evaluate_action
from microcleaning.control_system.serial.stm32_protocol import encode_pump, encode_stop
from microcleaning.control_system.serial.stm32_serial import STM32SerialController
from microcleaning.data_learning.image_quality import ImageQuality, build_observation
from microcleaning.vision.contamination import ContaminationMeasurement
from microcleaning.vision.state_estimator import estimate_state


class ScriptedNucleoSerial:
    """按最近一次写入返回 PONG/STATUS/ACK/DONE，不打开 COM 口。"""

    def __init__(
        self,
        *,
        estop: bool = False,
        fail_pump: bool = False,
        timeout_status: bool = False,
        timeout_pump: bool = False,
    ) -> None:
        self.estop = estop
        self.fail_pump = fail_pump
        self.timeout_status = timeout_status
        self.timeout_pump = timeout_pump
        self.writes: list[bytes] = []
        self._queue: list[bytes] = []
        self.closed = False

    def reset_input_buffer(self) -> None:
        return None

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        line = data.decode("ascii").strip()
        parts = line.split("|")
        kind = parts[1] if len(parts) > 1 else ""
        if kind == "PING":
            self._queue.append(b"MCV1|PONG\n")
        elif kind == "STATUS":
            if self.timeout_status:
                return len(data)
            flag = "1" if self.estop else "0"
            self._queue.append(f"MCV1|STATUS|ESTOP={flag}|PUMP=0\n".encode("ascii"))
        elif kind == "STOP":
            self._queue.append(b"MCV1|ACK|STOP\n")
            self._queue.append(b"MCV1|DONE|STOP\n")
        elif kind == "PUMP":
            if self.timeout_pump:
                return len(data)
            action_id = parts[2]
            if self.fail_pump:
                self._queue.append(f"MCV1|ERR|{action_id}|ESTOP\n".encode("ascii"))
            else:
                self._queue.append(f"MCV1|ACK|{action_id}\n".encode("ascii"))
                self._queue.append(f"MCV1|DONE|{action_id}\n".encode("ascii"))
        return len(data)

    def flush(self) -> None:
        return None

    def readline(self) -> bytes:
        if not self._queue:
            return b""
        return self._queue.pop(0)

    def close(self) -> None:
        self.closed = True


class STM32SerialControllerTests(unittest.TestCase):
    def setUp(self):
        quality = ImageQuality(0.95, 0.95, 0.95)
        self.pre = build_observation(
            task_id="serial",
            frame_id="pre",
            raw_image_ref="replay://pre.png",
            quality=quality,
        )
        self.state = estimate_state(
            self.pre,
            ContaminationMeasurement(80.0, (10.0, 12.0), 0.2, 0.95),
            device_state={"controller_connected": True, "interlock_ok": True},
        )
        self.request = propose_pump_in_place(self.state)
        human = evaluate_action(self.state, self.request)
        self.allow = approve_human_gate(self.state, self.request, human, confirmed=True)

    def test_unarmed_controller_refuses_allow_pump(self):
        serial = ScriptedNucleoSerial()
        controller = STM32SerialController(arm_pump=False, serial_factory=lambda: serial)
        with self.assertRaises(PermissionError) as caught:
            controller.execute(self.request, self.allow)
        self.assertIn("未武装", str(caught.exception))
        self.assertEqual([], serial.writes)

    def test_armed_controller_refuses_human_decision(self):
        serial = ScriptedNucleoSerial()
        controller = STM32SerialController(arm_pump=True, serial_factory=lambda: serial)
        human = evaluate_action(self.state, self.request)
        with self.assertRaises(PermissionError):
            controller.execute(self.request, human)
        self.assertEqual([], serial.writes)

    def test_armed_allow_path_sends_short_pump_and_records_done(self):
        serial = ScriptedNucleoSerial()
        controller = STM32SerialController(arm_pump=True, serial_factory=lambda: serial)
        receipt = controller.execute(self.request, self.allow)
        self.assertTrue(receipt.success)
        self.assertEqual("stm32_serial", receipt.mode)
        self.assertEqual("DONE", receipt.controller_state)
        self.assertEqual(self.request.duration_ms, receipt.actual_duration_ms)
        self.assertEqual((0.0, 0.0), receipt.actual_target_mm)
        self.assertIn(encode_pump(self.request.action_id, self.request.duration_ms, max_duration_ms=300), serial.writes)
        pump_writes = [item for item in serial.writes if item.startswith(b"MCV1|PUMP|")]
        self.assertEqual(1, len(pump_writes))

    def test_spray_at_point_is_not_translated(self):
        serial = ScriptedNucleoSerial()
        controller = STM32SerialController(arm_pump=True, serial_factory=lambda: serial)
        move_like = replace(self.request, primitive="SPRAY_AT_POINT")
        with self.assertRaises(PermissionError) as caught:
            controller.execute(move_like, self.allow)
        self.assertIn("PUMP_IN_PLACE", str(caught.exception))
        self.assertEqual([], serial.writes)

    def test_token_cannot_be_replayed(self):
        serial = ScriptedNucleoSerial()
        controller = STM32SerialController(arm_pump=True, serial_factory=lambda: serial)
        self.assertTrue(controller.execute(self.request, self.allow).success)
        with self.assertRaises(PermissionError):
            controller.execute(self.request, self.allow)

    def test_fake_serial_can_execute_confirmed_in_place_pump(self):
        receipt = FakeSerialController().execute(self.request, self.allow)
        self.assertTrue(receipt.success)
        self.assertEqual("fake_serial", receipt.mode)
        self.assertEqual((0.0, 0.0), receipt.actual_target_mm)

    def test_ping_works_without_arming(self):
        serial = ScriptedNucleoSerial()
        controller = STM32SerialController(arm_pump=False, serial_factory=lambda: serial)
        pong = controller.ping()
        self.assertEqual("PONG", pong.kind)
        self.assertTrue(any(item.startswith(b"MCV1|PING") for item in serial.writes))
        self.assertFalse(any(item.startswith(b"MCV1|PUMP|") for item in serial.writes))

    def test_stop_works_without_arming_and_does_not_send_pump(self):
        serial = ScriptedNucleoSerial()
        controller = STM32SerialController(arm_pump=False, serial_factory=lambda: serial)
        done = controller.stop()
        self.assertEqual("DONE", done.kind)
        self.assertEqual("STOP", done.action_id)
        self.assertIn(encode_stop(), serial.writes)
        self.assertFalse(any(item.startswith(b"MCV1|PUMP|") for item in serial.writes))

    def test_estop_status_blocks_pump_before_write(self):
        serial = ScriptedNucleoSerial(estop=True)
        controller = STM32SerialController(arm_pump=True, serial_factory=lambda: serial)
        receipt = controller.execute(self.request, self.allow)
        self.assertFalse(receipt.success)
        self.assertEqual("ESTOP", receipt.error_code)
        self.assertFalse(any(item.startswith(b"MCV1|PUMP|") for item in serial.writes))

    def test_status_timeout_does_not_send_pump(self):
        serial = ScriptedNucleoSerial(timeout_status=True)
        controller = STM32SerialController(arm_pump=True, serial_factory=lambda: serial)
        receipt = controller.execute(self.request, self.allow)
        self.assertFalse(receipt.success)
        self.assertEqual("RESPONSE_TIMEOUT", receipt.error_code)
        self.assertFalse(any(item.startswith(b"MCV1|PUMP|") for item in serial.writes))

    def test_pump_timeout_is_recorded_without_success(self):
        serial = ScriptedNucleoSerial(timeout_pump=True)
        controller = STM32SerialController(arm_pump=True, serial_factory=lambda: serial)
        receipt = controller.execute(self.request, self.allow)
        self.assertFalse(receipt.success)
        self.assertEqual("RESPONSE_TIMEOUT", receipt.error_code)
        self.assertTrue(any(item.startswith(b"MCV1|PUMP|") for item in serial.writes))

    def test_fail_pump_returns_estop_error_receipt(self):
        serial = ScriptedNucleoSerial(fail_pump=True)
        controller = STM32SerialController(arm_pump=True, serial_factory=lambda: serial)
        receipt = controller.execute(self.request, self.allow)
        self.assertFalse(receipt.success)
        self.assertEqual("ESTOP", receipt.error_code)


if __name__ == "__main__":
    unittest.main()
