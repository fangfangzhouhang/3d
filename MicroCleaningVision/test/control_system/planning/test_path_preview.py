"""路径规则、假设毫米与步进预览；不得写入动作申请。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import importlib.util

from microcleaning.control_system.planning.path_preview import (
    load_path_placeholders,
    path_placeholders_from_dict,
    plan_and_preview,
)
from microcleaning.control_system.planning.stepper_preview import StepperConfig, mm_delta_to_steps
from microcleaning.control_system.planning.work_frame import WorkFrameConfig, pixel_to_assumed_mm


HAS_PERCEPTION_DEPS = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


class StepperPreviewTests(unittest.TestCase):
    def test_lead_formula_matches_common_t8_example(self):
        stepper = StepperConfig(steps_per_rev=200, microstep=16, assumed_lead_mm_per_rev=8.0)
        steps_x, steps_y, rev_x, rev_y = mm_delta_to_steps((2.0, 0.0), stepper)
        self.assertEqual(800, steps_x)
        self.assertEqual(0, steps_y)
        self.assertAlmostEqual(0.25, rev_x)
        self.assertEqual(0.0, rev_y)

    def test_explicit_steps_per_mm_overrides_lead(self):
        stepper = StepperConfig(steps_per_mm_x=100.0, steps_per_mm_y=50.0)
        steps_x, steps_y, _rev_x, _rev_y = mm_delta_to_steps((2.0, 4.0), stepper)
        self.assertEqual(200, steps_x)
        self.assertEqual(200, steps_y)

    def test_send_to_controller_is_rejected(self):
        with self.assertRaises(ValueError):
            StepperConfig(send_to_controller=True).validate()


class WorkFrameTests(unittest.TestCase):
    def test_assumed_scale_and_flip_y(self):
        frame = WorkFrameConfig(assumed_mm_per_px=0.01, flip_y=True)
        right = pixel_to_assumed_mm(100.0, 0.0, frame)
        down = pixel_to_assumed_mm(0.0, 100.0, frame)
        self.assertAlmostEqual(1.0, right[0])
        self.assertAlmostEqual(0.0, right[1])
        self.assertAlmostEqual(0.0, down[0])
        self.assertAlmostEqual(-1.0, down[1])

    def test_nozzle_offset_is_added_after_scale(self):
        frame = WorkFrameConfig(assumed_mm_per_px=0.01, nozzle_offset_mm=(0.5, -0.25))
        x_mm, y_mm = pixel_to_assumed_mm(100.0, 0.0, frame)
        self.assertAlmostEqual(1.5, x_mm)
        self.assertAlmostEqual(-0.25, y_mm)

    def test_identity_homography_slot(self):
        identity = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        frame = WorkFrameConfig(homography_3x3=identity, nozzle_offset_mm=(1.0, 2.0))
        x_mm, y_mm = pixel_to_assumed_mm(5.0, 7.0, frame)
        self.assertAlmostEqual(6.0, x_mm)
        self.assertAlmostEqual(9.0, y_mm)

    def test_feeds_action_request_is_rejected(self):
        with self.assertRaises(ValueError):
            WorkFrameConfig(feeds_action_request=True).validate()


class PlaceholderLoadTests(unittest.TestCase):
    def test_json_cannot_arm_action_or_serial(self):
        with self.assertRaises(ValueError):
            path_placeholders_from_dict({"feeds_action_request": True})
        with self.assertRaises(ValueError):
            path_placeholders_from_dict({"stepper": {"send_to_controller": True}})

    def test_load_default_file_roundtrip(self):
        payload = {
            "assumed_spray_width_mm": 0.16,
            "work": {"assumed_mm_per_px": 0.01, "nozzle_offset_mm": [0.0, 0.0]},
            "stepper": {"steps_per_rev": 200, "microstep": 16, "assumed_lead_mm_per_rev": 8.0},
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "placeholders.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_path_placeholders(path)
        self.assertAlmostEqual(0.16, loaded.assumed_spray_width_mm)
        self.assertFalse(loaded.work.feeds_action_request)
        self.assertFalse(loaded.stepper.send_to_controller)


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class PathPreviewIntegrationTests(unittest.TestCase):
    def test_empty_mask_has_no_motion(self):
        import numpy as np

        preview = plan_and_preview(np.zeros((40, 50), dtype=np.uint8))
        self.assertEqual((), preview.waypoints)
        self.assertEqual((), preview.motion.legs)
        self.assertFalse(preview.to_dict()["feeds_action_request"])
        self.assertIn("没有污渍", "\n".join(preview.narrative))

    def test_two_blobs_use_nearest_neighbor_and_segment_travel(self):
        import numpy as np

        from microcleaning.control_system.planning.cleaning_plan import CleaningPlanPolicy, CleaningStrategy, plan_cleaning
        from microcleaning.control_system.planning.path_preview import build_path_preview

        mask = np.zeros((120, 200), dtype=np.uint8)
        mask[40:50, 20:30] = 255
        mask[30:100, 140:190] = 255
        nn_plan = plan_cleaning(mask)
        area_plan = plan_cleaning(mask, policy=CleaningPlanPolicy(visit_order="area_desc"))
        self.assertEqual(CleaningStrategy.RASTER_SCAN, nn_plan.strategy)
        self.assertLess(nn_plan.path_px[0][0], 40)
        self.assertGreater(area_plan.path_px[0][0], 120)

        preview = build_path_preview(nn_plan)
        roles = [leg.role for leg in preview.motion.legs]
        self.assertIn("home_to_first", roles)
        self.assertIn("segment_travel", roles)
        travel = [leg for leg in preview.motion.legs if leg.role == "segment_travel"]
        self.assertTrue(travel)
        self.assertFalse(travel[0].pump_on)
        self.assertFalse(preview.to_dict()["send_to_controller"])


if __name__ == "__main__":
    unittest.main()
