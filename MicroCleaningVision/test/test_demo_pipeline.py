"""Demo入口的最小端到端验收。"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


HAS_PERCEPTION_DEPS = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class DemoPipelineTests(unittest.TestCase):
    def test_simulation_mode_produces_visible_artifacts_and_action_evidence(self):
        from demo.demo_pipeline import run_demo

        with tempfile.TemporaryDirectory() as folder:
            run_dir = run_demo(generate_sample=True, mode="simulate", output_root=folder)
            for name in (
                "input.png",
                "mask.png",
                "contamination_overlay.png",
                "path_overlay.png",
                "post_mask.png",
                "summary.json",
            ):
                self.assertTrue((run_dir / name).is_file(), name)
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertGreater(summary["contamination"]["area_px"], 0)
            self.assertGreater(len(summary["cleaning_plan"]["path_px"]), 0)
            self.assertIsNotNone(summary["action_request"])
            self.assertEqual("fake_serial", summary["execution_receipt"]["mode"])

    def test_analysis_mode_never_invents_hardware_action(self):
        from demo.demo_pipeline import run_demo

        with tempfile.TemporaryDirectory() as folder:
            run_dir = run_demo(generate_sample=True, mode="analyze", output_root=folder)
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual("image_px", summary["state"]["coordinate_frame"])
            self.assertIsNone(summary["state"]["target_centroid_mm"])
            self.assertIsNone(summary["action_request"])


if __name__ == "__main__":
    unittest.main()
