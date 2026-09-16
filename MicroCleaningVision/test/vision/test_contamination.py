"""污染测量的异常输入与 B→状态估计交接测试；不访问硬件。"""

from dataclasses import asdict, replace
import unittest

from microcleaning.contracts import Observation
from microcleaning.vision.contamination import ContaminationMeasurement
from microcleaning.vision.state_estimator import estimate_state


class ContaminationMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.measurement = ContaminationMeasurement(100.0, (10.0, 20.0), 0.2, 0.95)

    def test_valid_measurement_preserves_fields_and_defaults(self):
        before = asdict(self.measurement)
        self.measurement.validate()
        self.assertEqual(before, asdict(self.measurement))
        self.assertEqual(0, self.measurement.component_count)
        self.assertIsNone(self.measurement.mask_ref)
        self.assertEqual("unavailable", self.measurement.algorithm_version)

    def test_no_target_and_numeric_boundaries_are_valid(self):
        for measurement in (
            ContaminationMeasurement(0, None, 0, 0),
            ContaminationMeasurement(1, (0, 0), 0, 1, component_count=1),
            replace(self.measurement, centroid_px=[10.0, 20.0]),
        ):
            measurement.validate()

    def test_invalid_scalar_types_and_nonfinite_values_are_rejected(self):
        for field in ("area_px", "uncertainty_px", "confidence"):
            for value in (True, False, None, "1", [], 1j, -1, float("nan"),
                          float("inf"), -float("inf"), 10 ** 400):
                with self.subTest(field=field, value_type=type(value).__name__), self.assertRaisesRegex(ValueError, field):
                    replace(self.measurement, **{field: value}).validate()

    def test_confidence_above_one_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "confidence"):
            replace(self.measurement, confidence=1.01).validate()

    def test_malformed_and_invalid_centroids_are_rejected(self):
        values = (0, True, "12", {}, (), (1,), (1, 2, 3),
                  (None, 2), (True, 2), ("1", 2), (-1, 2),
                  (float("inf"), 2), (1, float("nan")), (10 ** 400, 2))
        for value in values:
            with self.subTest(value_type=type(value).__name__), self.assertRaisesRegex(ValueError, "centroid_px"):
                replace(self.measurement, centroid_px=value).validate()

    def test_invalid_component_counts_are_rejected(self):
        for value in (True, False, -1, 1.5, "1", None, float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "component_count"):
                replace(self.measurement, component_count=value).validate()

    def test_state_handoff_keeps_pixels_and_rejects_invalid_measurements(self):
        observation = Observation(
            observation_id="obs-test", task_id="measurement-test",
            timestamp="2026-09-16T00:00:00+00:00", frame_id="pre",
            raw_image_ref="fixture://pre", focus_quality=0.95,
            illumination_quality=0.95, confidence=0.95,
            quality_flags=(), software_version="test",
        )
        state = estimate_state(observation, self.measurement)
        self.assertEqual("image_px", state.coordinate_frame)
        self.assertEqual(self.measurement.centroid_px, state.target_centroid_px)
        self.assertIsNone(state.target_centroid_mm)
        self.assertIsNone(state.uncertainty_mm)
        self.assertFalse(state.calibration_valid)
        self.assertFalse(state.device_state["controller_connected"])
        with self.assertRaisesRegex(ValueError, "area_px"):
            estimate_state(observation, replace(self.measurement, area_px=float("inf")))


if __name__ == "__main__":
    unittest.main()
