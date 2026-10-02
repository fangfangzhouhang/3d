"""位置账本：缺失/损坏/失败后都按未知处理；只累加固件确认走完的行。"""

import tempfile
import unittest
from pathlib import Path

from microcleaning.control_system.planning.stage2_position import (
    load_position,
    mark_unknown,
    record_completed,
    set_zero,
)


class Stage2PositionTests(unittest.TestCase):
    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.path = Path(self._folder.name) / "stage2" / "position.json"

    def tearDown(self):
        self._folder.cleanup()

    def test_missing_ledger_is_unknown(self):
        position = load_position(self.path)
        self.assertFalse(position.known)
        self.assertIsNone(position.xy())

    def test_zero_then_accumulate_signed_steps(self):
        set_zero(self.path, run_id="zero")
        record_completed(self.path, ("MOVEXY 640 FWD 320 REV", "MOVEXY 100 REV 0 FWD"), run_id="run1")
        self.assertEqual((540, -320), load_position(self.path).xy())

    def test_failure_marks_unknown_and_unknown_never_accumulates(self):
        set_zero(self.path)
        mark_unknown(self.path, run_id="run2", reason="TIMEOUT")
        self.assertIsNone(load_position(self.path).xy())
        record_completed(self.path, ("MOVEXY 10 FWD 0 FWD",), run_id="run3")
        self.assertIsNone(load_position(self.path).xy())

    def test_corrupt_ledger_is_unknown(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        self.assertFalse(load_position(self.path).known)
        self.path.write_text('{"format": "stage2-position-v0", "known": true, "x_steps": "1"}', encoding="utf-8")
        self.assertFalse(load_position(self.path).known)


if __name__ == "__main__":
    unittest.main()
