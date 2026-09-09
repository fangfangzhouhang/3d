"""探测普通 USB 视频设备，并可用 USBCamera 保存一张质量检查测试帧。

本脚本只访问相机，不访问串口、STM32、泵、电机或控制系统。
``--preview`` 弹出实时画面，用来确认哪一个 device_index 是显微镜；按 Q 或 Esc 退出。
Demo 的 ``--from-camera`` 仍是抓一帧后分析，不是持续预览。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from microcleaning.data_learning.usb_camera import USBCamera, USBCameraConfig, USBCameraError

CaptureFactory = Callable[..., Any]
ShowFrame = Callable[[str, Any], None]
WaitKey = Callable[[int], int]
DestroyWindows = Callable[[], None]
QUIT_KEYS = {ord("q"), ord("Q"), 27}


@dataclass(frozen=True)
class ProbeResult:
    device_index: int
    opened: bool
    frame_read: bool
    width_px: int | None
    height_px: int | None
    fps_reported: float | None
    error: str | None


def probe_device(device_index: int, *, backend: int | None = None) -> ProbeResult:
    cv2 = _load_cv2()
    capture: Any = None
    try:
        capture = cv2.VideoCapture(device_index) if backend is None else cv2.VideoCapture(device_index, backend)
        if capture is None or not bool(capture.isOpened()):
            return ProbeResult(device_index, False, False, None, None, None, "CAMERA_OPEN_FAILED")
        ok, frame = capture.read()
        if not ok:
            return ProbeResult(device_index, True, False, None, None, _reported_fps(capture, cv2), "FRAME_READ_FAILED")
        if frame is None or not hasattr(frame, "size") or int(frame.size) <= 0:
            return ProbeResult(device_index, True, False, None, None, _reported_fps(capture, cv2), "EMPTY_FRAME")
        height, width = frame.shape[:2]
        return ProbeResult(
            device_index,
            True,
            True,
            int(width),
            int(height),
            _reported_fps(capture, cv2),
            None,
        )
    except Exception as exc:
        return ProbeResult(device_index, False, False, None, None, None, f"PROBE_ERROR: {exc}")
    finally:
        if capture is not None:
            try:
                capture.release()
            except Exception:
                pass


def preview_device(
    device_index: int,
    *,
    backend: int | None = None,
    capture_factory: CaptureFactory | None = None,
    imshow: ShowFrame | None = None,
    wait_key: WaitKey | None = None,
    destroy_windows: DestroyWindows | None = None,
    max_frames: int | None = None,
) -> int:
    """打开指定设备并循环显示画面，直到按 Q/Esc 或达到 max_frames。"""

    cv2 = _load_cv2()
    factory = capture_factory if capture_factory is not None else cv2.VideoCapture
    show = imshow if imshow is not None else cv2.imshow
    key_fn = wait_key if wait_key is not None else cv2.waitKey
    close = destroy_windows if destroy_windows is not None else cv2.destroyAllWindows
    window_name = f"USB camera preview  index={device_index}  Q/Esc quit"
    capture: Any = None
    frames_shown = 0
    try:
        capture = factory(device_index) if backend is None else factory(device_index, backend)
        if capture is None or not bool(capture.isOpened()):
            raise RuntimeError(f"CAMERA_OPEN_FAILED: device_index={device_index}")
        while max_frames is None or frames_shown < max_frames:
            ok, frame = capture.read()
            if not ok or frame is None or not hasattr(frame, "size") or int(frame.size) <= 0:
                raise RuntimeError(f"FRAME_READ_FAILED: device_index={device_index}")
            labeled = frame.copy()
            cv2.putText(
                labeled,
                f"index={device_index}  press Q to quit",
                (16, 32),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )
            show(window_name, labeled)
            frames_shown += 1
            key = int(key_fn(1)) & 0xFF
            if key in QUIT_KEYS:
                break
    finally:
        if capture is not None:
            try:
                capture.release()
            except Exception:
                pass
        try:
            close()
        except Exception:
            pass
    return frames_shown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="探测 U500 / 普通 USB 视频设备（默认 index 0～5）")
    parser.add_argument("--max-index", type=int, default=5, help="探测 0 到该 index，默认 5")
    parser.add_argument("--backend", type=int, default=None, help="可选 OpenCV VideoCapture backend 整数")
    parser.add_argument("--device-index", type=int, default=None, help="指定设备编号；预览默认 0")
    parser.add_argument("--preview", action="store_true", help="弹出实时画面窗口，按 Q 或 Esc 退出")
    parser.add_argument("--capture-test", action="store_true", help="用第一个可读设备保存一张PNG并运行质量检查")
    parser.add_argument("--output-root", default="data/raw_images/usb_probe", help="测试帧输出目录")
    parser.add_argument("--warmup-frames", type=int, default=5, help="capture-test 丢弃的预热帧数")
    args = parser.parse_args(argv)
    if args.max_index < 0:
        parser.error("--max-index 必须大于等于 0")
    if args.device_index is not None and args.device_index < 0:
        parser.error("--device-index 必须大于等于 0")

    if args.preview:
        index = 0 if args.device_index is None else args.device_index
        print(f"正在打开实时画面：device_index={index}。对焦窗口后按 Q 或 Esc 退出。")
        try:
            frames_shown = preview_device(index, backend=args.backend)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if frames_shown <= 0:
            print("预览没有读到帧。", file=sys.stderr)
            return 2
        print(f"预览结束：显示了 {frames_shown} 帧。")
        return 0

    results = tuple(probe_device(index, backend=args.backend) for index in range(args.max_index + 1))
    print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2, allow_nan=False))
    readable = [result for result in results if result.opened and result.frame_read]
    if not readable:
        print("没有发现可读 USB 视频设备；这不是成功接入证据。", file=sys.stderr)
        return 1

    if args.capture_test:
        if args.device_index is None:
            selected = readable[0]
        else:
            match = next((item for item in readable if item.device_index == args.device_index), None)
            if match is None:
                print(f"device_index={args.device_index} 不可读，无法 capture-test。", file=sys.stderr)
                return 2
            selected = match
        camera = USBCamera(
            USBCameraConfig(
                device_index=selected.device_index,
                output_root=args.output_root,
                warmup_frames=args.warmup_frames,
                backend=args.backend,
            )
        )
        try:
            observation = camera.capture("usb-camera-probe", "pre")
        except USBCameraError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        inspection = camera.inspection("pre")
        print(
            json.dumps(
                {
                    "capture_test": "success",
                    "device_index": selected.device_index,
                    "raw_image_ref": observation.raw_image_ref,
                    "focus_quality": observation.focus_quality,
                    "illumination_quality": observation.illumination_quality,
                    "confidence": observation.confidence,
                    "quality_flags": observation.quality_flags,
                    "width_px": inspection.metrics.width_px,
                    "height_px": inspection.metrics.height_px,
                    "sha256": inspection.sha256,
                    "evidence_boundary": "真实抓帧成功；不代表相机标定或硬件动作已获批准",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def _reported_fps(capture: Any, cv2: Any) -> float | None:
    try:
        value = float(capture.get(cv2.CAP_PROP_FPS))
    except Exception:
        return None
    return value if math.isfinite(value) and value > 0 else None


def _load_cv2() -> Any:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "相机探测需要OpenCV；请安装 requirements/perception-opencv.txt"
        ) from exc
    return cv2


if __name__ == "__main__":
    raise SystemExit(main())

