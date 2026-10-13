"""P4: immutable offline inputs and a bounded worker that publishes latest only.

This module has no Tk, camera, serial, workbench or execution dependencies.
An in-flight P2 computation finishes normally; superseded results are dropped.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from microcleaning.control_system.planning.coverage_model import (
    FootprintProfile, KernelResult, PixelScale, array_hash, build_kernel, integer,
)
from microcleaning.control_system.planning.coverage_planner import (
    MAX_IMAGE_CELLS, CoverageConstraints, CoverageInput, CoveragePlan, CoveragePolicy, plan_coverage,
)
from microcleaning.control_system.planning.coverage_visualization import validate_rgb

SYNTHETIC_SHAPES = ("circle", "strip", "concave", "split", "border")


@dataclass(frozen=True, eq=False)
class CoverageDataset:
    image_rgb: np.ndarray
    input: CoverageInput

    def __post_init__(self) -> None:
        object.__setattr__(self, "image_rgb", validate_rgb(self.image_rgb, self.input.mask.shape))


def synthetic_dataset(shape: str) -> CoverageDataset:
    if shape not in SYNTHETIC_SHAPES:
        raise ValueError("SYNTHETIC_SHAPE_UNSUPPORTED")
    yy, xx = np.indices((240, 360))
    masks = {
        "circle": (xx - 180) ** 2 + (yy - 120) ** 2 <= 65 ** 2,
        "strip": (xx > 40) & (xx < 320) & (np.abs(yy - 120) < 16),
        "concave": ((xx - 175) ** 2 + (yy - 120) ** 2 < 78 ** 2)
            & ~((xx - 211) ** 2 + (yy - 100) ** 2 < 68 ** 2),
        "split": ((xx - 100) ** 2 + (yy - 105) ** 2 < 45 ** 2)
            | ((xx - 238) ** 2 + (yy - 140) ** 2 < 38 ** 2)
            | ((xx - 180) ** 2 + (yy - 45) ** 2 < 12 ** 2),
        "border": ((xx - 14) ** 2 + (yy - 122) ** 2 < 54 ** 2),
    }
    mask = masks[shape]
    texture = ((xx * 13 + yy * 17) % 19).astype(np.int16)
    background = np.stack([52 + texture, 65 + texture, 64 + texture], axis=-1).astype(np.uint8)
    background[mask] = np.column_stack([110 + texture[mask], 86 + texture[mask], 67 + texture[mask]])
    input = CoverageInput(mask, f"synthetic-{shape}", "synthetic", f"synthetic-fixture-v1:{shape}",
        frame_id="synthetic-fixture-v1", image_ref=f"synthetic:{shape}", image_sha256=array_hash(background))
    return CoverageDataset(background, input)


def load_image_mask(image_path: str | Path, mask_path: str | Path, *, mask_kind: str) -> CoverageDataset:
    """Load a supplied complete binary mask. Never threshold or select a component."""
    image_path, mask_path = Path(image_path), Path(mask_path)
    if image_path.stat().st_size > 64 * 1024 * 1024 or mask_path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("INPUT_FILE_RESOURCE_LIMIT")
    with Image.open(image_path) as image:
        if image.width * image.height > MAX_IMAGE_CELLS:
            raise ValueError("INPUT_IMAGE_RESOURCE_LIMIT")
        rgb = np.asarray(image.convert("RGB"))
    with Image.open(mask_path) as image:
        if image.width * image.height > MAX_IMAGE_CELLS or image.mode not in {"1", "L"}:
            raise ValueError("MASK_BINARY_IMAGE_REQUIRED")
        mask = np.asarray(image)
    input = CoverageInput(mask, image_path.stem, mask_kind, str(mask_path.resolve()),
        image_ref=str(image_path.resolve()), image_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),
        segmentation_sha256=hashlib.sha256(mask_path.read_bytes()).hexdigest())
    return CoverageDataset(rgb, input)


@dataclass(frozen=True, eq=False)
class CoverageRequest:
    generation: int
    dataset: CoverageDataset
    profile: FootprintProfile
    scale: PixelScale | None = None
    policy: CoveragePolicy = CoveragePolicy(candidate_stride_px=2)
    constraints: CoverageConstraints = CoverageConstraints()
    model: KernelResult | None = None

    def __post_init__(self) -> None:
        integer(self.generation, "generation", minimum=1)
        if self.model is not None and self.model.profile != self.profile:
            raise ValueError("REQUEST_MODEL_PROFILE_MISMATCH")


@dataclass(frozen=True, eq=False)
class CoverageResponse:
    generation: int
    plan: CoveragePlan | None
    error: str | None = None


def compute_request(request: CoverageRequest) -> CoveragePlan:
    return plan_coverage(request.dataset.input, request.model if request.model is not None else build_kernel(request.profile, request.scale),
        policy=request.policy, constraints=request.constraints)


class LatestCoverageWorker:
    """One running request, one replaceable pending request, one latest response."""

    def __init__(self, processor: Callable[[CoverageRequest], CoveragePlan] = compute_request):
        self._processor = processor
        self._condition = threading.Condition()
        self._pending = None
        self._response = None
        self._latest = 0
        self._closed = False
        self.submitted_count = 0
        self.thread = threading.Thread(target=self._run, name="coverage-preview", daemon=True)
        self.thread.start()

    def submit(self, request: CoverageRequest) -> None:
        with self._condition:
            if self._closed or request.generation <= self._latest:
                raise ValueError("WORKER_CLOSED_OR_GENERATION_NOT_INCREASING")
            self._latest = request.generation
            self._pending = request
            self._response = None
            self.submitted_count += 1
            self._condition.notify()

    def poll(self) -> CoverageResponse | None:
        with self._condition:
            result, self._response = self._response, None
            return result

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending = self._response = None
            self._condition.notify()

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                request, self._pending = self._pending, None
            try:
                response = CoverageResponse(request.generation, self._processor(request))
            except Exception as exc:
                response = CoverageResponse(request.generation, None, f"{type(exc).__name__}: {exc}")
            with self._condition:
                if self._closed:
                    return
                if response.generation == self._latest:
                    self._response = response
