"""探测普通 USB 视频设备，并可用 USBCamera 保存一张质量检查测试帧。

本脚本只访问相机，不访问串口、STM32、泵、电机或控制系统。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from microcleaning.data_learning.usb_camera import USBCamera, USBCameraConfig, USBCameraError


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="探测 U500 / 普通 USB 视频设备（默认 index 0～5）")
    parser.add_argument("--max-index", type=int, default=5, help="探测 0 到该 index，默认 5")
    parser.add_argument("--backend", type=int, default=None, help="可选 OpenCV VideoCapture backend 整数")
    parser.add_argument("--capture-test", action="store_true", help="用第一个可读设备保存一张PNG并运行质量检查")
    parser.add_argument("--output-root", default="data/raw_images/usb_probe", help="测试帧输出目录")
    parser.add_argument("--warmup-frames", type=int, default=5, help="capture-test 丢弃的预热帧数")
    args = parser.parse_args(argv)
    if args.max_index < 0:
        parser.error("--max-index 必须大于等于 0")

    results = tuple(probe_device(index, backend=args.backend) for index in range(args.max_index + 1))
    print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2, allow_nan=False))
    readable = [result for result in results if result.opened and result.frame_read]
    if not readable:
        print("没有发现可读 USB 视频设备；这不是成功接入证据。", file=sys.stderr)
        return 1

    if args.capture_test:
        selected = readable[0]
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

