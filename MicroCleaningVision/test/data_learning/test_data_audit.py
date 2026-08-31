"""成员 A 数据审计工具的一个综合场景测试。"""

import csv
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from microcleaning.data_learning.data_audit import audit_data


class DataAuditTests(unittest.TestCase):
    def test_audit_reports_duplicates_corruption_registration_and_missing_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            data_root = Path(folder)
            raw_root = data_root / "raw_images"
            raw_root.mkdir()
            image = np.full((12, 16, 3), 128, dtype=np.uint8)
            encoded_ok, encoded = cv2.imencode(".jpg", image)
            self.assertTrue(encoded_ok)
            (raw_root / "registered.jpg").write_bytes(encoded.tobytes())
            (raw_root / "duplicate.jpg").write_bytes(encoded.tobytes())
            (raw_root / "broken.png").write_text("not an image", encoding="utf-8")

            metadata = data_root / "metadata.csv"
            fields = (
                "image_name", "category", "source", "capture_date", "device",
                "resolution", "magnification", "annotation_status", "remark",
            )
            with metadata.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(
                    {
                        "image_name": "registered.jpg",
                        "category": "unknown",
                        "source": "unknown",
                        "capture_date": "unknown",
                        "device": "",
                        "resolution": "16x12",
                        "magnification": "unknown",
                        "annotation_status": "unlabeled",
                        "remark": "",
                    }
                )

            report = audit_data(raw_root, metadata)

            self.assertEqual(3, report["summary"]["image_count"])
            self.assertEqual(2, report["summary"]["decodable_count"])
            self.assertEqual(1, report["summary"]["corrupt_count"])
            self.assertEqual(1, report["summary"]["registered_raw_image_count"])
            self.assertEqual(2, report["summary"]["unregistered_raw_image_count"])
            self.assertEqual(1, report["summary"]["duplicate_group_count"])
            self.assertEqual(1, report["summary"]["metadata_rows_with_missing_required_fields"])
            self.assertEqual(["broken.png", "duplicate.jpg"], report["unregistered_images"])


if __name__ == "__main__":
    unittest.main()
