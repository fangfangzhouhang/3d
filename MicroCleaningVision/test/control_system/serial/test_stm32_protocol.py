"""成员 C：STM32 文本协议的无硬件合同测试。"""

import unittest

from microcleaning.control_system.stm32_protocol import (
    MAX_LINE_BYTES,
    STM32ProtocolError,
    encode_ping,
    encode_pump,
    encode_status,
    encode_stop,
    parse_response,
)


class STM32ProtocolTests(unittest.TestCase):
    def test_encodes_current_demo_commands(self):
        self.assertEqual(b"MCV1|PING\n", encode_ping())
        self.assertEqual(b"MCV1|STATUS\n", encode_status())
        self.assertEqual(b"MCV1|STOP\n", encode_stop())
        self.assertEqual(b"MCV1|PUMP|A001|300\n", encode_pump("A001", 300))

    def test_pump_requires_valid_id_and_bounded_integer_duration(self):
        bad_inputs = (
            ("", 100),
            ("含中文", 100),
            ("A001", 0),
            ("A001", 99),
            ("A001", 501),
            ("A001", 10.5),
            ("A001", True),
        )
        for action_id, duration in bad_inputs:
            with self.subTest(action_id=action_id, duration=duration):
                with self.assertRaises(STM32ProtocolError):
                    encode_pump(action_id, duration)  # type: ignore[arg-type]

    def test_parses_pong_and_status(self):
        self.assertEqual("PONG", parse_response(b"MCV1|PONG\r\n").kind)
        status = parse_response("MCV1|STATUS|ESTOP=1|PUMP=0\n")
        self.assertEqual("STATUS", status.kind)
        self.assertTrue(status.estop_active)
        self.assertFalse(status.pump_active)

    def test_parses_action_receipts(self):
        ack = parse_response("MCV1|ACK|A001\n")
        done = parse_response("MCV1|DONE|A001\n")
        error = parse_response("MCV1|ERR|A001|ESTOP\n")
        self.assertEqual(("ACK", "A001"), (ack.kind, ack.action_id))
        self.assertEqual(("DONE", "A001"), (done.kind, done.action_id))
        self.assertEqual(("ERR", "A001", "ESTOP"), (error.kind, error.action_id, error.error_code))

    def test_rejects_ambiguous_or_unknown_responses(self):
        bad_messages = (
            "",
            "MCV2|PONG\n",
            "MCV1|UNKNOWN\n",
            "MCV1|STATUS|ESTOP=0\n",
            "MCV1|STATUS|ESTOP=0|ESTOP=1\n",
            "MCV1|ACK|A001\nMCV1|DONE|A001\n",
            "MCV1|ERR|A001|bad-code\n",
        )
        for message in bad_messages:
            with self.subTest(message=message):
                with self.assertRaises(STM32ProtocolError):
                    parse_response(message)

    def test_rejects_non_ascii_and_oversized_responses(self):
        with self.assertRaises(STM32ProtocolError):
            parse_response("MCV1|ERR|A001|中文\n")
        with self.assertRaises(STM32ProtocolError):
            parse_response("X" * (MAX_LINE_BYTES + 1))


if __name__ == "__main__":
    unittest.main()
