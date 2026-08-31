"""USBCamera 无真实硬件测试：用 FakeVideoCapture 验证采集、失败和释放。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from microcleaning.contracts import Observation
from microcleaning.data_learning.image_quality import inspect_image_file as real_inspect_image_file
from microcleaning.data_learning.usb_camera import (
    CAMERA_OPEN_FAILED,
    EMPTY_FRAME,
    FRAME_READ_FAILED,
    OUTPUT_WRITE_FAILED,
    USBCamera,
    USBCameraConfig,
    USBCameraError,
)
from microcleaning.ports import CameraPort


class FakeVideoCapture:
    def __init__(self, *, opened: bool = True, reads=None, read_exception: Exception | None = None) -> None:
        self.opened = opened
        self.reads = list(reads or [])
        self.read_exception = read_exception
        self.read_index = 0
        self.released = False
        self.set_calls: list[tuple[int, float]] = []

    def isOpened(self) -> bool:
        return self.opened

    def read(self):
        if self.read_exception is not None:
            raise self.read_exception
        if not self.reads:
            return False, None
        index = min(self.read_index, len(self.reads) - 1)
        self.read_index += 1
        return self.reads[index]

    def release(self) -> None:
        self.released = True

    def set(self, prop: int, value: float) -> bool:
        self.set_calls.append((prop, value))
        return True


class USBCameraTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temporary_directory.name) / "captures"
        self.frame = np.full((24, 32, 3), 128, dtype=np.uint8)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _camera(self, fake: FakeVideoCapture, **config_overrides) -> USBCamera:
        config = USBCameraConfig(
            output_root=self.output_root,
            warmup_frames=config_overrides.pop("warmup_frames", 0),
            **config_overrides,
        )
        return USBCamera(config, capture_factory=lambda *args: fake)

    def test_normal_capture_saves_png_calls_quality_and_returns_observation(self):
        fake = FakeVideoCapture(reads=[(True, self.frame)])
        camera = self._camera(fake, width=640, height=480)

        with patch(
            "microcleaning.data_learning.usb_camera.inspect_image_file",
            wraps=real_inspect_image_file,
        ) as quality_spy:
            observation = camera.capture("u500-smoke", "pre")

        self.assertIsInstance(camera, CameraPort)
        self.assertIsInstance(observation, Observation)
        self.assertEqual("u500-smoke", observation.task_id)
        self.assertIn("usb-camera-v0.1", observation.software_version)
        self.assertTrue(camera.capture_path("pre").is_file())
        decoded = cv2.imread(str(camera.capture_path("pre")), cv2.IMREAD_COLOR)
        self.assertEqual((24, 32, 3), decoded.shape)
        self.assertEqual(1, quality_spy.call_count)
        self.assertEqual(32, camera.inspection("pre").metrics.width_px)
        self.assertEqual(24, camera.inspection("pre").metrics.height_px)
        self.assertTrue(fake.released)
        self.assertEqual(2, len(fake.set_calls))

    def test_warmup_frames_are_discarded_before_target_frame(self):
        warmup = np.zeros_like(self.frame)
        target = np.full_like(self.frame, 200)
        fake = FakeVideoCapture(reads=[(True, warmup), (True, warmup), (True, target)])
        camera = self._camera(fake, warmup_frames=2)

        camera.capture("warmup", "pre")
        decoded = cv2.imread(str(camera.capture_path("pre")), cv2.IMREAD_COLOR)

        self.assertEqual(3, fake.read_index)
        self.assertEqual(200, int(decoded[0, 0, 0]))
        self.assertTrue(fake.released)

    def test_camera_open_failure_has_reason_code_and_releases(self):
        fake = FakeVideoCapture(opened=False)
        camera = self._camera(fake)

        with self.assertRaises(USBCameraError) as caught:
            camera.capture("open-fail", "pre")

        self.assertEqual(CAMERA_OPEN_FAILED, caught.exception.reason_code)
        self.assertTrue(fake.released)

    def test_frame_read_failure_has_reason_code_and_releases(self):
        fake = FakeVideoCapture(reads=[(False, None)])
        camera = self._camera(fake)

        with self.assertRaises(USBCameraError) as caught:
            camera.capture("read-fail", "pre")

        self.assertEqual(FRAME_READ_FAILED, caught.exception.reason_code)
        self.assertTrue(fake.released)

    def test_driver_read_exception_is_normalized_and_releases(self):
        fake = FakeVideoCapture(read_exception=RuntimeError("driver failure"))
        camera = self._camera(fake)

        with self.assertRaises(USBCameraError) as caught:
            camera.capture("driver-exception", "pre")

        self.assertEqual(FRAME_READ_FAILED, caught.exception.reason_code)
        self.assertTrue(fake.released)

    def test_empty_frame_has_reason_code_and_releases(self):
        fake = FakeVideoCapture(reads=[(True, np.empty((0, 0, 3), dtype=np.uint8))])
        camera = self._camera(fake)

        with self.assertRaises(USBCameraError) as caught:
            camera.capture("empty", "pre")

        self.assertEqual(EMPTY_FRAME, caught.exception.reason_code)
        self.assertTrue(fake.released)

    def test_output_write_failure_has_reason_code_after_release(self):
        fake = FakeVideoCapture(reads=[(True, self.frame)])

        def failing_writer(path, frame):
            raise OSError("disk unavailable")

        camera = USBCamera(
            USBCameraConfig(output_root=self.output_root, warmup_frames=0),
            capture_factory=lambda *args: fake,
            image_writer=failing_writer,
        )

        with self.assertRaises(USBCameraError) as caught:
            camera.capture("write-fail", "pre")

        self.assertEqual(OUTPUT_WRITE_FAILED, caught.exception.reason_code)
        self.assertTrue(fake.released)


if __name__ == "__main__":
    unittest.main()
