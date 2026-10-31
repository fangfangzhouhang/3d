"""Child-process fault/barrier fixtures for archive tests; never used by the app."""
import argparse
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo.run_archive_worker import export_and_verify
from microcleaning.data_learning.run_archive import RunArchiveExporter


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--mode', required=True)
    p.add_argument('--run', required=True)
    p.add_argument('--data-root', required=True)
    p.add_argument('--control', required=True)
    a = p.parse_args()
    control = Path(a.control)
    control.mkdir(parents=True, exist_ok=True)
    def barrier():
        (control / 'ready').write_text(str(__import__('os').getpid()))
        while not (control / 'release').exists():
            time.sleep(.02)
    try:
        if a.mode in {'hang', 'hold_after_export', 'verify_fail'}:
            original = RunArchiveExporter.export_run
            def export(exporter, folder):
                if a.mode == 'hang':
                    import microcleaning.data_learning.run_archive as archive
                    original_copy = archive.shutil.copyfile
                    def copy(src, dst):
                        result = original_copy(src, dst)
                        barrier()  # actual P1 copy began; held before any publication
                        return result
                    with patch.object(archive.shutil, 'copyfile', copy):
                        return original(exporter, folder)
                result = original(exporter, folder)
                if a.mode == 'verify_fail':
                    return result
                barrier()
                return result
            with patch.object(RunArchiveExporter, 'export_run', export):
                if a.mode == 'verify_fail':
                    with patch('microcleaning.data_learning.run_archive.verify_archive', side_effect=OSError('validation unavailable')):
                        result = export_and_verify(a.run, a.data_root)
                else:
                    result = export_and_verify(a.run, a.data_root)
        elif a.mode == 'copy_fail':
            with patch('microcleaning.data_learning.run_archive.shutil.copyfile', side_effect=OSError('copy interrupted')):
                result = export_and_verify(a.run, a.data_root)
        else:
            failure = {'denied': PermissionError('denied shared directory'),
                'interrupt': KeyboardInterrupt('test interruption'), 'fail': RuntimeError('worker failed')}[a.mode]
            with patch.object(RunArchiveExporter, 'export_run', side_effect=failure):
                result = export_and_verify(a.run, a.data_root)
        print(json.dumps({'ok': True, 'result': result}), flush=True)
        return 0
    except BaseException as exc:
        print(json.dumps({'ok': False, 'error': f'{type(exc).__name__}: {exc}', 'path': getattr(exc, 'archive_path', None)}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
