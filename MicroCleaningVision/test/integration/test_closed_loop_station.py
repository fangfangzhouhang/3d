"""实际新 CLI 的软件验收；只有帧来源与物理串口是替身。"""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from demo.closed_loop_station import main


class ClosedLoopCLITests(unittest.TestCase):
    def test_terminal_confirmation_requires_uppercase_yes(self):
        from unittest.mock import patch
        from demo.closed_loop_station import _yes
        for answer, expected in (("yes", False), ("Y", False), ("YES", True)):
            with self.subTest(answer=answer), patch("builtins.input", return_value=answer):
                self.assertEqual(expected, _yes("confirm"))
    def run_cli(self, *arguments):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        with redirect_stdout(StringIO()):
            code = main(["--mock", "--output-root", temporary.name, "--no-tuned-policy", *arguments])
        summary_path = next(Path(temporary.name).rglob("summary.json"))
        return code, json.loads(summary_path.read_text(encoding="utf-8")), summary_path.parent

    def test_one_command_default_local_algorithm_runs_complete_loop(self):
        code, summary, folder = self.run_cli()
        self.assertEqual(0, code)
        self.assertEqual("SUCCESS", summary["status"])
        self.assertEqual(3, len(summary["cycles"]))
        phases = [event["phase"] for event in summary["events"]]
        for phase in ("CAPTURE_PRE", "SELECT_TARGET", "PLAN_TARGET", "AUTHORIZE", "MOVE_THEN_PUMP", "RETURN", "CAPTURE_POST", "VERIFY_TARGET"):
            self.assertIn(phase, phases)
        serial = json.loads((folder / "serial.json").read_text(encoding="utf-8"))
        tx = [event["line"] for event in serial["events"] if event["direction"] == "tx"]
        self.assertEqual(3, sum(line.startswith("MCV1|PUMP|") for line in tx))
        self.assertFalse(any("TO_NEEDLE" in line or "TO_SCOPE" in line for line in tx))
        self.assertTrue(all(cycle["segmentation_sha256"] == summary["segmentation_sha256"] for cycle in summary["cycles"]))

    def test_cli_reports_failure_and_human_routes(self):
        for scenario, expected in (("motion-short", 3), ("return-timeout", 3), ("decline", 2), ("noncomparable", 2)):
            with self.subTest(scenario=scenario):
                code, summary, _ = self.run_cli("--mock-scenario", scenario)
                self.assertEqual(expected, code)
                self.assertNotEqual("SUCCESS", summary["status"])

    def test_cli_cleanup_failure_keeps_full_run_summary_and_returns_error(self):
        from unittest.mock import patch
        from demo.closed_loop_fixture import MockFrames
        original = MockFrames.close
        def failing_close(frames):
            original(frames)
            raise OSError("camera cleanup fault")
        with patch.object(MockFrames, "close", failing_close):
            code, summary, folder = self.run_cli()
        self.assertEqual(3, code)
        self.assertEqual("ERROR", summary["status"])
        self.assertEqual(3, len(summary["cycles"]))
        self.assertIn("FRAME_SOURCE_CLOSE_FAILED", summary["reasons"])
        self.assertIn("FRAME_SOURCE_FINAL_CLOSE_FAILED", summary["reasons"])
        self.assertTrue((folder / "serial.json").exists())
        self.assertEqual(3, len(list(folder.rglob("episode_*.json"))))

    def test_real_missing_explicit_inputs_cannot_fall_back_to_mock(self):
        with self.assertRaises(SystemExit) as caught:
            main(["--real"])
        self.assertEqual(2, caught.exception.code)

    def test_mock_cannot_change_shared_real_position_ledger(self):
        with self.assertRaises(SystemExit) as caught:
            main(["--mock", "--position-path", "output/stage2/position.json", "--serial-port", "COM1"])
        self.assertEqual(2, caught.exception.code)
