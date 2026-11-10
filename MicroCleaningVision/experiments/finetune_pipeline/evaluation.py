"""Semantic adaptation around the repository's original binary mask evaluator.

No IoU/Precision/Recall/F1 formula is duplicated here. The original source is
loaded unchanged. Its optional cv2 file-reader dependency is isolated because
this experiment passes NumPy arrays and uses Pillow for image decoding.
"""
from __future__ import annotations

import builtins
import hashlib
import importlib.util
import sys
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

import numpy as np


_METRIC_KEYS = ("iou", "precision", "recall", "f1")


class _UnavailableCV2(ModuleType):
    """Explicit unavailable dependency, used only by this imported module."""
    def __getattr__(self, name):
        raise ImportError(
            "OpenCV is not installed in this experiment. Only evaluate_masks on "
            "NumPy arrays is supported; decode images using Pillow."
        )


@lru_cache(maxsize=1)
def load_mainline_evaluator():
    source = Path(__file__).resolve().parents[2] / "microcleaning/data_learning/mask_evaluation.py"
    name = "_mcv_finetune_mainline_mask_evaluation"
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import repository mask evaluator: {source}")
    module = importlib.util.module_from_spec(spec)
    normal_import = builtins.__import__
    fallback = _UnavailableCV2("cv2")
    adapter_used = False

    def scoped_import(import_name, globals=None, locals=None, fromlist=(), level=0):
        nonlocal adapter_used
        try:
            return normal_import(import_name, globals, locals, fromlist, level)
        except ModuleNotFoundError as error:
            if import_name != "cv2" or error.name != "cv2":
                raise
            adapter_used = True
            return fallback

    # This overrides import only in the original module's own namespace; no
    # global import hook and no fake sys.modules['cv2'] are installed.
    module.__dict__["__builtins__"] = {**vars(builtins), "__import__": scoped_import}
    sys.modules[name] = module  # dataclasses resolves annotations through this.
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    module.finetune_provenance = {
        "path": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "function": "evaluate_masks",
        "source_modified": False,
        "dependency_adapter": "scoped_unavailable_cv2" if adapter_used else "none",
        "note": "Original NumPy array evaluator; cv2 file decoding is not used.",
    }
    return module


def _evaluate_binary(gt: np.ndarray, prediction: np.ndarray) -> dict[str, Any]:
    # The upstream function disallows size-zero inputs. One false pixel is an
    # identity element for binary area/overlap counts, and permits empty splits
    # to share the same implementation without inventing metric formulas.
    if gt.size == 0:
        gt = np.zeros((1, 1), dtype=bool)
        prediction = np.zeros((1, 1), dtype=bool)
    result = asdict(load_mainline_evaluator().evaluate_masks(gt, prediction))
    result["status"] = "ok" if result["ground_truth_area_px"] > 0 else "无样本"
    if result["ground_truth_area_px"] == 0:
        for key in _METRIC_KEYS:
            result[key] = None
    # Flattened aggregation has no spatial meaning; expose count/overlap metrics
    # only, not misleading centroid coordinates or dimensions.
    for key in ("ground_truth_centroid_px", "predicted_centroid_px", "centroid_error_px", "width_px", "height_px"):
        result.pop(key, None)
    return result


def evaluate_predictions(
    pairs: Iterable[tuple[dict[str, Any], np.ndarray, np.ndarray]], schema,
) -> dict[str, Any]:
    """Evaluate original-size predictions only inside known annotation pixels.

    Aggregate metrics are pixel-micro metrics: known pixels from every image are
    concatenated and passed to the same original evaluator. Absent classes have
    null headline metrics and explicit false-positive counts.
    """
    gt_by_class: dict[str, list[np.ndarray]] = {item["name"]: [] for item in schema.classes}
    pred_by_class: dict[str, list[np.ndarray]] = {item["name"]: [] for item in schema.classes}
    per_image = []
    known_total = 0
    ignored_total = 0
    legal = {item["index"] for item in schema.classes} | {schema.ignore_index}
    for metadata, ground_truth, prediction in pairs:
        gt, pred = np.asarray(ground_truth), np.asarray(prediction)
        if gt.ndim != 2 or pred.shape != gt.shape or gt.size == 0:
            raise ValueError("Ground truth and prediction must be nonempty equal-size HW arrays")
        if not set(np.unique(gt)).issubset(legal) or not set(np.unique(pred)).issubset(legal):
            raise ValueError("Mask contains a class index absent from the configured schema")
        valid = gt != schema.ignore_index
        known_count = int(valid.sum())
        known_total += known_count
        ignored_count = int(gt.size - known_count)
        ignored_total += ignored_count
        rows = {}
        for item in schema.classes:
            name, index = item["name"], item["index"]
            binary_gt = ((gt == index) & valid)
            binary_pred = ((pred == index) & valid)
            rows[name] = _evaluate_binary(binary_gt, binary_pred)
            gt_by_class[name].append(binary_gt[valid])
            pred_by_class[name].append(binary_pred[valid])
        per_image.append({"metadata": dict(metadata), "known_pixels": known_count,
                          "ignored_pixels": ignored_count, "classes": rows})
    classes = {}
    for item in schema.classes:
        name = item["name"]
        gt_flat = np.concatenate(gt_by_class[name]) if gt_by_class[name] else np.empty(0, dtype=bool)
        pred_flat = np.concatenate(pred_by_class[name]) if pred_by_class[name] else np.empty(0, dtype=bool)
        classes[name] = _evaluate_binary(gt_flat.reshape(1, -1), pred_flat.reshape(1, -1))
        classes[name]["images_with_ground_truth"] = sum(
            row["classes"][name]["ground_truth_area_px"] > 0 for row in per_image
        )
    return {
        "status": "ok" if known_total else "无样本",
        "image_count": len(per_image),
        "known_pixels": known_total,
        "ignored_pixels": ignored_total,
        "aggregation": "pixel_micro_over_all_non_ignore_ground_truth_pixels",
        "missing_class_policy": "无样本; iou/precision/recall/f1=null; false positives retained",
        "classes": classes,
        "per_image": per_image,
        "metric_source": dict(load_mainline_evaluator().finetune_provenance),
    }
