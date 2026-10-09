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
            runtime = make_runtime(Path(temp))
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
            deadline = time.monotonic() + 25
            errors, captured = [], set()
            screenshot_wait = {}
            stage = [0]
            visited = {"cover"}
            max_heartbeat = [0]
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
                        raise AssertionError(f"UI timed out: {stage[0]}, {window.current_page}, {window.current_phase}")
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
                            window.confirm_yes.invoke()
                        if window.snapshot is not None:
                            if not capture_screen("05-results"):
                                window.root.after(150, drive)
                                return
                            self.assertEqual(window.snapshot["statistics"]["executed_targets"], 2)
                            self.assertEqual(window.snapshot["quality_status"], "FAIL")
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


if __name__ == "__main__":
    unittest.main()
