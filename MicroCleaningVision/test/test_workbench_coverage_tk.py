"""Actual five-page Tk + P4 worker, bound source switching and click export."""
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from test_workbench_coverage import source_fixture
from demo.station_window import WorkbenchWindow
from demo.coverage_session import LatestCoverageWorker, compute_request
from microcleaning.control_system.orchestration.workbench_events import EventBus, WorkbenchEvent
from microcleaning.control_system.reporting.coverage_evidence import read_coverage_evidence


class WorkbenchCoverageTkTests(unittest.TestCase):
    def make_window(self):
        commands, reads = [], []
        runtime = SimpleNamespace(args=SimpleNamespace(real=False), active=False, report_busy=False,
            bus=EventBus(), submit=lambda *a, **kw: commands.append((a, kw)),
            load_results=lambda *a, **kw: reads.append((a, kw)))
        try:
            window = WorkbenchWindow(runtime)
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        if not os.environ.get("MCV_P6_SCREENSHOTS"):
            window.root.withdraw()
        self.addCleanup(lambda: window._destroy() if not window.destroyed else None)
        return window, commands, reads

    def bind(self, window, *sources):
        targets = {}
        for source in sources:
            stable = source.binding["target_id"]
            targets[stable] = {"source": "algorithm", "decision": "PENDING_REVIEW", "eligibility_reasons": [],
                "instance": {"area_px": 97, "bbox": [10, 10, 20, 20], "centroid_px": [16, 16]}, "plan": {}, "finished": False}
        window.reference = sources[0].dataset.image_rgb[..., ::-1]
        window._task_update({"task_id": "test-task", "state": "DRAFT", "targets": targets, "execution_ids": []})
        for source in sources:
            window.coverage.ingest(source)
        window.views["review"].selected = sources[0].binding["target_id"]
        window.navigate("review")
        window.coverage_changed()

    def drive(self, window, callback, timeout=15):
        errors = []
        deadline = time.monotonic() + timeout
        def tick():
            try:
                if time.monotonic() > deadline:
                    raise AssertionError("Tk coverage test timeout")
                if callback():
                    window.root.quit()
                    return
                window.root.after(30, tick)
            except Exception as exc:
                errors.append(exc)
                window.root.quit()
        window.root.after(30, tick)
        window.root.mainloop()
        if errors:
            raise errors[0]

    def screenshot(self, window, name, panel=False):
        directory = os.environ.get("MCV_P6_SCREENSHOTS")
        if directory:
            from PIL import ImageGrab
            root = window.coverage.panel.root if panel else window.root
            root.lift()
            root.update_idletasks()
            Path(directory).mkdir(parents=True, exist_ok=True)
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(Path(directory) / (name + ".png"))

    def test_latest_target_frame_heartbeat_layers_playback_and_no_execution_commands(self):
        window, commands, _ = self.make_window()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def slow(request):
            entered.set()
            if not release.wait(8):
                raise AssertionError("release missing")
            return compute_request(request)
        old_worker = LatestCoverageWorker(slow)
        with tempfile.TemporaryDirectory() as tmp:
            s1 = source_fixture(Path(tmp) / "one", target="S0001")
            s2 = source_fixture(Path(tmp) / "two", target="S0002")
            self.bind(window, s1, s2)
            self.assertTrue(window.coverage.open("S0001", worker=old_worker))
            old = window.coverage.panel
            old.root.withdraw()
            state = [0]
            def tick():
                if state[0] == 0 and entered.is_set() and window.heartbeat_count >= 5:
                    window.views["review"].selected = "S0002"
                    window.coverage_select("S0002")
                    self.assertTrue(old.closed)
                    window.coverage_open()
                    panel = window.coverage.panel
                    self.assertIs(panel.worker, old_worker)
                    panel.root.withdraw() if not os.environ.get("MCV_P6_SCREENSHOTS") else None
                    self.assertIsNone(panel.profile.diameter)
                    self.assertFalse(panel.sample_selector.winfo_ismapped())
                    panel.diameter.set("12")
                    release.set()
                    state[0] = 1
                elif state[0] == 1:
                    panel = window.coverage.panel
                    if panel.current_plan is None or panel.current_plan.model.profile.diameter != 12:
                        return False
                    self.assertEqual(panel.current_plan.input.target_id, "S0002")
                    submitted = panel.worker.submitted_count
                    plan_id = panel.current_plan.to_dict()["plan_id"]
                    panel.show_step(1)
                    panel.layers["uncovered"].set(False)
                    panel._redraw()
                    self.assertEqual(panel.worker.submitted_count, submitted)
                    self.assertEqual(panel.current_plan.to_dict()["plan_id"], plan_id)
                    old.on_view(old)
                    self.assertEqual(window.coverage.preview[1].input.target_id, "S0002")
                    self.screenshot(window, "review")
                    self.screenshot(window, "panel", panel=True)
                    post = source_fixture(Path(tmp) / "two", target="S0002", stage="post", recheck=1, frame="new-post", valid=False)
                    window.current_target = "S0002"
                    window.coverage.ingest(post)
                    window.coverage_open(latest=True)
                    window.coverage.panel.root.withdraw()
                    state[0] = 2
                elif state[0] == 2:
                    panel = window.coverage.panel
                    if panel.current_plan is None:
                        return False
                    self.assertEqual(panel.current_plan.input.frame_id, "new-post")
                    self.assertIs(panel.worker, old_worker)
                    self.assertIn("输入位置/可比性未通过", window.views["monitor"].coverage_text.cget("text"))
                    self.assertIn("输入位置/可比性未通过", panel.provenance.get())
                    self.assertEqual(commands, [])
                    self.assertEqual(window.PAGE_ORDER, ("cover", "detection", "review", "monitor", "results"))
                    return True
                return False
            self.drive(window, tick)
        window._destroy()
        old_worker.thread.join(3)

    def test_export_freezes_click_generation_profile_mask_and_playback_prefix(self):
        window, commands, reads = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            source = source_fixture(tmp)
            self.bind(window, source)
            window.coverage_open()
            panel = window.coverage.panel
            panel.root.withdraw()
            panel.diameter.set("12")
            state, saved = [0], {}
            def tick():
                if state[0] == 0 and panel.current_plan is not None and panel.current_plan.model.profile.diameter == 12:
                    panel.show_step(1)
                    saved["plan_id"], saved["generation"] = panel.current_plan.to_dict()["plan_id"], panel.accepted_generation
                    panel._choose_export()
                    panel.diameter.set("18")
                    self.assertIsNone(window.coverage.preview)
                    state[0] = 1
                elif state[0] == 1 and not panel.export_busy:
                    self.assertIn("已保存归档", panel.export_status.get())
                    previews, issues = read_coverage_evidence(tmp, task_id="test-task", targets=("S0001",))
                    self.assertFalse(issues, issues)
                    preview = previews[0]
                    self.assertEqual(preview["plan_id"], saved["plan_id"])
                    self.assertEqual(preview["binding"]["generation"], saved["generation"])
                    self.assertEqual(preview["profile"]["diameter"], 12)
                    self.assertEqual(preview["step_count"], 1)
                    self.assertEqual(preview["binding"]["mask_sha256"], source.binding["mask_sha256"])
                    self.assertEqual(commands, [])
                    self.assertEqual(reads[0][1], {"auto_navigate": False})
                    saved["last_generation"] = panel.generation
                    panel.close()
                    window.coverage_open()
                    reopened = window.coverage.panel
                    reopened.root.withdraw()
                    self.assertIs(reopened.worker, panel.worker)
                    self.assertGreater(reopened.generation, saved["last_generation"])
                    self.assertEqual(reopened.diameter.get(), "18")
                    state[0] = 2
                elif state[0] == 2:
                    reopened = window.coverage.panel
                    if reopened.current_plan is not None:
                        self.assertEqual(reopened.current_plan.model.profile.diameter, 18)
                        return True
                return False
            self.drive(window, tick)
            panel.export_thread.join(3)

    def test_navigation_guards_preview_reopen_and_recheck_results_routes(self):
        window, commands, reads = self.make_window()
        for page in ("detection", "review", "monitor", "results"):
            self.assertTrue(window.nav[page].instate(["disabled"]))
            window.nav[page].invoke()
            window.navigate(page)
            self.assertEqual("cover", window.current_page)
        with tempfile.TemporaryDirectory() as tmp:
            source = source_fixture(tmp, manual=True)
            self.bind(window, source)
            window.coverage_open()
            first = window.coverage.panel
            first.root.withdraw()
            self.assertEqual("review", window.current_page)
            self.assertIsNone(window.root.grab_current())
            self.assertIn("人工框选区域", first.mask_title.get())
            first._alpha_changed(45)
            first.original_only.set(True)
            first.diameter.set("12")
            generation = first.generation
            first.close()
            self.assertEqual("review", window.current_page)
            window.coverage_open()
            panel = window.coverage.panel
            panel.root.withdraw()
            self.assertIs(first.worker, panel.worker)
            self.assertGreater(panel.generation, generation)
            self.assertEqual(0.45, panel.alpha)
            self.assertFalse(panel.original_only.get(), "temporary original-only viewing must not discard layer preferences")
            self.assertFalse(panel.layers["target"].get())
            np.testing.assert_array_equal(panel.input_images["original"], source.dataset.image_rgb)
            self.assertEqual(source.dataset.input.frame_id, panel.dataset.input.frame_id)
            def tick():
                if panel.current_plan is None or panel.current_plan.model.profile.diameter != 12:
                    return False
                submitted = panel.worker.submitted_count
                window._event(WorkbenchEvent("stage", {"phase": "MOVE", "current_target_id": "S0001", "explanation": "test stage only"}))
                self.assertEqual("monitor", window.current_page)
                window._event(WorkbenchEvent("recheck_choice", {"request_id": "test-recheck", "prompt": "test decision only",
                    "choices": ("rewash", "next", "retake", "pause"), "facts": {"last_target": False}}))
                self.assertEqual("monitor", window.current_page)
                self.assertTrue(all(not button.instate(["disabled"]) for button in window.choice_buttons.values()))
                window._event(WorkbenchEvent("confirmation_closed", {"request_id": "test-recheck"}))
                snapshot = {"folder": tmp, "statistics": {"approved": 1, "executed_targets": 0, "cleaned": 0,
                    "not_cleaned": 0, "uncertain": 1}, "quality_status": "UNCERTAIN", "workflow_status": "PAUSED",
                    "targets": [], "report_ready": False}
                window._event(WorkbenchEvent("results", {"snapshot": snapshot, "auto_navigate": False}))
                self.assertEqual("monitor", window.current_page)
                for page in window.PAGE_ORDER:
                    self.assertFalse(window.nav[page].instate(["disabled"]))
                    window.nav[page].invoke()
                    self.assertEqual(page, window.current_page)
                    self.assertEqual("grid", window.views[page].winfo_manager())
                    self.assertTrue(all(not view.winfo_manager() for key, view in window.views.items() if key != page))
                window._event(WorkbenchEvent("results", {"snapshot": {**snapshot, "report_ready": True}, "auto_navigate": True}))
                self.assertEqual("results", window.current_page)
                panel.close()
                self.assertEqual("results", window.current_page)
                window.nav["review"].invoke()
                window.coverage_open()
                reopened = window.coverage.panel
                reopened.root.withdraw()
                self.assertEqual("review", window.current_page)
                self.assertEqual(0.45, reopened.alpha)
                self.assertEqual(12, reopened.profile.diameter)
                self.assertEqual(submitted + 1, reopened.worker.submitted_count)
                self.assertEqual(commands, [])
                self.assertEqual(reads, [])
                return True
            self.drive(window, tick)


if __name__ == "__main__":
    unittest.main()
