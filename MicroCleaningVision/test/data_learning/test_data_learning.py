"""成员 A：数据与模型模块当前的真实像素输入测试。"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

from microcleaning.data_learning.image_quality import ImageQuality, build_observation
from microcleaning.data_learning.inspect_images import inspect_directory
from microcleaning.data_learning.replay_camera import ReplayCamera, ReplayFrame


HAS_PERCEPTION_DEPS = (
    importlib.util.find_spec("cv2") is not None
    and importlib.util.find_spec("numpy") is not None
)


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

    @unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
    def test_replay_camera_decodes_real_pixels_and_keeps_hash(self):
        import cv2
        import numpy as np

        with tempfile.TemporaryDirectory() as folder:
            image_path = Path(folder) / "phone-smoke.png"
            image = np.full((64, 64, 3), 32, dtype=np.uint8)
            image[::2, ::2] = 220
            image[1::2, 1::2] = 220
            self.assertTrue(cv2.imwrite(str(image_path), image))

            camera = ReplayCamera(
                (ReplayFrame("pre", "phone-001", image_path.name),),
                base_dir=folder,
            )
            observation = camera.capture("phone-smoke", "pre")
            inspection = camera.inspection("pre")

            self.assertEqual(image_path.name, observation.raw_image_ref)
            self.assertEqual(64, inspection.metrics.width_px)
            self.assertEqual(64, inspection.metrics.height_px)
            self.assertEqual(64, len(inspection.sha256))
            self.assertEqual("quality-gate1-v0", inspection.algorithm_version)
            self.assertGreater(inspection.metrics.laplacian_variance, 0)
            report = inspect_directory(Path(folder))
            self.assertEqual("OK", report[0]["status"])
            self.assertEqual(inspection.sha256, report[0]["sha256"])
            self.assertEqual("quality-gate1-v0", report[0]["algorithm_version"])
            self.assertEqual(100.0, report[0]["policy"]["focus_reference"])

    @unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
    def test_real_pixel_mode_rejects_missing_and_undecodable_files(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = ReplayCamera((ReplayFrame("pre", "missing", "missing.png"),), base_dir=folder)
            with self.assertRaises(FileNotFoundError):
                missing.capture("phone-smoke", "pre")

            broken_path = Path(folder) / "broken.png"
            broken_path.write_text("这不是图片", encoding="utf-8")
            broken = ReplayCamera((ReplayFrame("pre", "broken", broken_path.name),), base_dir=folder)
            with self.assertRaises(ValueError):
                broken.capture("phone-smoke", "pre")


if __name__ == "__main__":
    unittest.main()
