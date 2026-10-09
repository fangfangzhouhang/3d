"""可直接从 CLI 运行的 Mock：只替代相机像素来源和物理串口。"""

from datetime import datetime, timezone
from math import hypot
from threading import RLock

from microcleaning.control_system.orchestration.cleaning_loop import CapturedFrame
from microcleaning.control_system.planning.stage2_geometry import NozzleOffset


SCENARIOS = ("success", "retry", "no-target", "decline", "motion-short", "motion-timeout",
             "pump-timeout", "return-timeout", "wrong-protocol", "post-quality", "noncomparable", "stale-post", "raster",
             "workbench-mixed", "workbench-residual", "workbench-unmatched")


class MockFrames:
    def __init__(self, scenario="success"):
        import cv2
        import numpy as np
        self.cv2, self.np, self.scenario = cv2, np, scenario
        texture = np.random.default_rng(20261005).normal(0, 2.5, (240, 320)).round()
        gray = np.clip(140 + texture, 0, 255).astype(np.uint8)
        self.background = np.repeat(gray[:, :, None], 3, axis=2)
        self.centers = ((120, 90), (200, 110), (150, 170)) if scenario != "raster" else ((120, 90),)
        self.remaining = set(range(len(self.centers))) if scenario != "no-target" else set()
        self.selected = None
        self.sequence = 0
        self.pumps = 0
        self.closed = False
        self.capture_phases = []
        self._lock = RLock()
        self.residual = {}

    def select_target(self, target):
        with self._lock:
            choices = self.remaining or set(range(len(self.centers)))
            self.selected = min(choices, key=lambda index: hypot(self.centers[index][0] - target.centroid_px[0], self.centers[index][1] - target.centroid_px[1]))

    def pump_finished(self):
        with self._lock:
            self.pumps += 1
            if self.scenario == "workbench-residual" or (self.scenario == "workbench-mixed" and self.selected == 1):
                self.residual[self.selected] = 5
            elif self.scenario == "workbench-unmatched":
                self.residual[self.selected] = 1
            elif self.scenario != "retry" or self.pumps != 1:
                self.remaining.discard(self.selected)

    def preview(self):
        with self._lock:
            image = self.background.copy()
            for index in self.remaining:
                self.cv2.circle(image, self.centers[index], self.residual.get(index, 7 if self.scenario != "raster" else 52), (18, 18, 205), -1)
            return image

    def capture(self, phase, *, after=None):
        with self._lock:
            return self._capture(phase, after=after)

    def _capture(self, phase, *, after=None):
        self.sequence += 1
        self.capture_phases.append(phase)
        image = self.preview()
        if phase == "post" and self.scenario == "post-quality":
            image[:] = 0
        settings = {"mock_only": True, "fixed_synthetic_view": True, "size": [240, 320]}
        if phase == "post" and self.scenario == "noncomparable":
            settings["size"] = [240, 319]
        captured_at = datetime.now(timezone.utc).isoformat()
        if phase == "post" and self.scenario == "stale-post":
            captured_at = "2020-01-01T00:00:00+00:00"
        return CapturedFrame(image, f"mock_{self.sequence}", captured_at, "mock-fixed-view", settings)

    def close(self):
        self.closed = True


class MockF103Serial:
    """严格回复原协议；计数假设不代表真实位移或硬件验收。"""
    def __init__(self, frames: MockFrames, scenario="success"):
        self.frames, self.scenario = frames, scenario
        self.writes, self._queue = [], []
        self.counts, self.pending, self.position = (0, 0), (0, 0), (0, 0)
        self.pump_count, self.opens, self.close_count = 0, 0, 0
        self.closed = False

    def factory(self):
        self.opens += 1
        return self

    def reset_input_buffer(self):
        self._queue.clear()

    def write(self, data):
        self.writes.append(bytes(data))
        text = data.decode("ascii").strip()
        if text == "HELLO":
            self._queue.append(b"MCV1|DONE|foreign\n" if self.scenario == "wrong-protocol" else b"STEP_OK v0.3\r\n")
        elif text.startswith("MOVEXY "):
            _, nx, dx, ny, dy = text.split()
            x, y = int(nx), int(ny)
            self.counts = (x or self.counts[0], y or self.counts[1])
            self.pending = (x if dx == "FWD" else -x, y if dy == "FWD" else -y)
            self._queue.append(f"STEP2_START X={nx} {dx} Y={ny} {dy}\r\n".encode("ascii"))
        elif text == "READXY":
            if self.scenario == "motion-timeout" or (self.scenario == "return-timeout" and self.pump_count):
                return len(data)
            x, y = self.counts
            if self.scenario == "motion-short":
                x, y = max(0, x - 1), max(0, y - 1)
            self.position = tuple(a + b for a, b in zip(self.position, self.pending))
            self.pending = (0, 0)
            self._queue.append(f"STEP2 X={x} BX=0 Y={y} BY=0\r\n".encode("ascii"))
        elif text == "STOP":
            self.pending = (0, 0)
            self._queue.append(b"STEP_STOPPED\r\n")
        elif text == "MCV1|PING":
            self._queue.append(b"MCV1|PONG\n")
        elif text == "MCV1|STATUS":
            self._queue.append(b"MCV1|STATUS|ESTOP=0|PUMP=0\n")
        elif text == "MCV1|STOP":
            if self.scenario == "pump-timeout":
                self._queue.append(b"MCV1|ERR|interrupted|STOPPED\n")
            self._queue.extend((b"MCV1|ACK|STOP\n", b"MCV1|DONE|STOP\n"))
        elif text.startswith("MCV1|PUMP|"):
            self.pump_count += 1
            if self.scenario != "pump-timeout":
                action_id = text.split("|")[2]
                self._queue.extend((f"MCV1|ACK|{action_id}\n".encode(), f"MCV1|DONE|{action_id}\n".encode()))
                self.frames.pump_finished()
        else:
            raise AssertionError(f"Mock 不允许未知命令：{text}")
        return len(data)

    def flush(self):
        pass

    def readline(self):
        return self._queue.pop(0) if self._queue else b""

    def close(self):
        self.closed = True
        self.close_count += 1


def mock_offset():
    return NozzleOffset("mock-offset-v1", "synthetic-fixed-stage", (64, -32),
        axes_confirmed=True, calibration_source="synthetic fixture declaration",
        uncertainty_steps=(0.0, 0.0), mock_only=True)
