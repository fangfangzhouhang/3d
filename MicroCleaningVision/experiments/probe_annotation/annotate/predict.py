"""Image + pixel xy point config -> SAM2ImagePredictor masks and Labelme drafts."""
import argparse
import importlib.metadata
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import time

from .export import digest, export, validate_config, write_json
from .session import finish

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--image", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--session", type=Path, required=True)
    p.add_argument("--final", action="store_true", help="finish timer only after reviewed revision is selected")
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        # Reuse stage0 guard only, never its generator or output masks.
        from run_stage0 import gpu_guard
        gpu_guard(args.output, "before_import")
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
        import numpy as np
        from PIL import Image
        import torch
        from sam2.build_sam import build_sam2
        from sam2.sam2_image_predictor import SAM2ImagePredictor

        src = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
        checkpoint = ROOT / "weights" / src["checkpoint_file"]
        if digest(checkpoint) != src["checkpoint_sha256"]:
            raise ValueError("checkpoint hash mismatch")
        vendor = ROOT / "vendor/sam2"
        if subprocess.check_output(["git","rev-parse","HEAD"],cwd=vendor,text=True).strip() != src["sam2_commit"]:
            raise ValueError("SAM source revision mismatch")
        if subprocess.check_output(["git","status","--porcelain"],cwd=vendor,text=True).strip():
            raise ValueError("SAM source has local edits")
        config = json.loads(args.config.read_text(encoding="utf-8"))
        with Image.open(args.image) as img:
            rgb = np.array(img.convert("RGB"), copy=True)
        height, width = rgb.shape[:2]
        annotations = validate_config(config, width, height, digest(args.image))
        session = json.loads(args.session.read_text(encoding="utf-8"))
        if session["status"] == "exported" or session["image_sha256"] != digest(args.image):
            raise ValueError("session is completed or bound to different image")
        if not session["events"] or session["events"][-1]["config_sha256"] != digest(args.config):
            raise ValueError("record this point config before inference")
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA required, no silent fallback")
        torch.set_num_threads(4)
        torch.manual_seed(0)
        np.random.seed(0)
        torch.backends.cudnn.benchmark = False
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        torch.use_deterministic_algorithms(True)
        torch.cuda.init()
        torch.cuda.synchronize()
        start = time.perf_counter()
        # Disable optional dynamic multimask fallback: number/order matches config.
        model = build_sam2(src["config"],str(checkpoint),device="cuda",mode="eval",apply_postprocessing=False)
        predictor = SAM2ImagePredictor(model, mask_threshold=0.0, max_hole_area=0, max_sprinkle_area=0)
        torch.cuda.synchronize()
        load_seconds = time.perf_counter()-start
        gpu_guard(args.output, "before_image", allow_self=True)
        masks, prediction_records = [], []
        (args.output / "candidates").mkdir()
        torch.cuda.synchronize()
        start = time.perf_counter()
        with torch.inference_mode(), torch.autocast("cuda",dtype=torch.bfloat16):
            predictor.set_image(rgb)
            torch.cuda.synchronize()
            encode_seconds = time.perf_counter()-start
            for a in annotations:
                coords = np.array(a["positive"]+a.get("negative",[]),dtype=np.float32)
                labels = np.array([1]*len(a["positive"])+[0]*len(a.get("negative",[])),dtype=np.int32)
                torch.cuda.synchronize()
                start = time.perf_counter()
                predicted, scores, _ = predictor.predict(point_coords=coords, point_labels=labels,
                    multimask_output=a.get("multimask",False), normalize_coords=True)
                torch.cuda.synchronize()
                seconds = time.perf_counter()-start
                for k, mask in enumerate(predicted):
                    Image.fromarray(mask.astype(np.uint8)*255).save(args.output / "candidates" / f"{a['id']:04d}_{k}.png")
                masks.append(predicted[a.get("candidate_index",0)].astype(bool))
                prediction_records.append({"id": a["id"], "decode_seconds": seconds,
                    "candidate_count": len(predicted), "selected": a.get("candidate_index",0),
                    "model_internal_quality_scores_not_measured_accuracy": scores.tolist()})
        records = export(args.image,rgb,annotations,masks,args.output)
        shutil.copyfile(args.config,args.output / "points.json")
        metrics = {"status":"complete", "predictor":"SAM2ImagePredictor", "amg_used":False,
            "source":src, "image_sha256":digest(args.image), "width":width, "height":height,
            "config_sha256":digest(args.config), "python":sys.version, "executable":sys.executable,
            "base_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
            "branch":subprocess.check_output(["git","branch","--show-current"],cwd=ROOT,text=True).strip(),
            "packages":{n:importlib.metadata.version(n) for n in ("torch","torchvision","numpy","pillow","sam-2")},
            "code_sha256":{f.name:digest(f) for f in sorted(Path(__file__).parent.glob("*.py"))},
            "model_load_seconds":load_seconds, "encode_seconds":encode_seconds,
            "prediction_records":prediction_records, "precision":"bfloat16 autocast", "seed":0,
            "postprocessing":False, "annotation_status":"draft", "human_verified":False,
            "test_ground_truth_allowed":False, "counts":{label:sum(a["label"]==label for a in annotations) for label in ("structure","residue","substrate","ignore")},
            "positive_points":sum(len(a["positive"]) for a in annotations),
            "negative_points":sum(len(a.get("negative",[])) for a in annotations),
            "prompt_misses":[r["id"] for r in records if r["positive_hits"]!=r["positive_count"] or r["negative_hits"]]}
        write_json(args.output / "metrics.json",metrics)
        gpu_guard(args.output, "after_export", allow_self=True)
        if args.final:
            value = finish(args.session,args.config,args.image,args.output)
            write_json(args.output / "session.json",value)
            metrics["elapsed_selection_to_export_seconds"] = value["elapsed_seconds"]
            write_json(args.output / "metrics.json",metrics)
        print(json.dumps({k: metrics[k] for k in ("status","counts","positive_points","negative_points","prompt_misses")},ensure_ascii=False),flush=True)
    except Exception as e:
        write_json(args.output / "failure.json", {"type":type(e).__name__,"message":str(e)})
        raise


if __name__ == "__main__":
    main()
