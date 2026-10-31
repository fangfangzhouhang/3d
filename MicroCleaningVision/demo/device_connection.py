"""Workbench connection-only branch: camera and query whitelist, no actuator APIs."""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from microcleaning.control_system.orchestration.workbench_events import OperationCancelled
from microcleaning.control_system.serial.f103_session import F103SerialSession
from microcleaning.control_system.serial.resource_lease import ResourceLease

QUERY_REPLIES = {
    "HELLO": re.compile(r"STEP_OK v0\.3"),
    "READXY": re.compile(r"STEP2 X=\d+ BX=[01] Y=\d+ BY=[01]"),
    "MCV1|PING": re.compile(r"MCV1\|PONG"),
    "MCV1|STATUS": re.compile(r"MCV1\|STATUS\|ESTOP=[01]\|PUMP=[01]"),
}


class QueryOnlyConnection:
    """Only the exact four queries can reach this retained serial connection."""

    def __init__(self, *, port, baudrate=115200, timeout=2.0, cancellation=None, serial_factory=None):
        if (not isinstance(port, str) or not port.strip()
                or type(baudrate) is not int or baudrate <= 0
                or type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("CONNECTION_PARAMETERS_INVALID")
        self.__session = F103SerialSession(port=port, baudrate=baudrate, timeout=timeout,
            cancellation=cancellation, serial_factory=serial_factory)
        self.__lease = object()
        self.__cancel = cancellation
        self.__timeout = timeout

    @property
    def events(self):
        return self.__session.serial_events

    def query(self, command):
        # Validate before opening the device, including CR/LF and non-string input.
        if not isinstance(command, str) or command not in QUERY_REPLIES:
            raise PermissionError("CONNECTION_QUERY_NOT_ALLOWED")
        try:
            connection = self.__session.acquire("connection-only", self.__lease)
            connection.reset_input_buffer()
            data = (command + "\r\n").encode("ascii")
            if connection.write(data) != len(data):
                raise OSError("CONNECTION_QUERY_PARTIAL_WRITE")
            connection.flush()
            deadline = time.monotonic() + self.__timeout
            for _ in range(16):
                if self.__cancel is not None:
                    self.__cancel.check()
                if time.monotonic() >= deadline:
                    break
                raw = connection.readline()
                if len(raw) > 512:
                    raise ValueError("CONNECTION_REPLY_TOO_LONG")
                if raw and not raw.endswith((b"\r", b"\n")):
                    raise ValueError("CONNECTION_REPLY_INCOMPLETE")
                line = raw.decode("ascii").strip()
                if QUERY_REPLIES[command].fullmatch(line):
                    return line
            raise TimeoutError("CONNECTION_QUERY_REPLY_MISSING:" + command)
        except Exception:
            self.close()
            raise

    def close(self):
        # Deliberately only closes transport; no STOP, CLEAR, ARM or PUMP bytes.
        self.__session.close()


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _save_capture(folder, frame, segmenter, number):
    import cv2
    from demo.image_ops import _draw_contamination
    from microcleaning.data_learning.image_quality import inspect_image_file
    prefix = f"capture_{number:04d}"
    segmentation = segmenter(frame.image)
    images = {
        "raw": frame.image,
        "mask": segmentation.mask,
        "overlay": _draw_contamination(frame.image, segmentation.mask,
            segmentation.measurement.centroid_px, cv2),
    }
    references = {}
    for key, pixels in images.items():
        okay, encoded = cv2.imencode(".png", pixels)
        if not okay:
            raise OSError("CONNECTION_CAPTURE_ENCODE_FAILED")
        path = folder / f"{prefix}_{key}.png"
        raw = encoded.tobytes()
        with path.open("xb") as stream:
            stream.write(raw)
        references[key] = {"path": path.name, "sha256": hashlib.sha256(raw).hexdigest()}
    quality = inspect_image_file(folder / references["raw"]["path"])
    result = {
        "schema": "connection-capture-v1", "mode": "connection_only",
        "frame_id": frame.frame_id, "timestamp": frame.captured_at,
        "source_id": frame.source_id, "settings": frame.settings,
        "segmentation": segmenter.configuration, "segmentation_sha256": segmenter.sha256,
        "measurement": asdict(segmentation.measurement),
        "quality": asdict(quality.quality), "quality_flags": list(quality.quality.flags()),
        "files": references, "executable": False,
    }
    _write_json(folder / f"{prefix}.json", result)
    return result


def run_connection_task(runtime, metadata):
    """Own devices until cancel; never constructs a CleaningLoop or executor."""
    from demo.workbench_runtime import CameraBroker
    folder = runtime.args.output_root / f"connection_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    runtime.folder = folder.resolve()
    record = {
        "schema": "workbench-connection-v1", "mode": "connection_only",
        "status": "CONNECTING", "metadata": dict(metadata),
        "git_commit": runtime.git_commit, "git_dirty": runtime.git_dirty,
        "source_files_sha256": runtime.source_hashes,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "serial_port": runtime.args.serial_port, "camera_index": runtime.args.camera_index,
        "requested_camera_backend": runtime.args.camera_backend,
        "segmentation": runtime.segmenter.configuration, "segmentation_sha256": runtime.segmenter.sha256,
        "queries": {},
        "captures": [], "errors": [], "executable": False,
        "motion_armed": False, "pump_armed": False, "position_reference_established": False,
        "evidence_boundary": "Device queries and image capture only; no physical safety, calibration or cleaning acceptance.",
    }
    serial, lease, runtime.source = None, None, None
    try:
        folder.mkdir(parents=True)
        _write_json(folder / "connection.json", record)
        lease = ResourceLease(("com:" + runtime.args.serial_port.strip().casefold(),)).acquire()
        serial = QueryOnlyConnection(port=runtime.args.serial_port, baudrate=runtime.args.baudrate,
            timeout=runtime.args.serial_timeout, cancellation=runtime.cancel)
        for command in QUERY_REPLIES:
            reply = serial.query(command)
            record["queries"][command] = reply
            runtime.bus.emit("log", message=command + " → " + reply)
        runtime.bus.emit("connection_device", serial=runtime.args.serial_port + " 四项查询已响应",
            pump="未武装；" + record["queries"]["MCV1|STATUS"].removeprefix("MCV1|STATUS|"))
        record["serial_events"] = list(serial.events)
        runtime.source = CameraBroker(args=runtime.args, segmenter=runtime.segmenter,
            bus=runtime.bus, cancel=runtime.cancel)
        while not runtime.source.ready.wait(.05):
            runtime.cancel.check()
        if runtime.source.failure:
            raise runtime.source.failure
        record["status"] = "CONNECTED"
        _write_json(folder / "connection.json", record)
        runtime.bus.emit("log", message="真实设备已连接；空格只采图/识别。移动、喷水和归零均禁用。")
        while True:
            runtime.cancel.check()
            frame = runtime.source.capture("connection")
            result = _save_capture(folder, frame, runtime.segmenter, len(record["captures"]) + 1)
            record["captures"].append({key: result[key] for key in ("frame_id", "timestamp", "files", "quality_flags")})
            _write_json(folder / "connection.json", record)
            runtime.bus.emit("connection_capture", result=result)
    except OperationCancelled:
        record["status"] = "DISCONNECTED"
        record["disconnect_reason"] = runtime.cancel.reason
    except Exception as exc:
        record["status"] = "ERROR"
        record["errors"].append(str(exc) or type(exc).__name__)
        runtime.bus.emit("error", message=str(exc) or type(exc).__name__)
    finally:
        runtime.cancel.cancel("CONNECTION_CLOSED")
        for resource in (runtime.source, serial, lease):
            if resource is not None:
                try:
                    resource.close()
                except Exception as exc:
                    if resource is runtime.source and isinstance(resource.failure, OperationCancelled):
                        continue
                    record["errors"].append("CLOSE_FAILED:" + str(exc))
                    record["status"] = "ERROR"
        record["closed_at"] = datetime.now(timezone.utc).isoformat()
        record["serial_events"] = [] if serial is None else list(serial.events)
        runtime.last_status = "ERROR" if record["status"] == "ERROR" else "SUCCESS"
        try:
            _write_json(folder / "connection.json", record)
        except OSError as exc:
            runtime.last_status = "ERROR"
            runtime.bus.emit("error", message="连接记录保存失败：" + str(exc))
        finally:
            with runtime._guard:
                runtime._active = False
            runtime.bus.emit("connection_device", camera="已释放", serial="已断开",
                pump="未武装；仅连接模式")
            runtime.bus.emit("log", message="设备连接已收尾；记录：" + str(folder.resolve()))
            runtime.bus.emit("idle")
