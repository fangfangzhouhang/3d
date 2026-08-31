"""通用 USB 视频设备相机适配器（成员 A）。

U500 当前只确认“USB 连接”，没有证据证明存在专用厂商 SDK。因此本适配器按普通
OpenCV ``VideoCapture`` 设备接入。它只采集和保存真实像素、计算图像质量并返回
标准 ``Observation``；不会调用 B/C，也不会打开串口或产生硬件动作。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from microcleaning.contracts import Observation
from microcleaning.data_learning.image_quality import (
    QUALITY_ALGORITHM_VERSION,
    ImageInspection,
    ImageQualityPolicy,
    build_observation,
    inspect_image_file,
)
from microcleaning.ports import CameraPort


USB_CAMERA_ADAPTER_VERSION = "usb-camera-v0.1"

CAMERA_OPEN_FAILED = "CAMERA_OPEN_FAILED"
FRAME_READ_FAILED = "FRAME_READ_FAILED"
EMPTY_FRAME = "EMPTY_FRAME"
OUTPUT_WRITE_FAILED = "OUTPUT_WRITE_FAILED"


class USBCameraError(RuntimeError):
    """带机器可读 reason_code 的 USB 相机采集错误。"""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {message}")


@dataclass(frozen=True)
class USBCameraConfig:
    """USB 相机的最小显式配置，不包含未经验证的 U500 专有参数。"""

    device_index: int = 0
    output_root: str | Path = Path("data/raw_images/usb_camera")
    width: int | None = None
    height: int | None = None
    warmup_frames: int = 5
    backend: int | None = None

    def validate(self) -> None:
        if isinstance(self.device_index, bool) or not isinstance(self.device_index, int) or self.device_index < 0:
            raise ValueError("device_index 必须是大于等于 0 的整数")
        for name, value in (("width", self.width), ("height", self.height)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
                raise ValueError(f"{name} 必须是正整数或 None")
        if isinstance(self.warmup_frames, bool) or not isinstance(self.warmup_frames, int) or self.warmup_frames < 0:
            raise ValueError("warmup_frames 必须是大于等于 0 的整数")
        if self.backend is not None and (isinstance(self.backend, bool) or not isinstance(self.backend, int)):
            raise ValueError("backend 必须是 OpenCV backend 整数或 None")


CaptureFactory = Callable[..., Any]
ImageWriter = Callable[[Path, Any], None]


class USBCamera(CameraPort):
    """从普通 USB 视频设备抓取一帧并返回统一 Observation。"""

    def __init__(
        self,
        config: USBCameraConfig = USBCameraConfig(),
        *,
        quality_policy: ImageQualityPolicy = ImageQualityPolicy(),
        capture_factory: CaptureFactory | None = None,
        image_writer: ImageWriter | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self._quality_policy = quality_policy
        self._capture_factory = capture_factory
        self._image_writer = image_writer or _write_png
        self._inspections: dict[str, ImageInspection] = {}
        self._capture_paths: dict[str, Path] = {}

    def capture(self, task_id: str, phase: str) -> Observation:
        """打开设备、预热、抓帧、释放相机，再保存和构造 Observation。"""

        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id 不能为空")
        if not isinstance(phase, str) or not phase.strip():
            raise ValueError("phase 不能为空")

        capture = self._open_capture()
        frame: Any = None
        try:
            if capture is None or not _capture_is_open(capture, device_index=self.config.device_index):
                raise USBCameraError(
                    CAMERA_OPEN_FAILED,
                    f"无法打开 USB 相机 device_index={self.config.device_index}",
                )
            self._apply_requested_resolution(capture)
            for warmup_index in range(self.config.warmup_frames):
                _read_capture_frame(capture, context=f"第 {warmup_index + 1} 个 warm-up frame")

            frame = _read_capture_frame(capture, context="目标帧")
        finally:
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    # release 不能覆盖更早、更有价值的采集错误。
                    pass

        output_path = self._build_output_path(task_id=task_id, phase=phase)
        try:
            self._image_writer(output_path, frame)
        except USBCameraError:
            raise
        except Exception as exc:
            raise USBCameraError(
                OUTPUT_WRITE_FAILED,
                f"无法保存采集帧：{output_path}",
            ) from exc

        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise USBCameraError(OUTPUT_WRITE_FAILED, f"输出文件没有成功生成：{output_path}")

        inspection = inspect_image_file(output_path, policy=self._quality_policy)
        self._inspections[phase] = inspection
        self._capture_paths[phase] = output_path
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        return build_observation(
            task_id=task_id,
            frame_id=f"{_safe_component(phase)}-{timestamp}",
            raw_image_ref=_portable_path_reference(output_path),
            quality=inspection.quality,
            software_version=f"{USB_CAMERA_ADAPTER_VERSION}/{QUALITY_ALGORITHM_VERSION}",
        )

    def inspection(self, phase: str) -> ImageInspection:
        """返回该阶段最近一次真实像素质量检查。"""

        try:
            return self._inspections[phase]
        except KeyError as exc:
            raise KeyError(f"{phase!r} 尚未完成 USB 相机采集") from exc

    def capture_path(self, phase: str) -> Path:
        """返回该阶段最近一次成功保存的原始 PNG。"""

        try:
            return self._capture_paths[phase]
        except KeyError as exc:
            raise KeyError(f"{phase!r} 尚未完成 USB 相机采集") from exc

    def _open_capture(self) -> Any:
        factory = self._capture_factory
        if factory is None:
            cv2, _ = _load_perception_dependencies()
            factory = cv2.VideoCapture
        try:
            if self.config.backend is None:
                return factory(self.config.device_index)
            return factory(self.config.device_index, self.config.backend)
        except Exception as exc:
            raise USBCameraError(
                CAMERA_OPEN_FAILED,
                f"创建 VideoCapture 失败：device_index={self.config.device_index}",
            ) from exc

    def _apply_requested_resolution(self, capture: Any) -> None:
        cv2, _ = _load_perception_dependencies()
        if self.config.width is not None:
            try:
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.config.width))
            except Exception:
                pass
        if self.config.height is not None:
            try:
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.config.height))
            except Exception:
                pass

    def _build_output_path(self, *, task_id: str, phase: str) -> Path:
        output_root = Path(self.config.output_root).resolve()
        task_folder = output_root / _safe_component(task_id)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        filename = f"{_safe_component(phase)}_{timestamp}_{uuid4().hex[:8]}.png"
        return task_folder / filename


def _write_png(output_path: Path, frame: Any) -> None:
    cv2, _ = _load_perception_dependencies()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(".png", frame)
    if not success:
        raise USBCameraError(OUTPUT_WRITE_FAILED, f"OpenCV 无法编码 PNG：{output_path}")
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        temporary_path.write_bytes(encoded.tobytes())
        temporary_path.replace(output_path)
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        raise USBCameraError(OUTPUT_WRITE_FAILED, f"无法写入 PNG：{output_path}") from exc


def _is_empty_frame(frame: Any) -> bool:
    return frame is None or not hasattr(frame, "size") or int(frame.size) <= 0


def _capture_is_open(capture: Any, *, device_index: int) -> bool:
    try:
        return bool(capture.isOpened())
    except Exception as exc:
        raise USBCameraError(
            CAMERA_OPEN_FAILED,
            f"查询相机打开状态失败：device_index={device_index}",
        ) from exc


def _read_capture_frame(capture: Any, *, context: str) -> Any:
    try:
        ok, frame = capture.read()
    except Exception as exc:
        raise USBCameraError(FRAME_READ_FAILED, f"{context} 读取时驱动抛出异常") from exc
    if not ok:
        raise USBCameraError(FRAME_READ_FAILED, f"{context} 读取失败")
    if _is_empty_frame(frame):
        raise USBCameraError(EMPTY_FRAME, f"{context} 为空")
    return frame


def _safe_component(value: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
    return safe.strip("._") or "unnamed"


def _portable_path_reference(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _load_perception_dependencies() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "USB相机采集需要感知依赖；请在项目 .venv 安装 "
            "requirements/perception-opencv.txt"
        ) from exc
    return cv2, np
