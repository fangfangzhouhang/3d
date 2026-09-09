"""成员 B：视觉识别、状态测量和最小复检测试。"""

import importlib.util
import unittest

from microcleaning.contracts import ExecutionReceipt, NextRoute
from microcleaning.data_learning.image_quality import ImageQuality, build_observation
from microcleaning.vision.contamination import ContaminationMeasurement
from microcleaning.vision.state_estimator import estimate_state
from microcleaning.vision.verification import verify_area_change


HAS_PERCEPTION_DEPS = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


class MeasurementAndVerificationTests(unittest.TestCase):
    def setUp(self):
        quality = ImageQuality(0.95, 0.95, 0.95)
        self.pre = build_observation(task_id="measure", frame_id="pre", raw_image_ref="replay://pre.png", quality=quality)
        self.post = build_observation(task_id="measure", frame_id="post", raw_image_ref="replay://post.png", quality=quality)

    def test_measurement_becomes_state_without_hardware_authority(self):
        state = estimate_state(self.pre, ContaminationMeasurement(100.0, (10.0, 20.0), 0.2, 0.95))
        self.assertEqual(100.0, state.target_area_px)
        self.assertEqual((10.0, 20.0), state.target_centroid_px)
        self.assertIsNone(state.target_centroid_mm)
        self.assertEqual("image_px", state.coordinate_frame)
        self.assertIsNone(state.uncertainty_mm)
        self.assertEqual(0.2, state.uncertainty_px)
        self.assertFalse(state.calibration_valid)
        self.assertFalse(state.device_state["controller_connected"])

    def test_area_reduction_is_verification_not_execution_proof(self):
        receipt = ExecutionReceipt("a1", "fake_serial", "t0", "t1", (10.0, 20.0), 200, 0.3, "ACK_RECEIVED", "SIMULATED", True)
        result = verify_area_change(
            task_id="measure",
            pre=self.pre,
            post=self.post,
            pre_area_px=100.0,
            post_area_px=10.0,
            receipt=receipt,
        )
        self.assertEqual(NextRoute.STOP, result.next_route)
        self.assertAlmostEqual(0.90, result.removal_rate)

    def test_millimetre_target_requires_valid_calibration(self):
        measurement = ContaminationMeasurement(100.0, (10.0, 20.0), 0.2, 0.95)
        with self.assertRaises(ValueError):
            estimate_state(self.pre, measurement, target_centroid_mm=(1.0, 2.0))

    def test_calibrated_state_requires_millimetre_uncertainty(self):
        measurement = ContaminationMeasurement(100.0, (10.0, 20.0), 0.2, 0.95)
        with self.assertRaises(ValueError):
            estimate_state(
                self.pre,
                measurement,
                calibration_version="cal-v0",
                calibration_valid=True,
                target_centroid_mm=(1.0, 2.0),
            )


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class HSVBaselineTests(unittest.TestCase):
    def test_red_regions_become_mask_area_and_centroid(self):
        import cv2
        import numpy as np

        from microcleaning.vision.hsv_baseline import segment_contamination

        image = np.full((120, 160, 3), 210, dtype=np.uint8)
        cv2.circle(image, (80, 60), 20, (0, 0, 230), -1)
        result = segment_contamination(image)
        self.assertGreater(result.measurement.area_px, 1000)
        self.assertAlmostEqual(80.0, result.measurement.centroid_px[0], delta=1.0)
        self.assertAlmostEqual(60.0, result.measurement.centroid_px[1], delta=1.0)
        self.assertEqual(1, result.measurement.component_count)

    def test_small_red_noise_is_removed(self):
        import cv2
        import numpy as np

        from microcleaning.vision.hsv_baseline import segment_contamination

        image = np.full((80, 80, 3), 210, dtype=np.uint8)
        cv2.circle(image, (10, 10), 1, (0, 0, 255), -1)
        result = segment_contamination(image)
        self.assertEqual(0.0, result.measurement.area_px)
        self.assertIsNone(result.measurement.centroid_px)

    def test_optional_aspect_filter_drops_thin_lines_without_changing_default(self):
        import cv2
        import numpy as np

        from microcleaning.vision.hsv_baseline import (
            HSV_BASELINE_VERSION,
            HSVSegmentationPolicy,
            segment_contamination,
        )

        image = np.full((80, 160, 3), 210, dtype=np.uint8)
        image[8:12, 8:150] = (0, 0, 230)
        cv2.circle(image, (40, 50), 12, (0, 0, 230), -1)
        default = segment_contamination(image)
        filtered = segment_contamination(image, policy=HSVSegmentationPolicy(max_aspect_ratio=4.0))
        self.assertGreaterEqual(default.measurement.component_count, 2)
        self.assertEqual(HSV_BASELINE_VERSION, default.measurement.algorithm_version)
        self.assertEqual(1, filtered.measurement.component_count)
        self.assertEqual("hsv-red-baseline-v0.2-aspect", filtered.measurement.algorithm_version)


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class OtsuBaselineTests(unittest.TestCase):
    def test_dark_region_becomes_mask_area_and_centroid(self):
        import cv2
        import numpy as np

        from microcleaning.vision.otsu_baseline import OTSU_BASELINE_VERSION, segment_contamination

        image = np.full((160, 200, 3), 220, dtype=np.uint8)
        cv2.circle(image, (100, 80), 12, (20, 20, 20), -1)
        result = segment_contamination(image)
        self.assertEqual(image.shape[:2], result.mask.shape)
        self.assertGreater(result.measurement.area_px, 250)
        self.assertAlmostEqual(100.0, result.measurement.centroid_px[0], delta=2.0)
        self.assertAlmostEqual(80.0, result.measurement.centroid_px[1], delta=2.0)
        self.assertEqual(1, result.measurement.component_count)
        self.assertEqual(OTSU_BASELINE_VERSION, result.measurement.algorithm_version)

    def test_mask_shape_follows_non_square_image(self):
        import numpy as np

        from microcleaning.vision.otsu_baseline import segment_contamination

        image = np.full((90, 200, 3), 230, dtype=np.uint8)
        image[35:50, 90:110] = 15
        result = segment_contamination(image)
        self.assertEqual((90, 200), result.mask.shape)
        self.assertGreater(result.measurement.area_px, 0)

    def test_small_dark_noise_is_removed(self):
        import cv2
        import numpy as np

        from microcleaning.vision.otsu_baseline import segment_contamination

        image = np.full((80, 80, 3), 220, dtype=np.uint8)
        cv2.circle(image, (40, 40), 1, (0, 0, 0), -1)
        result = segment_contamination(image)
        self.assertEqual(0.0, result.measurement.area_px)
        self.assertIsNone(result.measurement.centroid_px)

    def test_invalid_policy_is_rejected(self):
        import numpy as np

        from microcleaning.vision.otsu_baseline import OtsuSegmentationPolicy, segment_contamination

        image = np.full((40, 40, 3), 200, dtype=np.uint8)
        with self.assertRaises(ValueError):
            segment_contamination(image, policy=OtsuSegmentationPolicy(blur_kernel_px=4))


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class ColorIndexBaselineTests(unittest.TestCase):
    def test_excess_green_keeps_green_and_ignores_red(self):
        import cv2
        import numpy as np

        from microcleaning.vision.exg_baseline import EXG_BASELINE_VERSION, segment_contamination

        green = np.full((120, 160, 3), 210, dtype=np.uint8)
        cv2.circle(green, (80, 60), 18, (0, 220, 0), -1)
        red = np.full((120, 160, 3), 210, dtype=np.uint8)
        cv2.circle(red, (80, 60), 18, (0, 0, 230), -1)
        green_result = segment_contamination(green)
        red_result = segment_contamination(red)
        self.assertGreater(green_result.measurement.area_px, 500)
        self.assertEqual(EXG_BASELINE_VERSION, green_result.measurement.algorithm_version)
        self.assertEqual(0.0, red_result.measurement.area_px)

    def test_excess_red_keeps_red_marker(self):
        import cv2
        import numpy as np

        from microcleaning.vision.exg_baseline import EXR_BASELINE_VERSION, segment_excess_red

        image = np.full((120, 160, 3), 210, dtype=np.uint8)
        cv2.circle(image, (80, 60), 18, (0, 0, 230), -1)
        result = segment_excess_red(image)
        self.assertGreater(result.measurement.area_px, 500)
        self.assertEqual(EXR_BASELINE_VERSION, result.measurement.algorithm_version)
        self.assertAlmostEqual(80.0, result.measurement.centroid_px[0], delta=3.0)


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class LocalContrastBaselineTests(unittest.TestCase):
    def test_dark_blob_on_tan_is_marked(self):
        import cv2
        import numpy as np

        from microcleaning.vision.local_contrast_baseline import (
            LOCAL_CONTRAST_VERSION,
            segment_contamination,
        )

        image = np.full((160, 200, 3), (90, 170, 210), dtype=np.uint8)
        cv2.circle(image, (100, 80), 16, (25, 25, 25), -1)
        result = segment_contamination(image)
        self.assertEqual(LOCAL_CONTRAST_VERSION, result.measurement.algorithm_version)
        self.assertGreater(result.measurement.area_px, 400)
        self.assertAlmostEqual(100.0, result.measurement.centroid_px[0], delta=4.0)
        self.assertAlmostEqual(80.0, result.measurement.centroid_px[1], delta=4.0)
        self.assertEqual(1, result.measurement.component_count)

    def test_light_blob_on_dark_is_marked(self):
        import cv2
        import numpy as np

        from microcleaning.vision.local_contrast_baseline import segment_contamination

        image = np.full((160, 200, 3), (25, 25, 25), dtype=np.uint8)
        cv2.circle(image, (90, 70), 16, (210, 190, 160), -1)
        result = segment_contamination(image)
        self.assertGreater(result.measurement.area_px, 400)
        self.assertAlmostEqual(90.0, result.measurement.centroid_px[0], delta=4.0)
        self.assertAlmostEqual(70.0, result.measurement.centroid_px[1], delta=4.0)

    def test_red_blob_is_still_found_without_hue_range(self):
        import cv2
        import numpy as np

        from microcleaning.vision.local_contrast_baseline import segment_contamination

        image = np.full((120, 160, 3), 210, dtype=np.uint8)
        cv2.circle(image, (80, 60), 18, (0, 0, 230), -1)
        result = segment_contamination(image)
        self.assertGreater(result.measurement.area_px, 500)

    def test_thin_line_is_dropped_while_blob_is_kept(self):
        import cv2
        import numpy as np

        from microcleaning.vision.local_contrast_baseline import segment_contamination

        image = np.full((80, 160, 3), 210, dtype=np.uint8)
        image[8:12, 8:150] = (0, 0, 230)
        cv2.circle(image, (40, 50), 12, (0, 0, 230), -1)
        result = segment_contamination(image)
        self.assertEqual(1, result.measurement.component_count)
        self.assertGreater(result.measurement.area_px, 200)
        self.assertAlmostEqual(40.0, result.measurement.centroid_px[0], delta=8.0)
        self.assertAlmostEqual(50.0, result.measurement.centroid_px[1], delta=8.0)

    def test_uniform_image_has_empty_mask(self):
        import numpy as np

        from microcleaning.vision.local_contrast_baseline import segment_contamination

        image = np.full((80, 80, 3), 180, dtype=np.uint8)
        result = segment_contamination(image)
        self.assertEqual(0.0, result.measurement.area_px)
        self.assertIsNone(result.measurement.centroid_px)

    def test_invalid_policy_is_rejected(self):
        import numpy as np

        from microcleaning.vision.local_contrast_baseline import (
            LocalContrastPolicy,
            segment_contamination,
        )

        image = np.full((40, 40, 3), 180, dtype=np.uint8)
        with self.assertRaises(ValueError):
            segment_contamination(image, policy=LocalContrastPolicy(blur_kernel_px=4))


if __name__ == "__main__":
    unittest.main()
