"""Two-image inference smoke only. Proposals are not semantic labels or truth."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
INPUTS = ("realpart_img1.jpg", "realpart_img2.jpg")
AMG = dict(points_per_side=32, points_per_batch=16, pred_iou_thresh=0.8,
           stability_score_thresh=0.95, stability_score_offset=1.0,
           mask_threshold=0.0, box_nms_thresh=0.7, crop_n_layers=0,
           crop_nms_thresh=0.7, crop_overlap_ratio=512 / 1500,
           crop_n_points_downscale_factor=1, min_mask_region_area=0,
           output_mode="binary_mask", use_m2m=False, multimask_output=True)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def gpu_guard(output, label, allow_self=False):
    full = subprocess.check_output(["nvidia-smi"], text=True)
    rows = subprocess.check_output([
        "nvidia-smi", "--query-compute-apps=pid,process_name,used_gpu_memory",
        "--format=csv,noheader"], text=True)
    (output / f"nvidia_smi_{label}.txt").write_text(full + "\n" + rows, encoding="utf-8")
    foreign = [row for row in csv.reader(rows.splitlines()) if row and
               not (allow_self and row[0].strip() == str(os.getpid()))]
    if foreign:
        raise RuntimeError(f"GPU occupied; stopped without taking over: {foreign}")


def save_proposals(image, proposals, directory):
    import numpy as np
    from PIL import Image, ImageDraw
    directory.mkdir()
    (directory / "masks").mkdir()
    height, width = image.shape[:2]
    labels = np.zeros((height, width), dtype=np.uint16)
    overlay = image.astype(np.float32).copy()
    palette = np.random.default_rng(0).integers(48, 240, size=(len(proposals), 3))
    # Largest first, then smaller proposals on top. Overlaps retained in individual PNGs.
    for index in sorted(range(len(proposals)), key=lambda i: -proposals[i]["area"]):
        mask = proposals[index]["segmentation"]
        Image.fromarray(mask.astype(np.uint8) * 255).save(directory / "masks" / f"{index + 1:04d}.png")
        labels[mask] = index + 1
        overlay[mask] = image[mask] * 0.65 + palette[index] * 0.35
    overlay_image = Image.fromarray(overlay.astype(np.uint8))
    overlay_image.save(directory / "overlay.png")
    Image.fromarray(labels).save(directory / "proposal_ids.png")
    Image.fromarray((labels > 0).astype(np.uint8) * 255).save(directory / "proposal_union.png")
    # Explicitly a visual index: ID map/union are not structure or residue segmentation.
    draw = ImageDraw.Draw(overlay_image)
    for index, proposal in enumerate(proposals):
        ys, xs = np.nonzero(proposal["segmentation"])
        if len(xs):
            j = len(xs) // 2
            draw.text((int(xs[j]), int(ys[j])), str(index + 1), fill="white", stroke_width=1, stroke_fill="black")
    overlay_image.save(directory / "overlay_ids.png")
    write_json(directory / "proposals.json", [
        {**{k: v for k, v in p.items() if k != "segmentation"},
         "proposal_id": i + 1, "mask": f"masks/{i + 1:04d}.png",
         "semantic_class": None, "annotation_status": "unlabeled_prediction"}
        for i, p in enumerate(proposals)])
    return {"mask_count": len(proposals), "width": width, "height": height,
            "union_pixels": int((labels > 0).sum())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="results/stage0_initial")
    args = parser.parse_args()
    output = ROOT / args.output
    output.mkdir(parents=True, exist_ok=False)
    try:
        gpu_guard(output, "before_import")  # before even creating a CUDA context
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
        import numpy as np
        from PIL import Image
        import torch
        from sam2.build_sam import build_sam2
        from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator

        source = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
        checkpoint = ROOT / "weights" / source["checkpoint_file"]
        assert digest(checkpoint) == source["checkpoint_sha256"], "checkpoint mismatch"
        for name in INPUTS:
            assert digest(ROOT / "inputs" / name) == source["input_sha256"][name], f"input changed: {name}"
        assert torch.cuda.is_available(), "CUDA required; no silent CPU fallback"
        torch.manual_seed(0)
        np.random.seed(0)
        torch.set_num_threads(4)
        torch.backends.cudnn.benchmark = False
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        torch.use_deterministic_algorithms(True)
        torch.cuda.init()
        torch.cuda.synchronize()
        load_start = time.perf_counter()
        model = build_sam2(source["config"], str(checkpoint), device="cuda", mode="eval")
        generator = SAM2AutomaticMaskGenerator(model, **AMG)
        torch.cuda.synchronize()
        report = {
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
            "python": sys.version, "platform": platform.platform(),
            "packages": {name: importlib.metadata.version(name) for name in
                         ("torch", "torchvision", "numpy", "pillow", "sam-2")},
            "cuda_runtime": torch.version.cuda, "gpu": torch.cuda.get_device_name(),
            "compute_capability": list(torch.cuda.get_device_capability()),
            "wheel_architectures": torch.cuda.get_arch_list(),
            "source": source, "amg": AMG, "precision": "bfloat16 autocast",
            "experiment_file_sha256": {name: digest(ROOT / name) for name in
                                       ("run_stage0.py", "sources.json", "requirements.txt")},
            "seed": 0, "warmup_runs": 0, "torch_compile": False,
            "deterministic_algorithms": True, "tf32": False,
            "cublas_workspace_config": os.environ["CUBLAS_WORKSPACE_CONFIG"],
            "cuda_extension_built": False, "small_region_postprocessing": False,
            "model_parameters": sum(p.numel() for p in model.parameters()),
            "model_load_seconds": time.perf_counter() - load_start,
            "checkpoint_bytes": checkpoint.stat().st_size,
            "timing_scope": "synchronized generate(RGB): transform+encode+grid decode+filter+CPU masks; excludes file I/O and model load; no warmup",
            "memory_scope": "torch.cuda max allocated/reserved includes resident weights; excludes driver/context/other processes",
            "images": [], "status": "running"}
        write_json(output / "metrics.json", report)
        for name in INPUTS:
            gpu_guard(output, f"before_{Path(name).stem}", allow_self=True)
            path = ROOT / "inputs" / name
            with Image.open(path) as opened:
                image = np.array(opened.convert("RGB"), copy=True)
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.synchronize()
            start = time.perf_counter()
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
                proposals = generator.generate(image)
            torch.cuda.synchronize()
            elapsed = time.perf_counter() - start
            measurement = {"filename": name, "input_sha256": digest(path),
                           "generate_seconds": elapsed,
                           "peak_allocated_mib": torch.cuda.max_memory_allocated() / 2**20,
                           "peak_reserved_mib": torch.cuda.max_memory_reserved() / 2**20,
                           "first_image_without_warmup": not report["images"]}
            measurement.update(save_proposals(image, proposals, output / path.stem))
            report["images"].append(measurement)
            write_json(output / "metrics.json", report)
            print(json.dumps(measurement), flush=True)
        gpu_guard(output, "after_generate", allow_self=True)
        report["status"] = "complete"
        write_json(output / "metrics.json", report)
    except Exception as exc:
        if "report" in locals():
            report["status"] = "failed"
            write_json(output / "metrics.json", report)
        write_json(output / "failure.json", {"type": type(exc).__name__, "message": str(exc)})
        raise


if __name__ == "__main__":
    main()
