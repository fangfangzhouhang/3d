"""G1 focus calibration from explicit local files; never opens a device.

This module reuses ImageQualityPolicy and inspect_image_file. All input labels
are operator declarations, all images are development/regression evidence, and
every exported profile remains a candidate. It does not pick a production
reference, validate optical settings, or authorize hardware.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import ntpath
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path, PurePosixPath
from typing import Any

from microcleaning.data_learning.image_quality import (
    QUALITY_ALGORITHM_VERSION, ImageQualityPolicy, inspect_image_file,
)

MANIFEST_SCHEMA = "focus-manifest-v1"
REPORT_SCHEMA = "focus-replay-v1"
PROFILE_SCHEMA = "focus-profile-v1"
LABELS = frozenset({"clear", "defocused", "uncertain"})
POLICY_FIELDS = frozenset(field.name for field in fields(ImageQualityPolicy))


@dataclass(frozen=True)
class FocusSample:
    sample_id: str
    relative_path: str
    path: Path
    sha256: str
    label: str
    session_id: str
    batch_id: str
    condition: str


@dataclass(frozen=True)
class FocusManifest:
    path: Path
    sha256: str
    context_json: str
    samples: tuple[FocusSample, ...]

    @property
    def context(self) -> dict[str, Any]:
        return json.loads(self.context_json)


@dataclass(frozen=True)
class FocusProfile:
    quality_policy: ImageQualityPolicy
    context_json: str
    sha256: str

    @property
    def context(self) -> dict[str, Any]:
        return json.loads(self.context_json)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _bad_constant(value: str) -> None:
    raise ValueError("NONFINITE_JSON_NUMBER: " + value)


def _read_json(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8-sig"), parse_constant=_bad_constant)
    if not isinstance(payload, dict):
        raise ValueError("JSON_OBJECT_REQUIRED")
    return raw, payload


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("NONEMPTY_TEXT_REQUIRED: " + field)
    return value


def _digest(value: Any) -> str:
    value = _text(value, "sha256").lower()
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError("SHA256_INVALID")
    return value


def _context(value: Any) -> str:
    if not isinstance(value, dict):
        raise ValueError("CONTEXT_OBJECT_REQUIRED")
    for key in ("setup_id", "sample_material", "magnification", "lighting"):
        _text(value.get(key), key)
    device = value.get("device")
    if not isinstance(device, dict):
        raise ValueError("DEVICE_OBJECT_REQUIRED")
    for key in ("name", "hardware_id"):
        _text(device.get(key), "device." + key)
    resolution = value.get("resolution_px")
    if (not isinstance(resolution, list) or len(resolution) != 2
            or any(type(v) is not int or v <= 0 for v in resolution)):
        raise ValueError("RESOLUTION_REQUIRES_POSITIVE_INTEGER_PAIR")
    return _canonical(value)


def _policy(value: Any) -> ImageQualityPolicy:
    if not isinstance(value, dict) or set(value) != POLICY_FIELDS:
        raise ValueError("QUALITY_POLICY_FIELDS_MISMATCH")
    for name, number in value.items():
        try:
            finite = type(number) in (int, float) and math.isfinite(number)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError("QUALITY_POLICY_FINITE_NUMBER_REQUIRED: " + name)
    for name in ("dark_pixel_max", "bright_pixel_min"):
        if type(value[name]) is not int:
            raise ValueError("QUALITY_POLICY_INTEGER_REQUIRED: " + name)
    result = ImageQualityPolicy(**value)
    result.validate()
    if result.minimum_quality <= 0:
        raise ValueError("POSITIVE_FOCUS_THRESHOLD_REQUIRED")
    return result


def _relative_image(root: Path, value: Any) -> tuple[str, Path]:
    text = _text(value, "path")
    posix = PurePosixPath(text)
    if (ntpath.isabs(text) or posix.is_absolute() or "\\" in text
            or ":" in text or ".." in posix.parts):
        raise ValueError("MANIFEST_RELATIVE_PATH_REQUIRED")
    path = (root / text).resolve()
    if not path.is_relative_to(root):
        raise ValueError("MANIFEST_IMAGE_OUTSIDE_ROOT")
    if not path.is_file():
        raise FileNotFoundError(path)
    return posix.as_posix(), path


def load_focus_manifest(path: str | Path) -> FocusManifest:
    """Load operator labels and verify original file bytes before replay."""
    source = Path(path).resolve()
    raw, body = _read_json(source)
    if body.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("FOCUS_MANIFEST_SCHEMA_UNSUPPORTED")
    if body.get("purpose") != "development_regression":
        raise ValueError("DEVELOPMENT_MANIFEST_REQUIRED")
    context_json = _context(body.get("context"))
    items = body.get("images")
    if not isinstance(items, list) or not items:
        raise ValueError("MANIFEST_IMAGES_REQUIRED")
    samples: list[FocusSample] = []
    ids: set[str] = set()
    paths: set[Path] = set()
    labels_by_hash: dict[str, set[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("MANIFEST_IMAGE_OBJECT_REQUIRED")
        sample_id = _text(item.get("sample_id"), "sample_id")
        relative, image_path = _relative_image(source.parent, item.get("path"))
        if sample_id in ids or image_path in paths:
            raise ValueError("DUPLICATE_SAMPLE_ID_OR_PATH")
        if item.get("label_source") != "operator_confirmed":
            raise ValueError("OPERATOR_LABEL_REQUIRED")
        label = item.get("label")
        if not isinstance(label, str) or label not in LABELS:
            raise ValueError("FOCUS_LABEL_UNSUPPORTED")
        expected = _digest(item.get("sha256"))
        actual = hashlib.sha256(image_path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError("IMAGE_SHA256_MISMATCH: " + relative)
        known_labels = labels_by_hash.setdefault(expected, set())
        known_labels.add(label)
        if {"clear", "defocused"} <= known_labels:
            raise ValueError("CONFLICTING_LABELS_FOR_SAME_FILE_BYTES")
        ids.add(sample_id)
        paths.add(image_path)
        samples.append(FocusSample(
            sample_id, relative, image_path, expected, label,
            _text(item.get("session_id"), "session_id"),
            _text(item.get("batch_id"), "batch_id"),
            _text(item.get("condition"), "condition"),
        ))
    if not {"clear", "defocused"} <= {sample.label for sample in samples}:
        raise ValueError("CLEAR_AND_DEFOCUSED_LABELS_REQUIRED")
    return FocusManifest(source, hashlib.sha256(raw).hexdigest(),
                         context_json, tuple(samples))


def _quality_result(inspection: Any, label: str) -> dict[str, Any]:
    flags = list(inspection.quality.flags())
    focus_low = "FOCUS_LOW" in flags
    correct = None if label == "uncertain" else focus_low == (label == "defocused")
    return {
        **asdict(inspection.quality), "quality_flags": flags,
        "focus_low": focus_low, "focus_matches_operator_label": correct,
    }


def _counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    return {
        "clear": sum(row["label"] == "clear" for row in rows),
        "defocused": sum(row["label"] == "defocused" for row in rows),
        "uncertain": sum(row["label"] == "uncertain" for row in rows),
        "missed_defocused": sum(
            row["label"] == "defocused" and not row[key]["focus_low"] for row in rows),
        "flagged_clear": sum(
            row["label"] == "clear" and row[key]["focus_low"] for row in rows),
        "correct_focus_classifications": sum(
            row[key]["focus_matches_operator_label"] is True for row in rows),
    }


def evaluate_focus_manifest(
    manifest: FocusManifest | str | Path, *,
    candidate_policy: ImageQualityPolicy | None = None,
) -> dict[str, Any]:
    """Report observed errors and a development-only interval; choose no value."""
    if not isinstance(manifest, FocusManifest):
        manifest = load_focus_manifest(manifest)
    default_policy = _policy(asdict(ImageQualityPolicy()))
    if candidate_policy is not None:
        candidate_policy = _policy(asdict(candidate_policy))
    rows: list[dict[str, Any]] = []
    for sample in manifest.samples:
        original = inspect_image_file(sample.path, policy=default_policy)
        if original.sha256 != sample.sha256:
            raise ValueError("IMAGE_CHANGED_DURING_REPLAY")
        if [original.metrics.width_px, original.metrics.height_px] != manifest.context["resolution_px"]:
            raise ValueError("IMAGE_RESOLUTION_CONTEXT_MISMATCH")
        row = {
            "sample_id": sample.sample_id, "path": sample.relative_path,
            "sha256": sample.sha256, "label": sample.label,
            "session_id": sample.session_id, "batch_id": sample.batch_id,
            "condition": sample.condition, "metrics": asdict(original.metrics),
            "default": _quality_result(original, sample.label),
        }
        if candidate_policy is not None:
            candidate = inspect_image_file(sample.path, policy=candidate_policy)
            if candidate.sha256 != sample.sha256:
                raise ValueError("IMAGE_CHANGED_DURING_REPLAY")
            row["candidate"] = _quality_result(candidate, sample.label)
        rows.append(row)
    threshold = (candidate_policy or default_policy).minimum_quality
    clear_min = min(row["metrics"]["laplacian_variance"] for row in rows if row["label"] == "clear")
    defocus_max = max(row["metrics"]["laplacian_variance"] for row in rows if row["label"] == "defocused")
    lower, upper = defocus_max / threshold, clear_min / threshold
    duplicate_groups: dict[str, list[str]] = {}
    for row in rows:
        duplicate_groups.setdefault(row["sha256"], []).append(row["sample_id"])
    return {
        "schema": REPORT_SCHEMA, "purpose": "development_regression",
        "manifest_sha256": manifest.sha256, "context": manifest.context,
        "quality_algorithm_version": QUALITY_ALGORITHM_VERSION,
        "default_policy": asdict(default_policy),
        "candidate_policy": None if candidate_policy is None else asdict(candidate_policy),
        "default_counts": _counts(rows, "default"),
        "candidate_counts": None if candidate_policy is None else _counts(rows, "candidate"),
        "reference_interval": {
            "minimum_quality": threshold, "lower_exclusive": lower,
            "upper_inclusive": upper, "separable_on_development_images": lower < upper,
            "chosen_reference": None,
        },
        "declared_session_count": len({sample.session_id for sample in manifest.samples}),
        "declared_batch_count": len({sample.batch_id for sample in manifest.samples}),
        "duplicate_file_hash_groups": [
            ids for ids in duplicate_groups.values() if len(ids) > 1
        ],
        "independent_validation_status": "not_performed",
        "can_claim_validated": False, "rows": rows,
        "evidence_boundary": (
            "Operator labels and file hashes are declarations, not authenticated truth. "
            "Same-session frames are not independent validation. The interval is not a "
            "recommended reference. No automatic camera-setting check or hardware authorization."
        ),
    }


def _profile_body(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("schema") != REPORT_SCHEMA or report.get("candidate_policy") is None:
        raise ValueError("EXPLICIT_CANDIDATE_REQUIRED")
    policy = _policy(report["candidate_policy"])
    return {
        "schema": PROFILE_SCHEMA, "status": "candidate",
        "context": json.loads(_context(report["context"])),
        "quality_policy": asdict(policy),
        "source_manifest_sha256": _digest(report["manifest_sha256"]),
        "source_images": [
            {key: row[key] for key in ("sample_id", "sha256", "label", "session_id", "batch_id")}
            for row in report["rows"]
        ],
        "independent_validation_status": "not_performed",
        "evidence_boundary": "Offline candidate only; not a validated runtime quality gate.",
    }


def write_candidate_profile(report: dict[str, Any], path: str | Path) -> Path:
    """Export only the explicitly evaluated candidate; never select or promote."""
    body = _profile_body(report)
    body["content_sha256"] = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(body, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return destination


def load_focus_profile(
    path: str | Path, *, expected_context: dict[str, Any] | None = None,
) -> FocusProfile:
    """Load a candidate as an immutable policy for offline consumers."""
    source = Path(path)
    raw, body = _read_json(source)
    if body.get("schema") != PROFILE_SCHEMA:
        raise ValueError("FOCUS_PROFILE_SCHEMA_UNSUPPORTED")
    if body.get("status") != "candidate" or body.get("independent_validation_status") != "not_performed":
        raise ValueError("ONLY_OFFLINE_CANDIDATE_SUPPORTED")
    digest = _digest(body.get("content_sha256"))
    unhashed = {key: value for key, value in body.items() if key != "content_sha256"}
    if hashlib.sha256(_canonical(unhashed).encode("utf-8")).hexdigest() != digest:
        raise ValueError("PROFILE_CONTENT_SHA256_MISMATCH")
    policy = _policy(body.get("quality_policy"))
    context_json = _context(body.get("context"))
    if expected_context is not None and context_json != _context(expected_context):
        raise ValueError("PROFILE_CONTEXT_MISMATCH")
    _digest(body.get("source_manifest_sha256"))
    evidence = body.get("source_images")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("PROFILE_SOURCE_IMAGES_REQUIRED")
    labels: set[str] = set()
    ids: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError("PROFILE_SOURCE_IMAGE_OBJECT_REQUIRED")
        sample_id = _text(item.get("sample_id"), "sample_id")
        if sample_id in ids:
            raise ValueError("PROFILE_DUPLICATE_SOURCE_ID")
        ids.add(sample_id)
        _digest(item.get("sha256"))
        for key in ("session_id", "batch_id"):
            _text(item.get(key), key)
        if not isinstance(item.get("label"), str) or item["label"] not in LABELS:
            raise ValueError("FOCUS_LABEL_UNSUPPORTED")
        labels.add(item["label"])
    if not {"clear", "defocused"} <= labels:
        raise ValueError("PROFILE_SOURCE_CLASSES_REQUIRED")
    return FocusProfile(policy, context_json, hashlib.sha256(raw).hexdigest())


def render_focus_report(report: dict[str, Any]) -> str:
    interval = report["reference_interval"]
    lines = [
        "# G1 focus replay / 对焦质量离线回放", "",
        "**仅开发/回归；没有独立验证，不授予硬件动作权限。**", "",
        "| 样例 | 操作者标签 | 原始方差 | 默认focus | 默认flags | 候选focus | 候选flags |",
        "|---|---|---:|---:|---|---:|---|",
    ]
    for row in report["rows"]:
        candidate = row.get("candidate")
        lines.append("| " + " | ".join((
            row["sample_id"].replace("|", "\\|"), row["label"],
            f'{row["metrics"]["laplacian_variance"]:.6f}',
            f'{row["default"]["focus"]:.6f}',
            ", ".join(row["default"]["quality_flags"]) or "无",
            "-" if candidate is None else f'{candidate["focus"]:.6f}',
            "-" if candidate is None else ", ".join(candidate["quality_flags"]) or "无",
        )) + " |")
    lines += [
        "", "开发图算术区间（下界开、上界闭）：",
        f'- {interval["lower_exclusive"]:.6f} < reference <= {interval["upper_inclusive"]:.6f}',
        f'- 此批是否可分离：{interval["separable_on_development_images"]}；未自动选择参考值。',
        f'- 声明采集会话数：{report["declared_session_count"]}；同会话帧不当独立留出。',
        "- 样本、倍率、灯光未知项按原记录保留；更换条件不能沿用本次结论。",
        "- 旧默认策略的真实漏报、候选误报/漏报均在report.json保留。",
        "- 候选配置不接入工作台、ROI或硬件；默认评分和0.25px位置规则未改变。", "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    candidate = parser.add_mutually_exclusive_group()
    candidate.add_argument("--candidate-reference", type=float)
    candidate.add_argument("--profile", type=Path, help="已有candidate配置，仅离线回放")
    parser.add_argument("--export-profile", action="store_true")
    args = parser.parse_args(argv)
    if args.export_profile and args.candidate_reference is None:
        parser.error("--export-profile requires explicit --candidate-reference")
    try:
        manifest = load_focus_manifest(args.manifest)
        policy = None
        loaded = None
        if args.candidate_reference is not None:
            policy = _policy({**asdict(ImageQualityPolicy()), "focus_reference": args.candidate_reference})
        elif args.profile is not None:
            loaded = load_focus_profile(args.profile, expected_context=manifest.context)
            policy = loaded.quality_policy
        report = evaluate_focus_manifest(manifest, candidate_policy=policy)
        if loaded is not None:
            report["loaded_profile_sha256"] = loaded.sha256
        args.output_dir.mkdir(parents=True, exist_ok=False)
        if args.export_profile:
            write_candidate_profile(report, args.output_dir / "candidate_profile.json")
        (args.output_dir / "report.md").write_text(render_focus_report(report), encoding="utf-8")
        with (args.output_dir / "report.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        print(json.dumps({
            "report": str((args.output_dir / "report.json").resolve()),
            "default_counts": report["default_counts"],
            "candidate_counts": report["candidate_counts"],
            "reference_interval": report["reference_interval"],
            "independent_validation_status": "not_performed",
        }, ensure_ascii=True, allow_nan=False))
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
