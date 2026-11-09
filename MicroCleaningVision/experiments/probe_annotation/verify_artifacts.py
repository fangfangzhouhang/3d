"""Validate original-size binary proposals, union, counts, hashes; optional rerun comparison."""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from run_stage0 import ROOT, digest


def verify(directory):
    metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["status"] == "complete", "incomplete inference"
    assert len(metrics["images"]) == 2
    total = 0
    for record in metrics["images"]:
        source = ROOT / "inputs" / record["filename"]
        assert digest(source) == record["input_sha256"]
        shape = (record["height"], record["width"])
        assert Image.open(source).size == shape[::-1]
        folder = directory / source.stem
        proposals = json.loads((folder / "proposals.json").read_text(encoding="utf-8"))
        assert len(proposals) == record["mask_count"]
        assert len(list((folder / "masks").glob("*.png"))) == len(proposals)
        union = np.zeros(shape, bool)
        for p in proposals:
            mask = np.asarray(Image.open(folder / p["mask"]))
            assert mask.shape == shape and set(np.unique(mask)) <= {0, 255}
            assert int((mask > 0).sum()) == p["area"]
            assert p["semantic_class"] is None
            union |= mask > 0
        np.testing.assert_array_equal(np.asarray(Image.open(folder / "proposal_union.png")) > 0, union)
        assert int(union.sum()) == record["union_pixels"]
        for filename in ("overlay.png", "overlay_ids.png", "proposal_ids.png"):
            assert Image.open(folder / filename).size == shape[::-1]
        total += len(proposals)
    return {"images": 2, "binary_proposals_checked": total, "status": "passed"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    result = verify(args.directory)
    if args.compare:
        verify(args.compare)
        relative = sorted(p.relative_to(args.directory) for p in args.directory.rglob("*.png"))
        other = sorted(p.relative_to(args.compare) for p in args.compare.rglob("*.png"))
        assert relative == other, "different image inventories"
        for path in relative:
            np.testing.assert_array_equal(np.asarray(Image.open(args.directory / path)),
                                          np.asarray(Image.open(args.compare / path)))
        result["identical_png_pixels"] = len(relative)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
