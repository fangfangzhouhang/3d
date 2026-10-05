"""从原 Demo 抽取的 demo.image_ops；保持既有单帧行为。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from pathlib import Path
from typing import Any
from microcleaning.control_system.planning.cleaning_plan import CleaningPlan
from microcleaning.control_system.planning.cleaning_plan import cleaning_plan_to_dict
from microcleaning.control_system.planning.path_preview import build_path_preview
from microcleaning.control_system.planning.path_preview import draw_path_overlay
from microcleaning.vision.exg_baseline import segment_contamination as segment_exg
from microcleaning.vision.exg_baseline import segment_excess_red as segment_exr
from microcleaning.vision.hsv_baseline import read_bgr_image
from microcleaning.vision.hsv_baseline import segment_contamination as segment_hsv
from microcleaning.vision.local_contrast_baseline import segment_contamination as segment_local
from microcleaning.vision.otsu_baseline import segment_contamination as segment_otsu
from demo import CaptureFactory, VISION_ALGORITHMS


def _load_demo_image(
    *,
    input_path: str | Path | None,
    generate_sample: bool,
    from_camera: bool,
    run_id: str,
    run_dir: Path,
    camera_index: int,
    warmup_frames: int,
    camera_width: int | None,
    camera_height: int | None,
    camera_backend: int | None,
    capture_factory: CaptureFactory | None,
    cv2: Any,
    np: Any,
) -> tuple[Any, str, str]:
    if generate_sample:
        return _generate_sample(np, cv2), "program-generated-red-marker", "generated"
    if input_path is not None:
        source_path = Path(input_path)
        return read_bgr_image(source_path), str(source_path.resolve()), "file"
    from microcleaning.data_learning.usb_camera import USBCamera, USBCameraConfig, USBCameraError

    camera = USBCamera(
        USBCameraConfig(
            device_index=camera_index,
            output_root=run_dir / "camera",
            width=camera_width,
            height=camera_height,
            warmup_frames=warmup_frames,
            backend=camera_backend,
        ),
        capture_factory=capture_factory,
    )
    try:
        camera.capture(run_id, "pre")
    except USBCameraError:
        raise
    captured = camera.capture_path("pre")
    return read_bgr_image(captured), f"usb-camera:index={camera_index}", "camera"


def segment_demo_image(
    image,
    algorithm: str = "local",
    policy=None,
    *,
    policy_path: str | Path | None = None,
    use_tuned_policy: bool | None = None,
):
    """按 Demo 当前选择的 B 算法分割；默认是邻域差异 local。HSV 只作对照。"""

    if algorithm == "otsu":
        return segment_otsu(image)
    if algorithm == "hsv":
        return segment_hsv(image)
    if algorithm == "exg":
        return segment_exg(image)
    if algorithm == "exr":
        return segment_exr(image)
    if algorithm == "local":
        from microcleaning.vision.local_contrast_baseline import resolve_local_contrast_policy

        resolved = policy
        if resolved is None:
            resolved = resolve_local_contrast_policy(
                policy_path=policy_path,
                use_tuned_policy=use_tuned_policy,
            )
        if resolved is None:
            return segment_local(image)
        return segment_local(image, policy=resolved)
    raise ValueError(f"algorithm必须是{'/'.join(VISION_ALGORITHMS)}")


def _draw_contamination(image, mask, centroid, cv2):
    overlay = image.copy()
    colored = image.copy()
    colored[mask > 0] = (0, 190, 255)
    overlay = cv2.addWeighted(overlay, 0.72, colored, 0.28, 0)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(overlay, contours, -1, (0, 255, 255), 2)
    if centroid is not None:
        cv2.drawMarker(overlay, (round(centroid[0]), round(centroid[1])), (255, 0, 0), cv2.MARKER_CROSS, 18, 2)
    return overlay


def _draw_plan(image, plan: CleaningPlan, cv2):
    preview = build_path_preview(plan)
    return draw_path_overlay(image, preview)


def _plan_as_dict(plan: CleaningPlan) -> dict[str, object]:
    return cleaning_plan_to_dict(plan)


def _generate_sample(np, cv2):
    rng = np.random.default_rng(20260820)
    image = np.full((360, 520, 3), (208, 214, 220), dtype=np.uint8)
    texture = rng.normal(0, 5, image.shape[:2]).astype(np.int16)
    for channel in range(3):
        image[:, :, channel] = np.clip(image[:, :, channel].astype(np.int16) + texture, 0, 255).astype(np.uint8)
    cv2.ellipse(image, (250, 178), (92, 54), -12, 0, 360, (18, 28, 225), -1)
    cv2.circle(image, (382, 248), 27, (12, 20, 210), -1)
    return image


def _load_dependencies():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "Demo需要NumPy/OpenCV；请安装requirements/perception-opencv.txt"
        ) from exc
    return cv2, np


@dataclass(frozen=True, init=False)
class FrozenSegmenter:
    """启动时解析一次策略；前后图调用同一算法、同一不可变策略对象。"""
    algorithm: str
    policy: object
    function: object

    def __init__(self, algorithm="local", *, policy_path=None, use_tuned_policy=None):
        if policy_path is not None and algorithm != "local":
            raise ValueError("--policy 仅支持已有 local 策略格式")
        if algorithm == "local":
            from microcleaning.vision.local_contrast_baseline import LocalContrastPolicy, resolve_local_contrast_policy
            policy = resolve_local_contrast_policy(policy_path=policy_path, use_tuned_policy=use_tuned_policy) or LocalContrastPolicy()
            function = segment_local
        elif algorithm == "hsv":
            from microcleaning.vision.hsv_baseline import HSVSegmentationPolicy
            policy, function = HSVSegmentationPolicy(), segment_hsv
        elif algorithm == "otsu":
            from microcleaning.vision.otsu_baseline import OtsuSegmentationPolicy
            policy, function = OtsuSegmentationPolicy(), segment_otsu
        elif algorithm in {"exg", "exr"}:
            from microcleaning.vision.exg_baseline import ColorIndexPolicy
            policy = ColorIndexPolicy(index=algorithm)
            function = segment_exg if algorithm == "exg" else segment_exr
        else:
            raise ValueError("UNKNOWN_SEGMENTATION_ALGORITHM")
        policy.validate()
        object.__setattr__(self, "algorithm", algorithm)
        object.__setattr__(self, "policy", policy)
        object.__setattr__(self, "function", function)

    @property
    def configuration(self):
        return {"algorithm": self.algorithm, "implementation": self.function.__module__,
                "policy_type": type(self.policy).__name__, "policy": asdict(self.policy)}

    @property
    def sha256(self):
        return hashlib.sha256(json.dumps(self.configuration, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def __call__(self, image):
        return self.function(image, policy=self.policy)
