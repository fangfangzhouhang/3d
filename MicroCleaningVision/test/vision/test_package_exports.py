"""B 包入口的导出合同与无设备副作用检查。"""

from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


class VisionPackageExportsTests(unittest.TestCase):
    def test_exports_preserve_existing_objects(self):
        import microcleaning.vision as vision
        from microcleaning.vision.contamination import ContaminationMeasurement
        from microcleaning.vision.hsv_baseline import SegmentationResult
        from microcleaning.vision.local_contrast_baseline import (
            LocalContrastPolicy, segment_contamination,
        )
        from microcleaning.vision.state_estimator import estimate_state
        from microcleaning.vision.verification import VerificationPolicy, verify_area_change

        expected = {
            "ContaminationMeasurement": ContaminationMeasurement,
            "LocalContrastPolicy": LocalContrastPolicy,
            "SegmentationResult": SegmentationResult,
            "segment_local": segment_contamination,
            "estimate_state": estimate_state,
            "VerificationPolicy": VerificationPolicy,
            "verify_area_change": verify_area_change,
        }
        self.assertEqual(set(expected), set(vision.__all__))
        self.assertEqual(len(expected), len(vision.__all__))
        for name, original in expected.items():
            with self.subTest(name=name):
                self.assertIs(original, getattr(vision, name))

    def test_fresh_import_requires_no_device_or_optional_dependencies(self):
        # 独立进程避免其他测试预加载模块掩盖包入口的依赖或设备副作用。
        script = textwrap.dedent('''
            import importlib.abc
            import sys
            class BlockDevices(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {'cv2', 'numpy', 'serial'}:
                        raise AssertionError('Unexpected dependency: ' + fullname)
                    if fullname.startswith(('microcleaning.control_system',
                                            'microcleaning.data_learning')):
                        raise AssertionError('Unexpected hardware/data dependency: ' + fullname)
            sys.meta_path.insert(0, BlockDevices())
            import microcleaning.vision as vision
            assert len(vision.__all__) == 7
        ''')
        result = subprocess.run(
            [sys.executable, "-B", "-c", script],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
