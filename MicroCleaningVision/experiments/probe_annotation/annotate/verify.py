"""Audit seed draft without treating it as ground truth or measuring accuracy."""
import argparse
import base64
import csv
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .export import LABELS, decode_shape, digest, validate_config, write_json
from .manifest import audit_rows


def verify_dataset(dataset, inputs):
    if not __debug__:
        raise RuntimeError("verification must run without Python -O")
    with (dataset/"manifest.csv").open(encoding="utf-8-sig",newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        for key in ("human_verified","test_ground_truth_allowed"):
            if row[key].lower() != "false":
                raise ValueError("unsafe draft flag in manifest")
            row[key] = False
    groups = audit_rows(rows)
    report = {"status":"pass", "groups":groups, "leakage":"none", "images":[]}
    for row in rows:
        image = inputs/row["filename"]
        out = dataset/image.stem
        config = json.loads((out/"points.json").read_text(encoding="utf-8"))
        data = json.loads((out/(image.stem+".json")).read_text(encoding="utf-8"))
        metrics = json.loads((out/"metrics.json").read_text(encoding="utf-8"))
        records = json.loads((out/"instances.json").read_text(encoding="utf-8"))
        session = json.loads((out/"session.json").read_text(encoding="utf-8"))
        with Image.open(image) as im:
            width,height = im.size
        annotations = validate_config(config,width,height,digest(image))
        assert (int(row["width"]),int(row["height"])) == (width,height)
        assert (data["imageWidth"],data["imageHeight"]) == (width,height)
        assert base64.b64decode(data["imageData"]) == image.read_bytes()
        assert data["flags"] == {"draft":True,"human_verified":False,"test_ground_truth_allowed":False}
        assert metrics["predictor"] == "SAM2ImagePredictor" and metrics["amg_used"] is False
        assert metrics["config_sha256"] == digest(out/"points.json")
        assert session["schema"] == "point-annotation-session-v1"
        assert session["status"] == "exported" and session["elapsed_seconds"] > 0
        assert session["image_sha256"] == digest(image)
        assert session["events"][-1]["config_sha256"] == digest(out/"points.json")
        assert session["events"][-1]["config"] == config
        assert metrics["elapsed_selection_to_export_seconds"] == session["elapsed_seconds"]
        assert session == json.loads((dataset/"sessions"/(image.stem+".json")).read_text(encoding="utf-8"))
        assert len(annotations) == len(records) == len(data["shapes"])
        shapes_by_id = {s["group_id"]:s for s in data["shapes"]}
        assert len(shapes_by_id) == len(annotations)
        labels_in_order = [s["label"]=="ignore" for s in data["shapes"]]
        assert labels_in_order == sorted(labels_in_order)
        all_masks, ignore = [], np.zeros((height,width),bool)
        for a,r in zip(annotations,records):
            s = shapes_by_id[a["id"]]
            assert a["id"] == r["id"] == s["group_id"]
            assert a["label"] == r["label"] == s["label"] and s["label"] in LABELS
            with Image.open(out/r["mask"]) as im:
                raw = np.array(im)
            assert raw.shape == (height,width) and set(np.unique(raw)) <= {0,255}
            mask = raw > 0
            assert mask.any() and int(mask.sum()) == r["area_px"]
            assert digest(out/r["mask"]) == r["mask_sha256"]
            np.testing.assert_array_equal(decode_shape(s,height,width),mask)
            with Image.open(out/"candidates"/f"{a['id']:04d}_{a.get('candidate_index',0)}.png") as im:
                np.testing.assert_array_equal(np.array(im)>0,mask)
            all_masks.append(mask)
            if a["label"] == "ignore":
                ignore |= mask
        assert len(list((out/"masks").glob("*.png"))) == len(annotations)
        with Image.open(out/"overlay_ids.png") as im:
            assert im.size == (width,height)
        # These counts audit exclusion semantics, not recall/coverage/accuracy.
        by_class = {label:np.zeros((height,width),bool) for label in LABELS}
        for a,mask in zip(annotations,all_masks):
            by_class[a["label"]] |= mask
        nonignore = [by_class[l] for l in LABELS if l!="ignore"]
        conflicts = np.sum(np.stack(nonignore),axis=0)>1
        report["images"].append({"filename":image.name,"mask_count":len(all_masks),
            "labelme_png_roundtrip":"pixel_equal", "raw_predictor_masks_unchanged":True,
            "counts":metrics["counts"],"positive_points":metrics["positive_points"],
            "negative_points":metrics["negative_points"],"prompt_misses":metrics["prompt_misses"],
            "elapsed_seconds":session["elapsed_seconds"],"revision_count":len(session["events"]),
            "ignore_union_pixels":int(ignore.sum()),
            "nonignore_class_conflict_pixels_outside_ignore":int((conflicts&~ignore).sum()),
            "not_test_ground_truth":True})
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset",type=Path,required=True)
    p.add_argument("--inputs",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args = p.parse_args()
    result = verify_dataset(args.dataset,args.inputs)
    write_json(args.output,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
