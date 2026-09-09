"""离线 mm/px：只做像素尺度，不生成动作。"""

import json
import tempfile
import unittest
from pathlib import Path

from microcleaning.vision.scale_measure import measure_mm_per_px


class ScaleMeasureTests(unittest.TestCase):
    def test_known_length_divides_pixel_distance(self):
        result = measure_mm_per_px(p1_px=(0.0, 0.0), p2_px=(100.0, 0.0), known_length_mm=1.0)
        self.assertEqual(0.01, result.mm_per_px)
        self.assertEqual(100.0, result.px_per_mm)
        self.assertFalse(result.feeds_action_request)
        self.assertEqual("image_px", result.coordinate_frame)
        self.assertIsNone(result.holdout_error_ratio)

    def test_holdout_segment_reports_relative_error(self):
        result = measure_mm_per_px(
            p1_px=(0.0, 0.0),
            p2_px=(100.0, 0.0),
            known_length_mm=1.0,
            holdout_p1_px=(0.0, 10.0),
            holdout_p2_px=(50.0, 10.0),
            holdout_known_mm=0.4,
        )
        self.assertAlmostEqual(0.25, result.holdout_error_ratio)

    def test_coincident_points_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "两点不能重合"):
            measure_mm_per_px(p1_px=(3.0, 4.0), p2_px=(3.0, 4.0), known_length_mm=1.0)

    def test_module_does_not_import_action_or_state(self):
        source = Path(__file__).resolve().parents[2] / "microcleaning" / "vision" / "scale_measure.py"
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("microcleaning.contracts", text)
        self.assertNotIn("state_estimator", text)
        self.assertNotIn("fixed_rule", text)

    def test_cli_writes_json_that_cannot_feed_actions(self):
        import importlib.util

        script = Path(__file__).resolve().parents[2] / "scripts" / "measure_scale_mm_per_px.py"
        spec = importlib.util.spec_from_file_location("measure_scale_mm_per_px", script)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "scale.json"
            code = module.main(
                [
                    "--p1",
                    "0,0",
                    "--p2",
                    "200,0",
                    "--known-mm",
                    "2",
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(0, code)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(payload["feeds_action_request"])
            self.assertAlmostEqual(0.01, payload["mm_per_px"])


if __name__ == "__main__":
    unittest.main()
