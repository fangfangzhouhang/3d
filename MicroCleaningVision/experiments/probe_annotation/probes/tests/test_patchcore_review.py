"""CPU regression cases for reference provenance and PatchCore bookkeeping."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from probes.base import ImageInput, ModelInfo, ProbeBlocked
from probes.patchcore import PatchCoreProbe, greedy_coreset, validate_reference_manifest


def write_image(root, name, color):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (28, 28), color).save(path)
    return path


def reference(root, path, design="design-a"):
    return {"raw_image_ref": path.relative_to(root).as_posix(), "path": path,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "design_id": design}


def write_manifest(root, ref, *, design="design-a", synthetic=False):
    path = root / "references.json"
    record = {key: value for key, value in ref.items() if key != "path"}
    path.write_text(json.dumps({"schema": "patchcore-reference-v1", "synthetic": synthetic,
                               "verified_clean": True, "verified_by": "fixture-reviewer",
                               "design_id": design, "images": [record]}), encoding="utf-8")
    return path


class CPUBackbone:
    """Deterministic RGB patch means; explicitly a test double, no neural model."""
    def __init__(self, params, root):
        self.model_info = ModelInfo(model_name="CPU test double")

    def load(self):
        pass

    def extract_features(self, rgb):
        features = rgb.reshape(2, 14, 2, 14, 3).mean(axis=(1, 3)).astype(np.float32) / 255
        return features, {"padded_height": 28, "padded_width": 28, "feature_extract_seconds": 0.0}

    def release(self):
        pass


class PatchCoreReferenceReviewTests(unittest.TestCase):
    def test_synthetic_manifest_never_qualifies_as_real_clean(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ref = reference(root, write_image(root, "synthetic.png", "gray"))
            manifest = write_manifest(root, ref, synthetic=True)
            with self.assertRaises(ProbeBlocked):
                validate_reference_manifest(manifest, root, "design-a")

    def test_empty_design_is_not_a_matching_design(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ref = reference(root, write_image(root, "normal.png", "gray"), design="")
            manifest = write_manifest(root, ref, design="")
            with self.assertRaises(ProbeBlocked):
                validate_reference_manifest(manifest, root, "")

    def test_clean_manifest_is_accepted_but_tampering_is_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            normal = write_image(root, "normal.png", "gray")
            manifest = write_manifest(root, reference(root, normal))
            self.assertEqual(len(validate_reference_manifest(manifest, root, "design-a")), 1)
            write_image(root, "normal.png", "white")
            with self.assertRaises(ProbeBlocked):
                validate_reference_manifest(manifest, root, "design-a")

    def test_real_source_cannot_be_renamed_into_clean_bank(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dirty = write_image(root, "renamed_as_clean.png", "red")
            ref = reference(root, dirty)
            (root / "sources.json").write_text(json.dumps({"input_sha256": {"realpart_img1.jpg": ref["sha256"]}}))
            manifest = write_manifest(root, ref)
            with self.assertRaises(ProbeBlocked):
                validate_reference_manifest(manifest, root, "design-a")

    def test_forbidden_reference_hash_does_not_forbid_scoring_query(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clean = write_image(root, "clean.png", "gray")
            query = write_image(root, "query.png", "red")
            qsha = hashlib.sha256(query.read_bytes()).hexdigest()
            write_manifest(root, reference(root, clean))
            params = {"device": "cpu", "reference_manifest": "references.json",
                      "forbidden_real_hashes": [qsha], "memory_cap": 2}
            with patch("probes.dino.DinoProbe", CPUBackbone):
                probe = PatchCoreProbe(params, root)
                probe.load()
                first = probe.infer(ImageInput("query", query, {"design_id": "design-a"}), root / "out1")
                second = probe.infer(ImageInput("query", query, {"design_id": "design-a"}), root / "out2")
            self.assertEqual(first.status, "ok")
            self.assertTrue(first.metrics["first_infer_includes_reference_setup"])
            self.assertFalse(second.metrics["first_infer_includes_reference_setup"])
            heat = np.load(root / "out1" / "heatmap.npy", allow_pickle=False)
            self.assertEqual(heat.shape, (28, 28))
            self.assertTrue(np.isfinite(heat).all())
            self.assertTrue((heat >= 0).all())

    def test_cached_bank_is_bound_to_reference_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_ref = reference(root, write_image(root, "normal1.png", "red"))
            second_ref = reference(root, write_image(root, "normal2.png", "green"))
            with patch("probes.dino.DinoProbe", CPUBackbone):
                probe = PatchCoreProbe({"device": "cpu", "memory_cap": 2}, root)
                probe.load()
                probe._setup([first_ref])
                first = probe.bank.copy()
                probe._setup([second_ref])
            self.assertFalse(np.array_equal(first, probe.bank))
            self.assertEqual(probe.bank_provenance[0]["sha256"], second_ref["sha256"])

    def test_constant_coreset_never_repeats_source_index(self):
        vectors = np.ones((5, 3), np.float32)
        bank, indices = greedy_coreset(vectors, cap=5)
        self.assertEqual(len(set(indices.tolist())), 5)
        np.testing.assert_array_equal(bank, vectors[indices])


if __name__ == "__main__":
    unittest.main()
