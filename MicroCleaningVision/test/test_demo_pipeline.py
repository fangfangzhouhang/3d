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
    def test_closed_loop_policy_is_parsed_once_even_if_file_changes(self):
        from pathlib import Path
        from demo.image_ops import FrozenSegmenter
        import cv2
        import numpy as np
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            path.write_text('{"min_residual": 8.0}', encoding="utf-8")
            segmenter = FrozenSegmenter("local", policy_path=path)
            image = np.full((80, 100, 3), 140, dtype=np.uint8)
            cv2.circle(image, (30, 25), 7, (18, 18, 200), -1)
            before, digest = segmenter(image), segmenter.sha256
            path.write_text('{"min_residual": 9999.0}', encoding="utf-8")
            after = segmenter(image)
            self.assertEqual(digest, segmenter.sha256)
            self.assertTrue(np.array_equal(before.mask, after.mask))
            self.assertGreater(after.measurement.area_px, 0)
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


class ScriptedStage2Port:
    """Stage 2 v0.3 假板子；silent_readxy=True 时 READXY 不回，模拟中途断线。"""

    def __init__(self, *, silent_readxy: bool = False) -> None:
        self.writes: list[bytes] = []
        self.silent_readxy = silent_readxy
        self._queue: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        text = data.decode("ascii").strip()
        if text == "HELLO":
            self._queue.append(b"STEP_OK v0.3\r\n")
        elif text.startswith("MOVEXY "):
            _cmd, nx, dx, ny, dy = text.split()
            old_x, old_y = getattr(self, "_counts", (0, 0))
            self._counts = (int(nx) or old_x, int(ny) or old_y)
            self._queue.append(f"STEP2_START X={nx} {dx} Y={ny} {dy}\r\n".encode("ascii"))
        elif text == "READXY" and not self.silent_readxy:
            self._queue.append(f"STEP2 X={self._counts[0]} BX=0 Y={self._counts[1]} BY=0\r\n".encode("ascii"))
        elif text == "STOP":
            self._queue.append(b"STEP_STOPPED\r\n")
        return len(data)

    def readline(self) -> bytes:
        return self._queue.pop(0) if self._queue else b""

    def close(self) -> None:
        return None


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class DemoStage2MotionTests(unittest.TestCase):
    """步进只能在 stage2-move 里、过运动关卡并经人确认后发送；任何结果都留记录。"""

    def setUp(self):
        from pathlib import Path

        self._folder = tempfile.TemporaryDirectory()
        self.root = Path(self._folder.name)
        self.calibration = self.root / "mm_per_px.json"
        self.calibration.write_text(
            json.dumps({"mm_per_px_x": 0.0187, "mm_per_px_y": 0.0181, "warnings": [], "feeds_action_request": False}),
            encoding="utf-8",
        )
        self.position = self.root / "stage2" / "position.json"
        self.opened = 0

    def tearDown(self):
        self._folder.cleanup()

    def _factory(self, port):
        def factory():
            self.opened += 1
            return port

        return factory

    def _run(self, port=None, **overrides):
        from demo.demo_pipeline import run_demo

        options = dict(
            generate_sample=True,
            mode="stage2-move",
            output_root=self.root / "out",
            stage2_calibration=self.calibration,
            stage2_position_path=self.position,
            arm_stage2_xy=True,
            serial_port="COM9",
            serial_factory=self._factory(port or ScriptedStage2Port()),
        )
        options.update(overrides)
        return run_demo(**options)

    def _summary(self, run_dir):
        return json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))

    def _zero(self):
        from microcleaning.control_system.planning.stage2_position import set_zero

        set_zero(self.position)

    def test_analyze_cannot_arm_stage2(self):
        with self.assertRaises(ValueError):
            self._run(mode="analyze", motion_confirm=lambda request, plan: True)
        self.assertEqual(0, self.opened)

    def test_analyze_with_stage2_xy_only_writes_text_from_image_center(self):
        run_dir = self._run(mode="analyze", stage2_xy=True, arm_stage2_xy=False)
        summary = self._summary(run_dir)
        self.assertEqual(0, self.opened)
        self.assertTrue((run_dir / "stage2_xy_pulses.txt").is_file())
        self.assertFalse((run_dir / "stage2_intent.json").exists())
        self.assertIsNone(summary["stage2_motion"])
        self.assertEqual(0, summary["hardware_actions"]["stage2_lines_sent"])
        work = summary["path_preview"]["placeholders"]["work"]
        self.assertEqual([260.0, 180.0], work["origin_px"])
        self.assertAlmostEqual(0.0181, work["effective_mm_per_px_y"])

    def test_without_confirmation_serial_is_never_opened(self):
        self._zero()
        run_dir = self._run(motion_confirm=None)
        summary = self._summary(run_dir)
        self.assertEqual(0, self.opened)
        self.assertEqual("human_pending", summary["stage2_motion"]["status"])
        self.assertTrue((run_dir / "stage2_intent.json").is_file())
        self.assertFalse((run_dir / "stage2_receipt.json").exists())
        self.assertIn("NO_MOTION_SENT", summary["verification"]["reason_codes"])

    def test_declined_confirmation_never_opens_serial(self):
        self._zero()
        run_dir = self._run(motion_confirm=lambda request, plan: False)
        self.assertEqual("human_declined", self._summary(run_dir)["stage2_motion"]["status"])
        self.assertEqual(0, self.opened)

    def test_unknown_position_is_denied_before_asking_human(self):
        asked = []
        run_dir = self._run(motion_confirm=lambda request, plan: asked.append(1) or True)
        motion = self._summary(run_dir)["stage2_motion"]
        self.assertEqual("denied", motion["status"])
        self.assertIn("POSITION_UNKNOWN", motion["final_decision"]["reason_codes"])
        self.assertEqual([], asked)
        self.assertEqual(0, self.opened)

    def test_missing_calibration_is_denied(self):
        self._zero()
        run_dir = self._run(stage2_calibration=None, motion_confirm=lambda request, plan: True)
        motion = self._summary(run_dir)["stage2_motion"]
        self.assertEqual("denied", motion["status"])
        self.assertIn("CALIBRATION_MISSING", motion["final_decision"]["reason_codes"])
        self.assertEqual(0, self.opened)

    def test_confirmed_motion_is_sent_and_recorded(self):
        from microcleaning.control_system.planning.stage2_axes import parse_movexy_line
        from microcleaning.control_system.planning.stage2_position import load_position

        self._zero()
        port = ScriptedStage2Port()
        seen = {}

        def confirm(request, plan):
            seen["plan"] = plan
            return True

        run_dir = self._run(port, motion_confirm=confirm)
        summary = self._summary(run_dir)
        lines = summary["stage2_dispatch"]["lines"]
        self.assertTrue(lines)
        self.assertEqual(1, self.opened)
        self.assertEqual("sent", summary["stage2_motion"]["status"])
        self.assertEqual("ALLOW", summary["stage2_motion"]["final_decision"]["outcome"])
        self.assertEqual(len(lines), summary["hardware_actions"]["stage2_lines_sent"])
        self.assertTrue((run_dir / "stage2_receipt.json").is_file())
        written = b"".join(port.writes)
        self.assertNotIn(b"PUMP", written)
        self.assertNotIn(b"MCV1", written)
        expected = [sum(parse_movexy_line(line)[axis] for line in lines) for axis in (0, 1)]
        self.assertEqual(tuple(expected), load_position(self.position).xy())
        self.assertEqual(list(expected), seen["plan"]["position_after"])
        episode = json.loads((run_dir / summary["episode_file"]).read_text(encoding="utf-8"))
        self.assertEqual("stage2_motion_sent", episode["mode"])
        self.assertIn("RECEIPT_IS_NOT_POSITION_PROOF", episode["verification"]["reason_codes"])

    def test_timeout_mid_move_is_recorded_stopped_and_position_unknown(self):
        from demo.demo_pipeline import Stage2MotionFailed
        from microcleaning.control_system.planning.stage2_position import load_position

        self._zero()
        port = ScriptedStage2Port(silent_readxy=True)
        with self.assertRaises(Stage2MotionFailed) as caught:
            self._run(port, motion_confirm=lambda request, plan: True)
        run_dir = caught.exception.run_dir
        summary = self._summary(run_dir)
        self.assertEqual("failed", summary["stage2_motion"]["status"])
        self.assertTrue(summary["hardware_actions"]["stage2_stopped"])
        self.assertIsNotNone(summary["hardware_actions"]["stage2_in_flight_line"])
        receipt = json.loads((run_dir / "stage2_receipt.json").read_text(encoding="utf-8"))
        self.assertEqual("TIMEOUT", receipt["error"]["reason_code"])
        self.assertIn(b"STOP\r\n", port.writes)
        self.assertFalse(load_position(self.position).known)

    def test_step_cap_cannot_be_raised(self):
        from microcleaning.control_system.safety.motion_gate import STAGE2_RUN_STEP_CAP
        with self.assertRaises(ValueError):
            self._run(stage2_max_steps=STAGE2_RUN_STEP_CAP + 1, motion_confirm=lambda request, plan: True)
        self.assertEqual(0, self.opened)

    def test_cli_rejects_arming_stage2_outside_stage2_move(self):
        from contextlib import redirect_stderr
        from io import StringIO

        from demo.demo_pipeline import main
        from microcleaning.control_system.safety.motion_gate import STAGE2_RUN_STEP_CAP

        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                main(["--generate-sample", "--mode", "analyze", "--stage2-xy", "--arm-stage2-xy", "--serial-port", "COM9"])
            with self.assertRaises(SystemExit):
                main(["--generate-sample", "--mode", "stage2-move", "--arm-stage2-xy", "--serial-port", "COM9"])
            with self.assertRaises(SystemExit):
                main(["--generate-sample", "--stage2-max-steps", str(STAGE2_RUN_STEP_CAP + 1)])


if __name__ == "__main__":
    unittest.main()
