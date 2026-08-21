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


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class ImageAlgorithmTests(unittest.TestCase):
    """直接测试 measure_image_quality 对合成 numpy 像素的边界行为。

    现有测试通过 ReplayCamera 间接调用算法；这里直接喂合成像素，
    验证算法对极端输入的行为正确，不依赖任何真实照片。
    """

    def test_all_black_image_flags_all_quality_problems(self):
        """全黑图：拉普拉斯方差为零、暗像素占满、illumination 崩溃。"""
        import numpy as np

        from microcleaning.data_learning.image_quality import measure_image_quality

        image = np.zeros((64, 64), dtype=np.uint8)
        metrics, quality = measure_image_quality(image)
        self.assertAlmostEqual(0.0, metrics.laplacian_variance)
        self.assertAlmostEqual(0.0, metrics.mean_intensity)
        self.assertAlmostEqual(1.0, metrics.dark_fraction, places=2)
        self.assertAlmostEqual(0.0, metrics.bright_fraction, places=2)
        self.assertEqual(1, metrics.channels)
        self.assertLess(quality.focus, 0.70)
        self.assertLess(quality.illumination, 0.70)
        self.assertIn("FOCUS_LOW", quality.flags())
        self.assertIn("ILLUMINATION_LOW", quality.flags())

    def test_all_white_image_flags_illumination_problem(self):
        """全白图：亮像素占满、illumination 崩溃、focus 同样为零。"""
        import numpy as np

        from microcleaning.data_learning.image_quality import measure_image_quality

        image = np.full((64, 64), 255, dtype=np.uint8)
        metrics, quality = measure_image_quality(image)
        self.assertAlmostEqual(0.0, metrics.laplacian_variance)
        self.assertAlmostEqual(255.0, metrics.mean_intensity)
        self.assertAlmostEqual(0.0, metrics.dark_fraction, places=2)
        self.assertAlmostEqual(1.0, metrics.bright_fraction, places=2)
        self.assertLess(quality.focus, 0.70)
        self.assertLess(quality.illumination, 0.70)
        self.assertIn("FOCUS_LOW", quality.flags())
        self.assertIn("ILLUMINATION_LOW", quality.flags())

    def test_sharp_noise_has_higher_focus_than_blurred(self):
        """随机噪声比高斯模糊后的 laplacian_variance 更高。"""
        import cv2
        import numpy as np

        from microcleaning.data_learning.image_quality import measure_image_quality

        np.random.seed(42)
        sharp = np.random.randint(0, 256, (64, 64), dtype=np.uint8)
        blurred = cv2.GaussianBlur(sharp, (15, 15), 0)

        sharp_metrics, sharp_quality = measure_image_quality(sharp)
        blurred_metrics, blurred_quality = measure_image_quality(blurred)
        self.assertGreater(sharp_metrics.laplacian_variance, blurred_metrics.laplacian_variance)
        self.assertGreaterEqual(sharp_quality.focus, blurred_quality.focus)

    def test_well_exposed_midgray_passes_illumination_but_not_focus(self):
        """均匀灰 128：illumination 通过，但纯色无细节导致 focus 低。"""
        import numpy as np

        from microcleaning.data_learning.image_quality import measure_image_quality

        image = np.full((64, 64), 128, dtype=np.uint8)
        metrics, quality = measure_image_quality(image)
        self.assertAlmostEqual(128.0, metrics.mean_intensity)
        self.assertAlmostEqual(0.0, metrics.dark_fraction, places=2)
        self.assertAlmostEqual(0.0, metrics.bright_fraction, places=2)
        self.assertGreaterEqual(quality.illumination, 0.70)
        self.assertNotIn("ILLUMINATION_LOW", quality.flags())
        self.assertIn("FOCUS_LOW", quality.flags())

    def test_grayscale_image_reports_one_channel(self):
        """灰度图 channels 应为 1。"""
        import numpy as np

        from microcleaning.data_learning.image_quality import measure_image_quality

        image = np.full((32, 32), 128, dtype=np.uint8)
        metrics, _ = measure_image_quality(image)
        self.assertEqual(1, metrics.channels)
        self.assertEqual(32, metrics.width_px)
        self.assertEqual(32, metrics.height_px)

    def test_bgr_image_reports_three_channels(self):
        """BGR 三通道图 channels 应为 3。"""
        import numpy as np

        from microcleaning.data_learning.image_quality import measure_image_quality

        image = np.full((32, 32, 3), 128, dtype=np.uint8)
        metrics, _ = measure_image_quality(image)
        self.assertEqual(3, metrics.channels)

    def test_bgra_image_reports_four_channels(self):
        """BGRA 四通道图 channels 应为 4。"""
        import numpy as np

        from microcleaning.data_learning.image_quality import measure_image_quality

        image = np.full((32, 32, 4), 128, dtype=np.uint8)
        metrics, _ = measure_image_quality(image)
        self.assertEqual(4, metrics.channels)

    def test_non_array_input_is_rejected(self):
        """非 numpy 数组必须被拒绝。"""
        from microcleaning.data_learning.image_quality import measure_image_quality

        with self.assertRaises(ValueError):
            measure_image_quality([1, 2, 3])
        with self.assertRaises(ValueError):
            measure_image_quality("not an image")

    def test_empty_array_is_rejected(self):
        """空数组必须被拒绝。"""
        import numpy as np

        from microcleaning.data_learning.image_quality import measure_image_quality

        with self.assertRaises(ValueError):
            measure_image_quality(np.array([], dtype=np.uint8))

    def test_wrong_ndim_is_rejected(self):
        """1D 和 2 通道数组必须被拒绝。"""
        import numpy as np

        from microcleaning.data_learning.image_quality import measure_image_quality

        with self.assertRaises(ValueError):
            measure_image_quality(np.zeros((32,), dtype=np.uint8))
        with self.assertRaises(ValueError):
            measure_image_quality(np.zeros((32, 32, 2), dtype=np.uint8))

    def test_non_uint8_dtype_is_rejected(self):
        """float32 和 uint16 必须被拒绝。"""
        import numpy as np

        from microcleaning.data_learning.image_quality import measure_image_quality

        with self.assertRaises(ValueError):
            measure_image_quality(np.full((32, 32), 128.0, dtype=np.float32))
        with self.assertRaises(ValueError):
            measure_image_quality(np.full((32, 32), 128, dtype=np.uint16))

    def test_custom_policy_adjusts_focus_threshold(self):
        """focus = clamp01(laplacian_variance / focus_reference)，改 policy 改 focus。"""
        import numpy as np

        from microcleaning.data_learning.image_quality import (
            ImageQualityPolicy,
            measure_image_quality,
        )

        image = np.zeros((64, 64), dtype=np.uint8)
        image[:, 32:] = 255
        metrics, _ = measure_image_quality(image)
        self.assertGreater(metrics.laplacian_variance, 0.0)
        # 动态构造 focus_reference = 2 倍实测值，确保 focus 不被 clamp 到 1.0
        ref = metrics.laplacian_variance * 2
        _, quality = measure_image_quality(image, policy=ImageQualityPolicy(focus_reference=ref))
        self.assertAlmostEqual(0.5, quality.focus, places=4)


if __name__ == "__main__":
    unittest.main()
