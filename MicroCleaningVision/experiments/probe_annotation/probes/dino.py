"""Frozen DINOv2 ViT-S/14 patch-feature probe, without fitting a classifier."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .base import BaseProbe, ImageInput, ModelInfo, ProbeBlocked, ProbeOutput
from .registry import register


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _official_constructor(source: Path):
    """Import only the Apache-2.0 ViT-S/14 entrypoint from the pinned checkout.

    Recent upstream hubconf also imports unrelated CELL-DINO and XRay-DINO
    entrypoints with different licences. They are not part of this experiment.
    """
    source = source.resolve()
    sys.path.insert(0, str(source))
    try:
        module = importlib.import_module("dinov2.hub.backbones")
        if not Path(module.__file__).resolve().is_relative_to(source):
            raise ValueError("A different DINO package is already imported")
        return module.dinov2_vits14
    finally:
        sys.path.remove(str(source))


@register("dino")
class DinoProbe(BaseProbe):
    """Use a pinned official source checkout and checkpoint already on disk.

    ``source_commit`` and ``checkpoint_sha256`` are required. Loading never
    downloads packages or weights, and never changes the stage 0/1 environment.
    GPU occupancy and memory budgets are enforced by the common runner.
    """

    def load(self) -> None:
        checkpoint = self.root / self.params.get("checkpoint", "weights/dinov2_vits14_pretrain.pth")
        source = self.root / self.params.get("source_dir", "vendor/dinov2")
        expected_hash = self.params.get("checkpoint_sha256")
        expected_commit = self.params.get("source_commit")
        self.model_info = ModelInfo(
            model_name="DINOv2 ViT-S/14", model_version=expected_commit,
            weights_version="dinov2_vits14_pretrain / LVD-142M",
            weights_ref=str(checkpoint.relative_to(self.root)),
            weights_sha256=expected_hash, weights_bytes=checkpoint.stat().st_size if checkpoint.is_file() else None,
            code_license="Apache-2.0", weights_license="Apache-2.0",
        )
        if not source.is_dir() or not checkpoint.is_file():
            raise ProbeBlocked("Official DINOv2 checkout/checkpoint is missing; use the pinned setup instructions.", code="blocked-env")
        if not expected_hash or not expected_commit:
            raise ValueError("DINO requires checkpoint_sha256 and source_commit")
        if _sha256(checkpoint) != expected_hash:
            raise ValueError("DINO checkpoint SHA256 mismatch")
        actual_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        if actual_commit != expected_commit:
            raise ValueError("DINO source commit mismatch")
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=source, text=True).strip():
            raise ValueError("DINO source checkout has local changes")
        # The optional xformers acceleration is deliberately not installed.
        os.environ["XFORMERS_DISABLED"] = "1"
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
        import torch
        self.torch = torch
        self.device = self.params.get("device", "cuda")
        if self.device.startswith("cuda") and not torch.cuda.is_available():
            raise ProbeBlocked("CUDA is unavailable; there is no silent CPU fallback.", code="blocked-env")
        torch.set_num_threads(4)
        torch.manual_seed(int(self.params.get("seed", 0)))
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        torch.backends.cudnn.benchmark = False
        torch.use_deterministic_algorithms(True)
        self.model = _official_constructor(source)(pretrained=False)
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)
        self.model.load_state_dict(state, strict=True)
        del state
        self.model.eval().requires_grad_(False).to(self.device)
        self.parameter_count = sum(p.numel() for p in self.model.parameters())

    def _draft_masks(self, image: ImageInput, shape: tuple[int, int]):
        import numpy as np
        from PIL import Image
        from .feature_utils import CLASSES
        reference = image.metadata.get("seed_dir")
        if not reference:
            raise ProbeBlocked("Image has no seed_dir metadata; draft feature statistics cannot be traced.", code="blocked-data")
        seed_dir = (self.root / reference).resolve()
        records_path = seed_dir / "instances.json"
        records = json.loads(records_path.read_text(encoding="utf-8"))
        points = seed_dir / "points.json"
        if json.loads(points.read_text(encoding="utf-8"))["image_sha256"] != _sha256(image.path):
            raise ValueError("Seed drafts belong to a different input image")
        masks = {label: np.zeros(shape, dtype=bool) for label in (*CLASSES, "ignore")}
        provenance = {"seed_dir": str(reference), "instances_sha256": _sha256(records_path),
                      "points_sha256": _sha256(points), "mask_files": []}
        for record in records:
            path = (seed_dir / record["mask"]).resolve()
            path.relative_to(seed_dir)
            if record["label"] not in masks:
                raise ValueError("Unknown draft label")
            digest = _sha256(path)
            if digest != record["mask_sha256"]:
                raise ValueError("Seed mask SHA256 mismatch")
            with Image.open(path) as opened:
                raw = np.asarray(opened)
            if raw.shape != shape or not np.isin(raw, [0, 255]).all():
                raise ValueError("Seed mask must be original-size binary 0/255")
            masks[record["label"]] |= raw > 0
            provenance["mask_files"].append({"id": record["id"], "label": record["label"],
                                             "ref": record["mask"], "sha256": digest})
        return masks, provenance

    def extract_features(self, rgb):
        """Return (H/14 x W/14 x 384 float32 descriptors, geometry/timing).

        Reused by the PatchCore memory-bank probe. No labels, masks, PCA or
        clustering enter the frozen backbone. Input must be uint8 H x W x 3.
        The common runner must perform GPU checks before calling this method.
        """
        import numpy as np
        torch = self.torch
        if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
            raise ValueError("DINO extraction expects H x W x 3 uint8 RGB")
        height, width = rgb.shape[:2]
        padded_height, padded_width = ((n + 13) // 14 * 14 for n in (height, width))
        padded = np.pad(rgb, ((0, padded_height - height), (0, padded_width - width), (0, 0)), mode="edge")
        normalized = (padded.astype(np.float32) / 255 - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
        tensor = torch.from_numpy(np.ascontiguousarray(normalized.transpose(2, 0, 1))).unsqueeze(0).to(self.device)
        if self.device.startswith("cuda"):
            torch.cuda.synchronize()
        start = time.perf_counter()
        with torch.inference_mode():
            tokens = self.model.forward_features(tensor)["x_norm_patchtokens"]
            features = tokens[0].float().cpu().numpy().reshape(padded_height // 14, padded_width // 14, -1)
        if self.device.startswith("cuda"):
            torch.cuda.synchronize()
        feature_seconds = time.perf_counter() - start
        del tensor, tokens
        if features.shape[-1] != 384 or not np.isfinite(features).all():
            raise ValueError("Expected finite 384-D ViT-S/14 patch features")
        return features, {
            "feature_extract_seconds": feature_seconds, "input_width": width, "input_height": height,
            "padded_width": padded_width, "padded_height": padded_height, "patch_size": 14,
        }

    def infer(self, image: ImageInput, output_dir: Path) -> ProbeOutput:
        import numpy as np
        from PIL import Image
        from .feature_utils import PALETTE, pca_rgb, cluster_features, draft_statistics
        with Image.open(image.path) as opened:
            rgb = np.array(opened.convert("RGB"), copy=True)
        height, width = rgb.shape[:2]
        features, geometry = self.extract_features(rgb)
        padded_height, padded_width = geometry["padded_height"], geometry["padded_width"]
        np.save(output_dir / "features.npy", features, allow_pickle=False)
        pca, projection = pca_rgb(features)
        np.savez(output_dir / "pca_projection.npz", **projection)
        Image.fromarray(pca).save(output_dir / "pca_patch_rgb.png")
        pca_image = Image.fromarray(pca).resize((padded_width, padded_height), Image.Resampling.BILINEAR).crop((0, 0, width, height))
        pca_image.save(output_dir / "pca_rgb.png")
        Image.blend(Image.fromarray(rgb), pca_image, 0.45).save(output_dir / "pca_overlay.png")
        clusters, centers, cluster_info = cluster_features(features, k=int(self.params.get("clusters", 4)), seed=int(self.params.get("seed", 0)))
        np.save(output_dir / "cluster_centers.npy", centers, allow_pickle=False)
        Image.fromarray(clusters).save(output_dir / "cluster_patch_ids.png")
        cluster_ids = Image.fromarray(clusters).resize((padded_width, padded_height), Image.Resampling.NEAREST).crop((0, 0, width, height))
        cluster_ids.save(output_dir / "cluster_ids.png")
        cluster_image = Image.fromarray(PALETTE[np.asarray(cluster_ids)])
        cluster_image.save(output_dir / "clusters.png")
        Image.blend(Image.fromarray(rgb), cluster_image, 0.45).save(output_dir / "overlay.png")
        # A common-size panel allows original/PCA/cluster inspection without inference.
        panel = Image.new("RGB", (width * 3, height))
        for x, picture in enumerate((Image.fromarray(rgb), pca_image, cluster_image)):
            panel.paste(picture, (x * width, 0))
        panel.save(output_dir / "original_pca_clusters.png")
        masks, provenance = self._draft_masks(image, (height, width))
        stats, weights = draft_statistics(features, masks, (height, width), 14)
        stats["provenance"] = provenance
        _write_json(output_dir / "draft_feature_statistics.json", stats)
        np.savez(output_dir / "draft_patch_pixel_counts.npz", **weights)
        metrics = {
            "model_parameters": self.parameter_count, "feature_extract_seconds": geometry["feature_extract_seconds"],
            "feature_shape": list(features.shape), "feature_dtype": str(features.dtype),
            "input_width": width, "input_height": height,
            "preprocessing": {"resize": False, "padding": "bottom/right edge replication to multiple of 14",
                              "padded_width": padded_width, "padded_height": padded_height,
                              "patch_size": 14, "normalization_mean": [0.485, 0.456, 0.406],
                              "normalization_std": [0.229, 0.224, 0.225], "precision": "float32"},
            "pca": {"scope": "per image; colours not comparable across images", "display_quantiles": [0.01, 0.99],
                    "explained_variance_ratio": projection["explained_variance_ratio"].tolist(),
                    "upsampling": "bilinear display only; raw patch features remain available"},
            "clustering": {**cluster_info, "scope": "per image; arbitrary IDs have no class semantics",
                           "palette_rgb": PALETTE[:cluster_info["k"]].tolist()},
            "draft_feature_statistics": stats,
            "training": False, "classifier": False, "labelled_accuracy": None,
            "qualitative_scope": "Inspect feature grouping and draft relative distances; missing classes stay unavailable.",
        }
        files = [path.name for path in sorted(output_dir.iterdir()) if path.is_file() and not path.name.startswith("nvidia_smi")]
        missing = stats["missing_classes"]
        return ProbeOutput(metrics=metrics, output_files=files,
                           status_reason="Feature extraction completed; full three-class separability unavailable because draft classes are missing." if missing else "Feature extraction and exploratory draft statistics completed.",
                           reason_codes=["draft-not-ground-truth", *["missing-draft-" + label for label in missing]])

    def release(self) -> None:
        self.model = None
        if hasattr(self, "torch") and getattr(self, "device", "cpu").startswith("cuda"):
            self.torch.cuda.empty_cache()
