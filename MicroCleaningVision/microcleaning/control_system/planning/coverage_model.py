"""Offline footprint profiles and pixel kernels (member C, coverage P1).

No nozzle diameter or image scale is inferred. A measured wet footprint is
still not evidence of stain removal. Arrays are copied onto immutable bytes.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from numbers import Integral, Real

import numpy as np

MAX_KERNEL_CELLS = 262_144  # Software allocation limit, not a physical limit.


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def finite_number(value: object, name: str, *, minimum: float = 0, inclusive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name}:FINITE_NUMBER_REQUIRED")
    result = float(value)
    if not math.isfinite(result) or (result < minimum if inclusive else result <= minimum):
        raise ValueError(f"{name}:OUT_OF_RANGE")
    return result


def integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name}:INTEGER_REQUIRED")
    return int(value)


def nonempty_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}:NONEMPTY_TEXT_REQUIRED")
    return value


def immutable_array(array: np.ndarray) -> np.ndarray:
    contiguous = np.ascontiguousarray(array)
    return np.frombuffer(contiguous.tobytes(), dtype=contiguous.dtype).reshape(contiguous.shape)


def binary_mask(mask: object, name: str = "mask") -> np.ndarray:
    if not isinstance(mask, np.ndarray) or mask.ndim != 2 or mask.size == 0:
        raise ValueError(f"{name}:NONEMPTY_2D_ARRAY_REQUIRED")
    if mask.dtype != np.bool_ and mask.dtype != np.uint8:
        raise ValueError(f"{name}:BOOL_OR_UINT8_REQUIRED")
    if mask.dtype == np.uint8 and not np.all((mask == 0) | (mask == 1) | (mask == 255)):
        raise ValueError(f"{name}:BINARY_VALUES_REQUIRED")
    return immutable_array(mask != 0)


def array_hash(array: np.ndarray) -> str:
    digest = hashlib.sha256(canonical_json({"shape_hw": array.shape, "dtype": str(array.dtype)}).encode())
    digest.update(np.ascontiguousarray(array).tobytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class Evidence:
    status: str
    source_ref: str
    sha256: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"assumed", "measured"}:
            raise ValueError("EVIDENCE_STATUS_MUST_BE_ASSUMED_OR_MEASURED")
        nonempty_text(self.source_ref, "source_ref")
        if self.sha256 is not None and (not isinstance(self.sha256, str) or len(self.sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.sha256)):
            raise ValueError("SHA256_LOWERCASE_HEX_REQUIRED")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SprayConditions:
    nozzle_id: str | None = None
    height_mm: float | None = None
    liquid_id: str | None = None
    substrate_id: str | None = None
    duration_ms: float | None = None
    supply_condition: str | None = None

    def __post_init__(self) -> None:
        for name in ("nozzle_id", "liquid_id", "substrate_id", "supply_condition"):
            value = getattr(self, name)
            if value is not None:
                nonempty_text(value, name)
        for name in ("height_mm", "duration_ms"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, finite_number(value, name, inclusive=name == "height_mm"))


@dataclass(frozen=True)
class FootprintProfile:
    profile_id: str
    diameter: float | None
    unit: str
    evidence: Evidence
    conditions: SprayConditions = SprayConditions()
    shape: str = "circle"
    semantics: str = "geometric_proxy"
    version: str = "footprint-profile-v1"
    parent_sha256: str | None = None

    def __post_init__(self) -> None:
        for name in ("profile_id", "shape", "version"):
            nonempty_text(getattr(self, name), name)
        if self.unit not in {"px", "mm"}:
            raise ValueError("FOOTPRINT_UNIT_MUST_BE_PX_OR_MM")
        if not isinstance(self.evidence, Evidence) or not isinstance(self.conditions, SprayConditions):
            raise ValueError("FOOTPRINT_EVIDENCE_AND_CONDITIONS_REQUIRED")
        if self.diameter is not None:
            object.__setattr__(self, "diameter", finite_number(self.diameter, "diameter"))
        if self.semantics not in {"geometric_proxy", "wet_footprint"}:
            raise ValueError("FOOTPRINT_SEMANTICS_INVALID")
        if self.evidence.status == "measured":
            if self.semantics != "wet_footprint" or any(v is None for v in asdict(self.conditions).values()):
                raise ValueError("MEASURED_WET_FOOTPRINT_REQUIRES_EXPLICIT_CONDITIONS")
        if self.parent_sha256 is not None:
            Evidence("assumed", "parent", self.parent_sha256)

    @property
    def radius(self) -> float | None:
        return None if self.diameter is None else self.diameter / 2

    def to_dict(self) -> dict:
        return {**asdict(self), "radius": self.radius}

    @property
    def sha256(self) -> str:
        return content_hash(self.to_dict())

    def with_diameter(self, diameter: float, *, profile_id: str | None = None) -> FootprintProfile:
        """Editing a measured value creates an assumed scenario, never a new measurement."""
        return replace(self, diameter=diameter, profile_id=profile_id or self.profile_id,
            evidence=Evidence("assumed", f"derived:{self.sha256}"), semantics="geometric_proxy",
            parent_sha256=self.sha256)


@dataclass(frozen=True)
class PixelScale:
    mm_per_px_x: float
    mm_per_px_y: float
    evidence: Evidence
    image_shape_hw: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        for name in ("mm_per_px_x", "mm_per_px_y"):
            object.__setattr__(self, name, finite_number(getattr(self, name), name))
        if not isinstance(self.evidence, Evidence):
            raise ValueError("SCALE_EVIDENCE_REQUIRED")
        area = self.mm_per_px_x * self.mm_per_px_y
        if not math.isfinite(area) or area <= 0:
            raise ValueError("SCALE_AREA_OUT_OF_RANGE")
        if self.image_shape_hw is not None:
            if len(self.image_shape_hw) != 2:
                raise ValueError("SCALE_SHAPE_HW_REQUIRED")
            object.__setattr__(self, "image_shape_hw", tuple(integer(v, "scale_shape", minimum=1) for v in self.image_shape_hw))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, eq=False)
class FootprintKernel:
    mask: np.ndarray
    anchor_px: tuple[int, int]  # x=column, y=row; shape is always (height, width).
    profile: FootprintProfile
    scale: PixelScale | None = None
    rasterization: str = "explicit_binary_mask"

    def __post_init__(self) -> None:
        mask = binary_mask(self.mask, "kernel")
        if mask.size > MAX_KERNEL_CELLS or not np.any(mask):
            raise ValueError("KERNEL_EMPTY_OR_RESOURCE_LIMIT")
        if len(self.anchor_px) != 2:
            raise ValueError("KERNEL_ANCHOR_XY_REQUIRED")
        anchor = tuple(integer(v, "anchor") for v in self.anchor_px)
        if anchor[0] >= mask.shape[1] or anchor[1] >= mask.shape[0]:
            raise ValueError("KERNEL_ANCHOR_OUTSIDE")
        if not isinstance(self.profile, FootprintProfile) or (self.scale is not None and not isinstance(self.scale, PixelScale)):
            raise ValueError("KERNEL_METADATA_INVALID")
        if self.rasterization not in {"explicit_binary_mask", "integer_pixel_centers_closed_disk"}:
            raise ValueError("KERNEL_RASTERIZATION_INVALID")
        object.__setattr__(self, "mask", mask)
        object.__setattr__(self, "anchor_px", anchor)

    def to_dict(self) -> dict:
        return {"schema_version": "footprint-kernel-v1", "coordinate_frame": "image_px",
            "shape_hw": list(self.mask.shape), "anchor_px": list(self.anchor_px),
            "area_px": int(np.count_nonzero(self.mask)), "mask_sha256": array_hash(self.mask),
            "profile": self.profile.to_dict(), "scale": None if self.scale is None else self.scale.to_dict(),
            "rasterization": self.rasterization, "executable": False}

    @property
    def sha256(self) -> str:
        return content_hash(self.to_dict())


@dataclass(frozen=True)
class KernelResult:
    profile: FootprintProfile
    kernel: FootprintKernel | None
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.profile, FootprintProfile):
            raise ValueError("KERNEL_RESULT_PROFILE_REQUIRED")
        reasons = tuple(self.reason_codes)
        for reason in reasons:
            nonempty_text(reason, "reason_code")
        if self.kernel is None:
            if not reasons:
                raise ValueError("UNAVAILABLE_KERNEL_REQUIRES_REASON")
        elif not isinstance(self.kernel, FootprintKernel) or self.kernel.profile != self.profile or reasons:
            raise ValueError("AVAILABLE_KERNEL_METADATA_MISMATCH")
        object.__setattr__(self, "reason_codes", reasons)

    @property
    def available(self) -> bool:
        return self.kernel is not None

    def to_dict(self) -> dict:
        return {"available": self.available, "reason_codes": list(self.reason_codes),
            "profile": self.profile.to_dict(), "kernel": None if self.kernel is None else self.kernel.to_dict()}


def build_kernel(profile: FootprintProfile, scale: PixelScale | None = None) -> KernelResult:
    """Circle factory. An mm circle becomes a pixel ellipse under anisotropic scale."""
    if not isinstance(profile, FootprintProfile) or (scale is not None and not isinstance(scale, PixelScale)):
        raise ValueError("FOOTPRINT_PROFILE_AND_OPTIONAL_PIXEL_SCALE_REQUIRED")
    if profile.shape != "circle":
        return KernelResult(profile, None, ("FOOTPRINT_SHAPE_UNSUPPORTED",))
    if profile.diameter is None:
        return KernelResult(profile, None, ("FOOTPRINT_DIAMETER_MISSING",))
    if profile.unit == "mm" and scale is None:
        return KernelResult(profile, None, ("PIXEL_SCALE_MISSING",))
    rx = profile.radius if profile.unit == "px" else profile.radius / scale.mm_per_px_x
    ry = profile.radius if profile.unit == "px" else profile.radius / scale.mm_per_px_y
    if not all(math.isfinite(v) and v > 0 for v in (rx, ry)):
        return KernelResult(profile, None, ("FOOTPRINT_CONVERSION_OUT_OF_RANGE",))
    ax, ay = math.ceil(rx), math.ceil(ry)
    if (2 * ax + 1) * (2 * ay + 1) > MAX_KERNEL_CELLS:
        return KernelResult(profile, None, ("KERNEL_RESOURCE_LIMIT",))
    ys, xs = np.ogrid[-ay:ay + 1, -ax:ax + 1]
    # No analytic pi*r*r substitution: coverage is counted on this exact pixel set.
    mask = (xs / rx) ** 2 + (ys / ry) ** 2 <= 1.0
    return KernelResult(profile, FootprintKernel(mask, (ax, ay), profile, scale,
        rasterization="integer_pixel_centers_closed_disk"))
