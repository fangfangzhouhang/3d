"""Frozen DINOv2 + PatchCore-style memory bank, not canonical PatchCore.

No network parameters are fitted. A small coreset stores reference descriptors;
queries receive exact nearest-neighbour distances. Synthetic references support
only explicitly synthetic self-checks, never real-image scoring.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image

from .base import BaseProbe, ImageInput, ModelInfo, ProbeBlocked, ProbeOutput
from .registry import register


def sha256_file(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def greedy_coreset(features: np.ndarray, cap: int = 128):
    """Select unique original rows by deterministic farthest-first traversal."""
    features = np.asarray(features, dtype=np.float32)
    if features.ndim < 2 or features.shape[-1] == 0:
        raise ValueError("features need a nonempty descriptor axis")
    values = features.reshape(-1, features.shape[-1])
    if not len(values) or not np.isfinite(values).all() or not isinstance(cap, int) or cap <= 0:
        raise ValueError("invalid coreset input/cap")
    count = min(cap, len(values))
    center = values.mean(axis=0, dtype=np.float64).astype(np.float32)
    chosen = np.empty(count, dtype=np.int64)
    chosen[0] = int(np.argmax(np.sum((values - center) ** 2, axis=1, dtype=np.float64)))
    nearest = np.sum((values - values[chosen[0]]) ** 2, axis=1, dtype=np.float64)
    nearest[chosen[0]] = -1  # Constant descriptors must not select the first row twice.
    for index in range(1, count):
        next_index = int(np.argmax(nearest))
        chosen[index] = next_index
        nearest = np.minimum(nearest, np.sum((values - values[next_index]) ** 2, axis=1, dtype=np.float64))
        nearest[chosen[:index + 1]] = -1
    return values[chosen].copy(), chosen


def nearest_euclidean(query, bank, chunk_size: int = 1024):
    """Exact Euclidean nearest-bank distance, chunked to bound CPU memory."""
    query, bank = np.asarray(query, dtype=np.float32), np.asarray(bank, dtype=np.float32)
    if query.ndim < 2 or bank.ndim < 2:
        raise ValueError("query and bank need descriptor axes")
    query, bank = query.reshape(-1, query.shape[-1]), bank.reshape(-1, bank.shape[-1])
    if (not len(bank) or query.shape[1] != bank.shape[1] or not np.isfinite(query).all()
            or not np.isfinite(bank).all() or not isinstance(chunk_size, int) or chunk_size <= 0):
        raise ValueError("invalid nearest-neighbour inputs")
    memory = bank.astype(np.float64)
    squared_memory = np.sum(memory * memory, axis=1)
    output = np.empty(len(query), dtype=np.float32)
    for start in range(0, len(query), chunk_size):
        block = query[start:start + chunk_size].astype(np.float64)
        distances = np.sum(block * block, axis=1, keepdims=True) + squared_memory[None] - 2 * block @ memory.T
        output[start:start + len(block)] = np.sqrt(np.maximum(distances.min(axis=1), 0)).astype(np.float32)
    return output


def _aggregate(features):
    """Local 3 x 3 mean with edge replication, before coreset or query scoring."""
    features = np.asarray(features, dtype=np.float32)
    if features.ndim != 3 or not np.isfinite(features).all():
        raise ValueError("features must be finite H x W x C")
    padded = np.pad(features, ((1, 1), (1, 1), (0, 0)), mode="edge")
    output = np.zeros_like(features)
    for y in range(3):
        for x in range(3):
            output += padded[y:y + features.shape[0], x:x + features.shape[1]]
    return output / 9


def _inside(root: Path, reference: str | Path) -> Path:
    root = root.resolve()
    path = (root / reference).resolve()
    if not path.is_relative_to(root):
        raise ProbeBlocked("Reference escapes experiment root.", "missing-clean-reference")
    return path


def _known_forbidden_reference_hashes(root: Path) -> set[str]:
    """Known real queries and generated fixtures are never real clean references."""
    hashes = set()
    sources = root / "sources.json"
    if sources.is_file():
        values = json.loads(sources.read_text(encoding="utf-8"))
        hashes.update(str(value).lower() for value in values.get("input_sha256", {}).values())
    for name in ("realpart_img1.jpg", "realpart_img2.jpg"):
        path = root / "inputs" / name
        if path.is_file():
            hashes.add(sha256_file(path))
    synthetic = root / "dataset" / "stage2_synthetic"
    if synthetic.is_dir():
        hashes.update(sha256_file(path) for path in synthetic.glob("*.png"))
    return hashes


def validate_reference_manifest(path, root, design_id):
    """Validate declarations and bytes; this does not certify physical cleanliness."""
    root = Path(root).resolve()
    if not isinstance(design_id, str) or not design_id.strip():
        raise ProbeBlocked("Real query design_id is missing; same-design reference matching is impossible.", "missing-clean-reference")
    path = _inside(root, path)
    if not path.is_file():
        raise ProbeBlocked("Clean reference manifest missing.", "missing-clean-reference")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (manifest.get("synthetic") is True or manifest.get("evidence_kind") == "synthetic"
            or manifest.get("schema") != "patchcore-reference-v1"
            or manifest.get("verified_clean") is not True
            or not isinstance(manifest.get("verified_by"), str) or not manifest["verified_by"].strip()
            or manifest.get("design_id") != design_id):
        raise ProbeBlocked("Manifest is not a real, verified-clean declaration for this design.", "missing-clean-reference")
    if not isinstance(manifest.get("images"), list) or not manifest["images"]:
        raise ProbeBlocked("Manifest has no clean references.", "missing-clean-reference")
    forbidden = _known_forbidden_reference_hashes(root)
    manifest_hash = sha256_file(path)
    references, seen_hashes = [], set()
    for item in manifest["images"]:
        if not isinstance(item, dict):
            raise ProbeBlocked("Malformed reference image record.", "missing-clean-reference")
        relative = item.get("raw_image_ref")
        if (not isinstance(relative, str) or not relative or item.get("design_id") != design_id
                or item.get("synthetic") is True or item.get("evidence_kind") == "synthetic"):
            raise ProbeBlocked("Synthetic, missing or mismatched reference.", "missing-clean-reference")
        image_path = _inside(root, relative)
        digest = str(item.get("sha256", "")).lower()
        if len(digest) != 64 or not image_path.is_file() or sha256_file(image_path) != digest:
            raise ProbeBlocked("Reference missing or SHA256 mismatch.", "missing-clean-reference")
        if digest in forbidden:
            raise ProbeBlocked("Known real query or generated synthetic image cannot become a clean reference.", "missing-clean-reference")
        if digest in seen_hashes:
            raise ProbeBlocked("Repeated reference bytes are not independent clean photographs.", "missing-clean-reference")
        seen_hashes.add(digest)
        references.append({**item, "path": image_path, "sha256": digest,
                           "reference_manifest_ref": path.relative_to(root).as_posix(),
                           "reference_manifest_sha256": manifest_hash, "verified_by": manifest["verified_by"],
                           "evidence_kind": "real_clean_reference"})
    return references


@register("patchcore")
class PatchCoreProbe(BaseProbe):
    def load(self):
        """Record identity now; load the backbone only after the reference gate."""
        self.backend = None
        self.bank = None
        self.bank_provenance = []
        self.bank_key = None
        self._setup_seconds = 0.0
        checkpoint = self.root / self.params.get("checkpoint", "weights/dinov2_vits14_pretrain.pth")
        self.model_info = ModelInfo(
            model_name="DINOv2 ViT-S/14 + PatchCore-style memory bank",
            model_version=self.params.get("source_commit"),
            weights_version="dinov2_vits14_pretrain / LVD-142M",
            weights_ref=checkpoint.relative_to(self.root).as_posix(),
            weights_sha256=sha256_file(checkpoint) if checkpoint.is_file() else None,
            weights_bytes=checkpoint.stat().st_size if checkpoint.is_file() else None,
            code_license="Apache-2.0 (DINOv2 backbone)", weights_license="Apache-2.0",
        )

    def _setup(self, references):
        """Build once per exact ordered reference identity; never reuse a stale bank."""
        identity = []
        for reference in references:
            if sha256_file(reference["path"]) != reference["sha256"]:
                raise ProbeBlocked("Reference bytes changed after manifest validation.", "missing-clean-reference")
            identity.append({key: reference.get(key) for key in (
                "raw_image_ref", "sha256", "design_id", "evidence_kind", "reference_manifest_sha256")})
        bank_key = hashlib.sha256(json.dumps({"references": identity, "memory_cap": self.params.get("memory_cap", 128),
                                             "weights_sha256": self.params.get("checkpoint_sha256"),
                                             "source_commit": self.params.get("source_commit")}, sort_keys=True).encode()).hexdigest()
        if self.bank is not None and self.bank_key == bank_key:
            return False
        started = time.perf_counter()
        if self.backend is None:
            from .dino import DinoProbe
            self.backend = DinoProbe(self.params, self.root)
            self.backend.load()
            self.model_info = replace(self.backend.model_info, model_name="DINOv2 ViT-S/14 + PatchCore-style memory bank")
        chunks, provenance = [], []
        for reference in references:
            with Image.open(reference["path"]) as opened:
                rgb = np.array(opened.convert("RGB"), copy=True)
            features, geometry = self.backend.extract_features(rgb)
            aggregated = _aggregate(features)
            chunks.append(aggregated.reshape(-1, aggregated.shape[-1]))
            provenance.append({**{key: value for key, value in reference.items() if key != "path"},
                               "feature_grid": list(features.shape[:2]), "geometry": geometry})
        bank, indices = greedy_coreset(np.concatenate(chunks), int(self.params.get("memory_cap", 128)))
        self.bank, self.bank_indices = bank, indices
        self.bank_provenance, self.bank_key = provenance, bank_key
        self._setup_seconds = time.perf_counter() - started
        return True

    def _references(self, image: ImageInput):
        synthetic = image.metadata.get("evidence_kind") == "synthetic"
        if synthetic:
            if not self.params.get("synthetic_reference", False):
                raise ProbeBlocked("Synthetic mode must be explicit.", "missing-clean-reference")
            path = _inside(self.root, self.params.get("synthetic_reference_path", "dataset/stage2_synthetic/normal.png"))
            if not path.is_file():
                raise ProbeBlocked("Synthetic normal fixture is missing; run probes.make_synthetic.", "missing-synthetic-reference")
            return [{"path": path, "raw_image_ref": path.relative_to(self.root).as_posix(),
                     "sha256": sha256_file(path), "design_id": "synthetic-periodic-v1", "evidence_kind": "synthetic"}], True
        manifest = self.params.get("reference_manifest")
        if not manifest:
            raise ProbeBlocked("No gold-standard clean reference photographs of the same design; real inference is blocked.", "missing-clean-reference")
        if self.params.get("synthetic_reference", False):
            raise ProbeBlocked("Synthetic memory bank cannot score real queries; use the clean-reference configuration.", "missing-clean-reference")
        references = validate_reference_manifest(self.root / manifest, self.root, image.metadata.get("design_id"))
        query_hash = sha256_file(image.path)
        if query_hash in {reference["sha256"] for reference in references}:
            raise ProbeBlocked("Query bytes cannot also serve as a clean reference.", "missing-clean-reference")
        forbidden = {str(value).lower() for value in self.params.get("forbidden_real_hashes", [])}
        if any(reference["sha256"] in forbidden for reference in references):
            raise ProbeBlocked("Known non-clean reference hash is forbidden.", "missing-clean-reference")
        return references, False

    def infer(self, image: ImageInput, output_dir: Path) -> ProbeOutput:
        references, synthetic = self._references(image)
        built_bank = self._setup(references)
        output_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        with Image.open(image.path) as opened:
            rgb = np.array(opened.convert("RGB"), copy=True)
        features, geometry = self.backend.extract_features(rgb)
        distances = nearest_euclidean(_aggregate(features), self.bank).reshape(features.shape[:2])
        height, width = rgb.shape[:2]
        padded_height, padded_width = int(geometry["padded_height"]), int(geometry["padded_width"])
        heatmap = np.asarray(Image.fromarray(distances).resize(
            (padded_width, padded_height), Image.Resampling.BILINEAR).crop((0, 0, width, height)), dtype=np.float32)
        inference_seconds = time.perf_counter() - started
        np.save(output_dir / "patch_distances.npy", distances, allow_pickle=False)
        np.save(output_dir / "heatmap.npy", heatmap, allow_pickle=False)
        display_p99 = float(np.quantile(heatmap, 0.99))
        display_scale = display_p99 if display_p99 > 0 else 1.0
        normalized = np.clip(heatmap / display_scale, 0, 1)
        colors = np.stack((255 * normalized, 255 * np.sqrt(normalized), 255 * (1 - normalized)), axis=-1).astype(np.uint8)
        Image.fromarray(colors).save(output_dir / "heatmap.png")
        Image.blend(Image.fromarray(rgb), Image.fromarray(colors), 0.45).save(output_dir / "overlay.png")
        np.save(output_dir / "memory_bank.npy", self.bank, allow_pickle=False)
        np.save(output_dir / "memory_bank_indices.npy", self.bank_indices, allow_pickle=False)
        (output_dir / "reference_provenance.json").write_text(json.dumps(self.bank_provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        metrics = {
            "patch_score_max": float(distances.max()), "patch_score_mean": float(distances.mean()),
            "heatmap_score_max": float(heatmap.max()), "heatmap_score_mean": float(heatmap.mean()),
            "feature_grid": list(features.shape[:2]), "memory_bank_size": len(self.bank),
            "memory_bank_cap": int(self.params.get("memory_cap", 128)), "memory_bank_identity": self.bank_key,
            "method": "frozen DINOv2 patch features; 3x3 local mean; unique farthest-first coreset; exact nearest Euclidean distance",
            "canonical_patchcore": False,
            "deviation": "DINOv2 replaces canonical PatchCore WideResNet backbone; no canonical performance claim",
            "reference_provenance": self.bank_provenance,
            "reference_setup_seconds": self._setup_seconds if built_bank else 0.0,
            "cached_bank_build_seconds": self._setup_seconds,
            "first_infer_includes_reference_setup": built_bank,
            "inference_seconds": inference_seconds,
            "query_feature_extract_seconds": geometry.get("feature_extract_seconds"),
            "inference_timing_scope": "query read + frozen feature extraction + aggregation + nearest-bank distances + bilinear upsampling; excludes reference setup and output writes",
            "visualization": {"display_p99": display_p99, "display_scale": display_scale,
                              "normalization": "per-image p99, clipped; only within-image visual contrast",
                              "cross_image_colors_comparable": False,
                              "raw_values": "Euclidean feature distances; not probabilities or material classes",
                              "upsampling": "bilinear patch-grid upsampling then crop to original dimensions"},
            "synthetic_only": synthetic, "training": False, "network_parameters_updated": False,
            "threshold_or_classifier_fitted": False,
        }
        if synthetic and image.metadata.get("synthetic_mask"):
            path = _inside(self.root, image.metadata["synthetic_mask"])
            with Image.open(path) as opened:
                mask = np.asarray(opened.convert("L")) > 0
            if mask.shape != heatmap.shape:
                raise ValueError("Synthetic spot mask must match original query dimensions")
            inside_mean = float(heatmap[mask].mean()) if mask.any() else None
            outside_mean = float(heatmap[~mask].mean()) if (~mask).any() else None
            metrics.update({
                "synthetic_mask_ref": path.relative_to(self.root).as_posix(), "synthetic_mask_sha256": sha256_file(path),
                "synthetic_patch_mean_inside": inside_mean, "synthetic_patch_mean_outside": outside_mean,
                "synthetic_patch_relative": inside_mean / (outside_mean + 1e-12) if inside_mean is not None and outside_mean is not None else None,
                "selfcheck_inside_gt_outside": inside_mean > outside_mean if inside_mean is not None and outside_mean is not None else None,
            })
        return ProbeOutput(metrics=metrics, output_files=[
            "patch_distances.npy", "heatmap.npy", "heatmap.png", "overlay.png",
            "memory_bank.npy", "memory_bank_indices.npy", "reference_provenance.json",
        ])

    def release(self):
        self.bank = None
        backend = getattr(self, "backend", None)
        if backend is not None:
            backend.release()
        self.backend = None
