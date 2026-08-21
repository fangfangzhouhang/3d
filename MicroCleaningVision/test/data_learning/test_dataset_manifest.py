"""A模块：数据集清单必须可重建、去重并发现证据损坏。"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

from microcleaning.data_learning.dataset_manifest import (
    import_image,
    initialize_dataset,
    read_records,
    validate_dataset,
)


HAS_PERCEPTION_DEPS = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class DatasetManifestTests(unittest.TestCase):
    @staticmethod
    def _write_image(path: Path, value: int = 128) -> None:
        import cv2
        import numpy as np

        image = np.full((24, 24, 3), value, dtype=np.uint8)
        if not cv2.imwrite(str(path), image):
            raise OSError(path)

    def test_import_is_traceable_and_duplicate_content_is_not_copied_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "dataset"
            source = Path(folder) / "source.png"
            self._write_image(source)
            initialize_dataset(root)
            first = import_image(
                source,
                root,
                source="phone",
                sample_id="pla-001",
                capture_session="session-01",
                contamination_type="red-marker",
            )
            second = import_image(
                source,
                root,
                source="phone",
                sample_id="pla-001",
                capture_session="session-01",
            )
            self.assertEqual(first, second)
            self.assertEqual(1, len(read_records(root)))
            self.assertEqual([], validate_dataset(root))

    def test_changed_raw_file_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "dataset"
            source = Path(folder) / "source.jpg"
            self._write_image(source, 100)
            record = import_image(
                source,
                root,
                source="usb-microscope",
                sample_id="pla-002",
                capture_session="session-02",
            )
            (root / record.relative_path).write_bytes(b"changed")
            self.assertTrue(any("哈希不一致" in issue for issue in validate_dataset(root)))

    def test_fake_image_extension_is_rejected_before_manifest_update(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "dataset"
            source = Path(folder) / "fake.png"
            source.write_bytes(b"not-a-real-image")
            with self.assertRaises(ValueError):
                import_image(
                    source,
                    root,
                    source="phone",
                    sample_id="pla-003",
                    capture_session="session-03",
                )
            self.assertEqual([], read_records(root))


if __name__ == "__main__":
    unittest.main()
