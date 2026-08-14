"""成员 B 的文件：污染测量、状态估计和复检测试。"""

import unittest

from microcleaning.contracts import ExecutionReceipt, NextRoute
from microcleaning.perception.contamination import ContaminationMeasurement
from microcleaning.perception.image_quality import ImageQuality, build_observation
from microcleaning.state.estimator import estimate_state
from microcleaning.verification.area_change import verify_area_change


class MeasurementAndVerificationTests(unittest.TestCase):
    def setUp(self):
        quality = ImageQuality(0.95, 0.95, 0.95)
        self.pre = build_observation(task_id="measure", frame_id="pre", raw_image_ref="replay://pre.png", quality=quality)
        self.post = build_observation(task_id="measure", frame_id="post", raw_image_ref="replay://post.png", quality=quality)

    def test_measurement_becomes_state_without_hardware_authority(self):
        state = estimate_state(self.pre, ContaminationMeasurement(100.0, (10.0, 20.0), 0.2, 0.95))
        self.assertEqual(100.0, state.target_area_px)
        self.assertIsNone(state.target_centroid_mm)
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


if __name__ == "__main__":
    unittest.main()
