"""共享实时预览与相机辅助；此模块不执行运动或喷水。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from microcleaning.data_learning.usb_camera import CAMERA_OPEN_FAILED, FRAME_READ_FAILED, USBCameraError

CaptureFactory = Callable[..., Any]
ShowFrame = Callable[[str, Any], None]
WaitKey = Callable[[int], int]
LIVE_WINDOW = "MicroCleaningVision live  SPACE=analyze  H/O/G/E/L=algo  Q=pause"


LIVE_WINDOW_PUMP = "MicroCleaningVision live  SPACE=analyze+pump if target  Q=pause"


RESULT_WINDOW = "Last Demo analysis (path overlay)"


IDLE_WINDOW = "Paused  any key=reopen  Q=exit program"


QUIT_KEYS = {ord("q"), ord("Q"), 27}


HSV_KEYS = {ord("h"), ord("H")}


OTSU_KEYS = {ord("o"), ord("O")}


EXG_KEYS = {ord("g"), ord("G")}


EXR_KEYS = {ord("e"), ord("E")}


LOCAL_KEYS = {ord("l"), ord("L")}


SPACE_KEY = 32


VISION_ALGORITHMS = ("hsv", "otsu", "exg", "exr", "local")


def _wait_until_camera_readable(
    *,
    camera_index: int,
    camera_backend: int | None,
    factory: CaptureFactory,
    sleep: SleepFn,
    max_attempts: int | None,
) -> Any:
    attempts = 0
    while max_attempts is None or attempts < max_attempts:
        attempts += 1
        capture = None
        keep_open = False
        try:
            capture = factory(camera_index) if camera_backend is None else factory(camera_index, camera_backend)
            if capture is not None and bool(capture.isOpened()):
                ok, frame = capture.read()
                if ok and frame is not None and hasattr(frame, "size") and int(frame.size) > 0:
                    print(f"已检测到 USB 相机 device_index={camera_index}，打开实时窗口。")
                    keep_open = True
                    return capture
        except Exception:
            pass
        finally:
            if capture is not None and not keep_open:
                try:
                    capture.release()
                except Exception:
                    pass
        sleep(1.0)
    raise USBCameraError(CAMERA_OPEN_FAILED, f"等待 USB 相机超时 device_index={camera_index}")


def _idle_pause(*, cv2, np, show: ShowFrame, key_fn: WaitKey, max_idle_frames: int | None) -> str:
    paused = np.zeros((220, 720, 3), dtype=np.uint8)
    _put_hud(paused, cv2, "Preview paused", y=70)
    _put_hud(paused, cv2, "Any other key = reopen camera", y=110)
    _put_hud(paused, cv2, "Q / Esc = exit program   no pump", y=150)
    frames = 0
    while max_idle_frames is None or frames < max_idle_frames:
        show(IDLE_WINDOW, paused)
        key = int(key_fn(100))
        frames += 1
        if key < 0:
            continue
        key = key & 0xFF
        if key in QUIT_KEYS:
            return "exit"
        return "reopen"
    return "exit"


def _compose_preview(
    frame,
    *,
    algorithm: str,
    camera_index: int,
    status: str,
    cv2,
    segment_demo_image,
    draw_contamination,
    pump_on_analyze: bool = False,
):
    try:
        segmentation = segment_demo_image(frame, algorithm)
        view = draw_contamination(frame, segmentation.mask, segmentation.measurement.centroid_px, cv2)
        area = segmentation.measurement.area_px
        centroid = segmentation.measurement.centroid_px
        hint = f"area={area:.0f}px  center={centroid}"
    except Exception as exc:
        view = frame.copy()
        hint = f"segment failed: {type(exc).__name__}"
        status = hint
    space_hint = "SPACE=analyze+pump" if pump_on_analyze else "SPACE=analyze"
    _put_hud(view, cv2, f"index={camera_index}  {algorithm}  {space_hint}  H/O/G/E/L=algo  Q=pause")
    _put_hud(view, cv2, hint, y=56)
    _put_hud(view, cv2, status, y=80)
    return view, status


def _put_hud(image, cv2, text: str, *, y: int = 32) -> None:
    cv2.putText(image, text[:88], (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(image, text[:88], (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1, cv2.LINE_AA)


def _apply_resolution(capture: Any, cv2, width: int | None, height: int | None) -> None:
    if width is not None:
        try:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
        except Exception:
            pass
    if height is not None:
        try:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
        except Exception:
            pass


class CameraPreview:
    """新闭环的持续相机会话；空格只抓新帧，窗口内不能切换算法。"""
    window = "MicroCleaningVision closed loop  SPACE=capture  Q=stop"

    def __init__(self, *, camera_index: int, segmenter, width=None, height=None,
                 backend=None, warmup_frames=5, capture_factory=None, show=None, wait_key=None, destroy_windows=None):
        from microcleaning.data_learning.usb_camera import USBCameraConfig
        USBCameraConfig(device_index=camera_index, width=width, height=height,
            backend=backend, warmup_frames=warmup_frames).validate()
        if warmup_frames < 1:
            raise ValueError("闭环必须丢弃旧缓存帧，warmup_frames 至少为 1")
        import cv2
        self.cv2, self.segmenter = cv2, segmenter
        self.index, self.width, self.height, self.backend = camera_index, width, height, backend
        self.warmup = warmup_frames
        self.factory = capture_factory or cv2.VideoCapture
        self.show, self.wait_key = show or cv2.imshow, wait_key or cv2.waitKey
        self.destroy_windows = destroy_windows or cv2.destroyAllWindows
        self.capture_handle = None
        self.sequence = 0

    def capture(self, phase, *, after=None):
        from microcleaning.control_system.orchestration.cleaning_loop import CapturedFrame
        from demo.image_ops import _draw_contamination
        if self.capture_handle is None:
            handle = self.factory(self.index) if self.backend is None else self.factory(self.index, self.backend)
            if not handle.isOpened():
                handle.release()
                raise USBCameraError(CAMERA_OPEN_FAILED, "指定相机无法打开")
            self.capture_handle = handle
            _apply_resolution(handle, self.cv2, self.width, self.height)
            setter = getattr(handle, "set", None)
            if setter is not None:
                setter(self.cv2.CAP_PROP_BUFFERSIZE, 1)
        handle = self.capture_handle
        for _ in range(self.warmup):
            okay, _frame = handle.read()
            if not okay:
                raise USBCameraError(FRAME_READ_FAILED, "丢弃缓存帧时相机断开")
        self.wait_key(1)  # 消耗运动前残留按键，不把它当作本次冻结。
        print(f"{phase.upper()}：同一相机窗口，空格冻结；Q 结束任务。分割参数已锁定。")
        while True:
            okay, frame = handle.read()
            if not okay or frame is None or not frame.size:
                raise USBCameraError(FRAME_READ_FAILED, "闭环抓图失败")
            segmentation = self.segmenter(frame)
            preview = _draw_contamination(frame, segmentation.mask, segmentation.measurement.centroid_px, self.cv2)
            _put_hud(preview, self.cv2, f"{phase.upper()}  SPACE=capture; Q=stop")
            self.show(self.window, preview)
            key = self.wait_key(30) & 0xff
            if key in QUIT_KEYS:
                raise USBCameraError("CAPTURE_CANCELLED", "操作员取消抓图")
            if key == SPACE_KEY:
                self.sequence += 1
                settings = {"device_index": self.index, "requested_width": self.width, "requested_height": self.height,
                    "backend": self.backend, "actual_size": list(frame.shape[:2]),
                    "exposure": self._setting(self.cv2.CAP_PROP_EXPOSURE), "gain": self._setting(self.cv2.CAP_PROP_GAIN),
                    "auto_exposure": self._setting(self.cv2.CAP_PROP_AUTO_EXPOSURE)}
                return CapturedFrame(frame.copy(), f"camera_{self.index}_{self.sequence}",
                    datetime.now(timezone.utc).isoformat(), f"usb-camera:{self.index}", settings)

    def _setting(self, property_id):
        import math
        getter = getattr(self.capture_handle, "get", None)
        if getter is None:
            return None
        value = getter(property_id)
        return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None

    def close(self):
        handle, self.capture_handle = self.capture_handle, None
        if handle is not None:
            handle.release()
            self.destroy_windows()
