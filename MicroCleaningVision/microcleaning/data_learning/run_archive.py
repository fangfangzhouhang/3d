"""Read-only run export into an external, versioned archive (standard library only).

CLI, from MicroCleaningVision::

    python -m microcleaning.data_learning.run_archive --run output/task --data-root D:/MicroCleaningData
    python -m microcleaning.data_learning.run_archive --source-root output --data-root D:/MicroCleaningData
    python -m microcleaning.data_learning.run_archive --verify D:/MicroCleaningData/runs/real/ARCHIVE_ID

``raw/`` preserves the entire source tree. Only published directories are archives;
``.partial-*`` directories retain failed copies for inspection and are never valid.
Training eligibility is deliberately opt-in in a later reviewed dataset workflow.
This exporter preserves evidence, and does not create cleaning or damage labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from microcleaning.control_system.reporting.evidence_reader import read_run, resolve_asset

SCHEMA = "microcleaning-run-archive-v1"
ORIGINS = {"real", "mock", "synthetic", "manual_change", "unknown"}
OBSERVATION_PATH = re.compile(r"^(cycle_\d{3,})/(?:recheck_\d{3,}/)?(pre|post)\.json$")


class ArchiveError(ValueError):
    """Unsafe destination, changed evidence, or inconsistent archive."""


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _encode(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _obj(value) -> dict:
    return value if isinstance(value, dict) else {}


def _known(value):
    return None if value in (None, "", "未记录", "未确认", "unrecorded", "unknown", "unconfirmed") else value


def _inventory(root: Path) -> tuple[list[dict], list[str]]:
    """Never follow symlinks/junctions or silently omit files outside the run."""
    if not root.is_dir() or root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        raise ArchiveError("INVALID_TREE_ROOT:" + str(root))
    files, directories = [], []
    for parent, dirs, names in os.walk(root, followlinks=False):
        for name in sorted(dirs + names):
            path = Path(parent) / name
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise ArchiveError(f"LINK_NOT_ALLOWED:{path}")
            if not path.resolve().is_relative_to(root):
                raise ArchiveError(f"FILE_OUTSIDE_RUN:{path}")
            relative = path.relative_to(root).as_posix()
            if path.is_dir():
                directories.append(relative)
            elif path.is_file():
                files.append({"path": relative, "sha256": _digest(path), "size_bytes": path.stat().st_size})
            else:
                raise ArchiveError(f"SPECIAL_FILE_NOT_ALLOWED:{path}")
    return sorted(files, key=lambda x: x["path"]), sorted(directories)


def _read_json(path: Path, issues: list[str], relative: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"),
                           parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return value
    except (OSError, ValueError) as exc:
        issues.append(f"INVALID_JSON:{relative}:{type(exc).__name__}")
        return {}


def verify_archive(folder: str | Path) -> dict:
    """Independently hash every archived source byte, including extra-file detection.

    The manifest cannot hash itself; this checks file integrity, not authenticity
    against an attacker who can rewrite both manifest and data.
    """
    root = Path(folder).resolve()
    if root.name.startswith("."):
        raise ArchiveError("UNPUBLISHED_ARCHIVE")
    manifest = _read_json(root / "run_manifest.json", [], "run_manifest.json")
    if (root / "run_manifest.json").is_symlink():
        raise ArchiveError("UNSAFE_ARCHIVE_MANIFEST")
    if manifest.get("schema") != SCHEMA or manifest.get("archive_id") != root.name:
        raise ArchiveError("INVALID_ARCHIVE_MANIFEST")
    files, directories = _inventory(root / "raw")
    if files != manifest.get("files") or directories != manifest.get("directories"):
        raise ArchiveError("ARCHIVE_CONTENT_MISMATCH")
    if sorted(p.name for p in root.iterdir()) != ["raw", "run_manifest.json"]:
        raise ArchiveError("UNEXPECTED_ARCHIVE_CONTENT")
    return manifest


class RunArchiveExporter:
    """Export without mutating the source. data_root is explicitly supplied.

    Non-mock origins use runs/real; data_origin remains authoritative and unknown
    or synthetic data is never promoted to real evidence by its storage bucket.
    A per-ID exclusive lock serializes cooperating exporters. Interrupted locks
    and partial folders are kept for operator inspection, never recursively erased.
    """

    def __init__(self, data_root: str | Path):
        self.data_root = Path(data_root).resolve()
        project = Path(__file__).resolve().parents[2]
        self._repositories = [project]
        self._repositories.extend(p for p in project.parents if (p / ".git").exists())
        self._validate_destination()

    def _validate_destination(self, source: Path | None = None):
        repositories = list(self._repositories)
        if source:
            repositories.extend(p for p in (source, *source.parents) if (p / ".git").exists())
            if self.data_root.is_relative_to(source) or source.is_relative_to(self.data_root):
                raise ArchiveError("SOURCE_DESTINATION_OVERLAP")
        if any(self.data_root.is_relative_to(repo) for repo in repositories):
            raise ArchiveError("DATA_ROOT_INSIDE_REPOSITORY")
        if any((p / ".git").exists() for p in (self.data_root, *self.data_root.parents)):
            raise ArchiveError("DATA_ROOT_INSIDE_REPOSITORY")

    def _manifest(self, source: Path, files: list[dict], directories: list[str]) -> dict:
        issues, documents = [], {}
        for item in files:
            relative = item["path"]
            if relative.endswith(".json"):
                documents[relative] = _read_json(source / relative, issues, relative)
            elif relative.endswith(".jsonl"):
                try:
                    for number, line in enumerate((source / relative).read_text(encoding="utf-8-sig").splitlines(), 1):
                        if line.strip() and not isinstance(json.loads(line), dict):
                            raise ValueError(f"line {number}")
                except (OSError, ValueError) as exc:
                    issues.append(f"INVALID_JSONL:{relative}:{type(exc).__name__}")
        for required in ("run_config.json", "task_manifest.json", "summary.json", "stage_events.jsonl", "serial.json"):
            if required not in {f["path"] for f in files}:
                issues.append("MISSING_FILE:" + required)
        config, summary, task = (documents.get(p, {}) for p in ("run_config.json", "summary.json", "task_manifest.json"))
        metadata = _obj(config.get("metadata"))
        if not metadata:
            issues.append("MISSING_METADATA")
        if _known(metadata.get("sample_id")) is None:
            issues.append("MISSING_SAMPLE_ID")
        ids = [d.get("task_id") for d in (config, summary, task) if d.get("task_id")]
        run_id = ids[0] if ids and isinstance(ids[0], str) else None
        if any(i != run_id for i in ids):
            issues.append("TASK_ID_CONFLICT")
        if run_id is None:
            issues.append("MISSING_TASK_ID")
        identity = run_id if run_id is not None else str(source)
        archive_id = ("run-" if run_id else "orphan-") + hashlib.sha256(identity.encode()).hexdigest()
        mode = summary.get("mode") or _obj(task.get("metadata")).get("mode") or metadata.get("mode") or "unknown"
        origin = metadata.get("data_origin", "unknown")
        if not isinstance(origin, str) or origin not in ORIGINS:
            origin = "unknown"
            issues.append("INVALID_DATA_ORIGIN")
        # Recorded mock mode is reliable negative evidence even without metadata.
        if mode == "mock":
            if origin not in {"mock", "unknown"}:
                issues.append("MODE_ORIGIN_CONFLICT")
            origin = "mock"
        if origin == "unknown":
            issues.append("UNKNOWN_DATA_ORIGIN")
        try:
            evidence = read_run(source)
            issues.extend(evidence.get("evidence_issues", []))
        except (ValueError, TypeError, AttributeError, KeyError, OSError) as exc:
            issues.append("EVIDENCE_READER_FAILED:" + type(exc).__name__)
            evidence = {}
        workflow = evidence.get("workflow_status", "UNRECORDED")
        segmentation = config.get("segmentation_sha256")
        file_map = {f["path"]: f for f in files}
        observations, cycles, episodes, actions = [], [], [], []

        def asset(reference, expected, label):
            if reference is not None and not isinstance(reference, str):
                issues.append(label + ":INVALID_REFERENCE")
                return None, None
            relative, error = resolve_asset(source, reference, expected)
            if error:
                issues.append(label + ":" + error)
            record = file_map.get(relative)
            if relative and record is None:
                issues.append(label + ":UNINDEXED_REFERENCE")
            return relative, record["sha256"] if record else None

        for relative, document in sorted(documents.items()):
            match = OBSERVATION_PATH.fullmatch(relative)
            if not match:
                continue
            cycle_id, stage = match.groups()
            cycle = documents.get(f"{cycle_id}/cycle.json", {})
            obs = _obj(document.get("observation")) or document
            image_ref, image_hash = asset(obs.get("raw_image_ref"), document.get("raw_sha256"), relative + ":IMAGE")
            measurement = _obj(document.get("measurement"))
            default_mask = str(Path(relative).with_name(stage + "_mask.png")).replace("\\", "/")
            reference = document.get("mask_ref") or measurement.get("mask_ref") or (default_mask if default_mask in file_map else None)
            mask_ref, mask_hash = asset(reference, document.get("mask_sha256"), relative + ":MASK")
            # Existing masks are algorithm output; unreviewed declarations cannot promote them.
            semantics = document.get("mask_semantics", "predicted_segmentation")
            if not isinstance(semantics, str) or semantics not in {"predicted_segmentation", "manual_region", "ground_truth", "simulation_only"}:
                semantics = "predicted_segmentation"
                issues.append(relative + ":UNKNOWN_MASK_SEMANTICS")
            if semantics == "ground_truth":
                issues.append(relative + ":GROUND_TRUTH_REQUIRES_SEPARATE_REVIEW")
                semantics = "predicted_segmentation"
            observations.append({"observation_id": obs.get("observation_id"), "frame_id": obs.get("frame_id"),
                "run_id": run_id, "cycle_id": cycle_id, "target_id": cycle.get("target_id"),
                "stage": stage, "recheck_id": next((p for p in Path(relative).parts if p.startswith("recheck_")), None),
                "record_ref": relative, "source_id": document.get("source_id"),
                "captured_at": document.get("captured_at") or obs.get("timestamp"), "settings": document.get("settings"),
                "original_image_ref": obs.get("raw_image_ref"), "original_mask_ref": reference,
                "declared_image_sha256": document.get("raw_sha256"), "declared_mask_sha256": document.get("mask_sha256"),
                "image_ref": image_ref, "image_sha256": image_hash, "mask_ref": mask_ref, "mask_sha256": mask_hash,
                "declared_mask_semantics": document.get("mask_semantics"),
                "mask_semantics": semantics, "quality_flags": obs.get("quality_flags", []),
                "segmentation_sha256": document.get("segmentation_sha256") or segmentation,
                "visual_ground_truth_eligible": False})
            if not obs.get("observation_id") or not obs.get("frame_id"):
                issues.append(relative + ":MISSING_OBSERVATION_IDENTITY")
            if obs.get("task_id") not in (None, run_id):
                issues.append(relative + ":OBSERVATION_TASK_ID_CONFLICT")

        for relative, episode in sorted(documents.items()):
            if "/episodes/" not in "/" + relative and "episode_id" not in episode:
                continue
            cycle_id = next((p for p in Path(relative).parts if re.fullmatch(r"cycle_\d{3,}", p)), None)
            record = documents.get(f"{cycle_id}/cycle.json", {})
            if not episode.get("episode_id"):
                issues.append(relative + ":MISSING_EPISODE_ID")
            episodes.append({"episode_id": episode.get("episode_id"), "run_id": run_id,
                "cycle_id": cycle_id, "target_id": record.get("target_id"), "record_ref": relative,
                "action_id": _obj(episode.get("action_request")).get("action_id"),
                "pre_observation_id": _obj(episode.get("observation_pre")).get("observation_id"),
                "post_observation_id": _obj(episode.get("observation_post")).get("observation_id")})
        cycle_ids = sorted({p.split("/")[0] for p in documents if re.match(r"cycle_\d{3,}/", p)})
        attempts = {f"cycle_{a['cycle']:03d}": a for a in evidence.get("attempts", [])}
        for cycle_id in cycle_ids:
            record = documents.get(f"{cycle_id}/cycle.json", {})
            attempt = attempts.get(cycle_id, {})
            execution = documents.get(f"{cycle_id}/execution.json", {}) or _obj(record.get("execution"))
            action_ids = sorted({x for x in [attempt.get("action_id"),
                _obj(execution.get("pump_request")).get("action_id"),
                *(e["action_id"] for e in episodes if e["cycle_id"] == cycle_id)] if isinstance(x, str)})
            for reference, request in [(f"{cycle_id}/execution.json", _obj(execution.get("pump_request"))),
                    *[(e["record_ref"], _obj(documents[e["record_ref"]].get("action_request")))
                      for e in episodes if e["cycle_id"] == cycle_id]]:
                if request.get("action_id"):
                    actions.append({"action_id": request["action_id"], "run_id": run_id, "cycle_id": cycle_id,
                        "target_id": record.get("target_id"), "record_ref": reference, "request": request})
            checks = []
            check_ids = sorted({p.split("/")[1] for p in documents
                if p.startswith(cycle_id + "/") and re.match(r"recheck_\d{3,}$", p.split("/")[1])})
            for check_id in check_ids:
                prefix = cycle_id + "/" + check_id + "/"
                checks.append({"recheck_id": check_id,
                    "observation_refs": [o["record_ref"] for o in observations if o["record_ref"].startswith(prefix)],
                    "verification_ref": prefix + "verification.json" if prefix + "verification.json" in documents else None,
                    "verification": documents.get(prefix + "verification.json")})
            cycles.append({"cycle_id": cycle_id, "run_id": run_id, "target_id": record.get("target_id"),
                "record_ref": f"{cycle_id}/cycle.json" if f"{cycle_id}/cycle.json" in documents else None,
                "action_ids": action_ids, "episode_ids": [e["episode_id"] for e in episodes if e["cycle_id"] == cycle_id],
                "observation_refs": [o["record_ref"] for o in observations if o["cycle_id"] == cycle_id],
                "rechecks": checks, "normalized_recheck_evidence": attempt.get("rechecks", []), "verification": attempt.get("verification"),
                "damage_status": "not_assessed", "measured_pressure": None, "measured_flow": None,
                "cleaning_result_label": None, "cleaning_effect_eligible": False})
        if workflow != "COMPLETED":
            issues.append("WORKFLOW_NOT_COMPLETED")
        if not observations:
            issues.append("NO_OBSERVATIONS")
        return {"schema": SCHEMA, "run_id": run_id, "archive_id": archive_id,
            "sample_id": _known(metadata.get("sample_id")), "experiment_id": _known(metadata.get("experiment_id")),
            "mode": mode, "data_origin": origin, "workflow_status": workflow,
            "quality_status": evidence.get("quality_status", "NOT_ASSESSED"),
            "git_commit": _known(metadata.get("git_commit")), "git_dirty": metadata.get("git_dirty") if isinstance(metadata.get("git_dirty"), bool) else None,
            "source_sha256": _known(metadata.get("source_sha256")), "segmentation_sha256": segmentation,
            "firmware_identity": _known(metadata.get("firmware_identity")),
            "raw_root": "raw", "reference_base": "raw", "files": files, "directories": directories,
            "observations": observations, "cycles": cycles, "episodes": episodes, "actions": actions,
            "quality_issues": sorted(set(issues)),
            "training_eligibility": {"real_cleaning_effect": False, "visual_ground_truth": False,
                "reasons": ["ARCHIVE_IS_NOT_A_REVIEWED_DATASET", "NO_NEW_LABELS_GENERATED",
                            *(["NON_REAL_ORIGIN"] if origin != "real" else []),
                            *(["INCOMPLETE_WORKFLOW"] if workflow != "COMPLETED" else [])]},
            "damage_status": "not_assessed", "measured_pressure": None, "measured_flow": None}

    def export_run(self, run_dir: str | Path) -> dict:
        source = Path(run_dir).resolve()
        if not source.is_dir():
            raise ArchiveError("SOURCE_NOT_DIRECTORY")
        self._validate_destination(source)
        files, directories = _inventory(source)
        manifest = self._manifest(source, files, directories)
        bucket = "mock" if manifest["mode"] == "mock" or manifest["data_origin"] == "mock" else "real"
        parent = self.data_root / "runs" / bucket
        # Recheck resolved routing directories, including preexisting junctions.
        if parent.resolve() != parent or not parent.resolve().is_relative_to(self.data_root):
            raise ArchiveError("UNSAFE_ARCHIVE_PARENT")
        parent.mkdir(parents=True, exist_ok=True)
        destination = parent / manifest["archive_id"]
        lock = parent.parent / ("." + manifest["archive_id"] + ".lock")
        try:
            handle = lock.open("x", encoding="ascii")
        except FileExistsError as exc:
            raise ArchiveError("ARCHIVE_LOCKED:" + str(lock)) from exc
        with handle:
            try:
                other = parent.parent / ("real" if bucket == "mock" else "mock") / manifest["archive_id"]
                if other.exists() or other.is_symlink():
                    raise ArchiveError("ARCHIVE_ID_CONTENT_CONFLICT:OTHER_BUCKET")
                if destination.exists():
                    if destination.is_symlink() or destination.resolve() != destination:
                        raise ArchiveError("UNSAFE_EXISTING_ARCHIVE")
                    previous = verify_archive(destination)
                    if previous != manifest or _inventory(source) != (files, directories):
                        raise ArchiveError("ARCHIVE_ID_CONTENT_CONFLICT")
                    return {"status": "skipped", "path": str(destination), "archive_id": manifest["archive_id"]}
                temporary = Path(tempfile.mkdtemp(prefix=".partial-", dir=parent))
                raw = temporary / "raw"
                raw.mkdir()
                for directory in directories:
                    (raw / directory).mkdir(parents=True, exist_ok=True)
                for item in files:
                    target = raw / item["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source / item["path"], target)
                if _inventory(raw) != (files, directories) or _inventory(source) != (files, directories):
                    raise ArchiveError("SOURCE_CHANGED_OR_COPY_MISMATCH")
                with (temporary / "run_manifest.json").open("xb") as stream:
                    stream.write(_encode(manifest))
                    stream.flush()
                    os.fsync(stream.fileno())
                if destination.exists():
                    raise ArchiveError("ARCHIVE_ID_CONTENT_CONFLICT")
                # Same-filesystem rename: readers see either no archive or a full one.
                os.rename(temporary, destination)
                return {"status": "exported", "path": str(destination), "archive_id": manifest["archive_id"]}
            finally:
                # Close before unlink for Windows. Only our own transient lock is removed.
                handle.close()
                lock.unlink()

    def export_new_runs(self, source_root: str | Path) -> list[dict]:
        """Discover roots once, prune each run, and isolate failures per run.

        A partial startup is discoverable by config, summary, manifest or events;
        arbitrary image folders are not silently treated as experiments.
        """
        source = Path(source_root).resolve()
        self._validate_destination(source)
        if not source.is_dir():
            raise ArchiveError("SOURCE_NOT_DIRECTORY")
        results = []
        markers = {"run_config.json", "summary.json", "task_manifest.json", "stage_events.jsonl"}
        for parent, dirs, names in os.walk(source, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (Path(parent) / d).is_symlink()
                             and not (hasattr(Path, "is_junction") and (Path(parent) / d).is_junction()))
            if markers.intersection(names):
                dirs[:] = []
                try:
                    results.append(self.export_run(parent))
                except (OSError, ArchiveError) as exc:
                    results.append({"status": "failed", "source": str(parent), "error": str(exc)})
        return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--run", type=Path)
    inputs.add_argument("--source-root", type=Path)
    inputs.add_argument("--verify", type=Path)
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args(argv)
    if not args.verify and args.data_root is None:
        parser.error("--data-root is required for export")
    try:
        if args.verify:
            verify_archive(args.verify)
            result = {"status": "verified", "path": str(args.verify)}
        else:
            exporter = RunArchiveExporter(args.data_root)
            result = exporter.export_run(args.run) if args.run else exporter.export_new_runs(args.source_root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(isinstance(result, list) and any(r["status"] == "failed" for r in result))
    except (OSError, ArchiveError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
