"""Lossless Labelme mask shapes. No contour approximation, hole filling, or AMG."""
import base64
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

LABELS = ("structure", "residue", "substrate", "ignore")
COLORS = {"structure": (30, 180, 255), "residue": (255, 65, 65),
          "substrate": (100, 220, 100), "ignore": (255, 185, 35)}


def digest(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_config(config, width, height, image_hash):
    if config.get("schema") != "sam2-points-v1" or config.get("image_sha256") != image_hash:
        raise ValueError("wrong config schema or image SHA256")
    annotations = config.get("annotations")
    if not isinstance(annotations, list) or not annotations:
        raise ValueError("annotations must be a nonempty list")
    ids = set()
    for a in annotations:
        aid = a.get("id")
        if type(aid) is not int or aid <= 0 or aid in ids:
            raise ValueError("IDs must be unique positive integers within the image")
        ids.add(aid)
        if a.get("label") not in LABELS:
            raise ValueError("unknown label")
        if not isinstance(a.get("positive"), list) or not a["positive"]:
            raise ValueError("each instance needs positive points")
        if not isinstance(a.get("negative", []), list):
            raise ValueError("negative points must be a list")
        all_points = a["positive"] + a.get("negative", [])
        for point in all_points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("points must be pixel [x,y] pairs")
            if any(type(v) not in (int, float) or not np.isfinite(v) for v in point):
                raise ValueError("point coordinates must be finite numbers")
            if not (0 <= point[0] < width and 0 <= point[1] < height):
                raise ValueError("point outside image")
        if len({tuple(p) for p in all_points}) != len(all_points):
            raise ValueError("duplicate or contradictory point")
        multi = a.get("multimask", False)
        if type(multi) is not bool:
            raise ValueError("multimask must be boolean")
        idx = a.get("candidate_index", 0)
        if type(idx) is not int or not 0 <= idx < (3 if multi else 1):
            raise ValueError("invalid candidate_index")
    return annotations


def mask_shape(mask, annotation):
    ys, xs = np.nonzero(mask)
    if not len(xs):
        raise ValueError(f"empty prediction for ID {annotation['id']}; retain failed run, revise points")
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    patch = Image.fromarray(mask[y0:y1+1, x0:x1+1].astype(bool))
    buffer = io.BytesIO()
    patch.save(buffer, format="PNG")
    return {"label": annotation["label"], "points": [[x0, y0], [x1, y1]],
            "group_id": annotation["id"], "shape_type": "mask",
            "flags": {"draft": True, "human_verified": False},
            "description": f"ID {annotation['id']:04d}: {annotation.get('name', '')}; SAM2 point prediction; DRAFT; NOT TEST GROUND TRUTH",
            "mask": base64.b64encode(buffer.getvalue()).decode("ascii")}


def decode_shape(shape, height, width):
    (x0, y0), (x1, y1) = shape["points"]
    patch = np.array(Image.open(io.BytesIO(base64.b64decode(shape["mask"]))), dtype=bool)
    if patch.shape != (y1-y0+1, x1-x0+1):
        raise ValueError("mask/bbox mismatch")
    mask = np.zeros((height, width), dtype=bool)
    mask[y0:y1+1, x0:x1+1] = patch
    return mask


def export(image_path, rgb, annotations, masks, directory):
    height, width = rgb.shape[:2]
    (directory / "masks").mkdir()
    shapes, records = [], []
    overlay = rgb.astype(np.float32).copy()
    # Ignore last, with documented priority. Individual overlapping masks remain exact.
    order = sorted(range(len(annotations)), key=lambda i: (annotations[i]["label"] == "ignore", annotations[i]["id"]))
    for a, mask in zip(annotations, masks):
        shape = mask_shape(mask, a)
        shapes.append(shape)
        filename = f"masks/{a['id']:04d}_{a['label']}.png"
        Image.fromarray(mask.astype(np.uint8)*255).save(directory / filename)
        points = a["positive"] + a.get("negative", [])
        hits = [bool(mask[int(y), int(x)]) for x, y in points]
        npos = len(a["positive"])
        records.append({**a, "mask": filename, "mask_sha256": digest(directory / filename),
                        "area_px": int(mask.sum()), "positive_hits": sum(hits[:npos]),
                        "positive_count": npos, "negative_hits": sum(hits[npos:]),
                        "negative_count": len(points)-npos})
    for i in order:
        a, mask = annotations[i], masks[i]
        overlay[mask] = overlay[mask]*0.72 + np.array(COLORS[a["label"]])*0.28
    im = Image.fromarray(overlay.astype(np.uint8))
    draw = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=13)
    occupied = []
    for a, mask in zip(annotations, masks):
        x, y = a["positive"][0]
        # Short ID only; keep labels readable around adjacent groove/root instances.
        choices = [(x+7,y+7),(x-45,y+7),(x+7,y-23),(x-45,y-23),(x+30,y+30),(x-65,y-40)]
        for xx, yy in choices:
            xx, yy = max(0,min(int(xx),width-42)), max(22,min(int(yy),height-20))
            box = (xx,yy,xx+40,yy+18)
            if not any(box[0]<b[2] and box[2]>b[0] and box[1]<b[3] and box[3]>b[1] for b in occupied):
                break
        occupied.append(box)
        draw.line([(x,y),(xx,yy+7)], fill="white", width=1)
        draw.text((xx,yy), f"{a['id']:04d}", font=font, fill="white", stroke_width=2, stroke_fill="black")
    draw.rectangle((0,0,width,18), fill="black")
    draw.text((4,1), "DRAFT / UNVERIFIED / NOT TEST GROUND TRUTH", font=font, fill="yellow")
    im.save(directory / "overlay_ids.png")
    # A second exact-size audit image displays the submitted positive/negative points.
    im = Image.fromarray(rgb.copy())
    draw = ImageDraw.Draw(im)
    for a in annotations:
        for kind in ("positive", "negative"):
            for x, y in a.get(kind, []):
                c = "lime" if kind == "positive" else "red"
                draw.ellipse((x-2,y-2,x+2,y+2), outline=c, width=1)
        x, y = a["positive"][0]
        draw.text((x+4,y+2), str(a["id"]), font=font, fill="white", stroke_width=1, stroke_fill="black")
    im.save(directory / "points.png")
    # Labelme's rasterizer assigns in shape order; ensure ignore survives overlaps.
    shapes.sort(key=lambda s: s["label"] == "ignore")
    labelme = {"version": "5.8.1", "flags": {"draft": True, "human_verified": False, "test_ground_truth_allowed": False},
               "shapes": shapes, "imagePath": image_path.name,
               "imageData": base64.b64encode(image_path.read_bytes()).decode("ascii"),
               "imageHeight": height, "imageWidth": width,
               "annotation_status": "draft", "provenance": "SAM2ImagePredictor; agent-selected pixel point prompts; no human verification",
               "unlabeled_pixel_policy": "unknown, never infer substrate/background from absence of annotation",
               "overlap_policy": "ignore has priority; remaining class conflicts require review"}
    write_json(directory / (image_path.stem+".json"), labelme)
    write_json(directory / "instances.json", records)
    return records
