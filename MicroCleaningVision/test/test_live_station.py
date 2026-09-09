"""实时预览窗口：B 叠加辅助 + 空格抓帧分析（无 GUI、无泵）。"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest

from demo.live_station import LIVE_WINDOW, RESULT_WINDOW, run_live_station
from microcleaning.data_learning.usb_camera import USBCameraError

HAS_PERCEPTION_DEPS = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


class FakeVideoCapture:
    def __init__(self, *, opened: bool = True, reads=None) -> None:
        self.opened = opened
        self.reads = list(reads or [])
        self.read_index = 0
        self.released = False

    def isOpened(self) -> bool:
        return self.opened

    def read(self):
        if not self.reads:
            return False, None
        index = min(self.read_index, len(self.reads) - 1)
        self.read_index += 1
        return self.reads[index]

    def release(self) -> None:
        self.released = True



@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class LiveStationTests(unittest.TestCase):
    def _red_frame(self):
        import cv2
        import numpy as np

        frame = np.full((120, 160, 3), 210, dtype=np.uint8)
        cv2.circle(frame, (80, 60), 18, (0, 0, 230), -1)
        return frame

    def test_space_freezes_current_frame_and_runs_analyze_without_pump(self):
        fake = FakeVideoCapture(reads=[(True, self._red_frame())])
        shown: list[str] = []
        keys = [32, ord("q")]

        with tempfile.TemporaryDirectory() as folder:
            run_dirs = run_live_station(
                camera_index=1,
                output_root=folder,
                warmup_frames=0,
                capture_factory=lambda *args, **kwargs: fake,
                imshow=lambda name, image: shown.append(name),
                wait_key=lambda delay: keys.pop(0) if keys else ord("q"),
                destroy_windows=lambda: None,
                max_frames=8,
            )
            self.assertEqual(1, len(run_dirs))
            summary = json.loads((run_dirs[0] / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual("local", summary["vision_algorithm"])
            self.assertEqual("camera", summary["source_kind"])
            self.assertTrue(summary["input_source"].startswith("usb-live:index="))
            self.assertEqual("analyze", summary["mode"])
            self.assertIsNone(summary["action_request"])
            self.assertIsNone(summary["execution_receipt"])
            self.assertGreater(summary["contamination"]["area_px"], 0)
            self.assertTrue((run_dirs[0] / "path_overlay.png").is_file())
            self.assertIn(LIVE_WINDOW, shown)
            self.assertIn(RESULT_WINDOW, shown)
            self.assertTrue(fake.released)

    def test_o_switches_to_otsu_before_space_capture(self):
        fake = FakeVideoCapture(reads=[(True, self._red_frame())])
        keys = [ord("o"), 32, ord("q")]

        with tempfile.TemporaryDirectory() as folder:
            run_dirs = run_live_station(
                camera_index=1,
                algorithm="hsv",
                output_root=folder,
                warmup_frames=0,
                capture_factory=lambda *args, **kwargs: fake,
                imshow=lambda name, image: None,
                wait_key=lambda delay: keys.pop(0) if keys else ord("q"),
                destroy_windows=lambda: None,
                max_frames=8,
            )
            summary = json.loads((run_dirs[0] / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual("otsu", summary["vision_algorithm"])
            self.assertIsNone(summary["action_request"])

    def test_g_switches_to_exg_before_space_capture(self):
        fake = FakeVideoCapture(reads=[(True, self._red_frame())])
        keys = [ord("g"), 32, ord("q")]

        with tempfile.TemporaryDirectory() as folder:
            run_dirs = run_live_station(
                camera_index=1,
                algorithm="hsv",
                output_root=folder,
                warmup_frames=0,
                capture_factory=lambda *args, **kwargs: fake,
                imshow=lambda name, image: None,
                wait_key=lambda delay: keys.pop(0) if keys else ord("q"),
                destroy_windows=lambda: None,
                max_frames=8,
            )
            summary = json.loads((run_dirs[0] / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual("exg", summary["vision_algorithm"])
            self.assertIsNone(summary["action_request"])
            self.assertIsNone(summary["execution_receipt"])

    def test_l_switches_to_local_before_space_capture(self):
        fake = FakeVideoCapture(reads=[(True, self._red_frame())])
        keys = [ord("l"), 32, ord("q")]

        with tempfile.TemporaryDirectory() as folder:
            run_dirs = run_live_station(
                camera_index=1,
                algorithm="hsv",
                output_root=folder,
                warmup_frames=0,
                capture_factory=lambda *args, **kwargs: fake,
                imshow=lambda name, image: None,
                wait_key=lambda delay: keys.pop(0) if keys else ord("q"),
                destroy_windows=lambda: None,
                max_frames=8,
            )
            summary = json.loads((run_dirs[0] / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual("local", summary["vision_algorithm"])
            self.assertIsNone(summary["action_request"])
            self.assertIsNone(summary["execution_receipt"])

    def test_open_failure_is_usb_camera_error(self):
        fake = FakeVideoCapture(opened=False)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(USBCameraError) as caught:
                run_live_station(
                    output_root=folder,
                    warmup_frames=0,
                    capture_factory=lambda *args, **kwargs: fake,
                    imshow=lambda name, image: None,
                    wait_key=lambda delay: ord("q"),
                    destroy_windows=lambda: None,
                    max_frames=1,
                )
            self.assertEqual("CAMERA_OPEN_FAILED", caught.exception.reason_code)
            self.assertTrue(fake.released)

    def test_main_rejects_live_without_camera_or_with_pump_flags(self):
        from demo.demo_pipeline import main

        with self.assertRaises(SystemExit):
            main(["--generate-sample", "--live"])
        with self.assertRaises(SystemExit):
            main(["--from-camera", "--live", "--mode", "arm-pump"])
        with self.assertRaises(SystemExit):
            main(["--from-camera", "--live", "--confirm-pump"])

    def test_session_q_pauses_then_other_key_reopens(self):
        from demo.live_station import IDLE_WINDOW, run_live_session

        frame = self._red_frame()
        created: list[FakeVideoCapture] = []

        def factory(*args, **kwargs):
            fake = FakeVideoCapture(reads=[(True, frame)])
            created.append(fake)
            return fake

        keys = [ord("q"), ord("r"), ord("q"), ord("q")]
        shown: list[str] = []
        with tempfile.TemporaryDirectory() as folder:
            run_dirs = run_live_session(
                camera_index=1,
                output_root=folder,
                warmup_frames=0,
                capture_factory=factory,
                imshow=lambda name, image: shown.append(name),
                wait_key=lambda delay: keys.pop(0) if keys else ord("q"),
                destroy_windows=lambda: None,
                max_frames=6,
                wait_usb=False,
                allow_reopen=True,
                sleep=lambda seconds: None,
                max_idle_frames=8,
            )
        self.assertGreaterEqual(len(created), 2)
        self.assertTrue(created[0].released)
        self.assertTrue(created[1].released)
        self.assertIn(IDLE_WINDOW, shown)
        self.assertEqual([], run_dirs)

    def test_wait_usb_retries_until_camera_is_readable(self):
        from demo.live_station import run_live_session

        frame = self._red_frame()
        first = FakeVideoCapture(opened=False)
        second = FakeVideoCapture(reads=[(True, frame)])
        fakes = [first, second]

        def factory(*args, **kwargs):
            return fakes.pop(0)

        keys = [ord("q"), ord("q")]
        with tempfile.TemporaryDirectory() as folder:
            run_live_session(
                camera_index=1,
                output_root=folder,
                warmup_frames=0,
                capture_factory=factory,
                imshow=lambda name, image: None,
                wait_key=lambda delay: keys.pop(0) if keys else ord("q"),
                destroy_windows=lambda: None,
                max_frames=4,
                wait_usb=True,
                allow_reopen=True,
                sleep=lambda seconds: None,
                max_wait_attempts=3,
                max_idle_frames=4,
            )
        self.assertEqual([], fakes)
        self.assertTrue(first.released)
        self.assertTrue(second.released)



if __name__ == "__main__":
    unittest.main()
