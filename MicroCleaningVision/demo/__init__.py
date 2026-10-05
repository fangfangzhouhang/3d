"""从原 Demo 抽取的 demo；保持既有单帧行为。"""

from __future__ import annotations

from typing import Any
from typing import Callable
from microcleaning.control_system.safety.motion_gate import MotionRequest


DEMO_VERSION = "microcleaning-demo-v0.2"


SIMULATION_CALIBRATION_VERSION = "simulation-normalized-v0"


DEMO_MODES = ("analyze", "simulate", "camera-analyze", "ping-only", "arm-pump", "stage2-move")


VISION_ALGORITHMS = ("hsv", "otsu", "exg", "exr", "local")


CaptureFactory = Callable[..., Any]


SerialFactory = Callable[[], Any]


MotionConfirm = Callable[[MotionRequest, dict], bool]
