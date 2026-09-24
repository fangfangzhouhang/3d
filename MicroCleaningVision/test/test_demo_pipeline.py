"""Demo入口的最小端到端验收。"""

import importlib.util
import json
import tempfile
import unittest


HAS_PERCEPTION_DEPS = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


class FakeVideoCapture:
    def __init__(self, *, opened: bool = True, reads=None) -> None:
        self.opened = opened
        self.reads = list(reads or [])
        self.read_index = 0
        self.released = False

    def isOpened(self) -> bool:
        return self.opened

    def read(self):
        if not self.reads:
            return False, None
        index = min(self.read_index, len(self.reads) - 1)
        self.read_index += 1
        return self.reads[index]

    def release(self) -> None:
        self.released = True


class ScriptedNucleoSerial:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self._queue: list[bytes] = []

    def reset_input_buffer(self) -> None:
        return None

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        line = data.decode("ascii").strip()
        parts = line.split("|")
        kind = parts[1] if len(parts) > 1 else ""
        if kind == "PING":
            self._queue.append(b"MCV1|PONG\n")
        elif kind == "STATUS":
            self._queue.append(b"MCV1|STATUS|ESTOP=0|PUMP=0\n")
        return len(data)

    def flush(self) -> None:
        return None

    def readline(self) -> bytes:
        if not self._queue:
            return b""
        return self._queue.pop(0)

    def close(self) -> None:
        return None


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class DemoPipelineTests(unittest.TestCase):
    def test_simulation_mode_produces_visible_artifacts_and_action_evidence(self):
        from demo.demo_pipeline import run_demo

        with tempfile.TemporaryDirectory() as folder:
            run_dir = run_demo(generate_sample=True, mode="simulate", output_root=folder)
            for name in (
                "input.png",
                "mask.png",
                "contamination_overlay.png",
                "path_overlay.png",
                "post_mask.png",
                "summary.json",
            ):
                self.assertTrue((run_dir / name).is_file(), name)
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertGreater(summary["contamination"]["area_px"], 0)
            self.assertGreater(len(summary["cleaning_plan"]["path_px"]), 0)
            self.assertIsNotNone(summary["action_request"])
            self.assertEqual("fake_serial", summary["execution_receipt"]["mode"])

    def test_analysis_mode_never_invents_hardware_action(self):
        from demo.demo_pipeline import run_demo

        with tempfile.TemporaryDirectory() as folder:
            run_dir = run_demo(generate_sample=True, mode="analyze", output_root=folder)
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual("image_px", summary["state"]["coordinate_frame"])
            self.assertIsNone(summary["state"]["target_centroid_mm"])
            self.assertIsNone(summary["action_request"])
            self.assertIn("不执行动作", summary["evidence_boundary"])
            self.assertEqual("local", summary["vision_algorithm"])
            self.assertFalse(summary["path_preview"]["feeds_action_request"])
            self.assertFalse(summary["path_preview"]["send_to_controller"])
            self.assertTrue(summary["path_preview"]["narrative"])
            self.assertTrue((run_dir / "path_narrative.txt").is_file())

    def test_exg_analyze_still_does_not_send_pump(self):
        from demo.demo_pipeline import run_demo

        with tempfile.TemporaryDirectory() as folder:
            run_dir = run_demo(
                generate_sample=True,
                mode="analyze",
                algorithm="exg",
                output_root=folder,
            )
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual("exg", summary["vision_algorithm"])
            self.assertIsNone(summary["action_request"])
            self.assertIsNone(summary["execution_receipt"])

    def test_local_analyze_still_does_not_send_pump(self):
        from demo.demo_pipeline import run_demo

        with tempfile.TemporaryDirectory() as folder:
            run_dir = run_demo(
                generate_sample=True,
                mode="analyze",
                algorithm="local",
                output_root=folder,
            )
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual("local", summary["vision_algorithm"])
            self.assertIsNone(summary["action_request"])
            self.assertIsNone(summary["execution_receipt"])
            self.assertGreater(summary["contamination"]["area_px"], 0)

    def test_from_camera_with_fake_capture_does_not_send_pump(self):
        import cv2
        import numpy as np

        from demo.demo_pipeline import run_demo
        from microcleaning.data_learning.usb_camera import USBCameraError

        frame = np.full((120, 160, 3), 210, dtype=np.uint8)
        cv2.circle(frame, (80, 60), 18, (0, 0, 230), -1)
        fake = FakeVideoCapture(reads=[(True, frame)])
        with tempfile.TemporaryDirectory() as folder:
            run_dir = run_demo(
                from_camera=True,
                mode="camera-analyze",
                output_root=folder,
                warmup_frames=2,
                capture_factory=lambda *args: fake,
            )
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual("camera", summary["source_kind"])
            self.assertEqual("camera-analyze", summary["mode"])
            self.assertIsNone(summary["action_request"])
            self.assertIsNone(summary["execution_receipt"])
            self.assertTrue((run_dir / "mask.png").is_file())
            self.assertTrue((run_dir / "path_overlay.png").is_file())
            self.assertGreater(summary["contamination"]["area_px"], 0)
            self.assertTrue(fake.released)
            self.assertIn("NO_PUMP_SENT", summary["verification"]["reason_codes"])

        missing = FakeVideoCapture(opened=False)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(USBCameraError) as caught:
                run_demo(
                    from_camera=True,
                    mode="camera-analyze",
                    output_root=folder,
                    capture_factory=lambda *args: missing,
                )
            self.assertEqual("CAMERA_OPEN_FAILED", caught.exception.reason_code)

    def test_arm_pump_requires_human_gate_and_fake_serial_can_execute(self):
        from demo.demo_pipeline import run_demo

        with tempfile.TemporaryDirectory() as folder:
            blocked = run_demo(
                generate_sample=True,
                mode="arm-pump",
                confirm_pump=False,
                controller_kind="fake",
                output_root=folder,
            )
            blocked_summary = json.loads((blocked / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual("PUMP_IN_PLACE", blocked_summary["action_request"]["primitive"])
            self.assertEqual("nozzle_fixed", blocked_summary["action_request"]["coordinate_frame"])
            self.assertEqual([0.0, 0.0], blocked_summary["action_request"]["target_centroid_mm"])
            self.assertEqual("HUMAN", blocked_summary["safety_decision"]["outcome"])
            self.assertIsNone(blocked_summary["execution_receipt"])
            self.assertIn("未发送PUMP", blocked_summary["evidence_boundary"])

        with tempfile.TemporaryDirectory() as folder:
            executed = run_demo(
                generate_sample=True,
                mode="arm-pump",
                confirm_pump=True,
                controller_kind="fake",
                output_root=folder,
            )
            executed_summary = json.loads((executed / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual("ALLOW", executed_summary["safety_decision"]["outcome"])
            self.assertEqual("fake_serial", executed_summary["execution_receipt"]["mode"])
            self.assertTrue(executed_summary["execution_receipt"]["success"])
            self.assertEqual([0.0, 0.0], executed_summary["action_request"]["target_centroid_mm"])
            self.assertFalse(executed_summary["state"]["calibration_valid"])

    def test_ping_only_and_unarmed_stm32_never_send_pump(self):
        from demo.demo_pipeline import run_demo

        with tempfile.TemporaryDirectory() as folder:
            ping_dir = run_demo(
                generate_sample=True,
                mode="ping-only",
                output_root=folder,
            )
            ping_summary = json.loads((ping_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertIsNone(ping_summary["action_request"])
            self.assertFalse(ping_summary["serial_probe"]["opened"])
            self.assertEqual("SERIAL_NOT_OPENED", ping_summary["serial_probe"]["reason"])
            self.assertIn("NO_PUMP_SENT", ping_summary["verification"]["reason_codes"])

        serial = ScriptedNucleoSerial()
        with tempfile.TemporaryDirectory() as folder:
            run_dir = run_demo(
                generate_sample=True,
                mode="arm-pump",
                confirm_pump=True,
                controller_kind="stm32",
                arm_pump=False,
                serial_factory=lambda: serial,
                output_root=folder,
            )
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual("ALLOW", summary["safety_decision"]["outcome"])
            self.assertIsNone(summary["execution_receipt"])
            self.assertIn("PUMP_REFUSED", summary["verification"]["reason_codes"])
            self.assertFalse(any(item.startswith(b"MCV1|PUMP|") for item in serial.writes))

    def test_rejects_multiple_or_missing_sources(self):
        from demo.demo_pipeline import run_demo

        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                run_demo(generate_sample=True, from_camera=True, output_root=folder)
            with self.assertRaises(ValueError):
                run_demo(output_root=folder)
            with self.assertRaises(ValueError):
                run_demo(generate_sample=True, mode="camera-analyze", output_root=folder)


if __name__ == "__main__":
    unittest.main()
