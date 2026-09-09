"""USB 相机探测脚本的无窗口预览回归。"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "probe_usb_camera.py"


def _load_probe_script():
    spec = importlib.util.spec_from_file_location("probe_usb_camera_under_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载探测脚本：{SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


class ProbeUsbCameraPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.probe = _load_probe_script()

    def test_preview_shows_frames_and_quits_on_q(self) -> None:
        frame = np.full((48, 64, 3), 40, dtype=np.uint8)
        fake = FakeVideoCapture(reads=[(True, frame)])
        shown: list[tuple[str, object]] = []
        keys = [255, 255, ord("q")]

        count = self.probe.preview_device(
            1,
            capture_factory=lambda *args, **kwargs: fake,
            imshow=lambda name, image: shown.append((name, image.copy())),
            wait_key=lambda delay: keys.pop(0) if keys else ord("q"),
            destroy_windows=lambda: None,
            max_frames=10,
        )

        self.assertGreaterEqual(count, 1)
        self.assertTrue(fake.released)
        self.assertIn("index=1", shown[0][0])

    def test_preview_fails_when_device_cannot_open(self) -> None:
        fake = FakeVideoCapture(opened=False)
        with self.assertRaisesRegex(RuntimeError, "CAMERA_OPEN_FAILED"):
            self.probe.preview_device(
                0,
                capture_factory=lambda *args, **kwargs: fake,
                imshow=lambda name, image: None,
                wait_key=lambda delay: ord("q"),
                destroy_windows=lambda: None,
                max_frames=1,
            )
        self.assertTrue(fake.released)


if __name__ == "__main__":
    unittest.main()
