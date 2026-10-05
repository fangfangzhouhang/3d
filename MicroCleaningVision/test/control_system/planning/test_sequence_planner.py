"""序列规划的合成测试。

这些断言只证明排序规则可重复。它们不证明真实清洗顺序已经最优。
"""

import ast
import math
import subprocess
import sys
import unittest
from itertools import permutations
from pathlib import Path

import microcleaning
from microcleaning.control_system.planning.sequence_planner import (
    STRATEGY_AREA_DESC,
    STRATEGY_NEAREST_NEIGHBOR,
    STRATEGY_WEIGHTED_SCORE,
    SequenceScoreBreakdown,
    SequenceScoreWeights,
    SequenceTarget,
    plan_sequence,
)


def _fixture() -> list[SequenceTarget]:
    return [
        SequenceTarget("near", (1.0, 0.0), 10.0),
        SequenceTarget("big", (20.0, 0.0), 40.0),
        SequenceTarget("mid", (20.0, 1.0), 15.0),
    ]


def _row(plan, target_id: str) -> SequenceScoreBreakdown:
    for row in plan.score_breakdown:
        if row.target_id == target_id:
            return row
    raise AssertionError(target_id)


class SequencePlannerTests(unittest.TestCase):
    def test_single_target_is_selected_for_every_strategy(self):
        target = SequenceTarget("only", (4.0, 7.0), 12.0, retry_count=1, difficulty=0.5, risk=0.25)
        for strategy in (STRATEGY_NEAREST_NEIGHBOR, STRATEGY_AREA_DESC, STRATEGY_WEIGHTED_SCORE):
            plan = plan_sequence([target], strategy=strategy, start_px=(1.0, 1.0))
            self.assertEqual("only", plan.selected_target_id)
            self.assertEqual(("only",), plan.ordered_target_ids)
            self.assertEqual(strategy, plan.strategy)
            self.assertEqual(("only",), tuple(row.target_id for row in plan.score_breakdown))
            self.assertIn("only", plan.reason)
            row = plan.score_breakdown[0]
            distance = math.hypot(3.0, 6.0)
            self.assertAlmostEqual(distance, row.distance_from_start_px)
            self.assertAlmostEqual(distance, row.stepwise_distance_px)
            self.assertAlmostEqual(
                1.0 * 12.0 - 1.0 * distance + 1.0 * 1 + 1.0 * 0.5 - 1.0 * 0.25,
                row.score,
            )

    def test_nearest_neighbor_walks_from_start_px(self):
        targets = _fixture()
        plan = plan_sequence(targets, strategy=STRATEGY_NEAREST_NEIGHBOR, start_px=(0.0, 0.0))
        self.assertEqual("near", plan.selected_target_id)
        self.assertEqual(("near", "big", "mid"), plan.ordered_target_ids)
        self.assertEqual("nearest_neighbor", plan.strategy)
        self.assertAlmostEqual(1.0, _row(plan, "near").stepwise_distance_px)
        self.assertAlmostEqual(19.0, _row(plan, "big").stepwise_distance_px)
        self.assertAlmostEqual(1.0, _row(plan, "mid").stepwise_distance_px)
        self.assertIn("near", plan.reason)
        self.assertIn("start_px", plan.reason)

        from_big = plan_sequence(targets, strategy=STRATEGY_NEAREST_NEIGHBOR, start_px=(20.0, 0.0))
        self.assertEqual(("big", "mid", "near"), from_big.ordered_target_ids)
        self.assertAlmostEqual(0.0, _row(from_big, "big").stepwise_distance_px)

    def test_area_desc_orders_largest_area_first(self):
        targets = _fixture()
        plan = plan_sequence(targets, strategy=STRATEGY_AREA_DESC, start_px=(0.0, 0.0))
        self.assertEqual("big", plan.selected_target_id)
        self.assertEqual(("big", "mid", "near"), plan.ordered_target_ids)
        self.assertEqual("area_desc", plan.strategy)
        self.assertIn("big", plan.reason)
        moved = plan_sequence(targets, strategy=STRATEGY_AREA_DESC, start_px=(20.0, 0.0))
        self.assertEqual(plan.ordered_target_ids, moved.ordered_target_ids)

    def test_weighted_score_uses_formula_and_start_distance(self):
        targets = _fixture()
        weights = SequenceScoreWeights()
        self.assertEqual((1.0, 1.0, 1.0, 1.0, 1.0), (
            weights.w_area,
            weights.w_distance,
            weights.w_retry,
            weights.w_difficulty,
            weights.w_risk,
        ))
        plan = plan_sequence(targets, strategy=STRATEGY_WEIGHTED_SCORE, start_px=(0.0, 0.0), weights=weights)
        self.assertEqual("big", plan.selected_target_id)
        self.assertEqual(("big", "near", "mid"), plan.ordered_target_ids)
        self.assertEqual("weighted_score", plan.strategy)
        self.assertIn("得分", plan.reason)
        self.assertIn("big", plan.reason)
        expected = {
            "near": 10.0 - 1.0,
            "big": 40.0 - 20.0,
            "mid": 15.0 - math.hypot(20.0, 1.0),
        }
        for row in plan.score_breakdown:
            self.assertAlmostEqual(expected[row.target_id], row.score)
            self.assertAlmostEqual(
                row.score,
                row.area_term - row.distance_term + row.retry_term + row.difficulty_term - row.risk_term,
            )
            self.assertAlmostEqual(row.area_term, row.area_px)
            self.assertAlmostEqual(row.distance_term, row.distance_from_start_px)

        distance_only = SequenceScoreWeights(
            w_area=0.0,
            w_distance=1.0,
            w_retry=0.0,
            w_difficulty=0.0,
            w_risk=0.0,
        )
        closer = plan_sequence(
            targets,
            strategy=STRATEGY_WEIGHTED_SCORE,
            start_px=(0.0, 0.0),
            weights=distance_only,
        )
        self.assertEqual(("near", "big", "mid"), closer.ordered_target_ids)

        area_only = SequenceScoreWeights(
            w_area=1.0,
            w_distance=0.0,
            w_retry=0.0,
            w_difficulty=0.0,
            w_risk=0.0,
        )
        by_area = plan_sequence(
            targets,
            strategy=STRATEGY_WEIGHTED_SCORE,
            weights=area_only,
        )
        self.assertEqual(("big", "mid", "near"), by_area.ordered_target_ids)

        from_big = plan_sequence(targets, strategy=STRATEGY_WEIGHTED_SCORE, start_px=(20.0, 0.0))
        self.assertEqual(("big", "mid", "near"), from_big.ordered_target_ids)
        self.assertAlmostEqual(40.0, _row(from_big, "big").score)

    def test_retry_count_can_change_the_first_target(self):
        plain = SequenceTarget("plain", (0.0, 0.0), 10.0, retry_count=0)
        small = SequenceTarget("retried", (0.0, 0.0), 6.0, retry_count=0)
        without = plan_sequence([small, plain], strategy=STRATEGY_WEIGHTED_SCORE)
        self.assertEqual("plain", without.selected_target_id)

        retried = SequenceTarget("retried", (0.0, 0.0), 6.0, retry_count=5)
        promoted = plan_sequence([plain, retried], strategy=STRATEGY_WEIGHTED_SCORE)
        self.assertEqual("retried", promoted.selected_target_id)
        self.assertEqual(("retried", "plain"), promoted.ordered_target_ids)
        self.assertAlmostEqual(11.0, _row(promoted, "retried").score)
        self.assertAlmostEqual(5.0, _row(promoted, "retried").retry_term)
        self.assertAlmostEqual(10.0, _row(promoted, "plain").score)
        self.assertIn("retried", promoted.reason)

    def test_risk_can_change_the_first_target(self):
        high = SequenceTarget("high", (0.0, 0.0), 12.0, risk=0.0)
        low = SequenceTarget("low", (0.0, 0.0), 8.0, risk=0.0)
        without = plan_sequence([low, high], strategy=STRATEGY_WEIGHTED_SCORE)
        self.assertEqual("high", without.selected_target_id)

        risky = SequenceTarget("high", (0.0, 0.0), 12.0, risk=5.0)
        demoted = plan_sequence([risky, low], strategy=STRATEGY_WEIGHTED_SCORE)
        self.assertEqual("low", demoted.selected_target_id)
        self.assertEqual(("low", "high"), demoted.ordered_target_ids)
        self.assertAlmostEqual(7.0, _row(demoted, "high").score)
        self.assertAlmostEqual(5.0, _row(demoted, "high").risk_term)
        self.assertAlmostEqual(8.0, _row(demoted, "low").score)
        self.assertIn("low", demoted.reason)

    def test_difficulty_can_change_the_first_target(self):
        plain = SequenceTarget("plain", (0.0, 0.0), 10.0, difficulty=0.0)
        harder = SequenceTarget("hard", (0.0, 0.0), 6.0, difficulty=5.0)
        without = plan_sequence(
            [SequenceTarget("plain", (0.0, 0.0), 10.0), SequenceTarget("hard", (0.0, 0.0), 6.0)],
            strategy=STRATEGY_WEIGHTED_SCORE,
        )
        self.assertEqual("plain", without.selected_target_id)
        promoted = plan_sequence([plain, harder], strategy=STRATEGY_WEIGHTED_SCORE)
        self.assertEqual("hard", promoted.selected_target_id)
        self.assertAlmostEqual(11.0, _row(promoted, "hard").score)
        self.assertAlmostEqual(5.0, _row(promoted, "hard").difficulty_term)

    def test_ties_use_target_id_independent_of_input_order(self):
        nearest_targets = [
            SequenceTarget("c", (0.0, 5.0), 1.0),
            SequenceTarget("a", (3.0, 4.0), 2.0),
            SequenceTarget("b", (5.0, 0.0), 3.0),
        ]
        area_targets = [
            SequenceTarget("c", (0.0, 0.0), 7.0),
            SequenceTarget("a", (9.0, 9.0), 7.0),
            SequenceTarget("b", (1.0, 0.0), 7.0),
        ]
        weighted_targets = [
            SequenceTarget("b", (0.0, 0.0), 10.0, retry_count=0),
            SequenceTarget("a", (3.0, 0.0), 10.0, retry_count=3),
        ]
        expected = {
            STRATEGY_NEAREST_NEIGHBOR: ("a", "c", "b"),
            STRATEGY_AREA_DESC: ("a", "b", "c"),
            STRATEGY_WEIGHTED_SCORE: ("a", "b"),
        }
        groups = {
            STRATEGY_NEAREST_NEIGHBOR: nearest_targets,
            STRATEGY_AREA_DESC: area_targets,
            STRATEGY_WEIGHTED_SCORE: weighted_targets,
        }
        for strategy, targets in groups.items():
            plans = [
                plan_sequence(list(order), strategy=strategy)
                for order in permutations(targets)
            ]
            self.assertEqual(expected[strategy], plans[0].ordered_target_ids)
            self.assertEqual(expected[strategy][0], plans[0].selected_target_id)
            self.assertIn("target_id", plans[0].reason)
            for plan in plans[1:]:
                self.assertEqual(plans[0], plan)

        weighted = plan_sequence(weighted_targets, strategy=STRATEGY_WEIGHTED_SCORE)
        nearest = plan_sequence(weighted_targets, strategy=STRATEGY_NEAREST_NEIGHBOR)
        self.assertEqual("a", weighted.selected_target_id)
        self.assertEqual("b", nearest.selected_target_id)
        self.assertAlmostEqual(_row(weighted, "a").score, _row(weighted, "b").score)

    def test_empty_list_returns_empty_order(self):
        for strategy in (STRATEGY_NEAREST_NEIGHBOR, STRATEGY_AREA_DESC, STRATEGY_WEIGHTED_SCORE):
            plan = plan_sequence([], strategy=strategy, start_px=(8.0, 9.0))
            self.assertIsNone(plan.selected_target_id)
            self.assertEqual((), plan.ordered_target_ids)
            self.assertEqual((), plan.score_breakdown)
            self.assertEqual(strategy, plan.strategy)
            self.assertEqual("没有可执行目标", plan.reason)
            again = plan_sequence([], strategy=strategy, start_px=(8.0, 9.0))
            self.assertEqual(plan, again)

    def test_repeated_calls_and_input_order_match(self):
        targets = _fixture()
        for strategy in (STRATEGY_NEAREST_NEIGHBOR, STRATEGY_AREA_DESC, STRATEGY_WEIGHTED_SCORE):
            first = plan_sequence(targets, strategy=strategy, start_px=(0.0, 0.0))
            for order in permutations(targets):
                for _ in range(2):
                    again = plan_sequence(list(order), strategy=strategy, start_px=(0.0, 0.0))
                    self.assertEqual(first, again)

    def test_unknown_strategy_and_duplicate_id_are_rejected(self):
        with self.assertRaises(ValueError):
            plan_sequence([], strategy="random")
        duplicated = [
            SequenceTarget("same", (0.0, 0.0), 1.0),
            SequenceTarget("same", (1.0, 0.0), 2.0),
        ]
        with self.assertRaises(ValueError):
            plan_sequence(duplicated)

    def test_module_does_not_depend_on_vision_target_instance(self):
        import microcleaning.control_system.planning.sequence_planner as planner

        source = Path(planner.__file__).read_text(encoding="utf-8")
        self.assertNotIn("TargetInstance", source)
        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
                imported.extend(alias.name for alias in node.names)
        for name in imported:
            self.assertNotIn("vision", name)
            self.assertNotEqual("TargetInstance", name)
            self.assertNotIn(name, {"random", "time", "datetime"})

        project_root = Path(microcleaning.__file__).resolve().parent.parent
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys\n"
                    "import microcleaning.control_system.planning.sequence_planner as planner\n"
                    "bad = [\n"
                    "    name for name in sys.modules\n"
                    "    if name == 'microcleaning.vision' or name.startswith('microcleaning.vision.')\n"
                    "]\n"
                    "raise SystemExit(1 if bad or hasattr(planner, 'TargetInstance') else 0)\n"
                ),
            ],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)


if __name__ == "__main__":
    unittest.main()
