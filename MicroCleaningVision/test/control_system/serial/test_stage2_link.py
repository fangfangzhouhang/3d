"""对抗性检查：喷水报文、超预算和未武装都不能进 Stage 2 双轴串口。"""

import unittest

from microcleaning.control_system.planning.stage2_axes import (
    AxisSlot,
    dispatch_motion,
    stage2_axis_slots,
    stage2_stepper,
)
from microcleaning.control_system.planning.stepper_preview import preview_motion
from microcleaning.control_system.serial.stage2_link import Stage2SerialLink
from microcleaning.control_system.serial.stage2_protocol import Stage2ProtocolError, encode_pulse, parse_stage2_reply
from microcleaning.control_system.serial.stm32_protocol import encode_ping


class ScriptedStage2:
    def __init__(self, *, hello: str = "STEP_OK v0.3", busy_once: bool = False) -> None:
        self.writes: list[bytes] = []
        self.hello = hello
        self.busy_once = busy_once
        self._queue: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        text = data.decode("ascii").strip()
        if text == "HELLO":
            self._queue.append((self.hello + "\r\n").encode("ascii"))
        elif text.startswith("MOVEXY "):
            _cmd, nx, dx, ny, dy = text.split()
            self._queue.append(
                f"STEP2_START X={nx} {dx} Y={ny} {dy}\r\n".encode("ascii")
            )
        elif text == "READXY":
            busy = "1" if self.busy_once else "0"
            self.busy_once = False
            self._queue.append(f"STEP2 X=1 BX={busy} Y=1 BY={busy}\r\n".encode("ascii"))
        elif text == "STOP":
            self._queue.append(b"STEP_STOPPED\r\n")
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
        self.closed = True


def _motion():
    return preview_motion(
        ((2.0, 1.0), (1.0, 1.0)),
        (0,),
        stage2_stepper(),
    )


class Stage2DispatchTests(unittest.TestCase):
    def test_xy_both_planned_and_present_on_wire(self):
        dispatch = dispatch_motion(_motion(), budget=10000)
        self.assertIn("MOVEXY 640 FWD 320 FWD", dispatch.lines)
        self.assertIn("MOVEXY 320 REV 0 FWD", dispatch.lines)
        self.assertEqual(960, dispatch.planned_abs_steps_x)
        self.assertEqual(320, dispatch.planned_abs_steps_y)
        self.assertNotIn("MCV1", dispatch.wire_text())
        self.assertTrue(dispatch.to_dict()["y_on_wire"])

    def test_budget_keeps_later_moves_off_the_list(self):
        dispatch = dispatch_motion(_motion(), budget=100)
        self.assertEqual((), dispatch.lines)
        self.assertTrue(dispatch.truncated)
        self.assertGreater(dispatch.planned_abs_steps_x, 100)

    def test_budget_stops_instead_of_skipping_to_a_later_short_leg(self):
        motion = preview_motion(((4.0, 0.0), (4.1, 0.0)), (0,), stage2_stepper())
        dispatch = dispatch_motion(motion, budget=700)
        self.assertEqual((), dispatch.lines)
        self.assertTrue(dispatch.truncated)

    def test_y_slot_is_wired_and_transmit_allowed(self):
        _x_slot, y_slot = stage2_axis_slots()
        self.assertTrue(y_slot.wired)
        self.assertTrue(y_slot.transmit)
        y_slot.validate()  # 双轴版本不再拒绝 Y

    def test_unwired_slot_still_rejected(self):
        with self.assertRaises(ValueError):
            AxisSlot("y", wired=False, transmit=True).validate()

    def test_mcv1_ping_is_not_a_pulse_line(self):
        self.assertEqual(b"PULSE 640 FWD\r\n", encode_pulse(640, "FWD"))
        self.assertNotIn(b"PULSE", encode_ping())
        with self.assertRaises(Stage2ProtocolError):
            parse_stage2_reply(b"MCV1|PONG\r\n")


class Stage2LinkTests(unittest.TestCase):
    def test_unarmed_does_not_construct_serial(self):
        opened = {"count": 0}

        def factory():
            opened["count"] += 1
            return ScriptedStage2()

        link = Stage2SerialLink(port="COM5", armed=False, serial_factory=factory)
        with self.assertRaises(PermissionError):
            link.transmit(dispatch_motion(_motion(), budget=10000))
        self.assertEqual(0, opened["count"])

    def test_movexy_commands_are_written(self):
        port = ScriptedStage2(busy_once=True)
        link = Stage2SerialLink(port="COM5", armed=True, serial_factory=lambda: port)
        result = link.transmit(dispatch_motion(_motion(), budget=10000))
        written = b"".join(port.writes)
        self.assertTrue(written.startswith(b"HELLO\r\n"))
        self.assertIn(b"MOVEXY 640 FWD 320 FWD\r\n", written)
        self.assertIn(b"READXY\r\n", written)
        self.assertNotIn(b"MCV1", written)
        self.assertNotIn(b"PULSE ", written)
        self.assertGreater(len(result.sent_lines), 0)
        self.assertTrue(port.closed)

    def test_wrong_hello_sends_no_movexy(self):
        port = ScriptedStage2(hello="STEP_OK v0.2")
        link = Stage2SerialLink(port="COM5", armed=True, serial_factory=lambda: port)
        with self.assertRaises(Stage2ProtocolError):
            link.transmit(dispatch_motion(_motion(), budget=10000))
        written = b"".join(port.writes)
        self.assertNotIn(b"MOVEXY", written)


if __name__ == "__main__":
    unittest.main()
