"""CPU 可跑的喷点热图模仿训练。

默认配置就是微型规模。检查点不能拿去发串口。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from microcleaning.data_learning.plan_imitation.config import ImitationStudy
from microcleaning.data_learning.plan_imitation.dataset import HeatmapPlanDataset, require_torch
from microcleaning.data_learning.plan_imitation.model import build_model
from microcleaning.data_learning.plan_imitation.proposal import propose_from_heatmap
from microcleaning.data_learning.plan_imitation.synthetic import generate_masks
from microcleaning.data_learning.plan_imitation.teacher import teacher_example


def build_examples(count: int, study: ImitationStudy, seed: int, *, mask_kind: str = "synthetic"):
    samples = generate_masks(count, study.shape_hw, seed)
    examples = []
    for index, sample in enumerate(samples):
        examples.append(teacher_example(
            sample.mask,
            study,
            kind=sample.kind,
            source_ref=f"plan-imitation:synthetic:{sample.kind}:{index}",
            target_id=f"synthetic-{seed}-{index}",
            mask_kind=mask_kind,
        ))
    return examples


def examples_from_directory(folder: Path, study: ImitationStudy):
    from microcleaning.data_learning.plan_imitation.synthetic import load_mask_directory

    loaded = load_mask_directory(folder)
    examples = []
    for index, (stem, mask) in enumerate(loaded):
        if mask.shape != study.shape_hw:
            raise ValueError(f"MASK_SHAPE_MISMATCH:{stem}:expected={study.shape_hw}:got={mask.shape}")
        examples.append(teacher_example(
            mask,
            study,
            kind="supplied",
            source_ref=f"plan-imitation:mask-dir:{stem}",
            target_id=stem,
            mask_kind="reference_annotation",
        ))
    return examples


def _study_from_args(args: argparse.Namespace) -> ImitationStudy:
    return ImitationStudy(
        height=args.height,
        width=args.width,
        diameter_px=args.diameter_px,
        candidate_stride_px=args.stride_px,
        max_stations=args.max_stations,
        target_coverage=args.target_coverage,
        start_px=(args.start_x, args.start_y),
        heatmap_sigma_px=args.sigma_px,
        peak_threshold=args.peak_threshold,
        min_peak_distance_px=args.min_peak_distance_px,
        channels=args.channels,
        seed=args.seed,
    )


def _loader(dataset: HeatmapPlanDataset, batch_size: int, shuffle: bool):
    torch = require_torch()
    return torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=shuffle, num_workers=0)


def _mean_loss(model, loader, device) -> float:
    torch = require_torch()
    model.eval()
    total = 0.0
    count = 0
    loss_fn = torch.nn.MSELoss()
    with torch.no_grad():
        for masks, heats in loader:
            predicted = torch.sigmoid(model(masks.to(device)))
            loss = loss_fn(predicted, heats.to(device))
            total += float(loss) * int(masks.shape[0])
            count += int(masks.shape[0])
    return total / count


def _predict(model, mask: np.ndarray, device) -> np.ndarray:
    torch = require_torch()
    model.eval()
    batch = torch.from_numpy(mask.astype(np.float32))[None, None].to(device)
    with torch.no_grad():
        return torch.sigmoid(model(batch))[0, 0].detach().cpu().numpy()


def _validation_geometry(model, examples, study: ImitationStudy, device) -> dict:
    coverages = []
    teacher = []
    points = []
    for item in examples:
        heatmap = _predict(model, item.mask, device)
        proposal = propose_from_heatmap(
            item.mask, heatmap, study, kind=item.kind, source_ref=item.source_ref,
            teacher=item,
        )
        if proposal.executable:
            raise RuntimeError("LEARNED_PLAN_BECAME_EXECUTABLE")
        if proposal.coverage_rate is not None:
            coverages.append(proposal.coverage_rate)
        if item.coverage_rate is not None:
            teacher.append(item.coverage_rate)
        points.append(proposal.station_count)
    def mean(values: list[float]) -> float | None:
        return None if not values else sum(values) / len(values)
    return {
        "learned_mean_coverage": mean(coverages),
        "teacher_mean_coverage": mean(teacher),
        "learned_mean_points": mean([float(v) for v in points]),
        "executable": False,
    }


def train_study(study: ImitationStudy, *, train_count: int, val_count: int, epochs: int,
        batch_size: int, learning_rate: float, output_dir: Path, device_name: str,
        mask_dir: Path | None = None) -> dict:
    torch = require_torch()
    if epochs < 1 or batch_size < 1 or train_count < 1 or val_count < 1:
        raise ValueError("TRAIN_SCHEDULE_INVALID")
    if not np.isfinite(learning_rate) or learning_rate <= 0:
        raise ValueError("LEARNING_RATE_INVALID")
    output_dir.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(study.seed)
    device = torch.device(device_name if device_name == "cpu" or torch.cuda.is_available() else "cpu")
    if mask_dir is None:
        train_examples = build_examples(train_count, study, study.seed)
        val_examples = build_examples(val_count, study, study.seed + 10_000)
    else:
        supplied = examples_from_directory(mask_dir, study)
        if len(supplied) < 2:
            raise ValueError("MASK_DIR_NEEDS_AT_LEAST_TWO")
        split = max(1, len(supplied) - max(1, min(val_count, len(supplied) // 5 or 1)))
        train_examples, val_examples = supplied[:split], supplied[split:] or supplied[:1]
    train_loader = _loader(HeatmapPlanDataset(train_examples), batch_size, shuffle=True)
    val_loader = _loader(HeatmapPlanDataset(val_examples), batch_size, shuffle=False)
    model = build_model(study.channels).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    loss_fn = torch.nn.MSELoss()
    history = []
    for epoch in range(1, epochs + 1):
        model.train()
        total = 0.0
        count = 0
        for masks, heats in train_loader:
            optimizer.zero_grad(set_to_none=True)
            predicted = torch.sigmoid(model(masks.to(device)))
            loss = loss_fn(predicted, heats.to(device))
            loss.backward()
            optimizer.step()
            total += float(loss.detach()) * int(masks.shape[0])
            count += int(masks.shape[0])
        row = {
            "epoch": epoch,
            "train_loss": total / count,
            "val_loss": _mean_loss(model, val_loader, device),
            "executable": False,
        }
        row.update(_validation_geometry(model, val_examples, study, device))
        history.append(row)
        print(
            f"epoch {epoch}: train_loss={row['train_loss']:.6f} val_loss={row['val_loss']:.6f} "
            f"val_coverage={row['learned_mean_coverage']} teacher_coverage={row['teacher_mean_coverage']}"
        )
    checkpoint_path = output_dir / "checkpoint.pt"
    # 只保存权重。硬件标志写在旁边的 config/summary，避免把计划当成可执行文件。
    torch.save(model.state_dict(), checkpoint_path)
    config_path = output_dir / "config.json"
    metrics_path = output_dir / "metrics.jsonl"
    summary_path = output_dir / "summary.json"
    config_path.write_text(json.dumps(study.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metrics_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in history), encoding="utf-8")
    summary = {
        "schema_version": "plan-imitation-train-v1",
        "checkpoint": checkpoint_path.name,
        "config": study.to_dict(),
        "epochs": history,
        "device": str(device),
        "train_count": len(train_examples),
        "val_count": len(val_examples),
        "label_source": "existing_greedy_plan_coverage",
        "data_source": "mask_dir" if mask_dir is not None else "synthetic",
        "executable": False,
        "feeds_action_request": False,
        "send_to_controller": False,
        "hardware_path": "unchanged",
        "note": "几何覆盖率不是清除率。检查点不可执行。",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"检查点：{checkpoint_path}")
    print("executable=false；本脚本不打开串口，也不生成动作申请。")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="用贪心覆盖规划器教一个小网络预测喷点热图。默认即 CPU 微型配置。")
    parser.add_argument("--tiny", action="store_true", help="确认使用内置微型 CPU 配置；省略其它规模参数时默认也是这套")
    parser.add_argument("--height", type=int, default=32)
    parser.add_argument("--width", type=int, default=32)
    parser.add_argument("--train-samples", type=int, default=4)
    parser.add_argument("--val-samples", type=int, default=2)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--lr", type=float, default=1e-2)
    parser.add_argument("--diameter-px", type=int, default=7)
    parser.add_argument("--stride-px", type=int, default=4)
    parser.add_argument("--max-stations", type=int, default=4)
    parser.add_argument("--target-coverage", type=float, default=0.90)
    parser.add_argument("--sigma-px", type=float, default=1.25)
    parser.add_argument("--peak-threshold", type=float, default=0.45)
    parser.add_argument("--min-peak-distance-px", type=float, default=3.0)
    parser.add_argument("--channels", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--start-x", type=float, default=0.0)
    parser.add_argument("--start-y", type=float, default=0.0)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--mask-dir", type=Path, default=None, help="可选 png Mask 目录；缺省则生成合成 Mask")
    parser.add_argument("--output-dir", type=Path, default=Path("output") / "plan_imitation" / "tiny")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.tiny:
        print("使用微型 CPU 配置。")
    try:
        require_torch()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    study = _study_from_args(args)
    train_study(
        study,
        train_count=args.train_samples,
        val_count=args.val_samples,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        output_dir=args.output_dir,
        device_name=args.device,
        mask_dir=args.mask_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
