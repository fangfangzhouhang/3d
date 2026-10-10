"""Tk 页面坐标与实际工作台交互；自动化只使用 Mock。"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from demo.station_window import WorkbenchWindow
from demo.workbench_views import PixelTransform
from integration.test_workbench_flow import make_runtime


class PixelMappingTests(unittest.TestCase):
    def test_letterbox_does_not_create_targets(self):
        mapping = PixelTransform(320, 240, 800, 400)
        self.assertIsNone(mapping.to_pixel(1, 200))
        self.assertIsNone(mapping.to_pixel(400, 400))
        x, y = mapping.to_canvas(120, 90)
        actual = mapping.to_pixel(x, y)
        self.assertAlmostEqual(actual[0], 120)
        self.assertAlmostEqual(actual[1], 90)


class TkWorkbenchTests(unittest.TestCase):
    def test_real_widgets_full_mock_flow_and_mainloop_heartbeat(self):
        import tkinter as tk
        with tempfile.TemporaryDirectory() as temp:
            runtime = make_runtime(Path(temp), scenario="workbench-residual")
            try:
                window = WorkbenchWindow(runtime)
            except tk.TclError as exc:
                self.skipTest(f"Tk display unavailable: {exc}")
            screenshots = os.environ.get("MCV_UI_SCREENSHOTS")
            if not screenshots:
                window.root.withdraw()
            else:
                Path(screenshots).mkdir(parents=True, exist_ok=True)
                window.root.lift()
                window.root.attributes("-topmost", True)
            deadline = time.monotonic() + 50
            errors, captured = [], set()
            screenshot_wait = {}
            stage = [0]
            visited = {"cover"}
            max_heartbeat = [0]
            recheck_choices = []
            target_decisions = {}
            def capture_screen(name):
                if screenshots and name not in captured:
                    if name not in screenshot_wait:
                        screenshot_wait[name] = time.monotonic() + .6
                        return False
                    if time.monotonic() < screenshot_wait[name]:
                        return False
                    from PIL import ImageGrab
                    window.root.update_idletasks()
                    x, y = window.root.winfo_rootx(), window.root.winfo_rooty()
                    ImageGrab.grab(bbox=(x, y, x+window.root.winfo_width(), y+window.root.winfo_height())).save(Path(screenshots) / f"{name}.png")
                    captured.add(name)
                return True
            def drive():
                try:
                    visited.add(window.current_page)
                    max_heartbeat[0] = window.heartbeat_count
                    if time.monotonic() > deadline:
                        raise AssertionError(f"UI timed out: {stage[0]}, {window.current_page}, {window.current_phase}; " + window.log.get("end-15l", "end"))
                    if stage[0] == 0:
                        if not capture_screen("01-cover"):
                            window.root.after(150, drive)
                            return
                        window.sample.set("DEMO-3-2")
                        window.operator.set("Mock 操作员")
                        window.views["cover"].start.invoke()
                        self.assertFalse(runtime.start({}), "同一时刻只能启动一个设备任务")
                        stage[0] = 1
                    elif stage[0] == 1 and window.capture_ready:
                        if not capture_screen("02-detection"):
                            window.root.after(150, drive)
                            return
                        window.views["detection"].capture.invoke()
                        stage[0] = 2
                    elif stage[0] == 2 and window.task is not None:
                        review = window.views["review"]
                        review.select("S0001")
                        window.root.update_idletasks()
                        runtime.submit("decision", target_id="S0001", decision="APPROVED")
                        runtime.submit("decision", target_id="S0002", decision="APPROVED")
                        runtime.submit("decision", target_id="S0003", decision="EXCLUDED", reason="本轮排除")
                        stage[0] = 3
                    elif stage[0] == 3 and window.task["targets"]["S0003"]["decision"] == "EXCLUDED":
                        if screenshots:
                            window.root.update_idletasks()
                            page = window.views["review"]
                            for control in (page.lock, page.begin, page.recapture):
                                self.assertGreater(control.winfo_height(), 20)
                                self.assertGreaterEqual(control.winfo_rooty(), page.winfo_rooty())
                                self.assertLessEqual(control.winfo_rooty()+control.winfo_height(), page.winfo_rooty()+page.winfo_height(), "主动作按钮必须处于页面可视区域")
                        if not capture_screen("03-review"):
                            window.root.after(150, drive)
                            return
                        window.views["review"].lock.invoke()
                        stage[0] = 4
                    elif stage[0] == 4 and window.task["state"] == "LOCKED":
                        window.views["review"].begin.invoke()
                        stage[0] = 5
                    elif stage[0] == 5:
                        if window.current_page == "monitor":
                            if not capture_screen("04-monitor"):
                                window.root.after(150, drive)
                                return
                        if window.capture_ready:
                            window.capture()
                        if window.request_id:
                            if window.answer_mode == "recheck_choice":
                                target_id = window.current_target
                                decision_number = target_decisions.get(target_id, 0)
                                choice = "rewash" if target_id == "S0001" and decision_number == 0 else "retake" if target_id == "S0001" and decision_number == 1 else "next"
                                if not capture_screen(f"04-recheck-{len(recheck_choices)+1}-{choice}"):
                                    window.root.after(150, drive)
                                    return
                                window.choice_reason.set("合成测试操作员核对同位置图像后的决定")
                                if window.choice_buttons[choice].instate(["disabled"]):
                                    window.choice_buttons["pause"].invoke()
                                else:
                                    target_decisions[target_id] = decision_number + 1
                                    recheck_choices.append(choice)
                                    window.choice_buttons[choice].invoke()
                            else:
                                window.confirm_yes.invoke()
                        if window.snapshot is not None:
                            if window.current_page != "results":
                                window.navigate("results")
                            visited.add(window.current_page)
                            if not capture_screen("05-results"):
                                window.root.after(150, drive)
                                return
                            self.assertEqual(window.snapshot["statistics"]["executed_targets"], 2)
                            self.assertEqual(["rewash", "retake", "next", "next"], recheck_choices)
                            first = window.snapshot["targets"][0]
                            self.assertEqual(2, len(first["attempts"]))
                            self.assertEqual(2, len(first["attempts"][1]["rechecks"]))
                            self.assertIn(window.snapshot["quality_status"], {"FAIL", "PASS", "UNCERTAIN", "PARTIAL"})
                            self.assertFalse((Path(window.snapshot["folder"]) / "reports").exists())
                            self.assertEqual(visited, set(window.PAGE_ORDER))
                            self.assertGreater(max_heartbeat[0], 10)
                            window.close()
                            return
                    window.root.after(50 if not screenshots else 180, drive)
                except Exception as exc:
                    errors.append(exc)
                    runtime.submit("cancel")
                    window.root.after(400, window._destroy)
            window.root.after(350, drive)
            window.root.mainloop()
            if runtime.worker:
                runtime.worker.join(timeout=4)
            if errors:
                raise errors[0]
            import gc
            del window
            gc.collect()


class TkWorkbenchAdversarialTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from types import SimpleNamespace
        from microcleaning.control_system.orchestration.workbench_events import EventBus
        class FakeRuntime:
            args = SimpleNamespace(real=False, output_root=Path("D:/大创/tmp/mcv_impl_20261010"))
            active, report_busy = False, False
            bus = EventBus()
            def __init__(self):
                self.commands, self.image_requests, self.report_requests = [], [], []
            def submit(self, command, **payload):
                self.commands.append((command, payload))
            def comparison_images(self, folder, target, **payload):
                self.image_requests.append((folder, target["target_id"], payload))
            def report(self, folder, formats, **payload):
                self.report_requests.append((folder, formats, payload))
        self.runtime = FakeRuntime()
        try:
            self.window = WorkbenchWindow(self.runtime)
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.window.root.withdraw()
        self.window.task = {"state": "RUNNING", "targets": {}, "execution_ids": []}
        self.addCleanup(self.window._destroy)

    def event(self, kind, **payload):
        from microcleaning.control_system.orchestration.workbench_events import WorkbenchEvent
        self.window._event(WorkbenchEvent(kind, payload))
        self.window.root.update()

    def test_recheck_strings_do_not_become_motion_yes_and_duplicate_click_is_ignored(self):
        self.event("recheck_choice", request_id="decision", prompt="是否复洗", choices=("rewash", "next", "retake", "pause"), facts={})
        self.window.confirm_yes.invoke()
        self.assertEqual([], self.runtime.commands)
        self.window.choice_buttons["rewash"].invoke()
        self.window.choice_buttons["rewash"].invoke()
        self.assertEqual([("confirm", {"request_id": "decision", "answer": "rewash", "reason": ""})], self.runtime.commands)
        self.event("confirmation", request_id="move", prompt="移动授权")
        self.window.answer("rewash")
        self.assertEqual(1, len(self.runtime.commands))
        self.window.confirm_no.invoke()
        self.assertIs(self.runtime.commands[-1][1]["answer"], False)

    def test_last_manual_accept_needs_reason_and_uncertain_alignment_disables_next(self):
        self.event("recheck_choice", request_id="last", prompt="最后目标", choices=("rewash", "next", "retake", "pause"),
                   facts={"last_target": True, "passed": False, "manual_accept_allowed": True, "require_reason": True})
        self.window.choice_buttons["next"].invoke()
        self.assertEqual([], self.runtime.commands)
        self.assertEqual("last", self.window.request_id)
        self.window.choice_reason.set("已核对同位置残留图，人工认可")
        self.window.choice_buttons["next"].invoke()
        self.assertEqual("已核对同位置残留图，人工认可", self.runtime.commands[-1][1]["reason"])
        self.event("recheck_choice", request_id="shifted", prompt="位置有偏移", choices=("rewash", "next", "retake", "pause"),
                   facts={"last_target": True, "passed": False, "manual_accept_allowed": False})
        self.assertTrue(self.window.choice_buttons["next"].instate(["disabled"]))
        self.window.choice_buttons["next"].invoke()
        self.assertEqual(1, len(self.runtime.commands))

    def test_nozzle_requires_calibration_and_invalid_position_removes_alignment(self):
        monitor = self.window.views["monitor"]
        geometry = {"execution_position": (300, 500), "observation_position": (0, 0)}
        offset = {"axes_confirmed": True, "scope_to_nozzle_delta_steps": (200, 300), "calibration_source": "实测参考",
                  "uncertainty_steps": (3, 4), "motor_calibration_sha256": "0"*64, "mock_only": False}
        self.event("position", status="READY", confirmed_xy_steps=(100, 200), estimated_xy_steps=None)
        self.event("geometry", geometry=geometry, offset=offset, motor_calibration={"sha256": "0"*64}, calibration_valid=False, real=True)
        self.assertIsNone(monitor.nozzle_remaining)
        self.event("geometry", geometry=geometry, offset=offset, motor_calibration={"sha256": "0"*64}, calibration_valid=True, real=True)
        self.assertEqual((200, 300), monitor.nozzle_remaining)
        self.event("position", status="MOVING", confirmed_xy_steps=(100, 200), estimated_xy_steps=(290, 495))
        self.assertEqual((10, 5), monitor.nozzle_remaining)
        self.event("position", status="POSITION_UNCERTAIN", confirmed_xy_steps=None, estimated_xy_steps=None)
        self.assertIsNone(monitor.nozzle_remaining)
        self.assertIn("位置不可信", monitor.position.cget("text"))

    def snapshot(self, ready=False):
        checks = [{"index": 1, "roi_analysis": {"valid": True, "pre_area_px": 100, "post_area_px": 50,
                     "removal_rate": .5, "iou": .5, "alignment_shift_px": (0, 0)}}]
        attempts = [{"cycle": 1, "pump_done": True, "requested_duration_ms": 500, "action_id": "a1", "rechecks": checks},
                    {"cycle": 2, "pump_done": True, "requested_duration_ms": 500, "action_id": "a2", "rechecks": checks*2}]
        return {"folder": "D:/大创/tmp/mcv_impl_20261010/archive", "summary": {"report_ready": ready},
            "statistics": {"approved": 1, "executed_targets": 1, "cleaned": 0, "not_cleaned": 1, "uncertain": 0},
            "quality_status": "FAIL", "workflow_status": "COMPLETED", "targets": [{"target_id": "S0001", "source": "algorithm",
                "decision": "APPROVED", "quality": "NOT_CLEANED", "quality_evidence": {"automatic_quality": "NOT_CLEANED", "reasons": ()},
                "attempts": attempts}]}

    def test_failed_final_stays_monitor_and_print_does_not_enable_after_report_busy(self):
        self.window.navigate("monitor")
        self.event("results", snapshot=self.snapshot(), auto_navigate=False)
        self.assertEqual("monitor", self.window.current_page)
        page = self.window.views["results"]
        self.assertTrue(page.print_button.instate(["disabled"]))
        self.event("report_busy", busy=False)
        self.assertTrue(page.print_button.instate(["disabled"]))
        self.window.export(("html",), printing=True)
        self.assertEqual([], self.runtime.report_requests)
        self.event("results", snapshot=self.snapshot(True), auto_navigate=True)
        self.assertEqual("results", self.window.current_page)
        self.assertFalse(page.print_button.instate(["disabled"]))

    def test_each_attempt_and_recheck_has_own_request_and_stale_image_is_rejected(self):
        import numpy as np
        self.event("results", snapshot=self.snapshot(), auto_navigate=True)
        page = self.window.views["results"]
        self.assertEqual(1, page.attempt_index)
        self.assertEqual(1, page.recheck_index)
        stale = self.window.result_image_nonce
        page.attempt_select.current(0)
        page._attempt_changed()
        current = self.window.result_image_nonce
        self.assertGreater(current, stale)
        requested = self.runtime.image_requests[-1][2]
        self.assertEqual(0, requested["attempt_index"])
        self.assertEqual(0, requested["recheck_index"])
        image = np.zeros((12, 12, 3), dtype=np.uint8)
        self.event("result_images", folder=self.window.snapshot["folder"], target_id="S0001", nonce=stale, pre=image, post=image)
        self.assertIsNone(page.pre.image)
        self.event("result_images", folder=self.window.snapshot["folder"], target_id="S0001", nonce=current, pre=image, post=image)
        self.assertIs(page.pre.image, image)
        self.assertIn("第 1 次清洗 / 第 1 次复检", page.detail.get("1.0", "end"))

    def test_inline_roi_calculation_shows_zero_and_rejects_shift_evidence(self):
        import numpy as np
        monitor = self.window.views["monitor"]
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        self.event("verification", target_id="S0001", verification={"removal_rate": 1.0}, quality_evidence={"quality": "UNCERTAIN"},
            roi_analysis={"valid": False, "pre_area_px": 100, "post_area_px": 0, "removal_rate": 0, "iou": 0,
                          "alignment_shift_px": (0, 0), "messages_zh": ["后图未检测到有效污渍"]},
            pre_crop=image, post_crop=image, overlay=image)
        self.assertIs(monitor.pre_crop.image, image)
        self.assertIs(monitor.post_crop.image, image)
        self.assertIs(monitor.overlay.image, image)
        self.assertIn("0.00%", monitor.calculation.cget("text"))
        self.assertIn("不能据此判为洗净", monitor.calculation.cget("text"))
        self.event("verification", target_id="S0001", verification={"removal_rate": None}, quality_evidence={"quality": "UNCERTAIN"},
            roi_analysis={"valid": False, "removal_rate": None, "alignment_shift_px": (3, -2), "messages_zh": ["前后偏移超限"]},
            pre_crop=image, post_crop=image, overlay=image)
        self.assertIn("无法计算", monitor.calculation.cget("text"))
        self.assertIn("前后偏移超限", monitor.calculation.cget("text"))


if __name__ == "__main__":
    unittest.main()
