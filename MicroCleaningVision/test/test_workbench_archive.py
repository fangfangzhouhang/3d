"""P2 demo integration only: synthetic runs, thread barriers, real Tk; no devices."""
import hashlib
import json
import os
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from demo.workbench_runtime import WorkbenchRuntime, task_files, task_key
from integration.test_workbench_flow import make_runtime
from microcleaning.control_system.orchestration.workbench_events import WorkbenchEvent
from microcleaning.data_learning.run_archive import RunArchiveExporter, verify_archive


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def contents(folder):
    return {p.relative_to(folder).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in folder.rglob("*") if p.is_file()}


class ArchiveRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with patch.dict(os.environ, {"MICROCLEANING_DATA_ROOT": ""}):
            self.runtime = make_runtime(self.root / "local", "success")
        self.runtime.dataset_root = self.root / "shared"
        self.threads = []

    def tearDown(self):
        self.runtime.shutdown_archives()
        for thread in [*self.runtime._archive_workers.values(), *self.threads]:
            if thread.ident is not None:
                thread.join(5)
            self.assertFalse(thread.is_alive(), "background operation did not finish")

    def source(self, status="SUCCESS", mode="mock", name="task-1"):
        folder = self.root / name
        folder.mkdir()
        write(folder / "summary.json", {"task_id": name, "mode": mode, "status": status,
            "workflow_status": "COMPLETED" if status == "SUCCESS" else "FAILED"})
        write(folder / "run_config.json", {"task_id": name, "metadata": {"sample_id": "sample", "mode": mode, "data_origin": mode}})
        write(folder / "task_manifest.json", {"task_id": name})
        write(folder / "serial.json", {"events": []})
        (folder / "stage_events.jsonl").write_text('{"phase":"END"}\n')
        (folder / "evidence.bin").write_bytes(b"synthetic evidence")
        return folder

    def schedule(self, folder):
        self.runtime._schedule_archive(folder, resources_released=True, local_saved=True)

    def wait_archive(self, folder):
        worker = self.runtime._archive_workers.get(task_key(folder))
        if worker is not None:
            worker.join(10)
            self.assertFalse(worker.is_alive())
        return self.runtime.archive_status(folder)

    def scenario(self, mode):
        control = self.root / ("control-" + mode)
        def command(folder):
            return [sys.executable, str(Path(__file__).with_name("archive_worker_scenarios.py")),
                "--mode", mode, "--run", str(folder), "--data-root", str(self.runtime.dataset_root), "--control", str(control)]
        self.runtime._archive_command = command
        return control

    def wait_ready(self, control, seconds=6):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if (control / "ready").is_file():
                return
            time.sleep(.01)
        self.fail("child barrier not reached")

    def thread(self, callback):
        worker = threading.Thread(target=callback)
        worker.start()
        self.threads.append(worker)
        return worker

    def test_event_identity_is_captured_not_read_from_current_runtime(self):
        from demo.workbench_runtime import _TaskEvents
        source = self.source()
        events = _TaskEvents(self.runtime.bus, source)
        self.runtime.folder = self.root / "another-task"
        events.emit("device", camera="closed")
        event = self.runtime.bus.drain()[-1]
        self.assertEqual(event.payload["task_key"], task_key(source))
        self.assertNotEqual(event.payload["task_key"], task_key(self.runtime.folder))

    def test_no_configuration_preserves_local_and_does_not_export(self):
        source = self.source()
        before = contents(source)
        self.runtime.dataset_root = None
        with patch.object(RunArchiveExporter, "export_run") as exporter:
            self.schedule(source)
        self.assertEqual(self.runtime.archive_status(source)["status"], "not_configured")
        exporter.assert_not_called()
        self.assertEqual(contents(source), before)
        self.assertFalse(self.runtime.archive_busy)

    def test_environment_fallback_and_explicit_precedence(self):
        with patch.dict(os.environ, {"MICROCLEANING_DATA_ROOT": str(self.root / "env")}):
            runtime = make_runtime(self.root / "other")
            self.assertEqual(runtime.dataset_root, self.root / "env")
            args = runtime.args
            args.dataset_root = self.root / "explicit"
            configured = WorkbenchRuntime(args=args, config=runtime.config, segmenter=runtime.segmenter,
                placeholders=runtime.placeholders, calibration=None, offset=runtime.offset)
            self.assertEqual(configured.dataset_root, self.root / "explicit")

    def test_cli_parameter_is_forwarded_without_opening_devices(self):
        from demo.closed_loop_station import main
        with patch("demo.workbench_runtime.WorkbenchRuntime") as runtime:
            runtime.return_value.run.return_value = 0
            with patch.dict(os.environ, {"MICROCLEANING_DATA_ROOT": str(self.root / "env")}):
                self.assertEqual(main(["--mock", "--workbench", "--no-tuned-policy", "--dataset-root", str(self.root / "explicit")]), 0)
            self.assertEqual(runtime.call_args.kwargs["args"].dataset_root, self.root / "explicit")

    def test_export_mock_and_real_mode_fixtures_have_distinct_buckets(self):
        for mode in ("mock", "real"):
            source = self.source(mode=mode, name=mode)
            before = contents(source)
            self.schedule(source)
            state = self.wait_archive(source)
            self.assertEqual(state["status"], "exported")
            self.assertEqual(state["verification"], "verified")
            self.assertEqual(Path(state["path"]).parent.name, mode)
            manifest = verify_archive(state["path"])
            for item in manifest["files"]:
                data = (Path(state["path"]) / "raw" / item["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])
            self.assertEqual(contents(source), before)

    def test_archive_failures_never_change_any_summary_status(self):
        for status in ("SUCCESS", "HUMAN", "ERROR", "CANCELLED"):
            source = self.source(status=status, name=status)
            before = contents(source)
            self.runtime.last_status = status
            self.scenario("fail")
            self.schedule(source)
            state = self.wait_archive(source)
            self.assertEqual(state["status"], "failed")
            self.assertEqual(self.runtime.last_status, status)
            self.assertEqual(contents(source), before)

    def test_unwritable_shared_root_failure_retains_source(self):
        source = self.source()
        before = contents(source)
        self.scenario("denied")
        self.schedule(source)
        state = self.wait_archive(source)
        self.assertIn("PermissionError", state["error"])
        self.assertEqual(before, contents(source))

    def test_shared_root_that_is_a_file_fails_without_touching_source(self):
        source = self.source()
        before = contents(source)
        self.runtime.dataset_root.write_text("not a directory")
        self.schedule(source)
        state = self.wait_archive(source)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(contents(source), before)
        self.assertEqual(self.runtime.dataset_root.read_text(), "not a directory")

    def test_post_publication_verification_failure_keeps_location_and_summary(self):
        source = self.source()
        before = contents(source)
        self.scenario("verify_fail")
        self.schedule(source)
        state = self.wait_archive(source)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["verification"], "not_verified")
        self.assertIn("validation unavailable", state["error"])
        self.assertTrue(Path(state["path"]).is_dir())
        self.assertEqual(contents(source), before)

    def test_copy_interrupt_keeps_local_and_unpublished_partial(self):
        source = self.source()
        before = contents(source)
        self.scenario("copy_fail")
        self.schedule(source)
        state = self.wait_archive(source)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(before, contents(source))
        self.assertTrue(list(self.runtime.dataset_root.glob("runs/mock/.partial-*")))
        self.assertFalse(list(self.runtime.dataset_root.glob("runs/mock/run-*")))

    def test_already_archived_task_is_skipped_and_schedule_is_once(self):
        source = self.source()
        existing = RunArchiveExporter(self.runtime.dataset_root).export_run(source)
        self.schedule(source)
        state = self.wait_archive(source)
        self.assertEqual(state["status"], "skipped")
        self.assertEqual(state["path"], existing["path"])
        with patch.object(RunArchiveExporter, "export_run") as exporter:
            self.schedule(source)
            exporter.assert_not_called()

    def test_thread_start_failure_is_only_archive_failure(self):
        source = self.source()
        before = contents(source)
        with patch("demo.workbench_runtime.threading.Thread.start", side_effect=RuntimeError("no threads")):
            self.schedule(source)
        self.assertEqual(self.runtime.archive_status(source)["status"], "failed")
        self.assertEqual(contents(source), before)

    def test_unconfirmed_cleanup_or_summary_never_copies(self):
        for released, saved in ((False, True), (True, False)):
            source = self.source(name=str(released) + str(saved))
            with patch.object(RunArchiveExporter, "export_run") as exporter:
                self.runtime._schedule_archive(source, resources_released=released, local_saved=saved)
                exporter.assert_not_called()
            self.assertEqual(self.runtime.archive_status(source)["status"], "failed")

    def test_missing_final_summary_fails_before_export(self):
        source = self.source()
        (source / "summary.json").unlink()
        with patch.object(RunArchiveExporter, "export_run") as exporter:
            self.schedule(source)
            self.wait_archive(source)
            exporter.assert_not_called()
        self.assertEqual(self.runtime.archive_status(source)["status"], "failed")

    def test_same_folder_alias_uses_same_lock_other_folder_does_not_wait(self):
        folder = self.source()
        entered, release, alias_done, other_done = (threading.Event() for _ in range(4))
        self.addCleanup(release.set)
        def hold():
            with task_files(folder):
                entered.set()
                release.wait(4)
        self.thread(hold)
        self.assertTrue(entered.wait(2))
        def run(path, done):
            with task_files(path):
                done.set()
        self.thread(lambda: run(folder / ".." / folder.name, alias_done))
        self.thread(lambda: run(self.root / "other", other_done))
        self.assertTrue(other_done.wait(2))
        self.assertFalse(alias_done.wait(.1))
        release.set()
        self.assertTrue(alias_done.wait(2))
        with self.assertRaisesRegex(RuntimeError, "BACKGROUND"):
            with task_files(folder):
                pass

    def test_archive_lock_spans_copy_verify_and_publish_and_late_files_stay_local(self):
        from integration.test_workbench_flow import run_reviewed
        from demo.workbench_coverage import freeze_source
        from demo.coverage_session import CoverageRequest, compute_request
        from microcleaning.control_system.planning.coverage_model import Evidence, FootprintProfile
        from microcleaning.control_system.planning.coverage_planner import CoverageConstraints
        from microcleaning.control_system.planning.coverage_visualization import RenderOptions
        from microcleaning.control_system.reporting.coverage_evidence import export_bound_preview
        from microcleaning.control_system.reporting import read_run
        import cv2
        import numpy as np
        source = self.root / "actual-run"
        run_reviewed(source)
        before = (source / "summary.json").read_bytes()
        review_before = (source / "review_log.jsonl").read_bytes()
        pre = json.loads((source / "cycle_001/pre.json").read_text(encoding="utf-8"))
        image = cv2.imdecode(np.frombuffer((source / "cycle_001/pre.png").read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        mask = cv2.imdecode(np.frombuffer((source / "cycle_001/pre_mask.png").read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
        frozen = freeze_source(task_id=pre["observation"]["task_id"], folder=source, target_id="S0001", stage="pre",
            cycle=1, recheck=None, frame_id=pre["observation"]["frame_id"], image_bgr=image, mask=mask,
            mask_kind="segmented_stain", metadata=pre)
        constraints = CoverageConstraints()
        plan = compute_request(CoverageRequest(1, frozen.dataset,
            FootprintProfile("test", 16, "px", Evidence("assumed", "synthetic-test")), constraints=constraints))
        control = self.scenario("hold_after_export")
        write(source / "reports/early.json", {"already_complete": True})
        self.schedule(source)
        self.wait_ready(control)
        report_done, review_done, coverage_done = (threading.Event() for _ in range(3))
        failures = []
        self.assertTrue(self.runtime.report(source))  # real HTML/CSV writer
        snapshot = read_run(source)
        target = snapshot["targets"][0]
        attempt = target["attempts"][-1]
        self.runtime.quality_review(source, target_id=target["target_id"], operator="actual test reviewer",
            conclusion="CLEANED", reason="synthetic concurrency review", evidence_refs=[attempt["pre_image"], attempt["post_image"]])
        # Enter through the production hub save callback; only the popup widgets
        # are substituted here. export_bound_preview and all its writes are REAL.
        from demo.workbench_coverage import WorkbenchCoverageHub
        owner = SimpleNamespace(root=None, scale=1, task={"task_id": pre["observation"]["task_id"]})
        hub = WorkbenchCoverageHub(owner)
        hub.ingest(frozen)
        panels = []
        class Panel:
            def __init__(self, *a, **kw):
                self.root, self.closed, self.accepted_generation = kw["root"], False, 1
                self.exporter_factory = kw["exporter_factory"]
                panels.append(self)
        with patch("tkinter.Toplevel", return_value=MagicMock()), patch("demo.coverage_panel.StandaloneCoveragePanel", Panel):
            self.assertTrue(hub.open("S0001", latest=True, worker=object()))
            save_coverage = panels[0].exporter_factory(panels[0])
        def coverage():
            try:
                save_coverage(frozen.dataset.image_rgb, plan, None,
                    constraints=constraints, options=RenderOptions(), step_count=len(plan.stations))
                coverage_done.set()
            except BaseException as exc:
                failures.append(exc)
        self.thread(coverage)
        time.sleep(.1)
        self.assertFalse(coverage_done.is_set())
        self.assertEqual((source / "review_log.jsonl").read_bytes(), review_before)
        self.assertEqual([p.name for p in (source / "reports").iterdir()], ["early.json"])
        other_done = threading.Event()
        def other():
            with task_files(self.root / "other"):
                other_done.set()
        self.thread(other)
        self.assertTrue(other_done.wait(2))
        (control / "release").touch()
        state = self.wait_archive(source)
        self.assertTrue(coverage_done.wait(10), failures)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if (source / "review_log.jsonl").read_bytes() != review_before and not self.runtime.report_busy:
                break
            time.sleep(.02)
        self.assertFalse(failures)
        self.assertNotEqual((source / "review_log.jsonl").read_bytes(), review_before)
        self.assertTrue(list((source / "reports").glob("report_*/index.html")))
        self.assertTrue(list((source / "reports").glob("report_*/targets.csv")))
        self.assertTrue(list((source / "coverage_previews").glob("*/binding.json")))
        archive = Path(state["path"]) / "raw"
        self.assertTrue((archive / "reports/early.json").is_file())
        self.assertEqual((archive / "review_log.jsonl").read_bytes(), review_before)
        self.assertFalse((archive / "coverage_previews").exists())
        self.assertFalse(list((archive / "reports").glob("report_*")))
        self.assertEqual(before, (source / "summary.json").read_bytes())
        manifest = verify_archive(state["path"])
        for item in manifest["files"]:
            self.assertEqual(hashlib.sha256((archive / item["path"]).read_bytes()).hexdigest(), item["sha256"])
        evidence = os.environ.get("MCV_P2_EVIDENCE")
        if evidence:
            import shutil
            from uuid import uuid4
            audit = Path(evidence) / "concurrency_records" / uuid4().hex
            shutil.copytree(source, audit / "local_after")
            shared_copy = audit / "shared" / Path(state["path"]).name
            shutil.copytree(state["path"], shared_copy)
            verify_archive(shared_copy)
            write(audit / "context.json", {"test_fixture_only": True,
                "source_summary_sha256": hashlib.sha256(before).hexdigest(),
                "snapshot_review_sha256": hashlib.sha256(review_before).hexdigest(),
                "actual_functions": ["RunArchiveExporter.export_run", "export_report", "record_quality_review", "WorkbenchCoverageHub.save -> export_bound_preview"]})
            write(Path(evidence) / "real_writer_concurrency.json", {"retained_evidence": str(audit),
                "actual_functions": ["RunArchiveExporter.export_run", "export_report", "record_quality_review", "export_bound_preview"],
                "report_html": [p.name for p in (source / "reports").glob("report_*/index.html")],
                "report_csv": [p.name for p in (source / "reports").glob("report_*/targets.csv")],
                "local_review_sha256": hashlib.sha256((source / "review_log.jsonl").read_bytes()).hexdigest(),
                "snapshot_review_sha256": hashlib.sha256(review_before).hexdigest(),
                "coverage_bindings": len(list((source / "coverage_previews").glob("*/binding.json"))),
                "late_report_absent_from_snapshot": True, "late_review_absent_from_snapshot": True,
                "late_coverage_absent_from_snapshot": True, "summary_unchanged": True,
                "independent_hash_checks": len(manifest["files"]), "other_task_lock_not_blocked": True})

    def test_coverage_export_callback_uses_the_same_lock(self):
        from demo.workbench_coverage import WorkbenchCoverageHub
        from test_workbench_coverage import source_fixture
        source = source_fixture(self.root / "coverage")
        owner = SimpleNamespace(root=None, scale=1, task={"task_id": "test-task"})
        hub = WorkbenchCoverageHub(owner)
        hub.ingest(source)
        panels = []
        class Panel:
            def __init__(self, *args, **kw):
                self.root = kw["root"]
                self.closed, self.accepted_generation = False, 1
                self.exporter_factory = kw["exporter_factory"]
                panels.append(self)
        locked, release, wrote = (threading.Event() for _ in range(3))
        self.addCleanup(release.set)
        def hold():
            with task_files(source.folder):
                locked.set()
                release.wait(4)
        self.thread(hold)
        self.assertTrue(locked.wait(2))
        def write_coverage(*a, **kw):
            write(Path(source.folder) / "coverage_previews/complete.json", {"done": True})
            wrote.set()
            return source.folder
        with patch("tkinter.Toplevel", return_value=MagicMock()), patch("demo.coverage_panel.StandaloneCoveragePanel", Panel), \
                patch("demo.workbench_coverage.export_bound_preview", write_coverage):
            self.assertTrue(hub.open("S0001", worker=object()))
            save = panels[0].exporter_factory(panels[0])  # Tk callback only freezes arguments.
            self.thread(lambda: save(None, None, None, constraints=None, options=None, step_count=0))
            self.assertFalse(wrote.wait(.1))
            release.set()
            self.assertTrue(wrote.wait(2))

    def test_interrupted_archive_thread_clears_busy_and_releases_task_lock(self):
        source = self.source()
        self.scenario("interrupt")
        self.schedule(source)
        state = self.wait_archive(source)
        self.assertEqual(state["status"], "failed")
        self.assertIn("KeyboardInterrupt", state["error"])
        self.assertFalse(self.runtime.archive_busy)
        entered = threading.Event()
        def enter():
            with task_files(source):
                entered.set()
        self.thread(enter)
        self.assertTrue(entered.wait(2))

    def test_real_mode_simulation_releases_all_resources_before_archive(self):
        source_closed, executor_closed, lease_closed = (threading.Event() for _ in range(3))
        from contextlib import ExitStack
        runtime = self.runtime
        runtime.args.real = True
        runtime.args.position_path = self.root / "position.json"
        runtime.args.serial_port = "SIMULATED_NO_DEVICE"
        runtime.confirmations = SimpleNamespace(ask=lambda *a, **kw: True)
        class Source:
            def __init__(self, **kw): pass
            def close(self): source_closed.set()
        class Executor:
            def __init__(self, **kw): pass
            position_snapshot = {}
            def close(self): executor_closed.set()
        class Lease:
            def acquire(self): return self
            def close(self): lease_closed.set()
        class Loop:
            task = None
            def __init__(self, output_dir, **kw): self.folder = output_dir
            def run(self):
                write(self.folder / "summary.json", {"task_id": "simulated-real", "status": "HUMAN", "mode": "real", "workflow_status": "HUMAN"})
                return {"status": "HUMAN"}
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = runtime._execute_archive
        def export(folder, stop):
            entered.set()
            self.assertTrue(release.wait(4))
            self.assertTrue(source_closed.is_set() and executor_closed.is_set() and lease_closed.is_set())
            self.assertEqual(json.loads((Path(folder) / "summary.json").read_text())["status"], "HUMAN")
            self.assertEqual(threading.current_thread().name, "mcv-run-archive")
            return original(folder, stop)
        with ExitStack() as stack:
            for name, value in {"CameraBroker": Source, "HardwareExecutor": Executor, "ResourceLease": lambda *a: Lease(),
                "device_resources": lambda *a: (), "F103SerialSession": lambda **kw: None,
                "Stage2SerialLink": lambda **kw: SimpleNamespace(), "STM32SerialController": lambda **kw: None, "CleaningLoop": Loop,
                "ConfirmationBroker": lambda *a: SimpleNamespace(ask=lambda *a, **kw: True)}.items():
                stack.enter_context(patch("demo.workbench_runtime." + name, value))
            stack.enter_context(patch.object(runtime, "_execute_archive", export))
            self.assertTrue(runtime.start({}))
            runtime.worker.join(4)
            self.assertFalse(runtime.active)
            self.assertTrue(entered.wait(2))
            self.assertTrue(runtime.archive_busy)
            self.assertFalse(runtime.worker.is_alive(), "shared copy must not hold the device thread")
            release.set()
            state = self.wait_archive(runtime.folder)
        self.assertEqual(state["status"], "exported")
        self.assertEqual(runtime.last_status, "HUMAN")

    def test_actual_mock_workbench_loop_archives_after_device_owner_finishes(self):
        runtime = self.runtime
        self.assertTrue(runtime.start({"sample_id": "synthetic-only", "operator": "test"}))
        deadline = time.monotonic() + 15
        reviewed = False
        while runtime.active and time.monotonic() < deadline:
            for event in runtime.bus.drain():
                if event.kind == "capture_ready":
                    runtime.submit("capture")
                elif event.kind == "candidates" and not reviewed:
                    for action in ("approve_all", "lock", "begin"):
                        runtime.submit(action)
                    reviewed = True
                elif event.kind == "confirmation":
                    runtime.submit("confirm", request_id=event.payload["request_id"], answer=True)
                elif event.kind == "recheck_choice":
                    choice = "next" if "next" in event.payload["choices"] else "pause"
                    runtime.submit("confirm", request_id=event.payload["request_id"], answer=choice, reason="synthetic review")
            time.sleep(.01)
        if runtime.active:
            runtime.submit("cancel")
        runtime.worker.join(4)
        self.assertFalse(runtime.active)
        state = self.wait_archive(runtime.folder)
        self.assertEqual(state["status"], "exported")
        self.assertEqual(runtime.last_status, "SUCCESS")
        self.assertTrue(verify_archive(state["path"])["cycles"])


class ArchivePageTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from demo.station_window import WorkbenchWindow
        from microcleaning.control_system.orchestration.workbench_events import EventBus
        self.runtime = SimpleNamespace(args=SimpleNamespace(real=False), active=False, report_busy=False,
            dataset_root=None, archive_busy=False, bus=EventBus(), submit=lambda *a, **kw: None)
        try:
            self.window = WorkbenchWindow(self.runtime)
        except tk.TclError as exc:
            self.fail(f"NOT VERIFIED: actual Tk initialization failed: {exc}")
        self.window.root.withdraw()
        self.addCleanup(lambda: self.window._destroy() if not self.window.destroyed else None)
        self.window.result_folder = str(Path(tempfile.gettempdir()) / "new-task")

    def event(self, kind, source_folder=None, **payload):
        folder = source_folder or self.window.result_folder
        self.window._event(WorkbenchEvent(kind, {"task_key": task_key(folder), "task_folder": str(folder), **payload}))
        self.window.root.update()

    def test_archive_failure_ui_has_local_preservation_without_changing_quality(self):
        self.event("archive_status", status="failed", local_saved=True, error="PermissionError: denied", verification="not_verified")
        text = self.window.views["results"].archive_state.cget("text")
        self.assertIn("本地实验档案：已保存", text)
        self.assertIn("本地原始档案仍然保留，需要稍后重新归档", text)
        self.assertEqual(self.window.views["results"].summary.cget("text"), "尚无任务结果")

    def test_old_archive_report_and_results_events_cannot_replace_current_page(self):
        self.event("archive_status", status="not_configured", local_saved=True, verification="not_checked")
        before = self.window.views["results"].archive_state.cget("text")
        old = Path(tempfile.gettempdir()) / "old-task"
        self.event("archive_status", old, status="exported", path="old-archive", verification="verified")
        self.event("report_ready", old, folder="old-report", formats=("html",))
        self.event("results", old, snapshot={"folder": str(old)})
        for kind in ("confirmation", "task", "device", "completed", "pair_images", "result_images"):
            self.event(kind, old)  # would require task-specific payload if incorrectly accepted
        self.assertEqual(self.window.views["results"].archive_state.cget("text"), before)
        self.assertIsNone(self.window.snapshot)
        self.assertIsNone(self.window.last_report)

    def test_shared_status_and_hash_state_display_without_claiming_cleaning_success(self):
        self.event("archive_status", status="skipped", local_saved=True, path="shared/archive", verification="verified")
        text = self.window.views["results"].archive_state.cget("text")
        self.assertIn("已有一致归档，已跳过", text)
        self.assertIn("逐文件哈希校验通过", text)
        self.assertIn("shared/archive", text)
        self.assertEqual(self.window.views["results"].summary.cget("text"), "尚无任务结果")

    def test_close_waits_for_archive_without_blocking_tk(self):
        self.runtime.archive_busy = True
        self.window.close()
        self.assertFalse(self.window.destroyed)
        heartbeat = []
        self.window.root.after(0, lambda: heartbeat.append(True))
        self.window.root.update()
        self.assertTrue(heartbeat)
        self.runtime.archive_busy = False
        self.window._event(WorkbenchEvent("archive_status", {"task_key": task_key(self.window.result_folder),
            "status": "failed", "local_saved": True}))
        self.assertTrue(self.window.destroyed)


class ArchivePageLogicTests(ArchivePageTests):
    """Exercise real page/event methods even when the local Tk install is broken.

    Widget calls are substituted; this is NOT a visual or real-Tk validation.
    """
    def setUp(self):
        from demo.station_window import WorkbenchWindow
        from demo.workbench_views import ResultsPage
        self.runtime = SimpleNamespace(args=SimpleNamespace(real=False), active=False, report_busy=False,
            dataset_root=None, archive_busy=False)
        class Label:
            def __init__(self, text=""): self.text = text
            def config(self, **kw): self.text = kw.get("text", self.text)
            def cget(self, key): return self.text
        page = SimpleNamespace(archive_state=Label(), summary=Label("尚无任务结果"), export_state=Label(), refresh_exports=lambda **kw: None)
        page.update_archive = lambda state: ResultsPage.update_archive(page, state)
        self.window = WorkbenchWindow.__new__(WorkbenchWindow)
        self.window.runtime = self.runtime
        self.window.views = {"results": page}
        self.window.result_folder = str(Path(tempfile.gettempdir()) / "new-task")
        self.window.archive_states = {}
        self.window.snapshot = self.window.last_report = None
        self.window.destroyed = self.window.closing = False
        self.window._close_started = self.window._close_after = None
        self.window.progress_text = Label()
        self.window.write = lambda text: None
        self.window._destroy = lambda: setattr(self.window, "destroyed", True)
        self.window.root = SimpleNamespace(update=lambda: None, after=lambda delay, cb: cb() if delay == 0 else "pending", after_cancel=lambda token: None)


if __name__ == "__main__":
    unittest.main()
