"""Predict original-resolution semantic PNG, overlay PNG and pixel areas."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
import torch

if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "finetune_pipeline"

from .data import ClassSchema
from .models import build_model
from .preprocess import prepare, restore_prediction
from .runtime import DISCLAIMER, gpu_guard, new_output, sha256, write_json
from .visuals import overlay


@torch.no_grad()
def infer_image(model, image, schema, preprocess_config, device):
    target = np.full((image.height, image.width), schema.ignore_index, dtype=np.uint8)
    tensor, _, meta = prepare(image, target, preprocess_config, schema.ignore_index)
    model.eval()
    predicted = model(tensor.unsqueeze(0).to(device)).argmax(1)[0].cpu().numpy().astype(np.uint8)
    return restore_prediction(predicted, meta)


def save_prediction(image, prediction, schema, output, metadata):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    Image.fromarray(prediction.astype(np.uint8)).save(output / "semantic.png")
    overlay(image, prediction, schema).save(output / "overlay.png")
    write_json(output / "areas.json", {
        **metadata,
        "measurement": "model_prediction_not_manual_annotation",
        "area_unit": "pixels",
        "width": image.width, "height": image.height,
        "total_pixels": int(prediction.size),
        "classes": {c["name"]: {"index": c["index"], "area_px": int(np.count_nonzero(prediction == c["index"]))} for c in schema.classes},
        "note": "预测覆盖整幅输入图；未知标注区域不因此成为人工真值，像素面积不是物理面积。",
    })


def load_checkpoint(path, device="cpu"):
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    schema = ClassSchema(checkpoint["class_schema"])
    model = build_model(checkpoint["config"]["model"], schema.num_classes)
    model.load_state_dict(checkpoint["model_state"])
    model.to(device).eval()
    return model, schema, checkpoint


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    args = parser.parse_args(argv)
    output = new_output(args.output)
    try:
        if args.device == "cuda":
            gpu_guard(output, "gpu_before_prediction")
        torch.set_num_threads(2)
        model, schema, checkpoint = load_checkpoint(args.checkpoint, args.device)
        with Image.open(args.input) as source:
            image = source.convert("RGB")
        prediction = infer_image(model, image, schema, checkpoint["config"]["preprocess"], args.device)
        metadata = {"input": str(args.input.resolve()), "input_sha256": sha256(args.input),
                    "checkpoint": str(args.checkpoint.resolve()), "checkpoint_sha256": sha256(args.checkpoint),
                    "draft_unverified": checkpoint["draft_unverified"], "mode": checkpoint["config"]["mode"],
                    "disclaimer": checkpoint["disclaimer"]}
        save_prediction(image, prediction, schema, output / "prediction", metadata)
        write_json(output / "status.json", {"status": "completed", **metadata})
        print(f"Prediction saved: {output / 'prediction'}")
        return 0
    except Exception as exc:
        write_json(output / "status.json", {"status": "blocked" if "GPU_BLOCKED" in str(exc) else "failed", "error": str(exc)})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
