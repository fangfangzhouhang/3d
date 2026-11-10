"""One command: manifest -> semantic labels -> augment -> train -> evaluate -> predict."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import time
import traceback

import numpy as np
from PIL import Image
import torch
from torch.utils.data import DataLoader

if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "finetune_pipeline"

from .data import ClassSchema, ManifestDataset
from .evaluation import evaluate_predictions
from .losses import build_loss
from .models import build_model
from .predict import infer_image, load_checkpoint, save_prediction
from .runtime import DISCLAIMER, ROOT, RunLogger, environment, gpu_guard, new_output, resolve_path, set_seed, sha256, write_json
from .visuals import save_augmentation_grid


def validate_config(config):
    if config.get("mode") not in ("smoke", "formal"):
        raise ValueError("mode must be smoke or formal")
    for key in ("manifest", "images_root", "classes", "seed", "epochs", "batch_size"):
        if config.get(key) is None:
            raise ValueError(f"Configuration is pending a decision: {key}")
    for key in ("epochs", "batch_size"):
        if not isinstance(config[key], int) or isinstance(config[key], bool) or config[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if config.get("device") not in ("cuda", "cpu"):
        raise ValueError("Explicit device cuda or cpu required")
    if not config["model"].get("name") or not config["optimizer"].get("name") or config["optimizer"].get("params", {}).get("lr") is None:
        raise ValueError("Model/optimizer/lr still pending")
    if config["preprocess"].get("mean") is None or config["preprocess"].get("std") is None:
        raise ValueError("Normalization parameters still pending")


def truth(value):
    return value is True or (isinstance(value, str) and value.lower() == "true")


def evidence_policy(records, mode):
    draft = any(not truth(r.get("human_verified")) or r.get("annotation_status") == "draft" for r in records)
    if mode == "formal":
        if draft or any(r.get("split_role") == "placeholder_demo" for r in records):
            raise ValueError("Formal training refuses draft/unverified/placeholder rows; obtain actual human review first")
        if any(r["split"] == "test" and not truth(r.get("test_ground_truth_allowed")) for r in records):
            raise ValueError("Test rows must explicitly permit use as test ground truth")
    return draft


def collate_samples(samples, ignore_index):
    height = max(s["image"].shape[1] for s in samples)
    width = max(s["image"].shape[2] for s in samples)
    images = torch.zeros(len(samples), 3, height, width)
    masks = torch.full((len(samples), height, width), ignore_index, dtype=torch.long)
    for index, sample in enumerate(samples):
        h, w = sample["mask"].shape
        images[index, :, :h, :w] = sample["image"]
        masks[index, :h, :w] = sample["mask"]
    return images, masks


def evaluate_dataset(model, dataset, schema, config, device):
    pairs = []
    for index in range(len(dataset)):
        image, mask, metadata = dataset.raw_item(index)
        prediction = infer_image(model, image, schema, config["preprocess"], device)
        pairs.append((metadata, mask, prediction))
    return evaluate_predictions(pairs, schema)


def epoch_ious(metrics):
    return {name: {"status": values["status"], "iou": values["iou"]} for name, values in metrics["classes"].items()}


def checkpoint_save(path, model, optimizer, scheduler, epoch, config, schema_payload, provenance, draft, disclaimer, selection):
    payload = {"format_version": 1, "epoch": epoch, "config": copy.deepcopy(config), "class_schema": schema_payload,
               "model_state": {name: value.detach().cpu() for name, value in model.state_dict().items()},
               "optimizer_state": optimizer.state_dict(), "scheduler_state": scheduler.state_dict() if scheduler else None,
               "provenance": provenance, "draft_unverified": draft, "disclaimer": disclaimer, "selection": selection,
               "torch_rng_state": torch.get_rng_state()}
    temporary = path.with_suffix(".tmp")
    torch.save(payload, temporary)
    temporary.replace(path)


def run(config, output, *, audit_only=False):
    validate_config(config)
    output = new_output(output)
    logger = RunLogger(output)
    start = time.perf_counter()
    disclaimer = DISCLAIMER if config["mode"] == "smoke" else "正式训练运行；泛化结论须基于独立组的已核验测试集，并遵守预先冻结的评价方案。"
    draft = None
    completed_epochs = 0
    try:
        schema_path = resolve_path(config["classes"])
        schema_payload = json.loads(schema_path.read_text(encoding="utf-8"))
        schema = ClassSchema(schema_payload)
        manifest = resolve_path(config["manifest"])
        images_root = resolve_path(config["images_root"])
        dataset_args = (manifest, images_root, schema)
        all_data = ManifestDataset(*dataset_args)
        draft = evidence_policy(all_data.records, config["mode"])
        logger.log("run_start", mode=config["mode"], draft_unverified=draft, disclaimer=disclaimer, requested_device=config["device"], audit_only=audit_only)
        write_json(output / "config.json", config)
        provenance = environment()
        provenance.update({"manifest_path": str(manifest), "manifest_sha256": sha256(manifest), "classes_path": str(schema_path), "classes_sha256": sha256(schema_path)})
        write_json(output / "environment.json", provenance)
        audit = all_data.audit()
        write_json(output / "data_audit.json", audit)
        logger.log("data_audit", audit=audit)
        semantic_dir = output / "semantic_labels"
        semantic_dir.mkdir()
        input_hashes = []
        for index in range(len(all_data)):
            image, mask, metadata = all_data.raw_item(index)
            filename = all_data.records[index]["filename"]
            Image.fromarray(mask.astype(np.uint8)).save(semantic_dir / f"{Path(filename).stem}.png")
            instance_ref = manifest.parent / Path(filename).stem / "instances.json"
            hashes = {"image": sha256(images_root / filename), "instances_json": sha256(instance_ref),
                      "masks": {entry["mask"]: sha256(instance_ref.parent / entry["mask"]) for entry in json.loads(instance_ref.read_text(encoding="utf-8"))}}
            input_hashes.append({"filename": filename, "sha256": hashes})
        write_json(output / "input_hashes.json", input_hashes)
        augmented = ManifestDataset(*dataset_args, split="train", preprocess_config=config["preprocess"], augment_config=config["augmentation"], seed=config["seed"])
        if len(augmented):
            save_augmentation_grid(augmented, output / "augmentation_grid.png", count=config.get("augmentation_preview_count", 3))
        else:
            logger.log("augmentation_preview", status="无样本")
        if audit_only:
            write_json(output / "status.json", {"status": "audit_completed", "training": "NOT_PERFORMED", "draft_unverified": draft, "disclaimer": disclaimer})
            logger.log("audit_completed", training="NOT_PERFORMED", draft_unverified=draft, disclaimer=disclaimer)
            return output
        train_data = ManifestDataset(*dataset_args, split="train", preprocess_config=config["preprocess"], augment_config=config["augmentation"], seed=config["seed"])
        splits = {split: ManifestDataset(*dataset_args, split=split) for split in ("train", "val", "test")}
        if len(train_data) == 0:
            write_json(output / "metrics.json", {"status": "无样本", "draft_unverified": draft, "disclaimer": disclaimer,
                        "splits": {s: {"image_count": len(d), "status": "无样本" if not len(d) else "not_evaluated_no_training"} for s, d in splits.items()}})
            logger.log("no_training_samples", status="无样本", draft_unverified=draft)
            write_json(output / "status.json", {"status": "no_training_samples", "training": "NOT_PERFORMED", "draft_unverified": draft, "disclaimer": disclaimer})
            return output
        if config["device"] == "cuda":
            gpu_guard(output)
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA requested but unavailable; no CPU fallback")
        set_seed(config["seed"], config.get("num_threads", 2))
        device = torch.device(config["device"])
        model = build_model(config["model"], schema.num_classes).to(device)
        loss_fn = build_loss(config["loss"], schema.ignore_index)
        optimizer_name = config["optimizer"]["name"]
        optimizers = {"Adam": torch.optim.Adam, "AdamW": torch.optim.AdamW, "SGD": torch.optim.SGD}
        if optimizer_name not in optimizers:
            raise ValueError(f"Unregistered optimizer: {optimizer_name}")
        optimizer = optimizers[optimizer_name](model.parameters(), **config["optimizer"]["params"])
        scheduler = None
        if config.get("scheduler") is not None:
            scheduler_config = config["scheduler"]
            schedulers = {"StepLR": torch.optim.lr_scheduler.StepLR, "CosineAnnealingLR": torch.optim.lr_scheduler.CosineAnnealingLR}
            scheduler = schedulers[scheduler_config["name"]](optimizer, **scheduler_config["params"])
        generator = torch.Generator().manual_seed(config["seed"])
        from functools import partial
        loader = DataLoader(train_data, batch_size=config["batch_size"], shuffle=True, num_workers=0,
                            generator=generator, collate_fn=partial(collate_samples, ignore_index=schema.ignore_index))
        selection_split = "val" if len(splits["val"]) else "train"
        selection_note = "validation_mean_present_class_iou" if selection_split == "val" else "training_mean_present_class_iou_no_validation_samples"
        checkpoints = output / "checkpoints"
        checkpoints.mkdir()
        best_score = float("-inf")
        history = []
        if device.type == "cuda":
            torch.cuda.reset_peak_memory_stats()
            logger.log("gpu_started", name=torch.cuda.get_device_name(device), draft_unverified=draft, disclaimer=disclaimer)
        for epoch in range(1, config["epochs"] + 1):
            train_data.set_epoch(epoch)
            model.train()
            total_loss = 0.0
            batch_count = 0
            skipped = 0
            for images, masks in loader:
                if not torch.any(masks != schema.ignore_index):
                    skipped += 1
                    continue
                optimizer.zero_grad(set_to_none=True)
                logits = model(images.to(device))
                loss = loss_fn(logits, masks.to(device))
                if not torch.isfinite(loss):
                    raise RuntimeError("Non-finite loss")
                loss.backward()
                optimizer.step()
                total_loss += float(loss.detach().cpu())
                batch_count += 1
            if not batch_count:
                raise ValueError("No supervised pixels survived augmentation in this epoch")
            train_metrics = evaluate_dataset(model, splits["train"], schema, config, device)
            selection_metrics = evaluate_dataset(model, splits["val"], schema, config, device) if selection_split == "val" else train_metrics
            values = [c["iou"] for c in selection_metrics["classes"].values() if c["iou"] is not None]
            if not values:
                raise ValueError("No labeled pixels available to select a checkpoint")
            score = float(sum(values) / len(values))
            row = {"epoch": epoch, "train_loss": total_loss / batch_count, "train_iou": epoch_ious(train_metrics),
                   "selection_split": selection_split, "selection_score": score, "learning_rate": optimizer.param_groups[0]["lr"],
                   "skipped_all_ignore_batches": skipped, "draft_unverified": draft, "disclaimer": disclaimer}
            history.append(row)
            completed_epochs = epoch
            logger.log("epoch", **row)
            if scheduler:
                scheduler.step()
            selection = {"split": selection_split, "criterion": selection_note, "score": score}
            checkpoint_save(checkpoints / "latest.pt", model, optimizer, scheduler, epoch, config, schema_payload, provenance, draft, disclaimer, selection)
            if score > best_score:
                best_score = score
                checkpoint_save(checkpoints / "best.pt", model, optimizer, scheduler, epoch, config, schema_payload, provenance, draft, disclaimer, selection)
        write_json(output / "history.json", history)
        model, schema, best = load_checkpoint(checkpoints / "best.pt", device)
        split_metrics = {split: evaluate_dataset(model, dataset, schema, config, device) for split, dataset in splits.items()}
        metrics = {"status": "completed", "mode": config["mode"], "draft_unverified": draft, "disclaimer": disclaimer,
                   "device": str(device), "best_epoch": best["epoch"], "checkpoint_selection": best["selection"],
                   "splits": split_metrics, "evaluator": split_metrics["train"]["metric_source"],
                   "train_evaluation_same_images": True, "independent_test_available": bool(split_metrics["test"]["known_pixels"]) and not draft,
                   "pixel_domain": "原图尺寸；仅参考标签非ignore的像素参与指标；空类别不计入平均"}
        write_json(output / "metrics.json", metrics)
        for index in range(len(all_data)):
            image, _, metadata = all_data.raw_item(index)
            prediction = infer_image(model, image, schema, config["preprocess"], device)
            save_prediction(image, prediction, schema, output / "predictions" / Path(all_data.records[index]["filename"]).stem,
                            {**metadata, "draft_unverified": draft, "disclaimer": disclaimer, "checkpoint": "best.pt", "epoch": best["epoch"]})
        elapsed = time.perf_counter() - start
        summary = {"status": "completed", "device": str(device), "epochs": len(history), "seconds": elapsed,
                   "draft_unverified": draft, "disclaimer": disclaimer,
                   "loss_first": history[0]["train_loss"], "loss_last": history[-1]["train_loss"],
                   "peak_allocated_bytes": torch.cuda.max_memory_allocated() if device.type == "cuda" else None,
                   "peak_reserved_bytes": torch.cuda.max_memory_reserved() if device.type == "cuda" else None,
                   "checkpoints": {p.name: {"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size, "sha256": sha256(p)} for p in checkpoints.glob("*.pt")}}
        write_json(output / "status.json", summary)
        write_json(output / "artifacts.json", {str(p.relative_to(output)): {"bytes": p.stat().st_size, "sha256": sha256(p)} for p in sorted(output.rglob("*")) if p.is_file() and p.name not in ("artifacts.json", "run.log", "events.jsonl")})
        logger.log("completed", **summary)
        return output
    except Exception as exc:
        status = "blocked" if "GPU_BLOCKED" in str(exc) else "failed"
        failure = {"status": status, "error": str(exc), "draft_unverified": draft, "disclaimer": disclaimer,
                   "completed_epochs": completed_epochs, "training": "NOT_PERFORMED" if completed_epochs == 0 else "incomplete",
                   "evaluation": "NOT_PERFORMED", "predictions": "NOT_PERFORMED"}
        write_json(output / "status.json", failure)
        if not (output / "metrics.json").exists():
            write_json(output / "metrics.json", {**failure, "metrics": None})
        (output / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        logger.log(status, error=str(exc), draft_unverified=draft, disclaimer=disclaimer)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/smoke.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--images-root", type=Path)
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    for key in ("device", "manifest", "images_root"):
        value = getattr(args, key)
        if value is not None:
            config[key] = str(value.resolve()) if isinstance(value, Path) else value
    run(config, args.output, audit_only=args.audit_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
