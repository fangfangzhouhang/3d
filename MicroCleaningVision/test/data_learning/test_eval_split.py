"""开发图与留出图冻结名单。"""

import unittest

from microcleaning.data_learning.eval_split import HOLD_OUT_STEMS, eval_split_for


class EvalSplitTests(unittest.TestCase):
    def test_frozen_holdout_stems_are_not_used_for_tuning(self):
        self.assertEqual(
            {"public_002", "public_011", "M9", "M12"},
            set(HOLD_OUT_STEMS),
        )
        self.assertEqual("holdout", eval_split_for("public_002"))
        self.assertEqual("holdout", eval_split_for("M9"))
        self.assertEqual("develop", eval_split_for("public_001"))
        self.assertEqual("develop", eval_split_for("public_008"))


if __name__ == "__main__":
    unittest.main()
