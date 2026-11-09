"""Sequential configuration runner with per-probe and per-image failure records."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import traceback
import uuid
from typing import Any

from .base import ImageInput, ModelInfo, ProbeBlocked, ProbeOutput, ProbeResult
from . import registry

ROOT = Path(__file__).resolve().parents[1]
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: Any) -> None:
    """Strict JSON: NaN is not an acceptable missing measurement."""
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(data, encoding="utf-8")
    temporary.replace(path)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def inside(root: Path, reference: str | Path) -> Path:
    path = (root / reference).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Path must stay inside the experiment directory: {reference}")
    return path


def _identifier(value: Any, kind: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"Invalid {kind}: {value!r}")
    return value


def _validate_config(config: dict[str, Any], root: Path) -> tuple[dict[str, ImageInput], list[dict[str, Any]]]:
    if config.get("schema") != "probe-config-v1":
        raise ValueError("Expected schema=probe-config-v1")
    if not isinstance(config.get("images"), list) or not config["images"]:
        raise ValueError("images must be a nonempty list")
    images = {}
    for item in config["images"]:
        image_id = _identifier(item["image_id"], "image_id")
        if image_id in images:
            raise ValueError(f"Duplicate image_id: {image_id}")
        if not isinstance(item.get("raw_image_ref"), str) or not item["raw_image_ref"]:
            raise ValueError("raw_image_ref must be a nonempty string")
        if not isinstance(item.get("metadata", {}), dict):
            raise ValueError("image metadata must be an object")
        images[image_id] = ImageInput(image_id, inside(root, item["raw_image_ref"]), copy.deepcopy(item.get("metadata", {})))
    if not isinstance(config.get("probes"), list) or not config["probes"]:
        raise ValueError("probes must be a nonempty list")
    seen = set()
    specs = []
    for item in config["probes"]:
        name = _identifier(item["name"], "probe name")
        if name in seen:
            raise ValueError(f"Duplicate probe name would overwrite results: {name}")
        seen.add(name)
        params = item.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("probe params must be an object")
        if name in {"dino", "micro_sam", "patchcore"} and "device" not in params:
            raise ValueError(f"Probe {name}: params.device must be explicit (cpu or cuda[:index]); implicit CUDA could bypass the GPU guard")
        if "device" in params and (not isinstance(params["device"], str) or not re.fullmatch(r"cpu|cuda(?::[0-9]+)?", params["device"])):
            raise ValueError(f"Probe {name}: device must be cpu or cuda[:index]")
        selected = item.get("images", list(images))
        if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected):
            raise ValueError(f"Probe {name}: images must be a nonempty list without duplicates")
        if any(image_id not in images for image_id in selected):
            raise ValueError(f"Probe {name}: unknown image reference")
        specs.append({"name": name, "params": copy.deepcopy(params), "images": list(selected)})
    return images, specs


def _environment(root: Path) -> dict[str, Any]:
    packages = {}
    for name in ("torch", "torchvision", "numpy", "pillow", "sam-2"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    git = {}
    for key, arguments in (("base_commit", ["rev-parse", "HEAD"]), ("branch", ["branch", "--show-current"])):
        try:
            git[key] = subprocess.check_output(["git", *arguments], cwd=root, text=True, stderr=subprocess.STDOUT).strip()
        except (OSError, subprocess.CalledProcessError):
            git[key] = None
    paths = sorted((root / "probes").rglob("*.py")) + [root / name for name in
             ("run_probes.py", "run_stage0.py", "stage2_sources.json", "sources.json", "requirements.txt")]
    return {"python": sys.version, "executable": sys.executable, "platform": platform.platform(),
            "packages": packages, **git,
            "source_sha256": {p.relative_to(root).as_posix(): digest(p) for p in paths if p.is_file()}}


def _gpu_preflight(directory: Path, label: str, params: dict[str, Any]) -> None:
    """Reuse the accepted stage-0 occupation check before touching CUDA."""
    from run_stage0 import gpu_guard
    try:
        gpu_guard(directory, label, allow_self=True)
        device = str(params.get("device", "cuda"))
        index = device.split(":", 1)[1] if ":" in device else "0"
        free_text = subprocess.check_output([
            "nvidia-smi", f"--id={index}", "--query-gpu=memory.free,memory.total",
            "--format=csv,noheader,nounits"], text=True)
        (directory / f"gpu_memory_{label}.txt").write_text(free_text, encoding="utf-8")
        free, total = [float(part.strip()) for part in free_text.strip().split(",")]
        budget = float(params.get("gpu_budget_mib", 2000))
        if budget <= 0:
            raise ValueError("gpu_budget_mib must be positive")
        if free < budget:
            raise ProbeBlocked(f"GPU free {free:.0f} MiB / {total:.0f} MiB is below the predeclared {budget:.0f} MiB budget; no inference started", "blocked-resource")
    except ProbeBlocked:
        raise
    except RuntimeError as exc:
        raise ProbeBlocked(str(exc), "blocked-resource") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProbeBlocked(f"GPU preflight unavailable: {exc}", "blocked-env") from exc


def _cuda_module(params: dict[str, Any]):
    if not str(params.get("device", "cpu")).startswith("cuda"):
        return None
    try:
        import torch
    except ImportError as exc:
        raise ProbeBlocked(f"PyTorch is unavailable: {exc}", "blocked-env") from exc
    if not torch.cuda.is_available():
        raise ProbeBlocked("CUDA is unavailable; no silent CPU fallback", "blocked-env")
    device = str(params.get("device", "cuda"))
    torch.cuda.set_device(0 if device == "cuda" else device)
    return torch


def _failure(exc: Exception) -> tuple[str, str, list[str]]:
    if isinstance(exc, ProbeBlocked):
        return "blocked", str(exc), [exc.code]
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return "blocked", f"{type(exc).__name__}: {exc}", ["blocked-env"]
    if "out of memory" in str(exc).lower():
        return "failed", f"{type(exc).__name__}: {exc}; reduce the variant/resolution/bank before retrying", ["cuda-oom"]
    return "failed", f"{type(exc).__name__}: {exc}", ["probe-error"]


def _inventory(directory: Path, declared: list[str] | None = None) -> list[dict[str, Any]]:
    for reference in declared or []:
        if not isinstance(reference, str) or not inside(directory, reference).is_file():
            raise ValueError(f"Probe declared a missing output: {reference!r}")
    output = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name not in {"metrics.json", "metrics.json.tmp"}:
            inside(directory, path)
            output.append({"path": path.relative_to(directory).as_posix(),
                           "sha256": digest(path), "bytes": path.stat().st_size})
    return output


def _publish_summary(output: Path, summary: dict[str, Any]) -> None:
    counts = {status: sum(row["status"] == status for row in summary["results"]) for status in ("ok", "blocked", "failed")}
    summary["status_counts"] = counts
    write_json(output / "summary.json", summary)
    lines = ["# Stage 2 run records", "", "This table is operational evidence, not recognition accuracy.", "",
             "| Probe | Image | Status | Duration ms | Peak allocated MiB | Reason | Record |",
             "|---|---|---|---:|---:|---|---|"]
    for row in summary["results"]:
        duration = "n/a" if row["duration_ms"] is None else f'{row["duration_ms"]:.3f}'
        peak = "n/a" if row["peak_allocated_mib"] is None else f'{row["peak_allocated_mib"]:.3f}'
        reason = row["status_reason"].replace("|", "\\|").replace("\n", " ")
        reference = f'{row["probe_name"]}/{row["frame_id"]}/metrics.json'
        lines.append(f'| {row["probe_name"]} | {row["frame_id"]} | {row["status"]} | {duration} | {peak} | {reason} | [metrics]({reference}) |')
    (output / "run_table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_config(config_path: str | Path, *, root: Path | None = None,
               output_override: str | Path | None = None,
               load_builtins: bool = True) -> dict[str, Any]:
    """Run all valid configured jobs; blocked/failed jobs do not abort the batch.

    Invalid configuration and existing output directories fail before inference.
    Model lifecycle failures are reflected in every affected per-image record.
    """
    root = (root or ROOT).resolve()
    config_path = inside(root, config_path)
    config_bytes = config_path.read_bytes()
    config = json.loads(config_bytes.decode("utf-8-sig"))
    images, specs = _validate_config(config, root)
    output = inside(root, output_override or config.get("output_root", "results/stage2"))
    if output == root:
        raise ValueError("Output must be a new child directory, not the experiment root")
    output.mkdir(parents=True, exist_ok=False)
    (output / "config.json").write_bytes(config_bytes)
    if load_builtins:
        registry.load_builtin_probes()
    task_id = f"stage2-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    summary = {"interface_version": "probe-batch-v1", "task_id": task_id,
               "started_at": now(), "ended_at": None, "status": "running",
               "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
               "config_ref": config_path.relative_to(root).as_posix(),
               "output_root": output.relative_to(root).as_posix(),
               "environment": _environment(root), "results": [], "lifecycle": [],
               "annotation_status": "not_ground_truth", "test_ground_truth_allowed": False}
    _publish_summary(output, summary)
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    for spec in specs:
        name, params = spec["name"], spec["params"]
        directory = output / name
        directory.mkdir()
        probe, torch = None, None
        load_error, load_trace, model_load_ms = None, None, None
        lifecycle = {"probe_name": name, "load_status": "pending", "release_status": "pending"}
        lifecycle["model_load_peak_allocated_mib"] = None
        lifecycle["model_load_peak_reserved_mib"] = None
        rows = []
        try:
            try:
                if name in registry.BUILTIN_IMPORT_ERRORS and name not in registry.REGISTRY:
                    raise ProbeBlocked(registry.BUILTIN_IMPORT_ERRORS[name], "blocked-env")
                probe = registry.get_probe(name)(params, root)
                if str(params.get("device", "cpu")).startswith("cuda"):
                    _gpu_preflight(directory, "before_load", params)
                    torch = _cuda_module(params)
                    torch.cuda.empty_cache()
                    torch.cuda.reset_peak_memory_stats()
                    torch.cuda.synchronize()
                load_start = time.perf_counter()
                try:
                    probe.load()
                    if torch is not None:
                        torch.cuda.synchronize()
                finally:
                    model_load_ms = (time.perf_counter() - load_start) * 1000
                    if torch is not None:
                        lifecycle["model_load_peak_allocated_mib"] = torch.cuda.max_memory_allocated() / 2**20
                        lifecycle["model_load_peak_reserved_mib"] = torch.cuda.max_memory_reserved() / 2**20
                lifecycle["load_status"] = "ok"
            except Exception as exc:
                load_error = _failure(exc)
                load_trace = traceback.format_exc()
                lifecycle["load_status"] = load_error[0]
                lifecycle["load_reason"] = load_error[1]
                (directory / "load_failure.txt").write_text(load_trace, encoding="utf-8")
            for image_id in spec["images"]:
                image = images[image_id]
                image_dir = directory / image_id
                image_dir.mkdir()
                started_at = now()
                status, reason, codes = "ok", "completed", []
                duration_ms, peak_allocated, peak_reserved, image_hash = None, None, None, None
                metrics, declared = {}, []
                inference_started = None
                measured_cuda = False
                try:
                    image_hash = digest(image.path)
                    expected_hash = image.metadata.get("input_sha256") or image.metadata.get("image_sha256")
                    if expected_hash is not None and expected_hash != image_hash:
                        raise ValueError("Input SHA256 does not match the configured frozen identity")
                    if load_error:
                        status, reason, codes = load_error
                        (image_dir / "failure.txt").write_text(load_trace, encoding="utf-8")
                    else:
                        if torch is not None:
                            # Release this process's unused cached blocks before
                            # comparing free device memory with the next image's
                            # budget. Resident weights remain allocated.
                            torch.cuda.synchronize()
                            torch.cuda.empty_cache()
                            _gpu_preflight(image_dir, "before_image", params)
                            torch.cuda.reset_peak_memory_stats()
                            torch.cuda.synchronize()
                            measured_cuda = True
                        inference_started = time.perf_counter()
                        result = probe.infer(image, image_dir)
                        if torch is not None:
                            torch.cuda.synchronize()
                        duration_ms = (time.perf_counter() - inference_started) * 1000
                        if not isinstance(result, ProbeOutput):
                            raise TypeError("infer must return ProbeOutput")
                        if result.status not in {"ok", "blocked", "failed"}:
                            raise ValueError("ProbeOutput status must be ok, blocked or failed")
                        if not isinstance(result.metrics, dict) or not isinstance(result.output_files, list):
                            raise TypeError("ProbeOutput metrics/output_files have invalid types")
                        if not isinstance(result.status_reason, str) or not isinstance(result.reason_codes, list) or not all(isinstance(code, str) for code in result.reason_codes):
                            raise TypeError("ProbeOutput status_reason/reason_codes must be strings/list of strings")
                        json.dumps(result.metrics, allow_nan=False)
                        status, reason, codes = result.status, result.status_reason, list(result.reason_codes)
                        metrics, declared = result.metrics, result.output_files
                        _inventory(image_dir, declared)
                except Exception as exc:
                    status, reason, codes = _failure(exc)
                    (image_dir / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
                    if inference_started is not None:
                        duration_ms = (time.perf_counter() - inference_started) * 1000
                finally:
                    if measured_cuda:
                        try:
                            torch.cuda.synchronize()
                            peak_allocated = torch.cuda.max_memory_allocated() / 2**20
                            peak_reserved = torch.cuda.max_memory_reserved() / 2**20
                        except Exception as exc:
                            status, reason, codes = "failed", f"CUDA measurement failed: {exc}", ["cuda-measurement-failed"]
                if status == "blocked":
                    # A prerequisite check is not model inference. Preserve its
                    # operational overhead separately, without claiming a GPU
                    # inference duration/peak for an unperformed experiment.
                    if duration_ms is not None:
                        metrics["blocked_check_duration_ms"] = duration_ms
                    duration_ms, peak_allocated, peak_reserved = None, None, None
                try:
                    model_info = copy.deepcopy(probe.model_info) if probe is not None else ModelInfo(model_name=name)
                    if not isinstance(model_info, ModelInfo):
                        raise TypeError("probe.model_info must be ModelInfo")
                    json.dumps(model_info.__dict__, allow_nan=False)
                    artifacts = _inventory(image_dir)
                except Exception as exc:
                    status, reason, codes = "failed", f"Result publication validation: {exc}", ["result-validation-failed"]
                    (image_dir / "publication_failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
                    model_info = ModelInfo(model_name=name)
                    # Preserve a failure record even if a malformed artifact was
                    # emitted (e.g. a symlink escaping the output directory).
                    artifacts = []
                record = ProbeResult(task_id, f"{image_id}:{(image_hash or 'unreadable')[:12]}", image_id,
                                     image.path.relative_to(root).as_posix(), image_hash, name, model_info,
                                     started_at, now(), duration_ms, model_load_ms, peak_allocated, peak_reserved,
                                     status, reason, codes, artifacts, metrics).as_dict()
                write_json(image_dir / "metrics.json", record)
                rows.append(record)
                summary["results"].append(record)
                _publish_summary(output, summary)
                print(json.dumps({"probe": name, "image": image_id, "status": status, "reason": reason}, ensure_ascii=False), flush=True)
        finally:
            if probe is not None:
                try:
                    probe.release()
                    if torch is not None:
                        torch.cuda.empty_cache()
                    lifecycle["release_status"] = "ok"
                except Exception as exc:
                    lifecycle["release_status"] = "failed"
                    lifecycle["release_reason"] = f"{type(exc).__name__}: {exc}"
                    trace = traceback.format_exc()
                    (directory / "release_failure.txt").write_text(trace, encoding="utf-8")
                    for record in rows:
                        record["status"] = "failed"
                        record["status_reason"] += f"; release failed: {exc}"
                        record["reason_codes"] = [*record["reason_codes"], "release-failed"]
                        image_dir = directory / record["frame_id"]
                        (image_dir / "release_failure.txt").write_text(trace, encoding="utf-8")
                        try:
                            record["output_files"] = _inventory(image_dir)
                        except Exception as inventory_exc:
                            record["status_reason"] += f"; output inventory failed: {inventory_exc}"
                            record["reason_codes"].append("result-validation-failed")
                            record["output_files"] = []
                            (image_dir / "publication_failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
                        write_json(image_dir / "metrics.json", record)
            else:
                lifecycle["release_status"] = "not-created"
            lifecycle["model_load_ms"] = model_load_ms
            summary["lifecycle"].append(lifecycle)
            write_json(directory / "lifecycle.json", lifecycle)
            _publish_summary(output, summary)
    summary["status"] = "completed"
    summary["ended_at"] = now()
    _publish_summary(output, summary)
    return summary
