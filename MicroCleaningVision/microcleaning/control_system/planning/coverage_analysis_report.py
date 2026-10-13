"""P5 headless reports and verified replay; no workbench or hardware imports.

The complete P3 input archive is preserved beside distinct P5 analysis results.
CSV/NPZ/optional scientific PNGs precede an atomically published JSON manifest.
Existing directories are never overwritten. Hashes are not digital signatures.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import platform
import sys
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from microcleaning.control_system.planning.coverage_analysis import (
    ANALYSIS_VERSION, EVALUATION_NAMES, MAX_ANALYSIS_MASK_BYTES, MAX_ANALYSIS_RUNS,
    METHODS, AnalysisSpec, CoverageAnalysis, analyze_coverage,
)
from microcleaning.control_system.planning.coverage_archive import (
    CoverageArchive, export_coverage_preview, load_coverage_preview,
)
from microcleaning.control_system.planning.coverage_metrics import CoverageMetrics, footprint_slice
from microcleaning.control_system.planning.coverage_model import array_hash, canonical_json, finite_number
from microcleaning.control_system.planning.coverage_visualization import COLORS, validate_rgb

REPORT_VERSION = "coverage-analysis-report-v1"
BASE_FILES = {"analysis.csv", "analysis_masks.npz"}
FIGURE_FILES = {"comparison.png", "sensitivity.png"}
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024
CSV_FIELDS = ("run_id", "method", "phase", "parameter_name", "parameter_value", "parameter_unit",
    "station_budget", "status", "qualifies_for_comparison", "mask_kind", "area_kind",
    "station_count", "rejected_proposals", "off_grid_proposals", "constraint_violations", "candidate_count",
    *CoverageMetrics.__dataclass_fields__, "route_length_px", "planning_evaluation_seconds", "audit_seconds",
    "raw_prefix_count", "raw_prefix_geometric_coverage_rate", "raw_prefix_outside_image_area_px",
    "reason_codes", "executable")
# station_count is already a metrics field; keep one unambiguous CSV column.
CSV_FIELDS = tuple(dict.fromkeys(CSV_FIELDS))
METHOD_LABELS = {"CENTER_POINT_COMPONENTS": "Centers per component",
    "RASTER_SCAN_DWELL": "Legacy raster points / dwell", "COVERAGE_AWARE": "Coverage-aware greedy"}
METHOD_COLORS = dict(zip(METHODS, ("#d18b36", "#7063b5", "#139a88")))


def _sha(blob):
    return hashlib.sha256(blob).hexdigest()


def analysis_csv(analysis: CoverageAnalysis) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    area_kind = analysis.input.to_dict()["area_kind"]
    for run in analysis.runs:
        data = run.to_dict()
        row = {"run_id": data["run_id"], "method": run.method, "phase": run.phase,
            "parameter_name": run.parameter_name, "parameter_value": run.parameter_value,
            "parameter_unit": run.parameter_unit, "station_budget": run.policy.max_stations,
            "status": run.status, "qualifies_for_comparison": run.qualifies_for_comparison,
            "mask_kind": analysis.input.mask_kind, "area_kind": area_kind,
            "station_count": len(run.points_px), "rejected_proposals": len(run.rejected_proposals),
            "off_grid_proposals": sum(i.reason == "CANDIDATE_GRID_MISMATCH" for i in run.rejected_proposals),
            "constraint_violations": len(run.constraint_violations), "candidate_count": run.candidate_count,
            "route_length_px": run.route_length_px, "planning_evaluation_seconds": run.planning_evaluation_seconds,
            "audit_seconds": run.audit_seconds, "raw_prefix_count": run.raw_prefix_count,
            "raw_prefix_geometric_coverage_rate": None if run.raw_prefix_metrics is None else run.raw_prefix_metrics.geometric_coverage_rate,
            "raw_prefix_outside_image_area_px": None if run.raw_prefix_metrics is None else run.raw_prefix_metrics.outside_image_area_px,
            "reason_codes": "|".join(run.reason_codes), "executable": False}
        if run.metrics is not None:
            row.update(run.metrics.to_dict())
        writer.writerow(row)
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


def _arrays(analysis):
    return {f"r{index:03d}_{name}": getattr(run.evaluation, name)
        for index, run in enumerate(analysis.runs) if run.evaluation is not None
        for name in EVALUATION_NAMES if getattr(run.evaluation, name) is not None}


def _overlay(image_rgb, input, run):
    pixels = image_rgb.copy()
    for mask, color in ((input.mask, "target"),
            (None if run.evaluation is None else run.evaluation.covered_mask, "covered"),
            (None if run.evaluation is None else run.evaluation.uncovered_mask, "uncovered")):
        if mask is not None:
            pixels[mask] = np.rint(0.38 * pixels[mask] + 0.62 * np.array(COLORS[color])).astype(np.uint8)
    kernel = run.model.kernel
    if kernel is not None:
        outline = kernel.mask & ~(cv2.erode(kernel.mask.astype(np.uint8), np.ones((3, 3), np.uint8),
            borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool))
        for point in run.points_px:
            (ys, xs), stamp = footprint_slice(input.mask.shape, outline, kernel.anchor_px, point)
            pixels[ys, xs][stamp] = COLORS["footprints"]
    return pixels


def _rate(run, basis="nominal"):
    if run.metrics is None:
        return None
    return run.metrics.geometric_coverage_rate if basis == "nominal" else run.metrics.conservative_coverage_rate


def _draw_figures(image_rgb, analysis, destination):
    # The Agg canvas writes scientific artifacts without pyplot, Tk or a GUI.
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
    except ImportError as exc:
        raise ImportError("Install requirements/coverage-analysis.txt or request --no-figures") from exc
    metadata = {"analysis_id": analysis.to_dict(include_timing=False)["analysis_id"],
        "safety": "executable=false; geometric coverage is not stain removal"}
    fig = Figure(figsize=(13, 8), dpi=150, layout="constrained")
    canvas = FigureCanvasAgg(fig)
    axes = fig.subplots(2, 3)
    budget = max(analysis.spec.station_budgets)
    for column, method in enumerate(METHODS):
        rows = [r for r in analysis.runs if r.phase == "comparison" and r.method == method]
        run = next(r for r in rows if r.policy.max_stations == budget)
        ax = axes[0, column]
        ax.imshow(_overlay(image_rgb, analysis.input, run))
        if run.points_px:
            pts = np.array(run.points_px)
            route = np.array((run.policy.start_px, *run.points_px))
            ax.plot(route[:, 0], route[:, 1], "--", color="#ffdd69", linewidth=0.7)
            ax.scatter(pts[:, 0], pts[:, 1], s=20, color="#12394e", edgecolors="white", linewidths=0.5)
        rate = _rate(run)
        value = "unknown" if rate is None else f"{rate:.1%}"
        ax.set_title(f"{METHOD_LABELS[method]}\n{value} | dwell {len(run.points_px)} | rejected {len(run.rejected_proposals)}", fontsize=10)
        ax.set_xlabel("original image x / px")
        ax.set_ylabel("original image y / px")
        ax.set_xlim(-0.5, image_rgb.shape[1] - 0.5)
        ax.set_ylim(image_rgb.shape[0] - 0.5, -0.5)
        x = [r.policy.max_stations for r in rows]
        for metric_column, values in enumerate(([_rate(r, analysis.spec.coverage_basis) for r in rows],
                [r.route_length_px if r.evaluation is not None else None for r in rows],
                [r.planning_evaluation_seconds for r in rows])):
            axes[1, metric_column].plot(x, [np.nan if v is None else v for v in values],
                "o-", color=METHOD_COLORS[method], label=METHOD_LABELS[method], markersize=4)
    labels = (f"{analysis.spec.coverage_basis} geometric coverage", "visit links / px", "single local compute call / s")
    for ax, label in zip(axes[1], labels):
        ax.set_xlabel("station budget ceiling")
        ax.set_ylabel(label)
        ax.grid(alpha=0.2)
    axes[1, 0].set_ylim(0, 1.05)
    axes[1, 0].axhline(analysis.spec.target_coverage, linestyle=":", color="#888888", linewidth=1)
    axes[1, 2].legend(fontsize=8)
    profile = analysis.model.profile
    fig.suptitle(f"SIMULATION ONLY | {analysis.input.mask_kind} | diameter {profile.diameter} {profile.unit} ({profile.evidence.status})\n"
        f"grid {analysis.spec.candidate_stride_px}px | raster {analysis.spec.raster_step_px}px | "
        "green=covered, red=omitted, blue=kernel | geometric coverage != removal", fontsize=11)
    canvas.print_png(destination / "comparison.png", metadata=metadata)

    fig = Figure(figsize=(13, 8), dpi=150, layout="constrained")
    canvas = FigureCanvasAgg(fig)
    axes = fig.subplots(2, 2)
    for column, parameter in enumerate(("diameter", "position_error")):
        for row, basis in enumerate(("nominal", "conservative")):
            ax = axes[row, column]
            any_values = False
            for method in METHODS:
                for mode, style in (("fixed", "--"), ("replan", "-")):
                    values = [r for r in analysis.runs if r.phase == f"{parameter}_{mode}"
                        and r.method == method and r.policy.max_stations == budget]
                    if not values:
                        continue
                    rates = [_rate(r, basis) for r in values]
                    any_values |= any(v is not None for v in rates)
                    ax.plot([r.parameter_value for r in values], [np.nan if v is None else v for v in rates],
                        style, marker="o", markersize=3, color=METHOD_COLORS[method],
                        label=f"{METHOD_LABELS[method]} / {mode}")
                    bad = [(r.parameter_value, v) for r, v in zip(values, rates) if r.constraint_violations and v is not None]
                    if bad:
                        ax.scatter(*zip(*bad), marker="x", s=55, color="#cb3c50", zorder=5)
            unit = profile.unit if parameter == "diameter" else analysis.spec.position_error_unit
            ax.set_xlabel(f"{'footprint diameter' if parameter == 'diameter' else 'position error radius'} / {unit}")
            scenarios = analysis.spec.diameters if parameter == "diameter" else analysis.spec.position_errors
            if scenarios:
                margin = (scenarios[-1] - scenarios[0]) * 0.05 or max(0.05, scenarios[0] * 0.05)
                ax.set_xlim(max(0, scenarios[0] - margin), scenarios[-1] + margin)
                ax.set_xticks(scenarios)
            else:
                ax.set_xticks([])
            ax.set_ylabel(f"{basis} geometric coverage")
            ax.set_ylim(0, 1.05)
            ax.grid(alpha=0.2)
            if not any_values:
                ax.text(0.5, 0.5, "not requested / unavailable\nunknown is not zero", ha="center", va="center", transform=ax.transAxes)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    if not handles:
        handles, labels = axes[0, 1].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="outside lower center", ncol=3, fontsize=8)
    fig.suptitle(f"Fixed selected points vs replanning | budget {budget} | explicit assumed scenarios\n"
        "red x = constraint violation / unranked | conservative = conditional pixel-box model, not measured reliability", fontsize=11)
    canvas.print_png(destination / "sensitivity.png", metadata=metadata)


def export_analysis_report(archive: CoverageArchive, analysis: CoverageAnalysis, destination: str | Path, *,
        include_figures: bool = True) -> Path:
    if not isinstance(archive, CoverageArchive) or not isinstance(analysis, CoverageAnalysis) or not isinstance(include_figures, bool):
        raise ValueError("ANALYSIS_ARCHIVE_AND_RESULT_REQUIRED")
    if (archive.plan.input.sha256 != analysis.input.sha256
            or canonical_json(archive.plan.model.to_dict()) != canonical_json(analysis.model.to_dict())
            or canonical_json(archive.constraints.to_dict()) != canonical_json(analysis.constraints.to_dict())):
        raise ValueError("ANALYSIS_REQUIRES_MATCHING_COMPLETE_INPUT_ARCHIVE")
    original = validate_rgb(archive.image_rgb, analysis.input.mask.shape)
    if include_figures:
        import importlib.util
        if importlib.util.find_spec("matplotlib") is None:
            raise ImportError("Install requirements/coverage-analysis.txt or request --no-figures")
    arrays = _arrays(analysis)
    if sum(v.nbytes for v in arrays.values()) > MAX_ANALYSIS_MASK_BYTES:
        raise ValueError("ANALYSIS_MASK_RESOURCE_LIMIT")
    stored = analysis.to_dict()
    if len(canonical_json(stored).encode("utf-8")) > MAX_MANIFEST_BYTES - 1024 * 1024:
        raise ValueError("ANALYSIS_MANIFEST_RESOURCE_LIMIT")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    export_coverage_preview(original, archive.plan, destination / "input", constraints=archive.constraints,
        options=archive.options, step_count=archive.step_count)
    (destination / "analysis.csv").write_bytes(analysis_csv(analysis))
    np.savez_compressed(destination / "analysis_masks.npz", **arrays)
    if include_figures:
        _draw_figures(original, analysis, destination)
    names = BASE_FILES | FIGURE_FILES if include_figures else BASE_FILES
    files = {name: {"sha256": _sha((destination / name).read_bytes()),
        "size_bytes": (destination / name).stat().st_size} for name in sorted(names)}
    import PIL
    environment = {"python": platform.python_version(), "numpy": np.__version__, "opencv": cv2.__version__, "pillow": PIL.__version__}
    if include_figures:
        import matplotlib
        environment["matplotlib"] = matplotlib.__version__
    manifest = {"schema_version": REPORT_VERSION, "analysis": stored, "include_figures": include_figures,
        "input_manifest_sha256": _sha((destination / "input" / "coverage_preview.json").read_bytes()),
        "files": files, "arrays": {k: {"shape": list(v.shape), "dtype": str(v.dtype), "sha256": array_hash(v)} for k, v in arrays.items()},
        "environment": environment, "integrity_semantics": "sha256_corruption_detection_not_signature",
        "executable": False, "feeds_action_request": False, "send_to_controller": False}
    encoded = canonical_json(manifest) + "\n"
    if len(encoded.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise ValueError("ANALYSIS_MANIFEST_RESOURCE_LIMIT")
    temporary = destination / "analysis.json.tmp"
    temporary.write_text(encoded, encoding="utf-8")
    temporary.rename(destination / "analysis.json")
    return destination


@dataclass(frozen=True, eq=False)
class AnalysisReport:
    archive: CoverageArchive
    analysis: CoverageAnalysis
    environment_json: str
    include_figures: bool


def _validate_npz(blob, metadata, shape):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        members = archive.infolist()
        if (len(members) > MAX_ANALYSIS_RUNS * len(EVALUATION_NAMES)
                or sum(m.file_size for m in members) > MAX_ANALYSIS_MASK_BYTES + len(members) * 256
                or len({m.filename for m in members}) != len(members)
                or {m.filename for m in members} != {key + ".npy" for key in metadata}):
            raise ValueError("ANALYSIS_ARRAY_RESOURCE_OR_SET_INVALID")
        for member in members:
            with archive.open(member) as stream:
                version = np.lib.format.read_magic(stream)
                if version == (1, 0):
                    declared, _, dtype = np.lib.format.read_array_header_1_0(stream)
                elif version == (2, 0):
                    declared, _, dtype = np.lib.format.read_array_header_2_0(stream)
                else:
                    raise ValueError("ANALYSIS_ARRAY_FORMAT_UNSUPPORTED")
                if tuple(declared) != tuple(shape) or dtype not in (np.dtype(bool), np.dtype(np.uint32)):
                    raise ValueError("ANALYSIS_ARRAY_SHAPE_OR_DTYPE_INVALID")


def load_analysis_report(directory: str | Path) -> AnalysisReport:
    directory = Path(directory)
    manifest_path = directory / "analysis.json"
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("ANALYSIS_MANIFEST_RESOURCE_LIMIT")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != REPORT_VERSION or manifest.get("analysis", {}).get("schema_version") != ANALYSIS_VERSION:
        raise ValueError("ANALYSIS_SCHEMA_UNSUPPORTED")
    stored = manifest["analysis"]
    if not isinstance(stored.get("runs"), list) or len(stored["runs"]) > MAX_ANALYSIS_RUNS:
        raise ValueError("ANALYSIS_RUN_RESOURCE_LIMIT")
    if not isinstance(manifest.get("include_figures"), bool):
        raise ValueError("ANALYSIS_FIGURE_FLAG_INVALID")
    for item in (manifest, stored, *stored["runs"]):
        if any(item.get(key) is not False for key in ("executable", "feeds_action_request", "send_to_controller")):
            raise ValueError("ANALYSIS_HARDWARE_FLAGS_REJECTED")
    names = BASE_FILES | FIGURE_FILES if manifest["include_figures"] else BASE_FILES
    if set(manifest["files"]) != names:
        raise ValueError("ANALYSIS_FIXED_FILE_SET_REQUIRED")
    input_manifest = directory / "input" / "coverage_preview.json"
    if input_manifest.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("ARCHIVE_MANIFEST_RESOURCE_LIMIT")
    if _sha(input_manifest.read_bytes()) != manifest["input_manifest_sha256"]:
        raise ValueError("ANALYSIS_INPUT_MANIFEST_HASH_MISMATCH")
    archive = load_coverage_preview(directory / "input")
    spec = AnalysisSpec(**stored["spec"])
    if len(stored["runs"]) != spec.run_count:
        raise ValueError("ANALYSIS_RUN_COUNT_MISMATCH")
    blobs = {}
    for name in sorted(names):
        path = directory / name
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("ANALYSIS_FILE_RESOURCE_LIMIT")
        blob = path.read_bytes()
        if _sha(blob) != manifest["files"][name]["sha256"] or len(blob) != manifest["files"][name]["size_bytes"]:
            raise ValueError("ANALYSIS_FILE_HASH_MISMATCH")
        blobs[name] = blob
    _validate_npz(blobs["analysis_masks.npz"], manifest["arrays"], archive.plan.input.mask.shape)
    replay = analyze_coverage(archive.plan.input, archive.plan.model, spec=spec, constraints=archive.constraints)
    without_timing = {k: v for k, v in stored.items() if k != "elapsed_seconds"}
    without_timing["runs"] = [{k: v for k, v in row.items() if k != "timing"} for row in stored["runs"]]
    if canonical_json(replay.to_dict(include_timing=False)) != canonical_json(without_timing):
        raise ValueError("ANALYSIS_DETERMINISTIC_REPLAY_MISMATCH")
    restored = []
    for row, recorded in zip(replay.runs, stored["runs"]):
        timing = recorded["timing"]
        expected = row.to_dict()["timing"]
        if set(timing) != set(expected) or timing["semantics"] != expected["semantics"]:
            raise ValueError("ANALYSIS_TIMING_SCHEMA_INVALID")
        restored.append(replace(row,
            planning_evaluation_seconds=finite_number(timing["planning_evaluation_seconds"], "timing", inclusive=True),
            audit_seconds=finite_number(timing["audit_seconds"], "timing", inclusive=True)))
    replay = replace(replay, runs=tuple(restored), elapsed_seconds=finite_number(stored["elapsed_seconds"], "timing", inclusive=True))
    if analysis_csv(replay) != blobs["analysis.csv"]:
        raise ValueError("ANALYSIS_CSV_REPLAY_MISMATCH")
    arrays = _arrays(replay)
    if set(arrays) != set(manifest["arrays"]):
        raise ValueError("ANALYSIS_REPLAY_ARRAY_SET_MISMATCH")
    with np.load(io.BytesIO(blobs["analysis_masks.npz"]), allow_pickle=False) as data:
        for key, expected in arrays.items():
            value, meta = data[key], manifest["arrays"][key]
            if (list(value.shape) != meta["shape"] or str(value.dtype) != meta["dtype"]
                    or array_hash(value) != meta["sha256"] or array_hash(value) != array_hash(expected)):
                raise ValueError("ANALYSIS_REPLAY_ARRAY_HASH_MISMATCH")
    for name in FIGURE_FILES & names:
        with Image.open(io.BytesIO(blobs[name])) as image:
            if image.width * image.height > 8_000_000 or image.info.get("analysis_id") != stored["analysis_id"]:
                raise ValueError("ANALYSIS_FIGURE_METADATA_INVALID")
            image.verify()
    return AnalysisReport(archive, replay, canonical_json(manifest["environment"]), manifest["include_figures"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline P5 geometric comparison; no hardware actions")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--archive", type=Path, help="verified P3 complete input archive")
    mode.add_argument("--verify", type=Path, help="verify and recompute an existing P5 report")
    parser.add_argument("--output", type=Path, help="new report directory; existing directories rejected")
    parser.add_argument("--budgets", nargs="+", type=int, default=None)
    parser.add_argument("--diameters", nargs="+", type=float, default=None, help="explicit scenarios in profile units")
    parser.add_argument("--position-errors", nargs="+", type=float, default=None)
    parser.add_argument("--position-error-unit", choices=("px", "mm"), default=None)
    parser.add_argument("--raster-step-px", type=int, default=None)
    parser.add_argument("--candidate-stride-px", type=int, default=None)
    parser.add_argument("--target-coverage", type=float, default=None)
    parser.add_argument("--coverage-basis", choices=("nominal", "conservative"), default=None)
    parser.add_argument("--no-figures", action="store_true")
    args = parser.parse_args(argv)
    if args.verify and (args.output or args.no_figures or any(getattr(args, key) is not None for key in (
            "budgets", "diameters", "position_errors", "position_error_unit", "raster_step_px",
            "candidate_stride_px", "target_coverage", "coverage_basis"))):
        parser.error("--verify restores recorded settings; do not mix scenario/export arguments")
    if args.archive and args.output is None:
        parser.error("--archive requires --output NEW_DIRECTORY")
    try:
        if args.verify:
            result = load_analysis_report(args.verify)
            print(canonical_json({"verified": True, "analysis_id": result.analysis.to_dict()["analysis_id"],
                "runs": len(result.analysis.runs), "executable": False}))
        else:
            archive = load_coverage_preview(args.archive)
            spec = AnalysisSpec(station_budgets=tuple(args.budgets or (1, 4, 8, 16, 32, 64)),
                target_coverage=archive.plan.policy.target_coverage if args.target_coverage is None else args.target_coverage,
                # Dense default avoids accidentally dropping rounded centers on a coarse grid.
                candidate_stride_px=1 if args.candidate_stride_px is None else args.candidate_stride_px,
                start_px=archive.plan.policy.start_px, coverage_basis=args.coverage_basis or archive.plan.policy.coverage_basis,
                raster_step_px=16 if args.raster_step_px is None else args.raster_step_px,
                diameters=tuple(args.diameters or ()), position_errors=tuple(args.position_errors or ()),
                position_error_unit=args.position_error_unit or "px")
            analysis = analyze_coverage(archive.plan.input, archive.plan.model, spec=spec, constraints=archive.constraints)
            output = export_analysis_report(archive, analysis, args.output, include_figures=not args.no_figures)
            print(canonical_json({"output": str(output.resolve()), "analysis_id": analysis.to_dict()["analysis_id"],
                "runs": len(analysis.runs), "executable": False, "send_to_controller": False}))
    except (OSError, ValueError, ImportError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print(f"P5 report failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
