"""P6 optional simulation evidence. Never participates in quality or execution.

The four-file P3 archive is kept intact. A separate binding is published last.
Report reads check bounded files and hashes; deterministic replay is performed
on the export worker, not on Tk or while handling a device action.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from PIL import Image
import numpy as np

from microcleaning.control_system.planning.coverage_archive import (
    FILES, MAX_ARCHIVE_BYTES, export_coverage_preview, load_coverage_preview,
)
from microcleaning.control_system.planning.coverage_model import array_hash, canonical_json, content_hash
from microcleaning.control_system.planning.coverage_planner import MAX_IMAGE_CELLS

FLAGS = ("executable", "feeds_action_request", "send_to_controller")
SCHEMA = "workbench-coverage-v1"


def _safe(root: Path, reference: str) -> Path:
    if not isinstance(reference, str) or not reference:
        raise ValueError("COVERAGE_REFERENCE_REQUIRED")
    path = Path(reference)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("COVERAGE_PATH_OUTSIDE_TASK")
    return path


def _json(path: Path, limit=1024 * 1024):
    if path.stat().st_size > limit:
        raise ValueError("COVERAGE_JSON_RESOURCE_LIMIT")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("COVERAGE_JSON_OBJECT_REQUIRED")
    return value


def _hash(path: Path):
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("COVERAGE_FILE_RESOURCE_LIMIT")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _context(root: Path, binding: dict):
    task = _json(root / "task_manifest.json", 8 * 1024 * 1024)
    if binding.get("task_id") != task.get("task_id"):
        raise ValueError("COVERAGE_TASK_MISMATCH")
    candidate = (task.get("targets") or {}).get(binding.get("target_id"))
    if not isinstance(candidate, dict):
        raise ValueError("COVERAGE_TARGET_MISMATCH")
    stage, cycle, check = binding.get("stage"), binding.get("cycle"), binding.get("recheck")
    if type(cycle) is not int or cycle < 1 or stage not in {"initial", "pre", "post"}:
        raise ValueError("COVERAGE_STAGE_OR_CYCLE_INVALID")
    if stage == "initial":
        if cycle != 1 or check is not None:
            raise ValueError("COVERAGE_INITIAL_IDENTITY_INVALID")
        expected = "cycle_001/pre.json"
    elif stage == "pre":
        if check is not None:
            raise ValueError("COVERAGE_PRE_IDENTITY_INVALID")
        expected = f"cycle_{cycle:03d}/pre.json"
    else:
        if type(check) is not int or check < 1:
            raise ValueError("COVERAGE_RECHECK_INVALID")
        expected = f"cycle_{cycle:03d}/recheck_{check:03d}/post.json"
    if binding.get("source_ref") != expected:
        raise ValueError("COVERAGE_SOURCE_REF_MISMATCH")
    metadata = _json(_safe(root, expected), 8 * 1024 * 1024)
    observation = metadata.get("observation") or {}
    if (observation.get("observation_id") != binding.get("observation_id")
            or observation.get("frame_id") != binding.get("frame_id")):
        raise ValueError("COVERAGE_FRAME_MISMATCH")
    if (binding.get("image_sha256") != metadata.get("raw_sha256")
            or binding.get("segmentation_sha256") != metadata.get("segmentation_sha256")):
        raise ValueError("COVERAGE_SOURCE_HASH_MISMATCH")
    source = _safe(root, binding.get("image_ref"))
    if source != _safe(root, observation.get("raw_image_ref")) or _hash(source) != binding.get("image_sha256"):
        raise ValueError("COVERAGE_SOURCE_IMAGE_MISMATCH")
    if stage == "initial" and source != _safe(root, (task.get("references") or {}).get("initial_image")):
        raise ValueError("COVERAGE_INITIAL_SOURCE_MISMATCH")
    cycle_path = root / f"cycle_{cycle:03d}/cycle.json"
    if stage != "initial" and cycle_path.exists() and _json(cycle_path).get("target_id") != binding.get("target_id"):
        raise ValueError("COVERAGE_CYCLE_TARGET_MISMATCH")
    if stage != "initial":
        roi = metadata.get("roi_segmentation") or {}
        if binding.get("roi_segmentation_sha256") != roi.get("sha256"):
            raise ValueError("COVERAGE_ROI_POLICY_MISMATCH")
    expected_kind = ("manual_region" if stage == "initial" else "manual_roi_segmented") if candidate.get("source") == "manual" else "segmented_stain"
    if binding.get("mask_kind") != expected_kind:
        raise ValueError("COVERAGE_MASK_KIND_MISMATCH")
    return source


def export_bound_preview(image_rgb, plan, run_folder, binding, *, generation, constraints, options, step_count):
    """Freeze caller's click snapshot; export/replay on a background worker."""
    root = Path(run_folder).resolve()
    binding = json.loads(canonical_json(binding))
    source = _context(root, binding)
    with Image.open(source) as image:
        if image.width * image.height > MAX_IMAGE_CELLS:
            raise ValueError("COVERAGE_SOURCE_RESOURCE_LIMIT")
        if array_hash(np.asarray(image.convert("RGB"))) != array_hash(image_rgb):
            raise ValueError("COVERAGE_SOURCE_PIXELS_MISMATCH")
    meta = plan.input.to_dict()
    for key in ("target_id", "mask_kind", "source_ref", "frame_id", "image_ref", "image_sha256", "segmentation_sha256", "mask_sha256"):
        if binding.get(key) != meta.get(key):
            raise ValueError("COVERAGE_PLAN_BINDING_MISMATCH:" + key)
    if type(generation) is not int or generation < 1:
        raise ValueError("COVERAGE_GENERATION_REQUIRED")
    directory = root / "coverage_previews" / uuid4().hex
    # P3 uses a fresh directory; a failed save has no binding.json publication.
    export_coverage_preview(image_rgb, plan, directory / "archive", constraints=constraints,
        options=options, step_count=step_count)
    replay = load_coverage_preview(directory / "archive")
    if replay.plan.to_dict()["plan_id"] != plan.to_dict()["plan_id"]:
        raise ValueError("COVERAGE_EXPORT_REPLAY_MISMATCH")
    binding.update(schema=SCHEMA, generation=generation, plan_id=plan.to_dict()["plan_id"],
        source_pixels_sha256=array_hash(image_rgb), manifest_sha256=_hash(directory / "archive/coverage_preview.json"),
        validation="deterministic_replay_at_export", simulation=True,
        comparison_status="hypothetical_plan_was_not_executed", **dict.fromkeys(FLAGS, False))
    temporary = directory / "binding.json.tmp"
    temporary.write_text(canonical_json(binding) + "\n", encoding="utf-8")
    temporary.rename(directory / "binding.json")
    return directory


def _read_one(root: Path, directory: Path, task_id, targets):
    directory = _safe(root, str(directory))
    if directory.parent != root / "coverage_previews":
        raise ValueError("COVERAGE_ARCHIVE_DIRECTORY_MISMATCH")
    binding = _json(_safe(root, str(directory / "binding.json")))
    if binding.get("schema") != SCHEMA or binding.get("simulation") is not True:
        raise ValueError("COVERAGE_BINDING_SCHEMA_INVALID")
    if binding.get("task_id") != task_id or binding.get("target_id") not in targets:
        raise ValueError("COVERAGE_REPORT_TASK_OR_TARGET_MISMATCH")
    if any(binding.get(key) is not False for key in FLAGS):
        raise ValueError("COVERAGE_HARDWARE_FLAGS_REJECTED")
    _context(root, binding)
    manifest_path = _safe(root, str(directory / "archive/coverage_preview.json"))
    if manifest_path.parent != directory / "archive":
        raise ValueError("COVERAGE_ARCHIVE_PATH_REDIRECTED")
    if _hash(manifest_path) != binding.get("manifest_sha256"):
        raise ValueError("COVERAGE_MANIFEST_HASH_MISMATCH")
    manifest = _json(manifest_path, 8 * 1024 * 1024)
    plan, view = manifest["plan"], manifest["view"]
    if manifest.get("schema_version") != "coverage-preview-v1":
        raise ValueError("COVERAGE_ARCHIVE_SCHEMA_INVALID")
    for item in (manifest, plan, view):
        if any(item.get(key) is not False for key in (FLAGS if item is not view else ("executable", "send_to_controller"))):
            raise ValueError("COVERAGE_HARDWARE_FLAGS_REJECTED")
    if content_hash({key: value for key, value in plan.items() if key != "plan_id"}) != plan["plan_id"]:
        raise ValueError("COVERAGE_PLAN_HASH_MISMATCH")
    if binding["plan_id"] != plan["plan_id"] or view.get("plan_id") != plan["plan_id"]:
        raise ValueError("COVERAGE_PLAN_ID_MISMATCH")
    for key in ("target_id", "mask_kind", "source_ref", "frame_id", "image_ref", "image_sha256", "segmentation_sha256", "mask_sha256"):
        if plan["input"].get(key) != binding.get(key):
            raise ValueError("COVERAGE_PLAN_BINDING_MISMATCH:" + key)
    if manifest.get("source_pixels_sha256") != binding.get("source_pixels_sha256"):
        raise ValueError("COVERAGE_PIXELS_BINDING_MISMATCH")
    if set(manifest["files"]) != set(FILES):
        raise ValueError("COVERAGE_FIXED_FILE_SET_REQUIRED")
    for name in FILES:
        path = _safe(root, str(directory / "archive" / name))
        if path.parent != directory / "archive":
            raise ValueError("COVERAGE_ARCHIVE_PATH_REDIRECTED")
        if _hash(path) != manifest["files"][name]["sha256"] or path.stat().st_size != manifest["files"][name]["size_bytes"]:
            raise ValueError("COVERAGE_ARTIFACT_HASH_MISMATCH")
    return {"binding": binding, "plan_id": plan["plan_id"], "target_id": binding["target_id"],
        "stage": binding["stage"], "cycle": binding["cycle"], "recheck": binding["recheck"],
        "frame_id": binding["frame_id"], "mask_kind": binding["mask_kind"], "status": plan["status"],
        "profile": plan["model"]["profile"], "metrics": plan.get("metrics"), "view_metrics": view.get("display_metrics"),
        "step_count": view["step_count"], "station_count": len(plan["stations"]),
        "overlay_ref": (directory / "archive/coverage_overlay.png").relative_to(root).as_posix(),
        "archive_ref": (directory / "archive").relative_to(root).as_posix(),
        "integrity": "bounded_hash_check_replay_not_run_at_report_read", "simulation": True,
        "comparison_status": "hypothetical_plan_was_not_executed", **dict.fromkeys(FLAGS, False)}


def read_coverage_evidence(root, *, task_id, targets):
    """Absent legacy previews are normal. Optional issues never change quality."""
    root = Path(root).resolve()
    base = root / "coverage_previews"
    if not base.exists():
        return [], []
    previews, issues = [], []
    try:
        base = _safe(root, str(base))
        for index, directory in enumerate(sorted(base.iterdir())):
            if index >= 256:
                issues.append("COVERAGE_PREVIEW_COUNT_LIMIT")
                break
            try:
                previews.append(_read_one(root, directory, task_id, set(targets)))
            except Exception as exc:
                issues.append(f"{directory.name}:{type(exc).__name__}:{exc}")
    except (OSError, ValueError) as exc:
        issues.append(f"COVERAGE_DIRECTORY_UNAVAILABLE:{exc}")
    return previews, issues
