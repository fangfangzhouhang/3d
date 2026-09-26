"""Stage2 双轴步进与现有 MCV1 串口的软件对照。不开 COM，不发脉冲。"""

import unittest

from microcleaning.control_system.planning.stepper_preview import StepperConfig, mm_delta_to_steps
from microcleaning.control_system.serial.stm32_protocol import (
    STM32ProtocolError,
    encode_ping,
    encode_stop,
    parse_response,
)


# 两台 DM542 都拨成 1600 脉冲/转；丝杆或当量是每转 5 mm。细分已含在 1600 里。
XY_AXIS = StepperConfig(
    version="stage2-xy-v0",
    steps_per_rev=1600,
    microstep=1,
    lead_mm_per_rev_x=5.0,
    lead_mm_per_rev_y=5.0,
)


class Stage2XYAxisMathTests(unittest.TestCase):
    def test_five_millimetres_per_rev_is_320_steps_per_mm(self):
        steps_per_mm, source = XY_AXIS.steps_per_mm("x")
        self.assertEqual(320.0, steps_per_mm)
        self.assertEqual("lead_formula", source)

    def test_two_millimetres_forward_is_640_pulses(self):
        steps_x, steps_y, rev_x, _rev_y = mm_delta_to_steps((2.0, 0.0), XY_AXIS)
        self.assertEqual(640, steps_x)
        self.assertEqual(0, steps_y)
        self.assertAlmostEqual(0.4, rev_x)

    def test_one_millimetre_back_is_negative_320_pulses(self):
        steps_x, steps_y, rev_x, _rev_y = mm_delta_to_steps((-1.0, 0.0), XY_AXIS)
        self.assertEqual(-320, steps_x)
        self.assertEqual(0, steps_y)
        self.assertAlmostEqual(-0.2, rev_x)

    def test_preview_computes_both_axes(self):
        """双轴版本：X/Y 都按 5 mm/转算步数，均可发往 Stage 2。"""

        steps_x, steps_y, _rev_x, _rev_y = mm_delta_to_steps((1.0, 1.0), XY_AXIS)
        self.assertEqual(320, steps_x)
        self.assertEqual(320, steps_y)


class Stage2ProtocolConflictTests(unittest.TestCase):
    def test_mcv1_text_is_not_a_stage2_command(self):
        self.assertEqual(b"MCV1|PING\n", encode_ping())
        self.assertEqual(b"MCV1|STOP\n", encode_stop())
        self.assertNotIn(b"PULSE", encode_ping())
        self.assertNotIn(b"HELLO", encode_stop())
        self.assertNotEqual(b"STOP\r\n", encode_stop())

    def test_stage2_replies_are_rejected_by_mcv1_parser(self):
        for line in ("STEP_OK v0.2", "STEP_START N=640 FWD", "STEP_SENT=200 BUSY=0"):
            with self.assertRaises(STM32ProtocolError) as caught:
                parse_response(line + "\n")
            self.assertEqual("UNSUPPORTED_VERSION", caught.exception.reason_code)

    def test_stage2_motor_config_still_cannot_be_sent(self):
        with self.assertRaises(ValueError):
            StepperConfig(
                steps_per_rev=1600,
                microstep=1,
                lead_mm_per_rev_x=5.0,
                send_to_controller=True,
            ).validate()


if __name__ == "__main__":
    unittest.main()
