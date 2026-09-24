"""像素路径规则测试。"""

import importlib.util
import unittest

HAS_PERCEPTION_DEPS = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class CleaningPlanTests(unittest.TestCase):
    def test_small_target_uses_one_center_point(self):
        import cv2
        import numpy as np

        from microcleaning.control_system.planning.cleaning_plan import CleaningStrategy, plan_cleaning

        mask = np.zeros((100, 100), dtype=np.uint8)
        cv2.circle(mask, (40, 60), 5, 255, -1)
        plan = plan_cleaning(mask)
        self.assertEqual(CleaningStrategy.CENTER_POINT, plan.strategy)
        self.assertEqual(1, len(plan.path_px))
        self.assertAlmostEqual(40.0, plan.path_px[0][0], delta=1.0)

    def test_large_target_uses_in_mask_raster_points(self):
        import numpy as np

        from microcleaning.control_system.planning.cleaning_plan import CleaningStrategy, plan_cleaning

        mask = np.zeros((120, 160), dtype=np.uint8)
        mask[20:100, 30:140] = 255
        plan = plan_cleaning(mask)
        self.assertEqual(CleaningStrategy.RASTER_SCAN, plan.strategy)
        self.assertGreater(len(plan.path_px), 5)
        for x, y in plan.path_px:
            self.assertGreater(mask[round(y), round(x)], 0)

    def test_empty_mask_creates_no_path(self):
        import numpy as np

        from microcleaning.control_system.planning.cleaning_plan import CleaningStrategy, plan_cleaning

        plan = plan_cleaning(np.zeros((40, 50), dtype=np.uint8))
        self.assertEqual(CleaningStrategy.NO_TARGET, plan.strategy)
        self.assertEqual((), plan.path_px)

    def test_disconnected_targets_have_separate_spray_segments(self):
        import numpy as np

        from microcleaning.control_system.planning.cleaning_plan import plan_cleaning

        mask = np.zeros((120, 180), dtype=np.uint8)
        mask[20:90, 20:80] = 255
        mask[40:110, 125:170] = 255
        plan = plan_cleaning(mask)
        self.assertEqual(2, len(plan.segment_start_indices))
        second_start = plan.segment_start_indices[1]
        self.assertGreater(second_start, 0)
        self.assertEqual("nearest_neighbor", plan.visit_order)


if __name__ == "__main__":
    unittest.main()
