"""成员 A metadata 半自动生成工具的关键行为测试。"""

import csv
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from microcleaning.data_learning.metadata_builder import METADATA_FIELDS, build_metadata


class MetadataBuilderTests(unittest.TestCase):
    def _write_image(self, path: Path, *, width: int = 20, height: int = 12) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = np.full((height, width, 3), 128, dtype=np.uint8)
        self.assertTrue(cv2.imwrite(str(path), image))

    def _write_metadata(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=METADATA_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def test_dry_run_fills_only_provable_fields_without_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = root / "raw"
            image = raw / "sample.png"
            metadata = root / "metadata.csv"
            self._write_image(image)
            self._write_metadata(metadata, [])
            original = metadata.read_bytes()

            result = build_metadata(raw, metadata)

            self.assertEqual("DRY_RUN", result.mode)
            self.assertEqual(1, result.new_record_count)
            self.assertEqual(original, metadata.read_bytes())
            row = result.new_rows[0]
            self.assertEqual("sample.png", row["image_name"])
            self.assertEqual("20x12", row["resolution"])
            self.assertEqual("unknown", row["source"])
            self.assertEqual("unlabeled", row["annotation_status"])

    def test_apply_appends_new_row_and_preserves_manual_row(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = root / "raw"
            metadata = root / "metadata.csv"
            self._write_image(raw / "manual.png")
            self._write_image(raw / "new.png", width=30, height=18)
            manual = {
                "image_name": "manual.png",
                "category": "spot-like",
                "source": "usb_microscope",
                "capture_date": "2026-08-29",
                "device": "human-confirmed-device",
                "resolution": "20x12",
                "magnification": "unknown",
                "annotation_status": "labeled",
                "remark": "人工填写，不得覆盖",
            }
            self._write_metadata(metadata, [manual])

            result = build_metadata(raw, metadata, apply=True, source="unknown")

            self.assertEqual("APPLY", result.mode)
            self.assertEqual(1, result.new_record_count)
            with metadata.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(manual, rows[0])
            self.assertEqual("new.png", rows[1]["image_name"])
            self.assertEqual("30x18", rows[1]["resolution"])

    def test_duplicate_basenames_are_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = root / "raw"
            metadata = root / "metadata.csv"
            self._write_image(raw / "batch-a" / "same.png")
            self._write_image(raw / "batch-b" / "same.png")
            self._write_metadata(metadata, [])
            original = metadata.read_bytes()

            with self.assertRaisesRegex(ValueError, "同名文件"):
                build_metadata(raw, metadata, apply=True)
            self.assertEqual(original, metadata.read_bytes())


if __name__ == "__main__":
    unittest.main()
