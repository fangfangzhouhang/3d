"""Recheck local delivery: tests, exact Labelme pixels, grouping, source/scope integrity."""
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image

from .export import digest, write_json
from .verify import verify_dataset

ROOT = Path(__file__).resolve().parents[1]


def main():
    if not __debug__:
        raise RuntimeError("run without -O")
    unit = subprocess.run([sys.executable,"-m","unittest","test_stage0","annotate.test_annotation",
                           "annotate.test_manifest","-v"],cwd=ROOT,capture_output=True,text=True,encoding="utf-8")
    (ROOT/"logs/stage1_tests.log").write_text(unit.stdout+unit.stderr,encoding="utf-8")
    if unit.returncode:
        raise RuntimeError("targeted tests failed; see logs/stage1_tests.log")
    dataset = ROOT/"dataset/seed_v0"
    evidence = verify_dataset(dataset,ROOT/"inputs")
    write_json(ROOT/"logs/stage1_verify.json",evidence)
    reference = ROOT/"logs/labelme_format_reference"
    provenance = json.loads((reference/"provenance.json").read_text(encoding="utf-8"))
    assert digest(reference/"official_utils.py") == provenance["sha256"]
    assert digest(reference/"LICENSE") == provenance["license_sha256"]
    spec = importlib.util.spec_from_file_location("labelme_official_format_reference",reference/"official_utils.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    roundtrip, repeated_masks = [], 0
    for info in evidence["images"]:
        stem = Path(info["filename"]).stem
        output = dataset/stem
        path = output/(stem+".json")
        data = json.loads(path.read_text(encoding="utf-8"))
        loaded = module.load_label_file(str(path))
        assert loaded.image_data == (ROOT/"inputs"/info["filename"]).read_bytes()
        ignore = np.zeros((data["imageHeight"],data["imageWidth"]),bool)
        for shape in loaded.shapes:
            cls,_ = module.shapes_to_label(img_shape=ignore.shape,shapes=[shape],label_name_to_value={shape["label"]:1})
            name = f"{shape['group_id']:04d}_{shape['label']}.png"
            with Image.open(output/"masks"/name) as im:
                mask = np.array(im)>0
            np.testing.assert_array_equal(cls>0,mask)
            with Image.open(ROOT/"results/stage1_review"/(stem+"_first_export")/"masks"/name) as im:
                np.testing.assert_array_equal(np.array(im)>0,mask)
            repeated_masks += 1
            if shape["label"] == "ignore":
                ignore |= mask
        cls,_ = module.shapes_to_label(img_shape=ignore.shape,shapes=loaded.shapes,
                                      label_name_to_value={"structure":1,"residue":2,"substrate":3,"ignore":255})
        np.testing.assert_array_equal(cls==255,ignore)
        metrics = json.loads((output/"metrics.json").read_text(encoding="utf-8"))
        for name in ("predict.py","export.py","session.py"):
            assert digest(ROOT/"annotate"/name) == metrics["code_sha256"][name]
        roundtrip.append({"image":stem,"instances":len(loaded.shapes),"individual_masks":"pixel_equal",
                          "ignore_priority":"preserved by official sequential reader"})
    write_json(reference/"roundtrip_final.json",{"reader":provenance,"results":roundtrip})
    before = json.loads((ROOT/"logs/stage1_before.json").read_text(encoding="utf-8"))
    changed = [name for name,h in before.items() if not (ROOT/name).is_file() or digest(ROOT/name)!=h]
    allowed = {"README.md","failures.md","MODELS.md"}
    assert set(changed) <= allowed, changed
    tracked = subprocess.check_output(["git","diff","--name-only","HEAD"],cwd=ROOT,text=True)
    assert not tracked.strip(), tracked
    repo = ROOT.parents[2]
    untracked = subprocess.check_output(["git","-c","core.quotepath=false","ls-files","--others","--exclude-standard"],cwd=repo,text=True,encoding="utf-8").splitlines()
    assert all(p.startswith("MicroCleaningVision/experiments/probe_annotation/") for p in untracked)
    expected = {}
    for line in (ROOT/"requirements.txt").read_text(encoding="utf-8").splitlines():
        if "==" in line and not line.startswith("#"):
            name,version = line.split("==")
            actual = importlib.metadata.version(name)
            assert actual == version,(name,actual,version)
            expected[name] = actual
    with Image.open(ROOT/"inputs/realpart_img1.jpg") as im:
        exif1 = im.getexif().get(274)
    with Image.open(ROOT/"inputs/realpart_img2.jpg") as im:
        exif2 = im.getexif().get(274)
    assert exif1 in (None,1) and exif2 in (None,1)
    integrity = {"status":"pass","tracked_files_changed":0,"untracked_outside_experiment":0,
                 "stage0_preservation":"all prior files unchanged except documented experiment README/failures/MODELS additions",
                 "prior_snapshot_files":len(before),"changed_prior_files":changed,
                 "runtime":sys.executable,"locked_packages_verified":expected,
                 "final_vs_first_export_masks_pixel_equal":repeated_masks,
                 "exif_orientation":[exif1,exif2],"independent_labelme_reader":roundtrip}
    write_json(ROOT/"logs/stage1_scope_integrity.json",integrity)
    # A complete hash inventory for the seed payload; this file itself stays in logs.
    write_json(ROOT/"logs/seed_v0_inventory.json",{p.relative_to(dataset).as_posix():digest(p)
                for p in sorted(dataset.rglob("*")) if p.is_file()})
    print(json.dumps({"status":"pass","targeted_tests":"see logs/stage1_tests.log",
                      "dataset":evidence,"repeated_masks":repeated_masks,"scope":"experiment only"},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
