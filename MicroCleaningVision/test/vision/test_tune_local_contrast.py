"""邻域差异自动调参：只在开发集搜参，留出集只做闸门。"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HAS_PERCEPTION_DEPS = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class LocalContrastPolicyStoreTests(unittest.TestCase):
    def test_json_roundtrip_ignores_unknown_keys(self):
        from microcleaning.vision.local_contrast_baseline import (
            LOCAL_CONTRAST_VERSION,
            LocalContrastPolicy,
            load_local_contrast_policy,
            save_local_contrast_policy,
            segment_contamination,
        )
        import numpy as np

        policy = LocalContrastPolicy(min_residual=12.0, max_aspect_ratio=4.0)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            save_local_contrast_policy(policy, path, extra={"unknown": 1, "feeds_action_request": True})
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(payload["feeds_action_request"])
            self.assertEqual(LOCAL_CONTRAST_VERSION, payload["base_version"])
            loaded = load_local_contrast_policy(path)
        self.assertEqual(12.0, loaded.min_residual)
        self.assertEqual(4.0, loaded.max_aspect_ratio)
        image = np.full((40, 40, 3), 180, dtype=np.uint8)
        result = segment_contamination(image, policy=loaded)
        self.assertTrue(result.measurement.algorithm_version.endswith("+auto"))

    def test_resolve_false_ignores_active_file(self):
        from microcleaning.vision.local_contrast_baseline import (
            LocalContrastPolicy,
            resolve_local_contrast_policy,
            save_local_contrast_policy,
        )

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "active.json"
            save_local_contrast_policy(LocalContrastPolicy(min_residual=16.0), path)
            self.assertIsNone(
                resolve_local_contrast_policy(use_tuned_policy=False, active_path=path)
            )
            loaded = resolve_local_contrast_policy(use_tuned_policy=None, active_path=path)
            self.assertEqual(16.0, loaded.min_residual)


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class TuneLocalContrastTests(unittest.TestCase):
    def _blob_pair(self, stem: str, split: str):
        import cv2
        import numpy as np

        from microcleaning.vision.tune_local_contrast import LabeledExample

        image = np.full((160, 200, 3), (90, 170, 210), dtype=np.uint8)
        cv2.circle(image, (100, 80), 16, (25, 25, 25), -1)
        mask = np.zeros((160, 200), dtype=np.uint8)
        cv2.circle(mask, (100, 80), 16, 255, -1)
        return LabeledExample(stem=stem, image=image, ground_truth_mask=mask, eval_split=split)

    def test_search_lowers_too_high_min_residual_and_skips_holdout(self):
        from microcleaning.vision.local_contrast_baseline import LocalContrastPolicy
        from microcleaning.vision.tune_local_contrast import tune_local_contrast_policy

        develop = self._blob_pair("public_001", "develop")
        holdout = self._blob_pair("public_002", "holdout")
        result = tune_local_contrast_policy(
            [develop, holdout],
            start_policy=LocalContrastPolicy(min_residual=80.0),
            search_space={"min_residual": (8.0, 80.0)},
            max_rounds=1,
        )
        self.assertEqual(8.0, result.best_policy.min_residual)
        self.assertGreater(result.develop_best.mean_iou, result.develop_baseline.mean_iou)
        history_stems = {stem for step in result.history for stem in step["stems"]}
        self.assertIn("public_001", history_stems)
        self.assertNotIn("public_002", history_stems)
        self.assertTrue(all(step["eval_split"] == "develop" for step in result.history))
        self.assertTrue(result.apply_allowed)

    def test_holdout_drop_refuses_apply(self):
        from microcleaning.vision.tune_local_contrast import PolicyScore, decide_apply

        def score(iou: float, disaster: float = 0.0) -> PolicyScore:
            return PolicyScore(iou, 0.8, 0.8, disaster, 10.0, 1, ())

        allowed, reason = decide_apply(
            develop_baseline=score(0.20),
            develop_best=score(0.55),
            holdout_baseline=score(0.50),
            holdout_best=score(0.30),
        )
        self.assertFalse(allowed)
        self.assertIn("留出集", reason)

    def test_no_develop_examples_are_rejected(self):
        from microcleaning.vision.tune_local_contrast import tune_local_contrast_policy

        holdout = self._blob_pair("M9", "holdout")
        with self.assertRaises(ValueError):
            tune_local_contrast_policy([holdout])


if __name__ == "__main__":
    unittest.main()
