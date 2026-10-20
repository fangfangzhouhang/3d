"""随机污渍 Mask：团块、分裂、划痕、颗粒。

这些图是教师规划用的合成几何，不是 U500 采集，也不是人工真值。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

KINDS = ("blob", "split", "scratch", "particles")
SCHEMA = "plan-imitation-masks-v1"


@dataclass(frozen=True)
class SyntheticMask:
    mask: np.ndarray
    kind: str
    seed: int
    shape_hw: tuple[int, int]

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError("SYNTHETIC_KIND_UNKNOWN")
        if self.mask.dtype != np.uint8 or self.mask.shape != self.shape_hw:
            raise ValueError("SYNTHETIC_MASK_SHAPE_INVALID")
        if not np.all((self.mask == 0) | (self.mask == 1)):
            raise ValueError("SYNTHETIC_MASK_MUST_BE_BINARY")


def _clip_point(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    return int(np.clip(x, 0, width - 1)), int(np.clip(y, 0, height - 1))


def _paint_disk(canvas: np.ndarray, rng: np.random.Generator, radius_low: int, radius_high: int) -> None:
    height, width = canvas.shape
    radius_high = max(radius_low, min(radius_high, max(1, min(height, width) // 3)))
    radius = int(rng.integers(radius_low, radius_high + 1))
    margin = min(radius, max(0, min(height, width) // 2 - 1))
    cx = int(rng.integers(margin, max(margin + 1, width - margin)))
    cy = int(rng.integers(margin, max(margin + 1, height - margin)))
    cv2.circle(canvas, (cx, cy), radius, 1, thickness=-1)


def _paint_blob(canvas: np.ndarray, rng: np.random.Generator) -> None:
    height, width = canvas.shape
    count = int(rng.integers(1, 3))
    for _ in range(count):
        rx = int(rng.integers(2, max(3, min(width, height) // 4)))
        ry = int(rng.integers(2, max(3, min(width, height) // 4)))
        cx = int(rng.integers(rx, max(rx + 1, width - rx)))
        cy = int(rng.integers(ry, max(ry + 1, height - ry)))
        angle = int(rng.integers(0, 180))
        cv2.ellipse(canvas, (cx, cy), (rx, ry), angle, 0, 360, 1, thickness=-1)


def _paint_scratch(canvas: np.ndarray, rng: np.random.Generator) -> None:
    height, width = canvas.shape
    for _ in range(int(rng.integers(1, 3))):
        x = int(rng.integers(0, width))
        y = int(rng.integers(0, height))
        points = [(x, y)]
        for _step in range(int(rng.integers(4, 9))):
            x, y = _clip_point(x + int(rng.integers(-6, 7)), y + int(rng.integers(-2, 3)), width, height)
            points.append((x, y))
        cv2.polylines(canvas, [np.asarray(points, dtype=np.int32)], isClosed=False, color=1, thickness=1)
    kernel = np.ones((3, 3), dtype=np.uint8)
    dilated = cv2.dilate(canvas, kernel, iterations=1)
    canvas[:] = dilated


def generate_mask(kind: str, shape_hw: tuple[int, int], rng: np.random.Generator) -> np.ndarray:
    """返回 uint8 0/1 Mask。空结果会补一个小圆，避免教师收到无目标。"""
    if kind not in KINDS:
        raise ValueError("SYNTHETIC_KIND_UNKNOWN")
    height, width = shape_hw
    if height < 8 or width < 8:
        raise ValueError("SYNTHETIC_IMAGE_TOO_SMALL")
    canvas = np.zeros((height, width), dtype=np.uint8)
    if kind == "blob":
        _paint_blob(canvas, rng)
    elif kind == "split":
        for _ in range(int(rng.integers(2, 5))):
            _paint_disk(canvas, rng, 2, max(3, min(height, width) // 7))
    elif kind == "scratch":
        _paint_scratch(canvas, rng)
    else:
        for _ in range(int(rng.integers(8, 18))):
            _paint_disk(canvas, rng, 1, 2)
    if int(np.count_nonzero(canvas)) < 8:
        cv2.circle(canvas, (width // 2, height // 2), max(2, min(height, width) // 8), 1, thickness=-1)
    return canvas


def generate_masks(count: int, shape_hw: tuple[int, int], seed: int, kinds: tuple[str, ...] = KINDS) -> list[SyntheticMask]:
    if count < 1:
        raise ValueError("SYNTHETIC_COUNT_REQUIRED")
    if not kinds or any(kind not in KINDS for kind in kinds):
        raise ValueError("SYNTHETIC_KIND_UNKNOWN")
    rng = np.random.default_rng(seed)
    samples: list[SyntheticMask] = []
    for _ in range(count):
        kind = kinds[int(rng.integers(0, len(kinds)))]
        child = np.random.default_rng(int(rng.integers(0, 2**31 - 1)))
        mask = generate_mask(kind, shape_hw, child)
        samples.append(SyntheticMask(mask=mask, kind=kind, seed=seed, shape_hw=shape_hw))
    return samples


def save_mask_archive(path: str | Path, samples: list[SyntheticMask]) -> None:
    if not samples:
        raise ValueError("SYNTHETIC_ARCHIVE_EMPTY")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        destination,
        schema=np.array(SCHEMA),
        masks=np.stack([sample.mask for sample in samples]),
        kinds=np.array([sample.kind for sample in samples]),
        seed=np.array(samples[0].seed),
    )


def load_mask_archive(path: str | Path) -> list[SyntheticMask]:
    with np.load(path) as archive:
        schema = str(archive["schema"])
        if schema != SCHEMA:
            raise ValueError("SYNTHETIC_ARCHIVE_SCHEMA_MISMATCH")
        masks = archive["masks"]
        kinds = archive["kinds"]
        seed = int(archive["seed"])
    samples = []
    for mask, kind in zip(masks, kinds):
        array = np.asarray(mask)
        samples.append(SyntheticMask(array, str(kind), seed, (int(array.shape[0]), int(array.shape[1]))))
    return samples


def load_mask_directory(path: str | Path) -> list[tuple[str, np.ndarray]]:
    """读取将来的真实或人工 Mask 目录。灰度非零即为前景，坐标仍是 image_px。"""
    folder = Path(path)
    if not folder.is_dir():
        raise FileNotFoundError(f"缺少 Mask 目录：{folder}")
    paths = sorted(item for item in folder.iterdir() if item.suffix.lower() == ".png" and item.is_file())
    if not paths:
        raise FileNotFoundError(f"Mask 目录里没有 png：{folder}")
    loaded: list[tuple[str, np.ndarray]] = []
    for image_path in paths:
        gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if gray is None or gray.ndim != 2 or gray.size == 0:
            raise ValueError(f"MASK_UNREADABLE:{image_path.name}")
        loaded.append((image_path.stem, ((gray > 0).astype(np.uint8))))
    return loaded
