"""Explicit retention gate + real archive child + real Tk; no hardware."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from integration.test_workbench_flow import make_runtime
from demo.workbench_runtime import WorkbenchRuntime, task_key, task_files
from demo.workbench_views import DataSaveDialog, retention_advice
from microcleaning.data_learning.run_archive import verify_archive


def contents(folder):
    return {p.relative_to(folder).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in folder.rglob('*') if p.is_file()}


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        with patch.dict(os.environ, {'MICROCLEANING_DATA_ROOT': ''}):
            self.runtime = make_runtime(self.base/'pending')
        self.runtime.archive_policy = 'selected'
        self.runtime.dataset_root = self.base/'shared'
        self.addCleanup(self.runtime.shutdown_archives)
        self.source = self.base/'task'
        self.source.mkdir()
        for name, data in {
            'summary.json': {'task_id':'TASK', 'status':'CANCELLED', 'mode':'mock', 'workflow_status':'CANCELLED'},
            'run_config.json': {'task_id':'TASK', 'metadata':{'sample_id':'SAMPLE', 'mode':'mock', 'data_origin':'mock'}},
            'task_manifest.json': {'task_id':'TASK'}, 'serial.json': {'events':[]}
        }.items():
            (self.source/name).write_text(json.dumps(data),encoding='utf-8')
        (self.source/'raw.bin').write_bytes(b'synthetic experiment')

    def wait_archive(self):
        worker=self.runtime._archive_workers.get(task_key(self.source))
        if worker and worker.ident:
            worker.join(10)
            self.assertFalse(worker.is_alive())
        return self.runtime.archive_status(self.source)

    def keep(self, root=None, **kw):
        return self.runtime.save_archive(self.source, root or self.base/'selected',
            purpose='软件回归测试', reason=kw.pop('reason','复现代表性取消场景'), **kw)

    def test_default_policy_and_pending_task_location(self):
        args=self.runtime.args
        del args.archive_policy
        fresh=WorkbenchRuntime(args=args, config=self.runtime.config, segmenter=self.runtime.segmenter,
            placeholders=self.runtime.placeholders, calibration=None, offset=self.runtime.offset)
        self.assertEqual(fresh.archive_policy,'selected')
        with patch('threading.Thread.start'):
            fresh.start({})
        self.assertEqual(fresh.folder.parent.name,'待决定暂存')

    def test_configured_shared_root_does_not_auto_keep(self):
        before=contents(self.source)
        self.runtime._schedule_archive(self.source,resources_released=True,local_saved=True)
        self.assertEqual(self.runtime.archive_status(self.source)['status'],'awaiting_selection')
        self.assertFalse(self.runtime._archive_workers)
        self.assertFalse(self.runtime.dataset_root.exists())
        self.assertEqual(contents(self.source),before)

    def test_no_shared_configuration_still_requires_local_decision(self):
        self.runtime.dataset_root=None
        self.runtime._schedule_archive(self.source,resources_released=True,local_saved=True)
        self.assertEqual(self.runtime.archive_status(self.source)['retention'],'pending')
        self.assertTrue(self.keep())
        self.assertEqual(self.wait_archive()['status'],'exported')

    def test_manual_keep_requires_reason_and_preserves_every_source_file(self):
        before=contents(self.source)
        self.assertFalse(self.keep(reason=' '))
        self.assertFalse((self.base/'selected').exists())
        self.assertTrue(self.keep())
        state=self.wait_archive()
        self.assertEqual(state['status'],'exported')
        self.assertEqual(state['verification'],'verified')
        self.assertEqual(state['destination_kind'],'local')
        verify_archive(state['path'])
        # Independent check: do not use exporter's inventory or hashing helpers.
        archive = Path(state['path'])
        manifest = json.loads((archive/'run_manifest.json').read_text(encoding='utf-8'))
        indexed = {item['path'] for item in manifest['files']}
        actual = {f.relative_to(archive/'raw').as_posix() for f in (archive/'raw').rglob('*') if f.is_file()}
        self.assertEqual(indexed, actual)
        for item in manifest['files']:
            data = (archive/'raw'/item['path']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(),item['sha256'])
            self.assertEqual(len(data),item['size_bytes'])
        self.assertEqual(contents(self.source),before)
        receipt=json.loads(Path(state['selection_ref']).read_text(encoding='utf-8'))
        self.assertEqual(receipt['reason'],'复现代表性取消场景')
        self.assertFalse(receipt['training_eligibility_override'])
        self.assertFalse(self.runtime.dataset_root.exists())

    def test_manual_shared_duplicate_and_conflicting_snapshot(self):
        self.assertTrue(self.keep(self.runtime.dataset_root,destination_kind='shared'))
        first=self.wait_archive()
        verify_archive(first['path'])
        self.assertTrue(self.keep(self.runtime.dataset_root,destination_kind='shared'))
        self.assertEqual(self.wait_archive()['status'],'skipped')
        (self.source/'raw.bin').write_bytes(b'late local data')
        before=contents(self.source)
        self.assertTrue(self.keep(self.runtime.dataset_root,destination_kind='shared'))
        self.assertEqual(self.wait_archive()['status'],'failed')
        verify_archive(first['path'])
        self.assertEqual(contents(self.source),before)

    def test_defer_keeps_pending_records_and_can_later_keep(self):
        before=contents(self.source)
        self.assertTrue(self.runtime.defer_retention(self.source))
        self.assertEqual(self.runtime.archive_status(self.source)['status'],'deferred')
        self.assertEqual(contents(self.source),before)
        self.assertFalse(self.runtime.dataset_root.exists())
        self.assertTrue(self.keep())
        self.assertEqual(self.wait_archive()['status'],'exported')
        self.assertFalse(self.runtime.defer_retention(self.source))

    def test_manual_save_cannot_bypass_unconfirmed_cleanup(self):
        self.runtime._schedule_archive(self.source,resources_released=False,local_saved=True)
        self.assertFalse(self.keep())
        self.assertFalse(self.runtime._archive_workers)

    def test_manual_failure_does_not_change_cancelled_summary(self):
        before=contents(self.source)
        blocked=self.base/'blocked'
        blocked.write_text('a file is not a data root')
        self.assertTrue(self.keep(blocked))
        self.assertEqual(self.wait_archive()['status'],'failed')
        self.assertEqual(contents(self.source),before)
        self.assertEqual(json.loads((self.source/'summary.json').read_text())['status'],'CANCELLED')

    def test_manual_archive_waits_for_same_task_lock_off_main_thread(self):
        locked, release = threading.Event(), threading.Event()
        def writer():
            with task_files(self.source):
                locked.set()
                release.wait(5)
        writer_thread = threading.Thread(target=writer)
        writer_thread.start()
        try:
            self.assertTrue(locked.wait(3))
            start=time.monotonic()
            self.assertTrue(self.keep())
            self.assertLess(time.monotonic()-start,0.5)
            self.assertFalse((self.base/'selected').exists())
            self.assertFalse(self.keep())
        finally:
            release.set()
            writer_thread.join(5)
        self.assertFalse(writer_thread.is_alive())
        self.assertEqual(self.wait_archive()['status'],'exported')

    def test_advice_retains_failed_cases_and_never_promotes_mock(self):
        self.assertIn('软件验证',retention_advice({'mode':'mock'})[0])
        self.assertIn('失败案例',retention_advice({'mode':'real','workflow_status':'FAILED'})[0])
        self.assertIn('来源',retention_advice({'mode':'real','workflow_status':'COMPLETED'})[0])

    def test_cli_defaults_to_selected_and_history_open_does_not_run_devices(self):
        from demo.closed_loop_station import main
        with patch('demo.workbench_runtime.WorkbenchRuntime') as runtime:
            runtime.return_value.run.return_value=0
            self.assertEqual(main(['--mock','--open-run',str(self.source)]),0)
            args=runtime.call_args.kwargs['args']
            self.assertEqual(args.archive_policy,'selected')
            self.assertEqual(args.open_run,self.source)
            runtime.return_value.run.assert_called_once()

    def test_popup_only_accepts_events_for_its_stable_task(self):
        from demo.station_window import WorkbenchWindow
        from microcleaning.control_system.orchestration.workbench_events import WorkbenchEvent
        panel=WorkbenchWindow(self.runtime)
        panel.root.withdraw()
        self.addCleanup(lambda: panel._destroy() if not panel.destroyed else None)
        panel.snapshot={'folder':str(self.source),'mode':'mock','metadata':{}}
        panel.result_folder=self.source
        panel.show_data_save()
        panel.root.update()
        dialog=panel.data_save_window
        before=dialog.status.cget('text')
        other=self.base/'new-task'
        panel._event(WorkbenchEvent('archive_status',{'task_key':task_key(other),'task_folder':str(other),
            'status':'exported','path':'unrelated','verification':'verified'}))
        self.assertEqual(dialog.status.cget('text'),before)
        panel._event(WorkbenchEvent('archive_status',{'task_key':task_key(self.source),
            'task_folder':str(self.source),'status':'deferred','retention':'deferred','local_saved':True}))
        self.assertIn('暂不正式保留',dialog.status.cget('text'))

    def test_real_tk_confirm_runs_actual_archive_and_receives_verified_event(self):
        from demo.station_window import WorkbenchWindow
        from microcleaning.control_system.reporting import read_run
        panel=WorkbenchWindow(self.runtime)
        panel.root.withdraw()
        self.addCleanup(lambda: panel._destroy() if not panel.destroyed else None)
        panel.snapshot=read_run(self.source)
        panel.result_folder=self.source
        panel.show_data_save()
        dialog=panel.data_save_window
        before=contents(self.source)
        dialog.destination.set(str(self.base/'selected'))
        dialog.purpose.set('软件回归测试')
        dialog.reason.set('实际按钮到后台归档验收')
        dialog.confirmed.set(True)
        ticks=[]
        panel.root.after(20,lambda:ticks.append(threading.get_ident()))
        dialog.save_button.invoke()
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            panel.root.update()
            if '逐文件 SHA256 校验通过' in dialog.status.cget('text'):
                break
            time.sleep(.01)
        self.assertIn('逐文件 SHA256 校验通过',dialog.status.cget('text'))
        self.assertTrue(ticks)
        self.assertEqual(ticks[0],threading.get_ident())
        self.assertEqual(contents(self.source),before)
        self.assertEqual(panel.snapshot['legacy_status'],'CANCELLED')
        verify_archive(dialog.saved_path)

    def test_connection_only_dispatch_keeps_its_guard_with_task_folder_argument(self):
        self.runtime.connection_only=True
        with patch('demo.device_connection.run_connection_task', return_value='query-only') as connect:
            self.assertEqual(self.runtime._run_task({'operator':'test'},self.source),'query-only')
        connect.assert_called_once_with(self.runtime,{'operator':'test'})
        self.assertFalse(self.runtime._archive_workers)

    def test_connection_only_window_start_and_archive_cleanup_both_survive_merge(self):
        self.runtime.connection_only=True
        with patch('demo.station_window.WorkbenchWindow') as window, patch.object(self.runtime,'shutdown_archives') as shutdown:
            self.runtime.run()
        panel=window.return_value
        panel.root.after.assert_called_once_with(100,panel.start_task)
        panel.root.mainloop.assert_called_once()
        shutdown.assert_called_once()

    def test_real_tk_user_must_confirm_and_footer_stays_visible(self):
        import tkinter as tk
        root=tk.Tk()
        self.addCleanup(root.destroy)
        runtime=self.runtime
        calls=[]
        real_save=runtime.save_archive
        runtime.save_archive=lambda *a,**k: calls.append((a,k)) or True
        self.addCleanup(setattr,runtime,'save_archive',real_save)
        owner=SimpleNamespace(root=root, runtime=runtime, scale=1,
            snapshot={'folder':str(self.source),'mode':'mock','metadata':{}},
            open_data_location=lambda *a:None, data_save_window=None)
        dialog=DataSaveDialog(owner)
        self.addCleanup(dialog.close)
        root.update()
        self.assertFalse(dialog.confirmed.get())
        dialog.save_button.invoke()
        self.assertEqual(calls,[])
        self.assertEqual(dialog.source.get(),str(self.source))
        dialog.destination.set(str(self.base/'selected'))
        dialog.purpose.set('软件回归测试')
        dialog.reason.set('代表性异常')
        dialog.confirmed.set(True)
        dialog.save_button.invoke()
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][1]['reason'],'代表性异常')
        root.update()
        self.assertTrue(dialog.save_button.winfo_ismapped())
        self.assertLess(dialog.save_button.winfo_rooty(), dialog.window.winfo_rooty()+dialog.window.winfo_height())


if __name__=='__main__':
    unittest.main()
