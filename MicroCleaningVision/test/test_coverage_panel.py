"""P4 real Tk mainloops, responsive planning, snapshot export and shutdown.

Only synthetic inputs and the offline coverage worker are used. Optional screen
captures are evidence of widget layout, not any hardware or cleaning test.
"""

import gc
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path

import numpy as np

from demo.coverage_panel import StandaloneCoveragePanel
from demo.coverage_session import CoverageDataset, LatestCoverageWorker, compute_request, synthetic_dataset
from microcleaning.control_system.planning.coverage_archive import load_coverage_preview
from microcleaning.control_system.planning.coverage_model import Evidence, FootprintKernel, FootprintProfile, KernelResult, SprayConditions
from microcleaning.control_system.planning.coverage_planner import CoverageInput, CoveragePolicy


class CoveragePanelTests(unittest.TestCase):
    def make_panel(self, *, diameter=36, worker=None, profile=None, dataset=None, model=None, initial_step_count=None):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        profile = profile if profile is not None else FootprintProfile("virtual-test", diameter, "px", Evidence("assumed", "explicit-synthetic-test"))
        panel = StandaloneCoveragePanel(dataset if dataset is not None else synthetic_dataset("split"), profile, root=root, worker=worker,
            policy=CoveragePolicy(max_stations=30, candidate_stride_px=3), model=model, initial_step_count=initial_step_count)
        if not os.environ.get("MCV_COVERAGE_SCREENSHOTS"):
            root.withdraw()
        self.addCleanup(panel.close)
        return panel

    def drive_loop(self, panel, callback, timeout=15):
        errors = []
        deadline = time.monotonic() + timeout
        def drive():
            try:
                if time.monotonic() > deadline:
                    raise AssertionError("Tk test timed out: " + panel.status.get())
                if callback():
                    panel.close()
                    return
                panel._later(30, drive)
            except Exception as exc:
                errors.append(exc)
                panel.close()
        panel._later(30, drive)
        panel.root.mainloop()
        panel.worker.thread.join(3)
        if panel.export_thread:
            panel.export_thread.join(3)
        if errors:
            raise errors[0]

    def test_mainloop_latest_result_layers_playback_and_export_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        def processor(value):
            if value.generation == 1:
                entered.set()
                if not release.wait(8):
                    raise AssertionError("test release missing")
            return compute_request(value)
        worker = LatestCoverageWorker(processor)
        self.addCleanup(release.set)
        panel = self.make_panel(worker=worker)
        state = {"stage": 0}
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "snapshot"
            def drive():
                stage = state["stage"]
                if stage == 0 and entered.is_set() and panel.heartbeat_count >= 3:
                    panel.diameter.set("28")
                    panel.diameter.set("36")
                    self.assertIsNone(panel.current_plan)
                    self.assertTrue(panel.export_button.instate(["disabled"]))
                    state["generation"] = panel.generation
                    state["stage"] = 1
                    panel._later(240, release.set)
                elif stage == 1 and panel.accepted_generation == state["generation"]:
                    self.assertEqual(36, panel.current_plan.model.profile.diameter)
                    self.assertEqual(2, worker.submitted_count, "debounce should replace intermediate diameter")
                    self.assertGreater(panel.heartbeat_count, 6)
                    self.assertFalse(panel.current_plan.executable)
                    identity = panel.current_plan.to_dict()["plan_id"]
                    panel.layer_buttons["overlap"].invoke()
                    self.assertEqual(2, worker.submitted_count, "layers must not replan")
                    panel.show_step(0)
                    self.assertEqual(0, panel.current_view.metrics.covered_area_px)
                    panel.next_button.invoke()
                    self.assertEqual(1, panel.step.get())
                    self.assertGreater(panel.current_view.metrics.covered_area_px, 0)
                    self.assertEqual(identity, panel.current_plan.to_dict()["plan_id"])
                    panel.export_to(destination)
                    state["plan_id"] = identity
                    panel.diameter.set("44")
                    self.assertIsNone(panel.current_plan, "old result must stop being exportable immediately")
                    state["stage"] = 2
                elif stage == 2 and not panel.export_busy and panel.current_plan is not None:
                    loaded = load_coverage_preview(destination)
                    self.assertEqual(state["plan_id"], loaded.plan.to_dict()["plan_id"])
                    self.assertEqual(36, loaded.plan.model.profile.diameter)
                    self.assertEqual(1, loaded.step_count)
                    self.assertEqual(44, panel.current_plan.model.profile.diameter)
                    panel.show_step(0)
                    panel.play_button.invoke()
                    self.assertTrue(panel.playing)
                    state["stage"] = 3
                    state["play_deadline"] = time.monotonic() + .5
                elif stage == 3 and time.monotonic() >= state["play_deadline"]:
                    self.assertGreaterEqual(panel.step.get(), 2)
                    panel.play_button.invoke()
                    self.assertFalse(panel.playing)
                    panel.full_button.invoke()
                    folder = os.environ.get("MCV_COVERAGE_SCREENSHOTS")
                    if folder:
                        state["stage"] = 4
                        panel.root.lift()
                        panel.root.attributes("-topmost", True)
                        state["screen_deadline"] = time.monotonic() + .7
                    else:
                        return True
                elif stage == 4 and time.monotonic() >= state["screen_deadline"]:
                    from PIL import ImageGrab
                    folder = Path(os.environ["MCV_COVERAGE_SCREENSHOTS"])
                    folder.mkdir(parents=True, exist_ok=True)
                    panel.root.update_idletasks()
                    for widget in (panel.export_button, panel.play_button, panel.full_button, panel.diameter_entry):
                        self.assertGreater(widget.winfo_height(), 15)
                        self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), panel.root.winfo_rooty() + panel.root.winfo_height())
                    x, y = panel.root.winfo_rootx(), panel.root.winfo_rooty()
                    # Windows can virtualize Tk coordinates at 200% DPI while
                    # ImageGrab uses physical pixels. Map before cropping.
                    physical = ImageGrab.grab()
                    sx = physical.width / panel.root.winfo_screenwidth()
                    sy = physical.height / panel.root.winfo_screenheight()
                    physical.crop((round(x * sx), round(y * sy), round((x + panel.root.winfo_width()) * sx),
                        round((y + panel.root.winfo_height()) * sy))).save(folder / "coverage_panel.png")
                    return True
                return False
            self.drive_loop(panel, drive)
        del panel
        gc.collect()

    def test_unknown_then_invalid_then_explicit_diameter_recovers(self):
        panel = self.make_panel(diameter=None)
        stage = [0]
        def drive():
            if stage[0] == 0 and panel.current_plan is not None:
                self.assertEqual("MODEL_UNAVAILABLE", panel.current_plan.status)
                self.assertIsNone(panel.current_plan.metrics)
                self.assertIn("无法预测", panel.metrics_text.get())
                self.assertTrue(panel.diameter_slider.instate(["disabled"]))
                panel.diameter.set("nan")
                self.assertIsNone(panel.current_plan)
                stage[0] = 1
            elif stage[0] == 1 and panel.status.get().startswith("参数无效"):
                self.assertTrue(panel.export_button.instate(["disabled"]))
                panel.diameter.set("36")
                stage[0] = 2
            elif stage[0] == 2 and panel.current_plan is not None:
                self.assertEqual(36, panel.current_plan.model.profile.diameter)
                self.assertFalse(panel.diameter_slider.instate(["disabled"]))
                self.assertNotIn("nan", panel.metrics_text.get())
                return True
            return False
        self.drive_loop(panel, drive)

    def test_close_with_inflight_request_cancels_all_tk_callbacks(self):
        entered, release = threading.Event(), threading.Event()
        def processor(value):
            entered.set()
            release.wait(4)
            return compute_request(value)
        worker = LatestCoverageWorker(processor)
        self.addCleanup(release.set)
        panel = self.make_panel(worker=worker)
        callback_errors = []
        panel.root.report_callback_exception = lambda *args: callback_errors.append(args)
        def drive():
            if entered.is_set() and panel.heartbeat_count >= 3:
                panel.diameter.set("48")
                start = time.monotonic()
                panel.close()
                self.assertLess(time.monotonic() - start, .5)
                self.assertFalse(panel._after_ids)
                release.set()
                return True
            return False
        self.drive_loop(panel, drive)
        self.assertFalse(callback_errors)
        self.assertIsNone(worker.poll())

    def test_loaded_kernel_and_measured_profile_survive_policy_edit_only(self):
        profile = FootprintProfile("recorded-test-only", 8, "px", Evidence("measured", "synthetic-unit-test-evidence"),
            conditions=SprayConditions("fixture", 10, "water", "test-substrate", 100, "test-supply"), semantics="wet_footprint")
        kernel = FootprintKernel(np.array([[1, 1, 0], [0, 1, 0]], bool), (0, 0), profile)
        target = np.zeros((24, 32), bool)
        target[5:10, 6:12] = True
        dataset = CoverageDataset(np.full((24, 32, 3), 80, np.uint8), CoverageInput(target, "test", "synthetic", "test"))
        panel = self.make_panel(profile=profile, dataset=dataset, model=KernelResult(profile, kernel), initial_step_count=1)
        stage = [0]
        def drive():
            if panel.current_plan is None:
                return False
            if stage[0] == 0:
                np.testing.assert_array_equal(kernel.mask, panel.current_plan.model.kernel.mask)
                self.assertEqual(1, panel.step.get())
                panel.goal.set("85")
                stage[0] = 1
            elif stage[0] == 1:
                self.assertEqual("measured", panel.current_plan.model.profile.evidence.status)
                np.testing.assert_array_equal(kernel.mask, panel.current_plan.model.kernel.mask)
                panel.diameter.set("10")
                stage[0] = 2
            elif stage[0] == 2:
                self.assertEqual("assumed", panel.current_plan.model.profile.evidence.status)
                self.assertEqual(profile.sha256, panel.current_plan.model.profile.parent_sha256)
                self.assertEqual("integer_pixel_centers_closed_disk", panel.current_plan.model.kernel.rasterization)
                panel.diameter.set("")
                stage[0] = 3
            else:
                self.assertEqual("MODEL_UNAVAILABLE", panel.current_plan.status)
                self.assertEqual("assumed", panel.current_plan.model.profile.evidence.status)
                self.assertIsNone(panel.current_plan.metrics)
                self.assertEqual(profile.sha256, panel.current_plan.model.profile.parent_sha256)
                return True
            return False
        self.drive_loop(panel, drive)
