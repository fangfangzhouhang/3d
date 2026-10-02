"""对抗性检查：喷水报文、超预算和未武装都不能进 Stage 2 双轴串口；中途出错必须先 STOP。"""

import unittest

from microcleaning.control_system.planning.stage2_axes import (
    AxisSlot,
    dispatch_motion,
    parse_movexy_line,
    single_move_dispatch,
    stage2_axis_slots,
    stage2_stepper,
)
from microcleaning.control_system.planning.stepper_preview import preview_motion
from microcleaning.control_system.serial.stage2_link import (
    Stage2SerialLink,
    Stage2TransmitError,
    idle_wait_seconds,
)
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
        with self.assertRaises(Stage2TransmitError) as caught:
            link.transmit(dispatch_motion(_motion(), budget=10000))
        written = b"".join(port.writes)
        self.assertNotIn(b"MOVEXY", written)
        self.assertEqual("WRONG_VERSION", caught.exception.reason_code)
        self.assertFalse(caught.exception.motion_attempted)
        self.assertIsInstance(caught.exception.__cause__, Stage2ProtocolError)

    def test_timeout_mid_move_sends_stop_and_keeps_partial_record(self):
        port = SilentReadXY()
        link = Stage2SerialLink(port="COM5", armed=True, serial_factory=lambda: port)
        with self.assertRaises(Stage2TransmitError) as caught:
            link.transmit(dispatch_motion(_motion(), budget=10000))
        error = caught.exception
        self.assertEqual("TIMEOUT", error.reason_code)
        self.assertIn(b"STOP\r\n", port.writes)
        self.assertTrue(error.stopped)
        self.assertTrue(error.motion_attempted)
        self.assertEqual((), error.sent_lines)
        self.assertEqual("MOVEXY 640 FWD 320 FWD", error.in_flight_line)
        self.assertTrue(port.closed)

    def test_ctrl_c_during_move_still_sends_stop(self):
        port = InterruptOnReadXY()
        link = Stage2SerialLink(port="COM5", armed=True, serial_factory=lambda: port)
        with self.assertRaises(Stage2TransmitError) as caught:
            link.transmit(dispatch_motion(_motion(), budget=10000))
        self.assertEqual("INTERRUPTED", caught.exception.reason_code)
        self.assertEqual(b"STOP\r\n", port.writes[-1])
        self.assertTrue(caught.exception.stopped)

    def test_boot_banner_is_cleared_before_hello(self):
        port = BannerFirst()
        link = Stage2SerialLink(port="COM5", armed=True, serial_factory=lambda: port)
        result = link.transmit(dispatch_motion(_motion(), budget=10000))
        self.assertEqual("STEP_OK v0.3", result.replies[0])
        self.assertEqual(2, len(result.sent_lines))

    def test_idle_wait_follows_firmware_speed(self):
        self.assertAlmostEqual(0.7, idle_wait_seconds(100, 500))
        self.assertAlmostEqual(2.5, idle_wait_seconds(100, 50))
        with self.assertRaises(ValueError):
            Stage2SerialLink(port="COM5", speed_hz=5)


class SilentReadXY(ScriptedStage2):
    """MOVEXY 正常开始，但 READXY 一直不回：模拟线松或板子卡死。"""

    def write(self, data: bytes) -> int:
        if data.decode("ascii").strip() == "READXY":
            self.writes.append(data)
            return len(data)
        return super().write(data)


class InterruptOnReadXY(ScriptedStage2):
    """第一次 READXY 时人按了 Ctrl+C。"""

    def __init__(self) -> None:
        super().__init__()
        self.interrupted = False

    def write(self, data: bytes) -> int:
        if data.decode("ascii").strip() == "READXY" and not self.interrupted:
            self.interrupted = True
            raise KeyboardInterrupt
        return super().write(data)


class BannerFirst(ScriptedStage2):
    """上电横幅还在接收缓冲里。"""

    def __init__(self) -> None:
        super().__init__()
        self._queue.extend([b"=== STAGE2 STEPMOTOR XY TEST ===\r\n", b"Waiting...\r\n"])

    def reset_input_buffer(self) -> None:
        self._queue.clear()


class Stage2LineHelperTests(unittest.TestCase):
    def test_parse_movexy_line_signs(self):
        self.assertEqual((640, -320), parse_movexy_line("MOVEXY 640 FWD 320 REV"))
        self.assertEqual((0, 5), parse_movexy_line("MOVEXY 0 FWD 5 FWD"))
        with self.assertRaises(ValueError):
            parse_movexy_line("MOVEXY 10 XYZ 10 FWD")
        with self.assertRaises(ValueError):
            parse_movexy_line("MCV1|PUMP|a|100")

    def test_single_move_dispatch_shares_budget_rule(self):
        dispatch = single_move_dispatch(300, "REV", 0, "FWD", budget=400)
        self.assertEqual(("MOVEXY 300 REV 0 FWD",), dispatch.lines)
        over = single_move_dispatch(500, "FWD", 0, "FWD", budget=400)
        self.assertEqual((), over.lines)
        self.assertTrue(over.truncated)
        self.assertEqual((), single_move_dispatch(0, "FWD", 0, "FWD").lines)
        with self.assertRaises(ValueError):
            single_move_dispatch(10, "BACK", 0, "FWD")


if __name__ == "__main__":
    unittest.main()
