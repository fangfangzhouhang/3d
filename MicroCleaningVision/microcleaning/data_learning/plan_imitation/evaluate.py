"""把热图解码结果和现有中心点、扫描驻点、贪心规划放在同一几何条件下比较。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from microcleaning.control_system.planning.coverage_analysis import AnalysisSpec, analyze_coverage
from microcleaning.data_learning.plan_imitation.config import ImitationStudy
from microcleaning.data_learning.plan_imitation.dataset import require_torch
from microcleaning.data_learning.plan_imitation.model import build_model
from microcleaning.data_learning.plan_imitation.proposal import propose_from_heatmap
from microcleaning.data_learning.plan_imitation.synthetic import generate_masks
from microcleaning.data_learning.plan_imitation.teacher import coverage_input, teacher_example, virtual_kernel

HARDWARE_FLAGS = ("executable", "feeds_action_request", "send_to_controller")


def _mean(values: list[float | int | None]) -> float | None:
    usable = [float(value) for value in values if value is not None]
    return None if not usable else sum(usable) / len(usable)


def _refuse_executable(payload: dict) -> None:
    for key in HARDWARE_FLAGS:
        if payload.get(key) is not False:
            raise RuntimeError("CHECKPOINT_HARDWARE_FLAG_BROKEN")


def _run_metrics(run) -> dict:
    rate = None if run.metrics is None else run.metrics.geometric_coverage_rate
    return {
        "method": run.method,
        "status": run.status,
        "station_count": len(run.points_px),
        "route_length_px": float(run.route_length_px),
        "geometric_coverage_rate": None if rate is None else float(rate),
        "executable": False,
        "feeds_action_request": False,
        "send_to_controller": False,
    }


def load_checkpoint(checkpoint: Path, config_path: Path, device_name: str):
    torch = require_torch()
    device = torch.device(device_name if device_name == "cpu" or torch.cuda.is_available() else "cpu")
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    _refuse_executable(payload)
    study = ImitationStudy.from_dict(payload)
    state = torch.load(checkpoint, map_location=device, weights_only=True)
    model = build_model(study.channels).to(device)
    model.load_state_dict(state)
    model.eval()
    return model, study, device


def predict_heatmap(model, mask: np.ndarray, device) -> np.ndarray:
    torch = require_torch()
    batch = torch.from_numpy(np.asarray(mask, dtype=np.float32))[None, None].to(device)
    with torch.no_grad():
        return torch.sigmoid(model(batch))[0, 0].detach().cpu().numpy()


def compare_sample(mask: np.ndarray, heatmap: np.ndarray, study: ImitationStudy, *, kind: str,
        source_ref: str, target_id: str) -> dict:
    """解码热图，并用现有分析入口对照三种规划。教师热图应与贪心点数一致。"""
    teacher = teacher_example(mask, study, kind=kind, source_ref=source_ref + ":teacher", target_id=target_id)
    proposal = propose_from_heatmap(
        mask, heatmap, study, kind=kind, source_ref=source_ref, target_id=target_id, teacher=teacher,
    )
    sample = coverage_input(mask, source_ref=source_ref, target_id=target_id)
    spec = AnalysisSpec(
        station_budgets=(study.max_stations,),
        target_coverage=study.target_coverage,
        candidate_stride_px=study.candidate_stride_px,
        start_px=study.start_px,
        raster_step_px=max(int(study.diameter_px), int(study.candidate_stride_px)),
    )
    analysis = analyze_coverage(sample, virtual_kernel(study.diameter_px), spec=spec)
    comparison_runs = [run for run in analysis.runs if run.phase == "comparison"]
    greedy_points = tuple(next(run.points_px for run in comparison_runs if run.method == "COVERAGE_AWARE"))
    baselines = {run.method: _run_metrics(run) for run in comparison_runs}
    if proposal.executable or any(item["executable"] for item in baselines.values()):
        raise RuntimeError("COMPARISON_BECAME_EXECUTABLE")
    return {
        "kind": kind,
        "source_ref": source_ref,
        "learned": {
            "method": proposal.algorithm,
            "status": proposal.status,
            "station_count": proposal.station_count,
            "route_length_px": proposal.route_length_px,
            "geometric_coverage_rate": proposal.coverage_rate,
            "executable": False,
            "feeds_action_request": False,
            "send_to_controller": False,
        },
        "teacher_points_match_greedy": teacher.points_px == greedy_points,
        "baselines": baselines,
        "executable": False,
        "feeds_action_request": False,
        "send_to_controller": False,
    }


def evaluate_checkpoint(checkpoint: Path, config_path: Path, *, samples: int, seed: int,
        output_path: Path, device_name: str = "cpu") -> dict:
    model, study, device = load_checkpoint(checkpoint, config_path, device_name)
    rows = []
    for index, sample in enumerate(generate_masks(samples, study.shape_hw, seed)):
        heatmap = predict_heatmap(model, sample.mask, device)
        rows.append(compare_sample(
            sample.mask,
            heatmap,
            study,
            kind=sample.kind,
            source_ref=f"plan-imitation:eval:{sample.kind}:{index}",
            target_id=f"eval-{seed}-{index}",
        ))
    summary = {
        "schema_version": "plan-imitation-eval-v1",
        "checkpoint": checkpoint.name,
        "sample_count": len(rows),
        "seed": seed,
        "config": study.to_dict(),
        "means": {
            "learned_coverage": _mean([row["learned"]["geometric_coverage_rate"] for row in rows]),
            "learned_points": _mean([row["learned"]["station_count"] for row in rows]),
            "learned_route_px": _mean([row["learned"]["route_length_px"] for row in rows]),
            "greedy_coverage": _mean([row["baselines"]["COVERAGE_AWARE"]["geometric_coverage_rate"] for row in rows]),
            "greedy_points": _mean([row["baselines"]["COVERAGE_AWARE"]["station_count"] for row in rows]),
            "greedy_route_px": _mean([row["baselines"]["COVERAGE_AWARE"]["route_length_px"] for row in rows]),
            "center_coverage": _mean([row["baselines"]["CENTER_POINT_COMPONENTS"]["geometric_coverage_rate"] for row in rows]),
            "center_points": _mean([row["baselines"]["CENTER_POINT_COMPONENTS"]["station_count"] for row in rows]),
            "center_route_px": _mean([row["baselines"]["CENTER_POINT_COMPONENTS"]["route_length_px"] for row in rows]),
            "raster_coverage": _mean([row["baselines"]["RASTER_SCAN_DWELL"]["geometric_coverage_rate"] for row in rows]),
            "raster_points": _mean([row["baselines"]["RASTER_SCAN_DWELL"]["station_count"] for row in rows]),
            "raster_route_px": _mean([row["baselines"]["RASTER_SCAN_DWELL"]["route_length_px"] for row in rows]),
        },
        "samples": rows,
        "executable": False,
        "feeds_action_request": False,
        "send_to_controller": False,
        "note": "对比的是假定圆足迹下的几何覆盖，不是清除率，也不能发到控制器。",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    means = summary["means"]
    print(
        "均值 学习/贪心/中心/扫描 覆盖率："
        f"{means['learned_coverage']} / {means['greedy_coverage']} / "
        f"{means['center_coverage']} / {means['raster_coverage']}"
    )
    print(
        "均值点数 学习/贪心/中心/扫描："
        f"{means['learned_points']} / {means['greedy_points']} / "
        f"{means['center_points']} / {means['raster_points']}"
    )
    print(f"报告：{output_path}")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="评估喷点热图模仿，并对照现有覆盖规划器。")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=None, help="默认使用检查点旁边的 config.json")
    parser.add_argument("--samples", type=int, default=4)
    parser.add_argument("--seed", type=int, default=1000)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = args.config if args.config is not None else args.checkpoint.parent / "config.json"
    if not args.checkpoint.is_file() or not config_path.is_file():
        print("缺少检查点或 config.json。", file=sys.stderr)
        return 2
    try:
        require_torch()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    evaluate_checkpoint(
        args.checkpoint,
        config_path,
        samples=args.samples,
        seed=args.seed,
        output_path=args.output,
        device_name=args.device,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
