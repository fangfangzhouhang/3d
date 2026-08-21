"""成员 C：目标控制模块当前的软件回放与 FakeSerial 测试。"""

import importlib.util
import unittest
import tempfile
from pathlib import Path

from microcleaning.control_system.episode_store import write_episode
from microcleaning.control_system.fake_serial import FakeSerialController
from microcleaning.control_system.replay_mcl import ReplayMCLRunner
from microcleaning.contracts import SafetyOutcome
from microcleaning.data_learning.image_quality import ImageQuality, build_observation
from microcleaning.vision.contamination import ContaminationMeasurement
from microcleaning.vision.state_estimator import estimate_state


HAS_PERCEPTION_DEPS = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


class ReplayWorkflowTests(unittest.TestCase):
    def setUp(self):
        quality = ImageQuality(0.95, 0.95, 0.95)
        self.pre = build_observation(task_id="replay", frame_id="pre", raw_image_ref="replay://pre.png", quality=quality)
        self.post = build_observation(task_id="replay", frame_id="post", raw_image_ref="replay://post.png", quality=quality)

    def test_unready_controller_is_denied_before_fake_execution(self):
        state = estimate_state(
            self.pre,
            ContaminationMeasurement(100.0, (10.0, 20.0), 0.2, 0.95),
            calibration_version="replay-cal-v0",
            calibration_valid=True,
            target_centroid_mm=(10.0, 20.0),
            uncertainty_mm=0.2,
            device_state={"controller_connected": False, "interlock_ok": True},
        )
        episode = ReplayMCLRunner().run(pre=self.pre, state=state, post=self.post, post_area_px=10.0)
        self.assertEqual(SafetyOutcome.DENY, episode.safety_decision.outcome)
        self.assertIsNone(episode.execution_receipt)

    def test_approved_replay_produces_fake_receipt_and_post_verification(self):
        state = estimate_state(
            self.pre,
            ContaminationMeasurement(100.0, (10.0, 20.0), 0.2, 0.95),
            calibration_version="replay-cal-v0",
            calibration_valid=True,
            target_centroid_mm=(10.0, 20.0),
            uncertainty_mm=0.2,
            device_state={"controller_connected": True, "interlock_ok": True},
        )
        episode = ReplayMCLRunner().run(pre=self.pre, state=state, post=self.post, post_area_px=10.0)
        self.assertEqual("software_replay", episode.mode)
        self.assertEqual("fake_serial", episode.execution_receipt.mode)
        self.assertTrue(episode.execution_receipt.success)
        self.assertIsNotNone(episode.verification)

    def test_fake_serial_timeout_creates_failure_receipt(self):
        state = estimate_state(
            self.pre,
            ContaminationMeasurement(100.0, (10.0, 20.0), 0.2, 0.95),
            calibration_version="replay-cal-v0",
            calibration_valid=True,
            target_centroid_mm=(10.0, 20.0),
            uncertainty_mm=0.2,
            device_state={"controller_connected": True, "interlock_ok": True},
        )
        episode = ReplayMCLRunner(FakeSerialController(simulate_timeout=True)).run(
            pre=self.pre, state=state, post=self.post, post_area_px=10.0
        )
        self.assertFalse(episode.execution_receipt.success)
        self.assertEqual("ACK_TIMEOUT", episode.execution_receipt.error_code)

    def test_episode_store_refuses_overwrite(self):
        state = estimate_state(
            self.pre,
            ContaminationMeasurement(100.0, (10.0, 20.0), 0.2, 0.95),
            calibration_version="replay-cal-v0",
            calibration_valid=True,
            target_centroid_mm=(10.0, 20.0),
            uncertainty_mm=0.2,
            device_state={"controller_connected": True, "interlock_ok": True},
        )
        episode = ReplayMCLRunner().run(pre=self.pre, state=state, post=self.post, post_area_px=10.0)
        with tempfile.TemporaryDirectory() as folder:
            output = write_episode(episode, folder)
            self.assertTrue(output.exists())
            self.assertTrue(Path(folder, f"{episode.episode_id}.sha256").exists())
            with self.assertRaises(FileExistsError):
                write_episode(episode, folder)

    def test_state_from_another_observation_is_rejected(self):
        state = estimate_state(self.pre, ContaminationMeasurement(100.0, (10.0, 20.0), 0.2, 0.95))
        with self.assertRaises(ValueError):
            ReplayMCLRunner().run(pre=self.post, state=state, post=self.post, post_area_px=10.0)

    def test_bad_observation_never_becomes_false_no_target_success(self):
        bad_pre = build_observation(
            task_id="bad-image",
            frame_id="pre",
            raw_image_ref="replay://bad.png",
            quality=ImageQuality(0.20, 0.95, 0.95),
        )
        state = estimate_state(bad_pre, ContaminationMeasurement(0.0, None, 1.0, 0.20))
        episode = ReplayMCLRunner().run(pre=bad_pre, state=state, post=None, post_area_px=None)
        self.assertEqual("HUMAN", episode.verification.next_route.value)
        self.assertIn("OBSERVATION_LOW_QUALITY", episode.verification.reason_codes)


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class CleaningPlanTests(unittest.TestCase):
    def test_small_target_uses_one_center_point(self):
        import cv2
        import numpy as np

        from microcleaning.control_system.cleaning_plan import CleaningStrategy, plan_cleaning

        mask = np.zeros((100, 100), dtype=np.uint8)
        cv2.circle(mask, (40, 60), 5, 255, -1)
        plan = plan_cleaning(mask)
        self.assertEqual(CleaningStrategy.CENTER_POINT, plan.strategy)
        self.assertEqual(1, len(plan.path_px))
        self.assertAlmostEqual(40.0, plan.path_px[0][0], delta=1.0)

    def test_large_target_uses_in_mask_raster_points(self):
        import numpy as np

        from microcleaning.control_system.cleaning_plan import CleaningStrategy, plan_cleaning

        mask = np.zeros((120, 160), dtype=np.uint8)
        mask[20:100, 30:140] = 255
        plan = plan_cleaning(mask)
        self.assertEqual(CleaningStrategy.RASTER_SCAN, plan.strategy)
        self.assertGreater(len(plan.path_px), 5)
        for x, y in plan.path_px:
            self.assertGreater(mask[round(y), round(x)], 0)

    def test_empty_mask_creates_no_path(self):
        import numpy as np

        from microcleaning.control_system.cleaning_plan import CleaningStrategy, plan_cleaning

        plan = plan_cleaning(np.zeros((40, 50), dtype=np.uint8))
        self.assertEqual(CleaningStrategy.NO_TARGET, plan.strategy)
        self.assertEqual((), plan.path_px)

    def test_disconnected_targets_have_separate_spray_segments(self):
        import numpy as np

        from microcleaning.control_system.cleaning_plan import plan_cleaning

        mask = np.zeros((120, 180), dtype=np.uint8)
        mask[20:90, 20:80] = 255
        mask[40:110, 125:170] = 255
        plan = plan_cleaning(mask)
        self.assertEqual(2, len(plan.segment_start_indices))
        second_start = plan.segment_start_indices[1]
        self.assertGreater(second_start, 0)


if __name__ == "__main__":
    unittest.main()
