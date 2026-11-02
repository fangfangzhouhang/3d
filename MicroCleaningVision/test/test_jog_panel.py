"""触屏点动页无头逻辑：模式门、确认桥（超时/取消/迟到回复）与 Mock 装配。"""

from __future__ import annotations

import threading
import time
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from demo.jog_panel import (CONNECT_ONLY_REASON, JogConfirmBridge, POSITION_UNKNOWN_REASON,
    build_jog_controller, jog_block_reason, jog_mode, mode_banner, plan_display_lines, position_text)
from microcleaning.control_system.planning.stage2_position import Stage2Position
from microcleaning.control_system.safety.motion_gate import MotionRequest


def make_request():
    return MotionRequest(request_id="motion_test", task_id="jog_test",
        lines=("MOVEXY 320 FWD 160 REV",), position_before_steps=(0, 0),
        start_reference="image_center", calibration_ref="cal", calibration_sha256="sha")


PLAN = {"position_before": [0, 0], "position_after": [320, -160]}


class JogModeTests(unittest.TestCase):
    def test_mode_from_args(self):
        self.assertEqual("connect_only", jog_mode(SimpleNamespace(connect_only=True, real=True)))
        self.assertEqual("real", jog_mode(SimpleNamespace(real=True)))
        self.assertEqual("mock", jog_mode(SimpleNamespace()))
        self.assertIn("仅连接", mode_banner("connect_only"))
        self.assertIn("MOCK", mode_banner("mock"))

    def test_block_reason_priority(self):
        self.assertEqual(CONNECT_ONLY_REASON, jog_block_reason(
            mode="connect_only", runtime_active=True, busy=True, position_known=False))
        self.assertIn("任务", jog_block_reason(
            mode="mock", runtime_active=True, busy=False, position_known=True))
        self.assertIn("尚未结束", jog_block_reason(
            mode="mock", runtime_active=False, busy=True, position_known=True))
        self.assertEqual(POSITION_UNKNOWN_REASON, jog_block_reason(
            mode="real", runtime_active=False, busy=False, position_known=False))
        self.assertIsNone(jog_block_reason(
            mode="mock", runtime_active=False, busy=False, position_known=True))

    def test_position_text(self):
        known = Stage2Position(True, 320, -160, None, None, None, "")
        text = position_text(known)
        self.assertIn("320", text)
        self.assertIn("账本估算", text)
        unknown = Stage2Position(False, None, None, None, None, None, "")
        self.assertIn("位置未知", position_text(unknown))

    def test_plan_display_lines(self):
        lines = plan_display_lines(make_request(), PLAN)
        joined = "\n".join(lines)
        self.assertIn("X 轴正向 320 步", joined)
        self.assertIn("Y 轴反向 160 步", joined)
        self.assertIn("(0, 0) 步 → (320, -160) 步", joined)


class JogConfirmBridgeTests(unittest.TestCase):
    def ask_in_thread(self, bridge, **kwargs):
        outcome = {}
        worker = threading.Thread(
            target=lambda: outcome.setdefault("value", bridge.ask(make_request(), PLAN)), daemon=True)
        worker.start()
        return worker, outcome

    def test_reply_true_reaches_worker(self):
        bridge = JogConfirmBridge(timeout=5)
        worker, outcome = self.ask_in_thread(bridge)
        deadline = time.monotonic() + 5
        request = None
        while time.monotonic() < deadline and request is None:
            request = bridge.poll_request()
            time.sleep(0.01)
        self.assertIsNotNone(request)
        self.assertEqual("motion", request["kind"])
        self.assertTrue(any("320" in line for line in request["lines"]))
        self.assertTrue(bridge.reply(True))
        worker.join(timeout=5)
        self.assertIs(outcome["value"], True)

    def test_timeout_auto_declines_and_late_reply_invalid(self):
        bridge = JogConfirmBridge(timeout=0.25)
        worker, outcome = self.ask_in_thread(bridge)
        worker.join(timeout=5)
        self.assertIs(outcome["value"], False)
        self.assertFalse(bridge.reply(True))
        self.assertIsNone(bridge.poll_request())

    def test_cancel_all_declines_pending(self):
        bridge = JogConfirmBridge(timeout=30)
        worker, outcome = self.ask_in_thread(bridge)
        deadline = time.monotonic() + 5
        while bridge.poll_request() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        bridge.cancel_all()
        worker.join(timeout=5)
        self.assertIs(outcome["value"], False)

    def test_zero_request_kind_and_lines(self):
        bridge = JogConfirmBridge(timeout=5)
        outcome = {}
        worker = threading.Thread(target=lambda: outcome.setdefault("value", bridge.ask_zero()), daemon=True)
        worker.start()
        deadline = time.monotonic() + 5
        request = None
        while time.monotonic() < deadline and request is None:
            request = bridge.poll_request()
            time.sleep(0.01)
        self.assertIsNotNone(request)
        self.assertEqual("zero", request["kind"])
        self.assertTrue(any("零点" in line for line in request["lines"]))
        self.assertTrue(bridge.reply(False))
        worker.join(timeout=5)
        self.assertIs(outcome["value"], False)


class BuildControllerTests(unittest.TestCase):
    def test_connect_only_builds_nothing(self):
        args = SimpleNamespace(connect_only=True)
        self.assertIsNone(build_jog_controller(args, confirm=lambda request, plan: True))

    def test_mock_controller_jogs_with_bridge_confirm(self):
        with tempfile.TemporaryDirectory() as temp:
            bridge = JogConfirmBridge(timeout=5)
            args = SimpleNamespace(output_root=Path(temp), mock_scenario="success", serial_timeout=0.2)
            controller = build_jog_controller(args, confirm=bridge.ask)
            self.assertIsNotNone(controller)
            self.assertEqual((0, 0), controller.get_position().xy())
            outcome = {}
            def run():
                outcome["armed"] = controller.arm()
                outcome["result"] = controller.jog("FWD", None, 320)
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            deadline = time.monotonic() + 10
            while worker.is_alive() and time.monotonic() < deadline:
                if bridge.poll_request() is not None:
                    bridge.reply(True)
                time.sleep(0.01)
            worker.join(timeout=5)
            self.assertTrue(outcome["armed"])
            self.assertEqual("sent", outcome["result"].status, outcome["result"].message)
            self.assertEqual((320, 0), outcome["result"].position.xy())
            self.assertFalse(controller.armed)

    def test_stop_single_entry_cancels_pending_confirm_and_disarms(self):
        with tempfile.TemporaryDirectory() as temp:
            bridge = JogConfirmBridge(timeout=30)
            args = SimpleNamespace(output_root=Path(temp), mock_scenario="success", serial_timeout=0.2)
            controller = build_jog_controller(args, confirm=bridge.ask,
                confirm_canceller=bridge.cancel_all)
            outcome = {}
            def run():
                outcome["armed"] = controller.arm()
                outcome["result"] = controller.jog("FWD", None, 320)
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            deadline = time.monotonic() + 10
            while bridge.poll_request() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNotNone(bridge.poll_request())
            position = controller.stop()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertTrue(outcome["armed"])
            self.assertEqual("declined", outcome["result"].status, outcome["result"].message)
            self.assertFalse(controller.armed)
            self.assertEqual((0, 0), position.xy())
            self.assertEqual((0, 0), controller.get_position().xy())


class JogPageTkTests(unittest.TestCase):
    def make_window(self, runtime):
        import tkinter as tk
        from demo.station_window import WorkbenchWindow
        try:
            window = WorkbenchWindow(runtime)
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        if not __import__("os").environ.get("MCV_UI_SCREENSHOTS"):
            window.root.withdraw()
        return window

    def test_mock_page_arms_and_jogs_to_320(self):
        from integration.test_workbench_flow import make_runtime
        with tempfile.TemporaryDirectory() as temp:
            runtime = make_runtime(Path(temp), scenario="success")
            window = self.make_window(runtime)
            try:
                self.assertIn("jog", window.views)
                window.navigate("jog")
                page = window.views["jog"]
                self.assertEqual("mock", page.mode)
                self.assertEqual("停止", page.stop_button.cget("text"))
                self.assertIsNone(page.current_block_reason())
                page.toggle_arm()
                self.assertTrue(page.controller.armed)
                page.press_direction("FWD", None)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    window.root.update()
                    if page.bridge.poll_request() is not None:
                        page.bridge.reply(True)
                    if not page.busy and page.controller.get_position().xy() == (320, 0):
                        break
                    time.sleep(0.01)
                window.root.update()
                self.assertEqual((320, 0), page.controller.get_position().xy())
                self.assertIn("点动结果", page.result_label.cget("text"))
                self.assertFalse(page.controller.armed)
            finally:
                window._destroy()

    def test_connect_only_page_disabled(self):
        from integration.test_workbench_flow import make_runtime
        with tempfile.TemporaryDirectory() as temp:
            runtime = make_runtime(Path(temp), scenario="success")
            runtime.args.connect_only = True
            runtime.connection_only = True
            window = self.make_window(runtime)
            try:
                window.navigate("jog")
                page = window.views["jog"]
                self.assertIsNone(page.controller)
                self.assertEqual(CONNECT_ONLY_REASON, page.current_block_reason())
                self.assertEqual("disabled", str(page.dir_buttons[0].cget("state")))
                self.assertEqual("disabled", str(page.arm_button.cget("state")))
            finally:
                window._destroy()


if __name__ == "__main__":
    unittest.main()
