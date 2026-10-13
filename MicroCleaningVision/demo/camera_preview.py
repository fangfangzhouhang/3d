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


def _cross(cv2, image, x: int, y: int, color, size: int = 22, thick: int = 2) -> None:
    height, width = image.shape[:2]
    x = max(0, min(width - 1, int(x)))
    y = max(0, min(height - 1, int(y)))
    cv2.line(image, (max(0, x - size), y), (min(width - 1, x + size), y), color, thick, cv2.LINE_8)
    cv2.line(image, (x, max(0, y - size)), (x, min(height - 1, y + size)), color, thick, cv2.LINE_8)


def draw_stage_guides(image, cv2, marks=None) -> None:
    """画面正中十字是显微镜中心。针头目标在右下角小图的 N，不画进视野冒充重合。"""

    height, width = image.shape[:2]
    _cross(cv2, image, width // 2, height // 2, (0, 255, 255))
    cv2.putText(image, "SCOPE", (min(width - 70, width // 2 + 14), max(18, height // 2 - 12)),
        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_8)
    if not marks:
        return
    scope, nozzle, current = marks.get("scope_xy"), marks.get("nozzle_xy"), marks.get("current_xy")
    if scope is None or nozzle is None or current is None:
        return
    stain = marks.get("stain_px")
    if stain is not None and tuple(current) == tuple(scope):
        sx, sy = int(round(float(stain[0]))), int(round(float(stain[1])))
        if 0 <= sx < width and 0 <= sy < height:
            cv2.circle(image, (sx, sy), 14, (0, 128, 255), 2, cv2.LINE_8)
            cv2.putText(image, "STAIN", (min(width - 70, sx + 8), max(16, sy - 8)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 128, 255), 1, cv2.LINE_8)
    box_w, box_h, margin = 176, 132, 8
    if width < box_w + margin * 2 or height < box_h + margin * 2:
        return
    x0, y0 = width - box_w - margin, height - box_h - margin
    cv2.rectangle(image, (x0, y0), (x0 + box_w - 1, y0 + box_h - 1), (32, 32, 32), -1, cv2.LINE_8)
    cv2.rectangle(image, (x0, y0), (x0 + box_w - 1, y0 + box_h - 1), (0, 255, 255), 1, cv2.LINE_8)
    points = (tuple(scope), tuple(nozzle), tuple(current))
    xs, ys = [point[0] for point in points], [point[1] for point in points]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    spanx, spany = max(maxx - minx, 1), max(maxy - miny, 1)
    pad = 18

    def project(point):
        along = pad + (float(point[0]) - minx) / spanx * (box_w - 2 * pad)
        down = pad + (float(point[1]) - miny) / spany * (box_h - 2 * pad)
        return x0 + int(round(min(box_w - pad, max(pad, along)))), y0 + int(round(min(box_h - pad, max(pad, down))))

    scope_px, nozzle_px, current_px = project(scope), project(nozzle), project(current)
    cv2.line(image, scope_px, nozzle_px, (180, 180, 180), 1, cv2.LINE_8)
    cv2.circle(image, scope_px, 5, (0, 255, 255), -1, cv2.LINE_8)
    cv2.circle(image, nozzle_px, 5, (0, 0, 255), -1, cv2.LINE_8)
    at_nozzle = tuple(current) == tuple(nozzle)
    cv2.circle(image, current_px, 4, (0, 255, 0) if at_nozzle else (255, 255, 255), -1, cv2.LINE_8)
    cv2.putText(image, "S", (scope_px[0] + 6, max(y0 + 14, scope_px[1] - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_8)
    cv2.putText(image, "N", (nozzle_px[0] + 6, min(y0 + box_h - 6, nozzle_px[1] + 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv2.LINE_8)
    cv2.putText(image, "AT NOZZLE" if at_nozzle else "S scope  N nozzle", (x0 + 6, y0 + 14),
        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0) if at_nozzle else (220, 220, 220), 1, cv2.LINE_8)


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
                 backend=None, warmup_frames=5, capture_factory=None, show=None, wait_key=None, destroy_windows=None,
                 panel=None):
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
        self.panel = panel
        self.show = show or (panel.show_frame if panel is not None else cv2.imshow)
        self.wait_key = wait_key or (panel.wait_key if panel is not None else cv2.waitKey)
        self.destroy_windows = destroy_windows or ( (lambda: None) if panel is not None else cv2.destroyAllWindows)
        self.capture_handle = None
        self.sequence = 0
        self.path_window = "MicroCleaningVision path"
        self.marks = None
        self._blank_reads = 0
        self._backend_switched = False

    def set_marks(self, marks) -> None:
        self.marks = None if marks is None else dict(marks)

    def open(self) -> None:
        """先打开实时窗口。打不开指定后端时，同一相机编号再试 DirectShow。"""

        self._acquire()
        self.refresh("LIVE  yellow cross = microscope center")
        message = (
            f"实时画面在这个窗口左边。正中黄色十字是显微镜中心。相机 index={self.index}，后端 {self.backend}。"
            "确认和抓图都在这个窗口，不用回到 Cursor 终端。"
        )
        print(message, flush=True)
        self.tell(message)

    def refresh(self, status: str = "") -> bool:
        """刷新实时画面。画面失败不往外抛，避免挡住已经开始的步进。"""

        handle = self.capture_handle
        if handle is None:
            return False
        try:
            okay, frame = handle.read()
            if not okay or frame is None or not getattr(frame, "size", 0):
                self._recover_unreadable()
                return False
            self._blank_reads = 0
            view = frame.copy()
            draw_stage_guides(view, self.cv2, self.marks)
            hud = "".join(ch if ord(ch) < 128 else " " for ch in status)[:88]
            if hud.strip():
                _put_hud(view, self.cv2, hud)
            self.show(self.window, view)
            self.wait_key(1)
            return True
        except Exception:
            return False

    def tell(self, message: str) -> None:
        if self.panel is not None and hasattr(self.panel, "write"):
            self.panel.write(message, echo=False)

    def show_roster(self, image) -> None:
        if self.panel is not None and hasattr(self.panel, "show_roster"):
            self.panel.show_roster(image)

    def show_overlay(self, path: str) -> None:
        if self.panel is not None and getattr(self.panel, "roster_locked", False):
            self.tell(f"路径图已保存：{path}。右边继续显示第一次画面的标注。")
            return
        try:
            from pathlib import Path
            import numpy as np
            image = self.cv2.imdecode(np.frombuffer(Path(path).read_bytes(), dtype=np.uint8), self.cv2.IMREAD_COLOR)
            if image is None:
                print(f"路径图打不开，规划仍继续：{path}", flush=True)
                self.tell(f"路径图打不开，规划仍继续：{path}")
                return
            self.show(self.path_window, image)
            self.wait_key(1)
        except Exception as exc:
            print(f"路径图没有显示，规划仍继续：{exc}", flush=True)
            self.tell(f"路径图没有显示，规划仍继续：{exc}")

    def _release(self, candidate) -> None:
        if candidate is None:
            return
        release = getattr(candidate, "release", None)
        if callable(release):
            try:
                release()
            except Exception:
                pass

    def _readable(self, candidate) -> bool:
        """真实相机打开后先读几帧。Media Foundation 有时 isOpened 为真，但抓帧一直失败。"""

        import time
        for _ in range(12):
            try:
                okay, frame = candidate.read()
            except Exception:
                return False
            if okay and frame is not None and getattr(frame, "size", 0):
                return True
            time.sleep(0.05)
        return False

    def _recover_unreadable(self) -> None:
        self._blank_reads += 1
        if self._blank_reads < 8 or self._backend_switched or self.factory is not self.cv2.VideoCapture:
            return
        self._backend_switched = True
        previous = self.backend
        nxt = 700 if previous != 700 else 1400
        print(
            f"[相机] 后端 {previous} 连续读不到画面，改用后端 {nxt}。相机编号仍是 {self.index}。",
            flush=True,
        )
        self._release(self.capture_handle)
        self.capture_handle = None
        self.backend = nxt
        self._blank_reads = 0
        try:
            self._acquire()
        except Exception as exc:
            print(f"[相机] 换后端后仍没有画面：{exc}", flush=True)

    def _acquire(self):
        if self.capture_handle is not None:
            return self.capture_handle
        import cv2
        real_capture = self.factory is cv2.VideoCapture
        backends = [self.backend]
        if real_capture and 700 not in backends:
            backends.append(700)
        for backend in backends:
            candidate = self.factory(self.index) if backend is None else self.factory(self.index, backend)
            opened = candidate is not None and bool(getattr(candidate, "isOpened", lambda: False)())
            if opened:
                _apply_resolution(candidate, self.cv2, self.width, self.height)
                setter = getattr(candidate, "set", None)
                if setter is not None:
                    try:
                        setter(self.cv2.CAP_PROP_BUFFERSIZE, 1)
                    except Exception:
                        pass
                if real_capture and not self._readable(candidate):
                    print(
                        f"[相机] 后端 {backend} 已打开 index={self.index}，但读不到画面，改试下一个后端。",
                        flush=True,
                    )
                    opened = False
            if opened:
                if backend != self.backend:
                    print(
                        f"[相机] 后端 {self.backend} 打不开或读不到 index={self.index}，已改用后端 {backend}。相机编号没有换。",
                        flush=True,
                    )
                self.backend = backend
                self.capture_handle = candidate
                return candidate
            self._release(candidate)
        raise USBCameraError(CAMERA_OPEN_FAILED, f"指定相机无法打开或读不到画面 device_index={self.index}")

    def capture(self, phase, *, after=None):
        from microcleaning.control_system.orchestration.cleaning_loop import CapturedFrame
        from demo.image_ops import _draw_contamination
        self._acquire()
        handle = self.capture_handle
        for _ in range(self.warmup):
            okay, _frame = handle.read()
            if not okay:
                raise USBCameraError(FRAME_READ_FAILED, "丢弃缓存帧时相机断开")
        self.wait_key(1)  # 消耗运动前残留按键，不把它当作本次冻结。
        message = f"{phase.upper()}：在这个窗口按空格抓这一帧。按 Q 结束。不用回到 Cursor 终端。"
        print(message, flush=True)
        self.tell(message)
        if self.panel is not None and hasattr(self.panel, "set_status"):
            self.panel.set_status("现在按空格抓图。按 Q 结束。")
        while True:
            okay, frame = handle.read()
            if not okay or frame is None or not frame.size:
                raise USBCameraError(FRAME_READ_FAILED, "闭环抓图失败")
            segmentation = self.segmenter(frame)
            preview = _draw_contamination(frame, segmentation.mask, segmentation.measurement.centroid_px, self.cv2)
            draw_stage_guides(preview, self.cv2, self.marks)
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
