"""成员 C 的文件：安全治理、FakeSerial 和软件回放集成测试。"""

import unittest
import tempfile
from pathlib import Path

from microcleaning.app.replay_mcl import ReplayMCLRunner
from microcleaning.contracts import SafetyOutcome
from microcleaning.data.episode_store import write_episode
from microcleaning.execution.fake_serial import FakeSerialController
from microcleaning.perception.contamination import ContaminationMeasurement
from microcleaning.perception.image_quality import ImageQuality, build_observation
from microcleaning.state.estimator import estimate_state


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


if __name__ == "__main__":
    unittest.main()
