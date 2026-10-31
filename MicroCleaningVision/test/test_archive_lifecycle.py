"""Real Tk + real child process shutdown probes, including an infinite archive."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from integration.test_workbench_flow import make_runtime
from demo.workbench_runtime import task_files, task_key


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def close_probe(base):
    from demo.station_window import WorkbenchWindow
    base = Path(base)
    source = base / 'local/task'
    write(source / 'summary.json', {'task_id': 'task', 'status': 'CANCELLED', 'workflow_status': 'CANCELLED', 'mode': 'mock'})
    write(source / 'run_config.json', {'task_id': 'task', 'metadata': {'mode': 'mock'}})
    (source / 'raw.bin').write_bytes(b'synthetic input')
    before = hashlib.sha256((source / 'summary.json').read_bytes()).hexdigest()
    runtime = make_runtime(base / 'output')
    runtime.dataset_root = base / 'shared'
    control = base / 'control'
    runtime._archive_command = lambda folder: [sys.executable, str(Path(__file__).with_name('archive_worker_scenarios.py')),
        '--mode', 'hang', '--run', str(folder), '--data-root', str(runtime.dataset_root), '--control', str(control)]
    window = WorkbenchWindow(runtime)
    window.root.withdraw()
    window.result_folder = source
    owner = threading.get_ident()
    updates = []
    config = window.views['results'].archive_state.config
    def record_config(**kw):
        assert threading.get_ident() == owner, 'background Tk update'
        updates.append(kw.get('text', ''))
        return config(**kw)
    window.views['results'].archive_state.config = record_config
    runtime._schedule_archive(source, resources_released=True, local_saved=True)
    started = time.monotonic()
    close_at = [None]
    errors = []
    pid = [None]
    heartbeat_at_close = [0]
    def tick():
        try:
            if time.monotonic() - started > 10:
                raise AssertionError('bounded close deadline exceeded')
            if close_at[0] is None and (control / 'ready').exists():
                pid[0] = int((control / 'ready').read_text())
                close_at[0] = time.monotonic()
                heartbeat_at_close[0] = window.heartbeat_count
                window.close()
            if not window.destroyed:
                window.root.after(20, tick)
        except BaseException as exc:
            errors.append(str(exc))
            runtime.request_archive_stop()
            window._destroy()
    window.root.after(20, tick)
    window.root.mainloop()
    runtime.shutdown_archives()
    if errors:
        raise AssertionError(errors)
    state = runtime.archive_status(source)
    assert state['status'] == 'interrupted', state
    assert state['verification'] != 'verified'
    assert not runtime._archive_processes
    assert all(not worker.is_alive() for worker in runtime._archive_workers.values())
    assert not list(runtime.dataset_root.glob('runs/*/run-*'))
    assert list(runtime.dataset_root.glob('runs/*/.partial-*'))
    assert before == hashlib.sha256((source / 'summary.json').read_bytes()).hexdigest()
    entered = threading.Event()
    def acquire():
        with task_files(source):
            entered.set()
    check = threading.Thread(target=acquire)
    check.start(); check.join(1)
    assert entered.is_set(), 'task lock not released after process termination'
    result = {'status': state, 'close_seconds': time.monotonic()-close_at[0], 'child_pid': pid[0],
        'child_reaped': True, 'supervisor_finished': True, 'lock_released': True,
        'heartbeat_during_close': window.heartbeat_count-heartbeat_at_close[0],
        'tk_updates_on_main_thread': True, 'observed_status_text': updates,
        'summary_unchanged': True, 'no_formal_archive': True,
        'partial_paths': [str(p.relative_to(base)) for p in runtime.dataset_root.glob('runs/*/.partial-*')]}
    assert result['close_seconds'] < 5
    assert result['heartbeat_during_close'] > 5
    write(base / 'close_evidence.json', result)
    print(json.dumps(result, ensure_ascii=True), flush=True)


class ArchiveLifecycleTests(unittest.TestCase):
    def test_infinite_archive_real_tk_close_and_application_exit_are_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            started = time.monotonic()
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--close-probe', temp],
                cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', timeout=12,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertLess(time.monotonic()-started, 12)
            evidence = json.loads((Path(temp) / 'close_evidence.json').read_text(encoding='utf-8'))
            self.assertTrue(evidence['child_reaped'])
            self.assertTrue(evidence['lock_released'])
            self.assertTrue(evidence['no_formal_archive'])
            self.assertGreater(evidence['heartbeat_during_close'], 5)
            output = os.environ.get('MCV_P2_EVIDENCE')
            if output:
                write(Path(output) / 'infinite_archive_close.json', evidence)

    def test_real_tk_archive_success_failure_and_controls_remain_visible(self):
        from demo.station_window import WorkbenchWindow
        from microcleaning.control_system.reporting import read_run
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                source = base / "task"
                write(source / "summary.json", {"task_id": "task", "status": "CANCELLED", "workflow_status": "CANCELLED", "mode": "mock"})
                write(source / "run_config.json", {"task_id": "task", "metadata": {"mode": "mock"}})
                runtime = make_runtime(base / "output")
                runtime.dataset_root = base / "shared"
                if fail:
                    runtime.dataset_root.write_text("not a directory")
                window = WorkbenchWindow(runtime)
                window.root.lift()
                window.root.attributes("-topmost", True)
                window.result_folder = source
                runtime._task_event("results", source, snapshot=read_run(source), auto_navigate=True)
                runtime._schedule_archive(source, resources_released=True, local_saved=True)
                errors, captured = [], []
                owner = threading.get_ident()
                config = window.views["results"].archive_state.config
                def checked_config(**kw):
                    if threading.get_ident() != owner:
                        errors.append("Tk update from background thread")
                    return config(**kw)
                window.views["results"].archive_state.config = checked_config
                deadline = time.monotonic() + 8
                def tick():
                    try:
                        if time.monotonic() > deadline:
                            raise AssertionError("real Tk status timeout")
                        text = window.views["results"].archive_state.cget("text")
                        expected = "归档失败" if fail else "归档成功"
                        if expected in text:
                            self.assertIn("本地实验档案：已保存", text)
                            self.assertIn("未通过完整校验" if fail else "逐文件哈希校验通过", text)
                            self.assertIn("任务取消", window.views["results"].summary.cget("text"))
                            self.assertEqual(window.current_page, "results")
                            window.root.update_idletasks()
                            for button in window.views["results"].export_buttons:
                                self.assertTrue(button.winfo_ismapped())
                                self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), window.root.winfo_rooty() + window.root.winfo_height())
                            output = os.environ.get("MCV_P2_EVIDENCE")
                            if output:
                                from PIL import ImageGrab
                                directory = Path(output) / "tk_release_screenshots"
                                directory.mkdir(parents=True, exist_ok=True)
                                x,y = window.root.winfo_rootx(),window.root.winfo_rooty()
                                ImageGrab.grab(bbox=(x,y,x+window.root.winfo_width(),y+window.root.winfo_height())).save(directory / ("archive-failed.png" if fail else "archive-success.png"))
                            captured.append(text)
                            window.close()
                            return
                        window.root.after(30, tick)
                    except BaseException as exc:
                        errors.append(exc)
                        window._destroy()
                window.root.after(30, tick)
                try:
                    window.root.mainloop()
                finally:
                    runtime.shutdown_archives()
                    if not window.destroyed:
                        window._destroy()
                if errors:
                    raise AssertionError(errors)
                self.assertTrue(captured)

    def test_shutdown_cancels_archive_waiting_for_same_task_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'task'
            write(source / 'summary.json', {'status': 'HUMAN'})
            runtime = make_runtime(Path(temp) / 'output')
            runtime.dataset_root = Path(temp) / 'shared'
            entered, release = threading.Event(), threading.Event()
            def hold():
                with task_files(source):
                    entered.set(); release.wait(5)
            thread = threading.Thread(target=hold)
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                runtime._schedule_archive(source, resources_released=True, local_saved=True)
                self.assertTrue(runtime.shutdown_archives(timeout=1))
                self.assertEqual(runtime.archive_status(source)['status'], 'interrupted')
                self.assertFalse(runtime._archive_processes)
                self.assertFalse(runtime.archive_busy)
            finally:
                release.set(); thread.join(2)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--close-probe':
        close_probe(sys.argv[2])
    else:
        unittest.main()
