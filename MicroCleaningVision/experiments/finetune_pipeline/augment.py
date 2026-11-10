"""Deterministic paired PIL/NumPy augmentation; no external augmentation package."""
from __future__ import annotations

import math
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter


def _range(config, key, default, *, positive=False, nonnegative=False):
    value = config.get(key, default)
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{key} must be [minimum, maximum]")
    low, high = map(float, value)
    if not (math.isfinite(low) and math.isfinite(high) and low <= high):
        raise ValueError(f"invalid {key} range")
    if (positive and low <= 0) or (nonnegative and low < 0):
        raise ValueError(f"invalid {key} lower bound")
    return low, high


def augment_pair(image, mask, config, seed, ignore_index):
    """Return synchronized image/mask with local RNG only.

    Order: flips, quarter turns, small rotation, crop, scale, then photometry.
    Crop/scale may change dimensions. Rotation fill is unknown (ignore_index).
    Brightness, contrast, gamma, blur radius and noise sigma use two-element
    ranges. Noise sigma is in [0, 1] image units. Missing keys are identity.
    """
    config = config or {}
    allowed = {"hflip_p", "vflip_p", "rotate90_p", "rotate90_choices",
               "rotate_degrees", "crop_fraction", "scale_range", "brightness",
               "contrast", "gamma", "blur_radius", "noise_std", "image_fill"}
    if set(config) - allowed:
        raise ValueError(f"unknown augmentation keys: {sorted(set(config) - allowed)}")
    image = image.convert("RGB")
    mask = np.asarray(mask)
    if mask.ndim != 2 or image.size != (mask.shape[1], mask.shape[0]):
        raise ValueError("image/mask dimensions differ")
    label = Image.fromarray(mask.astype(np.uint8))
    rng = np.random.default_rng(int(seed))
    fill = tuple(config.get("image_fill", [0, 0, 0]))

    def chance(key):
        probability = float(config.get(key, 0))
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError(f"{key} must be between 0 and 1")
        return rng.random() < probability

    for key, operation in (("hflip_p", Image.Transpose.FLIP_LEFT_RIGHT),
                           ("vflip_p", Image.Transpose.FLIP_TOP_BOTTOM)):
        if chance(key):
            image, label = image.transpose(operation), label.transpose(operation)
    if chance("rotate90_p"):
        choices = config.get("rotate90_choices", [0, 1, 2, 3])
        if not choices or any(type(x) is not int or x not in (0, 1, 2, 3) for x in choices):
            raise ValueError("rotate90_choices must be quarter-turn counts from 0 to 3")
        turns = int(rng.choice(choices))
        if turns:
            operation = {1: Image.Transpose.ROTATE_90, 2: Image.Transpose.ROTATE_180,
                         3: Image.Transpose.ROTATE_270}[turns]
            image, label = image.transpose(operation), label.transpose(operation)
    angle = rng.uniform(*_range(config, "rotate_degrees", [0, 0]))
    if angle:
        image = image.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=fill)
        label = label.rotate(angle, resample=Image.Resampling.NEAREST, fillcolor=int(ignore_index))
    low, high = _range(config, "crop_fraction", [1, 1], positive=True)
    if high > 1:
        raise ValueError("crop_fraction cannot exceed 1")
    fraction = rng.uniform(low, high)
    width, height = image.size
    crop_w, crop_h = max(1, round(width * fraction)), max(1, round(height * fraction))
    left = int(rng.integers(0, width - crop_w + 1))
    top = int(rng.integers(0, height - crop_h + 1))
    box = (left, top, left + crop_w, top + crop_h)
    image, label = image.crop(box), label.crop(box)
    scale = rng.uniform(*_range(config, "scale_range", [1, 1], positive=True))
    if scale != 1:
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(size, Image.Resampling.BILINEAR)
        label = label.resize(size, Image.Resampling.NEAREST)
    image = ImageEnhance.Brightness(image).enhance(rng.uniform(*_range(config, "brightness", [1, 1], nonnegative=True)))
    image = ImageEnhance.Contrast(image).enhance(rng.uniform(*_range(config, "contrast", [1, 1], nonnegative=True)))
    gamma = rng.uniform(*_range(config, "gamma", [1, 1], positive=True))
    if gamma != 1:
        values = np.asarray(image, dtype=np.float32) / 255.0
        image = Image.fromarray(np.clip(np.rint(values ** gamma * 255), 0, 255).astype(np.uint8))
    radius = rng.uniform(*_range(config, "blur_radius", [0, 0], nonnegative=True))
    if radius:
        image = image.filter(ImageFilter.GaussianBlur(radius))
    sigma = rng.uniform(*_range(config, "noise_std", [0, 0], nonnegative=True))
    if sigma:
        values = np.asarray(image, dtype=np.float32) + rng.normal(0, sigma * 255, (*image.size[::-1], 3))
        image = Image.fromarray(np.clip(np.rint(values), 0, 255).astype(np.uint8))
    return image, np.array(label, dtype=np.uint8, copy=True)
