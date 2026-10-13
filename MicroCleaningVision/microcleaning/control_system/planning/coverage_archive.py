"""P3: explicit preview export and verified, offline deterministic replay.

An archive is a new directory. Its manifest is written last; a failed export
leaves an incomplete directory without a manifest. Hashes detect corruption,
not provide a signature or establish physical calibration.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, PngImagePlugin

from microcleaning.control_system.planning.coverage_model import (
    Evidence, FootprintKernel, FootprintProfile, KernelResult, PixelScale,
    SprayConditions, array_hash, canonical_json, immutable_array,
)
from microcleaning.control_system.planning.coverage_planner import (
    MAX_IMAGE_CELLS, CoverageConstraints, CoverageInput, CoveragePlan,
    CoveragePolicy, PositionUncertainty, plan_coverage,
)
from microcleaning.control_system.planning.coverage_visualization import (
    RenderOptions, render_coverage, render_report, validate_rgb,
)

FILES = ("coverage_overlay.png", "source_image.png", "coverage_masks.npz")
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
EVALUATION_ARRAYS = ("footprint_mask", "covered_mask", "uncovered_mask", "overlap_mask",
    "coverage_counts", "conservative_covered_mask")
CONSTRAINT_ARRAYS = ("allowed_centers_mask", "allowed_spray_mask", "forbidden_mask")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def profile_from_dict(data: dict) -> FootprintProfile:
    values = {key: value for key, value in data.items() if key != "radius"}
    values["evidence"] = Evidence(**values["evidence"])
    values["conditions"] = SprayConditions(**values.get("conditions", {}))
    return FootprintProfile(**values)


def scale_from_dict(data: dict | None) -> PixelScale | None:
    if data is None:
        return None
    return PixelScale(**{**data, "evidence": Evidence(**data["evidence"])})


def _arrays(plan: CoveragePlan, constraints: CoverageConstraints) -> dict[str, np.ndarray]:
    arrays = {"target_mask": plan.input.mask}
    if plan.model.kernel is not None:
        arrays["kernel_mask"] = plan.model.kernel.mask
    if plan.evaluation is not None:
        for key in EVALUATION_ARRAYS:
            value = getattr(plan.evaluation, key)
            if value is not None:
                arrays[key] = value
    for key in CONSTRAINT_ARRAYS:
        value = getattr(constraints, key)
        if value is not None:
            arrays[key] = value
    return arrays


def export_coverage_preview(image_rgb: np.ndarray, plan: CoveragePlan, destination: str | Path, *,
        constraints: CoverageConstraints | None = None, options: RenderOptions = RenderOptions(),
        step_count: int | None = None) -> Path:
    constraints = constraints if constraints is not None else CoverageConstraints()
    if canonical_json(constraints.to_dict()) != plan.constraints_json:
        raise ValueError("EXPORT_REQUIRES_ORIGINAL_CONSTRAINT_MASKS")
    original = validate_rgb(image_rgb, plan.input.mask.shape)
    view = render_coverage(original, plan, options=options, step_count=step_count)
    report = render_report(plan, view)
    arrays = _arrays(plan, constraints)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    png_info = PngImagePlugin.PngInfo()
    png_info.add_text("plan_id", plan.to_dict()["plan_id"])
    png_info.add_text("safety", "executable=false; send_to_controller=false; geometric coverage is not removal")
    report.save(destination / FILES[0], pnginfo=png_info)
    Image.fromarray(original).save(destination / FILES[1])
    np.savez_compressed(destination / FILES[2], **arrays)
    files = {name: {"sha256": _sha((destination / name).read_bytes()),
        "size_bytes": (destination / name).stat().st_size} for name in FILES}
    manifest = {"schema_version": "coverage-preview-v1", "plan": plan.to_dict(), "view": view.to_dict(),
        "source_pixels_sha256": array_hash(original), "files": files,
        "arrays": {key: {"shape": list(value.shape), "dtype": str(value.dtype), "sha256": array_hash(value)}
            for key, value in arrays.items()},
        "executable": False, "feeds_action_request": False, "send_to_controller": False,
        "integrity_semantics": "sha256_corruption_detection_not_signature"}
    # Publish a complete manifest with a same-directory rename. A short write
    # leaves only the temporary file, never a plausible completed archive.
    temporary_manifest = destination / "coverage_preview.json.tmp"
    temporary_manifest.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    temporary_manifest.rename(destination / "coverage_preview.json")
    return destination


@dataclass(frozen=True, eq=False)
class CoverageArchive:
    image_rgb: np.ndarray
    plan: CoveragePlan
    constraints: CoverageConstraints
    options: RenderOptions
    step_count: int


def load_coverage_preview(directory: str | Path) -> CoverageArchive:
    directory = Path(directory)
    manifest_path = directory / "coverage_preview.json"
    if manifest_path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("ARCHIVE_MANIFEST_RESOURCE_LIMIT")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "coverage-preview-v1":
        raise ValueError("ARCHIVE_SCHEMA_UNSUPPORTED")
    stored = manifest["plan"]
    for item in (manifest, stored, manifest["view"]):
        if item.get("executable") is not False or item.get("send_to_controller") is not False:
            raise ValueError("ARCHIVE_HARDWARE_FLAGS_REJECTED")
    if manifest.get("feeds_action_request") is not False or stored.get("feeds_action_request") is not False:
        raise ValueError("ARCHIVE_HARDWARE_FLAGS_REJECTED")
    if set(manifest["files"]) != set(FILES):
        raise ValueError("ARCHIVE_FIXED_FILE_SET_REQUIRED")
    blobs = {}
    for name in FILES:
        path = directory / name
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("ARCHIVE_FILE_RESOURCE_LIMIT")
        blob = path.read_bytes()
        if _sha(blob) != manifest["files"][name]["sha256"] or len(blob) != manifest["files"][name]["size_bytes"]:
            raise ValueError("ARCHIVE_FILE_HASH_MISMATCH")
        blobs[name] = blob
    with zipfile.ZipFile(io.BytesIO(blobs[FILES[2]])) as archive:
        members = archive.infolist()
        if (len(members) > 12 or sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES
                or len({item.filename for item in members}) != len(members)):
            raise ValueError("ARCHIVE_ARRAY_RESOURCE_LIMIT")
        allowed = {"target_mask", "kernel_mask", *EVALUATION_ARRAYS, *CONSTRAINT_ARRAYS}
        if not set(manifest["arrays"]).issubset(allowed) or {item.filename for item in members} != {
                key + ".npy" for key in manifest["arrays"]}:
            raise ValueError("ARCHIVE_ARRAY_SET_MISMATCH")
        # Inspect declared shapes before np.load allocates them, including a
        # tiny compressed file whose header claims a huge array.
        for member in members:
            with archive.open(member) as stream:
                version = np.lib.format.read_magic(stream)
                if version == (1, 0):
                    shape, _, dtype = np.lib.format.read_array_header_1_0(stream)
                elif version == (2, 0):
                    shape, _, dtype = np.lib.format.read_array_header_2_0(stream)
                else:
                    raise ValueError("ARCHIVE_ARRAY_FORMAT_UNSUPPORTED")
                if (len(shape) != 2 or any(v <= 0 for v in shape) or shape[0] * shape[1] > MAX_IMAGE_CELLS
                        or dtype not in (np.dtype(bool), np.dtype(np.uint32))):
                    raise ValueError("ARCHIVE_ARRAY_RESOURCE_OR_DTYPE_INVALID")
    with np.load(io.BytesIO(blobs[FILES[2]]), allow_pickle=False) as data:
        if set(data.files) != set(manifest["arrays"]):
            raise ValueError("ARCHIVE_ARRAY_SET_MISMATCH")
        arrays = {key: immutable_array(data[key]) for key in data.files}
    for key, array in arrays.items():
        meta = manifest["arrays"][key]
        if (list(array.shape) != meta["shape"] or str(array.dtype) != meta["dtype"]
                or array_hash(array) != meta["sha256"]):
            raise ValueError("ARCHIVE_ARRAY_HASH_MISMATCH")
    with Image.open(io.BytesIO(blobs[FILES[1]])) as image:
        if image.width * image.height > MAX_IMAGE_CELLS or image.mode != "RGB":
            raise ValueError("ARCHIVE_IMAGE_RESOURCE_OR_MODE_INVALID")
        original = validate_rgb(np.asarray(image), tuple(stored["input"]["shape_hw"]))
    if array_hash(original) != manifest["source_pixels_sha256"]:
        raise ValueError("ARCHIVE_SOURCE_PIXEL_HASH_MISMATCH")
    meta = stored["input"]
    input = CoverageInput(arrays["target_mask"], **{key: meta[key] for key in (
        "target_id", "mask_kind", "source_ref", "frame_id", "image_ref", "image_sha256", "segmentation_sha256")})
    profile = profile_from_dict(stored["model"]["profile"])
    kernel_meta = stored["model"]["kernel"]
    kernel = None if kernel_meta is None else FootprintKernel(arrays["kernel_mask"], tuple(kernel_meta["anchor_px"]),
        profile, scale_from_dict(kernel_meta["scale"]), kernel_meta["rasterization"])
    model = KernelResult(profile, kernel, tuple(stored["model"]["reason_codes"]))
    constraint_meta = stored["constraints"]
    uncertainty = constraint_meta["position_uncertainty"]
    if uncertainty is not None:
        uncertainty = PositionUncertainty(**{**uncertainty, "evidence": Evidence(**uncertainty["evidence"])})
    constraints = CoverageConstraints(**{key: arrays.get(key) for key in CONSTRAINT_ARRAYS},
        evidence=None if constraint_meta["evidence"] is None else Evidence(**constraint_meta["evidence"]),
        position_uncertainty=uncertainty)
    plan = plan_coverage(input, model, policy=CoveragePolicy(**stored["policy"]), constraints=constraints)
    if canonical_json(plan.to_dict()) != canonical_json(stored):
        raise ValueError("ARCHIVE_DETERMINISTIC_REPLAY_MISMATCH")
    expected_arrays = _arrays(plan, constraints)
    if set(expected_arrays) != set(arrays) or any(array_hash(expected_arrays[k]) != array_hash(arrays[k]) for k in arrays):
        raise ValueError("ARCHIVE_REPLAY_MASK_MISMATCH")
    options = RenderOptions(**manifest["view"]["options"])
    view = render_coverage(original, plan, options=options, step_count=manifest["view"]["step_count"])
    if canonical_json(view.to_dict()) != canonical_json(manifest["view"]):
        raise ValueError("ARCHIVE_REPLAY_VISUALIZATION_MISMATCH")
    return CoverageArchive(original, plan, constraints, options, view.step_count)
