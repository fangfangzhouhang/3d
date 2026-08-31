"""比较人工 Ground Truth Mask 与算法 Predicted Mask。

这里评价的是 B 算法输出与 A 人工标注之间的几何差异，不修改 B 的识别算法。
所有非零像素都视为污染区域，因此输入不必恰好只有 0/255 两个值。
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class MaskEvaluation:
    width_px: int
    height_px: int
    ground_truth_area_px: int
    predicted_area_px: int
    intersection_px: int
    union_px: int
    iou: float
    area_error_px: int
    absolute_area_error_px: int
    relative_area_error: float | None
    ground_truth_centroid_px: tuple[float, float] | None
    predicted_centroid_px: tuple[float, float] | None
    centroid_error_px: float | None


def evaluate_masks(ground_truth: np.ndarray, predicted: np.ndarray) -> MaskEvaluation:
    """评价两个同尺寸二维 Mask；尺寸不一致时拒绝比较。"""

    gt = _as_binary_mask(ground_truth, name="人工 Mask")
    pred = _as_binary_mask(predicted, name="算法 Mask")
    if gt.shape != pred.shape:
        raise ValueError(f"Mask 尺寸不一致：人工={gt.shape}，算法={pred.shape}")

    intersection = int(np.count_nonzero(gt & pred))
    union = int(np.count_nonzero(gt | pred))
    gt_area = int(np.count_nonzero(gt))
    pred_area = int(np.count_nonzero(pred))
    area_error = pred_area - gt_area
    gt_centroid = _centroid(gt)
    pred_centroid = _centroid(pred)

    if union == 0:
        iou = 1.0
    else:
        iou = intersection / union
    if gt_area == 0:
        relative_area_error = 0.0 if pred_area == 0 else None
    else:
        relative_area_error = area_error / gt_area
    if gt_centroid is None and pred_centroid is None:
        centroid_error = 0.0
    elif gt_centroid is None or pred_centroid is None:
        centroid_error = None
    else:
        centroid_error = math.dist(gt_centroid, pred_centroid)

    height, width = gt.shape
    return MaskEvaluation(
        width_px=width,
        height_px=height,
        ground_truth_area_px=gt_area,
        predicted_area_px=pred_area,
        intersection_px=intersection,
        union_px=union,
        iou=iou,
        area_error_px=area_error,
        absolute_area_error_px=abs(area_error),
        relative_area_error=relative_area_error,
        ground_truth_centroid_px=gt_centroid,
        predicted_centroid_px=pred_centroid,
        centroid_error_px=centroid_error,
    )


def evaluate_mask_files(ground_truth_path: Path, predicted_path: Path) -> MaskEvaluation:
    return evaluate_masks(
        _read_mask(ground_truth_path, name="人工 Mask"),
        _read_mask(predicted_path, name="算法 Mask"),
    )


def _as_binary_mask(mask: np.ndarray, *, name: str) -> np.ndarray:
    if not isinstance(mask, np.ndarray) or mask.size == 0:
        raise ValueError(f"{name}必须是非空 numpy 数组")
    if mask.ndim != 2:
        raise ValueError(f"{name}必须是二维灰度图，实际 shape={mask.shape}")
    return mask > 0


def _read_mask(path: Path, *, name: str) -> np.ndarray:
    if not path.is_file():
        raise FileNotFoundError(f"{name}文件不存在：{path}")
    encoded = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    mask = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
    if mask is None or mask.size == 0:
        raise ValueError(f"{name}无法解码：{path}")
    return mask


def _centroid(mask: np.ndarray) -> tuple[float, float] | None:
    y_coordinates, x_coordinates = np.nonzero(mask)
    if x_coordinates.size == 0:
        return None
    return (float(x_coordinates.mean()), float(y_coordinates.mean()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="评价人工 Mask 与算法 Mask 的几何差异")
    parser.add_argument("ground_truth", type=Path, help="A 人工标注生成的 Mask")
    parser.add_argument("predicted", type=Path, help="B 算法输出的 Mask")
    parser.add_argument("--output", type=Path, help="可选 JSON 报告路径")
    args = parser.parse_args(argv)
    result = evaluate_mask_files(args.ground_truth, args.predicted)
    payload = json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"Mask 评价报告已保存：{args.output}")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
