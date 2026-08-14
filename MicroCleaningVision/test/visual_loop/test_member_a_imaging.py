"""成员 A 的文件：成像质量与 Observation 测试。"""

import unittest

from microcleaning.adapters.replay_camera import ReplayCamera, ReplayFrame
from microcleaning.perception.image_quality import ImageQuality, build_observation


class ImagingQualityTests(unittest.TestCase):
    def test_low_focus_becomes_machine_readable_flag(self):
        observation = build_observation(
            task_id="imaging-low-focus",
            frame_id="pre-001",
            raw_image_ref="replay://pre-001.png",
            quality=ImageQuality(focus=0.30, illumination=0.90, confidence=0.90),
        )
        self.assertEqual(("FOCUS_LOW",), observation.quality_flags)

    def test_invalid_quality_score_is_rejected(self):
        with self.assertRaises(ValueError):
            ImageQuality(focus=1.20, illumination=0.90, confidence=0.90).flags()

    def test_replay_camera_converts_registered_frame_to_observation(self):
        camera = ReplayCamera((ReplayFrame("pre", "pre-001", "replay://pre.png", ImageQuality(0.9, 0.9, 0.9)),))
        observation = camera.capture("replay-task", "pre")
        self.assertEqual("replay://pre.png", observation.raw_image_ref)
        self.assertEqual((), observation.quality_flags)


if __name__ == "__main__":
    unittest.main()
