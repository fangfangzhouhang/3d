"""Aspect-preserving resize, bottom/right padding and configured normalization."""
from __future__ import annotations

import math
import numpy as np
import torch
from PIL import Image


def prepare(image, mask, config, ignore_index):
    image = image.convert("RGB")
    mask = np.asarray(mask)
    if mask.ndim != 2 or image.size != (mask.shape[1], mask.shape[0]):
        raise ValueError("image/mask dimensions differ")
    required = {"max_side", "pad_multiple", "mean", "std", "image_fill"}
    missing = required - set(config)
    if missing:
        raise ValueError(f"preprocessing configuration missing {sorted(missing)}")
    maximum = config["max_side"]
    multiple = config["pad_multiple"]
    if type(multiple) is not int or multiple <= 0:
        raise ValueError("pad_multiple must be a positive integer")
    if maximum is not None and (type(maximum) is not int or maximum <= 0):
        raise ValueError("max_side must be a positive integer or null")
    mean, std = np.asarray(config["mean"], np.float32), np.asarray(config["std"], np.float32)
    if mean.shape != (3,) or std.shape != (3,) or not np.isfinite(mean).all() or not np.isfinite(std).all() or (std <= 0).any():
        raise ValueError("mean/std must be three finite RGB values with positive std")
    fill = np.asarray(config["image_fill"])
    if fill.shape != (3,) or not np.isfinite(fill).all() or (fill < 0).any() or (fill > 255).any():
        raise ValueError("image_fill must contain three values in 0..255")
    original = image.size
    factor = min(1.0, maximum / max(original)) if maximum is not None else 1.0
    resized = (max(1, round(original[0] * factor)), max(1, round(original[1] * factor)))
    image = image.resize(resized, Image.Resampling.BILINEAR)
    labels = Image.fromarray(mask.astype(np.uint8)).resize(resized, Image.Resampling.NEAREST)
    padded = (math.ceil(resized[0] / multiple) * multiple, math.ceil(resized[1] / multiple) * multiple)
    array = np.empty((padded[1], padded[0], 3), dtype=np.uint8)
    array[:] = fill
    array[:resized[1], :resized[0]] = np.asarray(image)
    target = np.full((padded[1], padded[0]), ignore_index, dtype=np.int64)
    target[:resized[1], :resized[0]] = np.asarray(labels)
    normalized = (array.astype(np.float32) / 255.0 - mean) / std
    tensor = torch.from_numpy(np.ascontiguousarray(normalized.transpose(2, 0, 1)))
    metadata = {"original_size": list(original), "resized_size": list(resized),
                "padded_size": list(padded), "padding": "bottom_right"}
    return tensor, torch.from_numpy(target), metadata


def restore_prediction(mask, metadata):
    """Remove only preprocessing padding and restore original pixel coordinates."""
    values = np.asarray(mask)
    if values.ndim != 2:
        raise ValueError("prediction must be 2D")
    width, height = metadata["resized_size"]
    expected = metadata["padded_size"]
    if values.shape != (expected[1], expected[0]):
        raise ValueError("prediction shape differs from preprocessing metadata")
    return np.array(Image.fromarray(values[:height, :width].astype(np.uint8)).resize(
        tuple(metadata["original_size"]), Image.Resampling.NEAREST), copy=True)
