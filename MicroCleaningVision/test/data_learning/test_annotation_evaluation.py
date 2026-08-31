"""成员 A 人工标注转换与 Mask 评价的最小边界测试。"""

import json
import math
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from microcleaning.data_learning.annotation_tools import (
    batch_convert_labelme_annotations,
    labelme_to_binary_mask,
    resolve_labelme_image_path,
)
from microcleaning.data_learning.mask_evaluation import evaluate_masks


class LabelmeConversionTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary_directory.name)
        self.image_path = self.folder / "sample.png"
        self.annotation_path = self.folder / "sample.json"
        self.mask_path = self.folder / "masks" / "sample.png"
        image = np.full((20, 30, 3), 128, dtype=np.uint8)
        self.assertTrue(cv2.imwrite(str(self.image_path), image))

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _write_annotation(self, *, width=30, height=20, shapes=None):
        if shapes is None:
            shapes = [
                {
                    "label": "contamination",
                    "points": [[5, 5], [15, 5], [15, 12], [5, 12]],
                    "shape_type": "polygon",
                }
            ]
        self.annotation_path.write_text(
            json.dumps({"imageWidth": width, "imageHeight": height, "shapes": shapes}),
            encoding="utf-8",
        )

    def test_polygon_is_converted_to_same_size_binary_mask(self):
        self._write_annotation()
        result = labelme_to_binary_mask(self.image_path, self.annotation_path, self.mask_path)
        mask = cv2.imread(str(self.mask_path), cv2.IMREAD_GRAYSCALE)

        self.assertEqual((20, 30), mask.shape)
        self.assertEqual({0, 255}, set(np.unique(mask)))
        self.assertGreater(result.contamination_area_px, 0)
        self.assertEqual(1, result.polygon_count)

    def test_empty_shapes_create_empty_mask(self):
        self._write_annotation(shapes=[])
        result = labelme_to_binary_mask(self.image_path, self.annotation_path, self.mask_path)
        mask = cv2.imread(str(self.mask_path), cv2.IMREAD_GRAYSCALE)

        self.assertEqual(0, result.contamination_area_px)
        self.assertEqual(0, np.count_nonzero(mask))

    def test_circle_is_converted_to_binary_mask(self):
        self._write_annotation(
            shapes=[
                {
                    "label": "contamination",
                    "points": [[10, 10], [14, 10]],
                    "shape_type": "circle",
                }
            ]
        )
        result = labelme_to_binary_mask(self.image_path, self.annotation_path, self.mask_path)
        mask = cv2.imread(str(self.mask_path), cv2.IMREAD_GRAYSCALE)

        self.assertEqual(0, result.polygon_count)
        self.assertEqual(1, result.circle_count)
        self.assertEqual(255, int(mask[10, 10]))
        self.assertGreater(result.contamination_area_px, 0)

    def test_exact_canvas_edge_coordinate_is_clipped_to_last_pixel(self):
        self._write_annotation(
            shapes=[
                {
                    "label": "contamination",
                    "points": [[25, 5], [30, 5], [30, 12], [25, 12]],
                    "shape_type": "polygon",
                }
            ]
        )
        result = labelme_to_binary_mask(self.image_path, self.annotation_path, self.mask_path)
        mask = cv2.imread(str(self.mask_path), cv2.IMREAD_GRAYSCALE)

        self.assertEqual(1, result.polygon_count)
        self.assertEqual(255, int(mask[8, 29]))

    def test_annotation_size_mismatch_is_rejected(self):
        self._write_annotation(width=31)
        with self.assertRaisesRegex(ValueError, "尺寸与原图不一致"):
            labelme_to_binary_mask(self.image_path, self.annotation_path, self.mask_path)

    def test_invalid_json_is_rejected(self):
        self.annotation_path.write_text("{not-json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "JSON 格式错误"):
            labelme_to_binary_mask(self.image_path, self.annotation_path, self.mask_path)

    def test_missing_annotation_is_reported(self):
        with self.assertRaises(FileNotFoundError):
            labelme_to_binary_mask(self.image_path, self.annotation_path, self.mask_path)


class BatchLabelmeConversionTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary_directory.name)
        self.annotations_dir = self.folder / "data" / "annotations" / "labelme"
        self.images_root = self.folder / "data" / "raw_images"
        self.image_dir = self.images_root / "public"
        self.output_dir = self.folder / "data" / "annotations" / "masks"
        self.annotations_dir.mkdir(parents=True)
        self.image_dir.mkdir(parents=True)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _write_image(self, name: str) -> Path:
        path = self.image_dir / name
        image = np.full((20, 30, 3), 128, dtype=np.uint8)
        self.assertTrue(cv2.imwrite(str(path), image))
        return path

    def _write_annotation(self, name: str, *, image_path: str, shapes=None) -> Path:
        if shapes is None:
            shapes = [
                {
                    "label": "contamination",
                    "points": [[5, 5], [15, 5], [15, 12], [5, 12]],
                    "shape_type": "polygon",
                }
            ]
        path = self.annotations_dir / name
        path.write_text(
            json.dumps(
                {
                    "imagePath": image_path,
                    "imageWidth": 30,
                    "imageHeight": 20,
                    "shapes": shapes,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_batch_uses_labelme_image_path_and_creates_same_name_mask(self):
        image_path = self._write_image("sample.jpg")
        annotation_path = self._write_annotation(
            "sample.json",
            image_path="../../raw_images/public/sample.jpg",
        )

        resolved = resolve_labelme_image_path(
            annotation_path,
            json.loads(annotation_path.read_text(encoding="utf-8")),
            images_root=self.images_root,
        )
        result = batch_convert_labelme_annotations(
            self.annotations_dir,
            self.images_root,
            self.output_dir,
        )

        self.assertEqual(image_path.resolve(), resolved)
        self.assertEqual(1, result.total_count)
        self.assertEqual(1, result.converted_count)
        self.assertEqual(0, result.failed_count)
        self.assertTrue((self.output_dir / "sample.png").is_file())

    def test_one_bad_json_does_not_stop_other_conversions(self):
        self._write_image("good.jpg")
        self._write_annotation("good.json", image_path="../../raw_images/public/good.jpg")
        self._write_annotation("bad.json", image_path="../../raw_images/public/missing.jpg")

        result = batch_convert_labelme_annotations(
            self.annotations_dir,
            self.images_root,
            self.output_dir,
        )

        self.assertEqual(2, result.total_count)
        self.assertEqual(1, result.converted_count)
        self.assertEqual(1, result.failed_count)
        self.assertTrue((self.output_dir / "good.png").is_file())
        failed = next(item for item in result.items if item.status == "failed")
        self.assertIn("原图不存在", failed.error)

    def test_image_path_outside_images_root_is_rejected(self):
        outside_image = self.folder / "outside.jpg"
        image = np.full((20, 30, 3), 128, dtype=np.uint8)
        self.assertTrue(cv2.imwrite(str(outside_image), image))
        annotation_path = self._write_annotation(
            "outside.json",
            image_path="../../../outside.jpg",
        )

        with self.assertRaisesRegex(ValueError, "图片根目录之外"):
            resolve_labelme_image_path(
                annotation_path,
                json.loads(annotation_path.read_text(encoding="utf-8")),
                images_root=self.images_root,
            )


class MaskEvaluationTests(unittest.TestCase):
    def test_two_empty_masks_are_an_exact_match(self):
        empty = np.zeros((5, 5), dtype=np.uint8)
        result = evaluate_masks(empty, empty)
        self.assertEqual(1.0, result.iou)
        self.assertEqual(0, result.area_error_px)
        self.assertEqual(0.0, result.centroid_error_px)

    def test_identical_masks_have_iou_one(self):
        mask = np.zeros((5, 5), dtype=np.uint8)
        mask[1:3, 1:3] = 255
        result = evaluate_masks(mask, mask.copy())
        self.assertEqual(1.0, result.iou)
        self.assertEqual(0, result.area_error_px)
        self.assertEqual(0.0, result.centroid_error_px)

    def test_disjoint_masks_have_iou_zero(self):
        ground_truth = np.zeros((5, 5), dtype=np.uint8)
        predicted = np.zeros((5, 5), dtype=np.uint8)
        ground_truth[0, 0] = 255
        predicted[4, 4] = 255
        result = evaluate_masks(ground_truth, predicted)
        self.assertEqual(0.0, result.iou)
        self.assertAlmostEqual(math.sqrt(32), result.centroid_error_px)

    def test_partial_overlap_reports_expected_iou_and_centroid_error(self):
        ground_truth = np.zeros((4, 4), dtype=np.uint8)
        predicted = np.zeros((4, 4), dtype=np.uint8)
        ground_truth[0:2, 0:2] = 255
        predicted[1:3, 1:3] = 255
        result = evaluate_masks(ground_truth, predicted)
        self.assertAlmostEqual(1 / 7, result.iou)
        self.assertEqual(0, result.area_error_px)
        self.assertAlmostEqual(math.sqrt(2), result.centroid_error_px)

    def test_size_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "尺寸不一致"):
            evaluate_masks(
                np.zeros((4, 4), dtype=np.uint8),
                np.zeros((5, 4), dtype=np.uint8),
            )


if __name__ == "__main__":
    unittest.main()
