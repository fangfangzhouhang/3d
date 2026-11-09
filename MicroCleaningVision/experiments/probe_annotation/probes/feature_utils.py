"""Small, deterministic feature summaries; no network optimization or labels inferred.

All semantic statistics consume the existing *draft* masks. Unlabelled pixels are
unknown, including pixels outside every mask: they are never substrate examples.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np


CLASSES = ("structure", "residue", "substrate")
PALETTE = np.array([
    [31, 119, 180], [255, 127, 14], [44, 160, 44], [214, 39, 40],
    [148, 103, 189], [140, 86, 75], [227, 119, 194], [127, 127, 127],
    [188, 189, 34], [23, 190, 207], [55, 65, 81], [250, 204, 21],
    [14, 165, 233], [244, 63, 94], [163, 230, 53], [168, 85, 247],
], dtype=np.uint8)


def unit_rows(values: np.ndarray) -> np.ndarray:
    """L2 normalize the final axis; zero vectors remain zero."""
    values = np.asarray(values, dtype=np.float64)
    return values / np.maximum(np.linalg.norm(values, axis=-1, keepdims=True), 1e-12)


def pca_rgb(features: np.ndarray) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Per-image PCA with canonical signs and 1/99 percentile display scaling.

    Colour is local to this image's projection, never a semantic class or a
    colour correspondence between different images. Original features survive.
    """
    if features.ndim != 3 or features.shape[-1] < 3:
        raise ValueError("PCA expects H x W x D with D >= 3")
    flat = features.reshape(-1, features.shape[-1]).astype(np.float64)
    if len(flat) < 3 or not np.isfinite(flat).all():
        raise ValueError("PCA needs at least three finite patch vectors")
    mean = flat.mean(axis=0)
    centered = flat - mean
    _, singular, vt = np.linalg.svd(centered, full_matrices=False)
    components = vt[:3].copy()
    for row in components:
        # Resolve the arbitrary sign to make the same environment reproducible.
        if row[np.argmax(np.abs(row))] < 0:
            row *= -1
    projected = centered @ components.T
    low, high = np.quantile(projected, [0.01, 0.99], axis=0)
    scaled = np.clip((projected - low) / np.maximum(high - low, 1e-12), 0, 1)
    total = float(np.sum(singular ** 2))
    explained = singular[:3] ** 2 / total if total else np.zeros(3)
    return np.rint(scaled.reshape(*features.shape[:2], 3) * 255).astype(np.uint8), {
        "mean": mean, "components": components, "display_low": low,
        "display_high": high, "explained_variance_ratio": explained,
    }


def cluster_features(features: np.ndarray, k: int = 4, seed: int = 0,
                     max_iterations: int = 50) -> tuple[np.ndarray, np.ndarray, dict]:
    """Lloyd clustering of normalized vectors, deterministic farthest initialization.

    This exploratory unsupervised partition has arbitrary IDs and no semantic
    assignment. It does not fit or update any DINO model parameters.
    """
    flat = unit_rows(features.reshape(-1, features.shape[-1]))
    if not 2 <= k <= min(16, len(flat)) or max_iterations < 1:
        raise ValueError("clusters must be 2..16 and no more than patch count")
    if not np.isfinite(flat).all():
        raise ValueError("features contain non-finite values")
    first = int(np.random.default_rng(seed).integers(len(flat)))
    selected = [first]
    closest = np.sum((flat - flat[first]) ** 2, axis=1)
    for _ in range(1, k):
        index = int(np.argmax(closest))
        selected.append(index)
        closest = np.minimum(closest, np.sum((flat - flat[index]) ** 2, axis=1))
    centers = flat[selected].copy()
    previous = None
    converged = False
    for iteration in range(1, max_iterations + 1):
        distances = (flat ** 2).sum(1)[:, None] + (centers ** 2).sum(1)[None] - 2 * flat @ centers.T
        labels = np.argmin(distances, axis=1)
        if previous is not None and np.array_equal(labels, previous):
            converged = True
            break
        for index in range(k):
            assigned = flat[labels == index]
            if len(assigned):
                centers[index] = assigned.mean(axis=0)
        previous = labels.copy()
    # Reassign after the last centre update so labels always match saved centres.
    distances = (flat ** 2).sum(1)[:, None] + (centers ** 2).sum(1)[None] - 2 * flat @ centers.T
    labels = np.argmin(distances, axis=1)
    return labels.reshape(features.shape[:2]).astype(np.uint8), centers, {
        "k": k, "seed": seed, "max_iterations": max_iterations,
        "iterations": iteration, "converged": converged,
        "initialization": "seeded first patch then deterministic farthest point",
        "input_normalization": "L2 normalize each patch; Euclidean Lloyd clustering",
        "cluster_patch_counts": [int((labels == index).sum()) for index in range(k)],
        "semantic_assignment": None,
    }


def patch_counts(mask: np.ndarray, padded_shape: tuple[int, int], patch_size: int) -> np.ndarray:
    """Count original-image true pixels per patch; padding contributes no labels."""
    height, width = padded_shape
    if height % patch_size or width % patch_size:
        raise ValueError("padded shape is not a multiple of patch size")
    if mask.shape[0] > height or mask.shape[1] > width:
        raise ValueError("mask exceeds padded image")
    padded = np.zeros((height, width), dtype=np.uint8)
    padded[:mask.shape[0], :mask.shape[1]] = mask
    return padded.reshape(height // patch_size, patch_size,
                          width // patch_size, patch_size).sum(axis=(1, 3)).astype(np.float64)


def draft_statistics(features: np.ndarray, masks: dict[str, np.ndarray],
                     image_shape: tuple[int, int], patch_size: int = 14) -> tuple[dict, dict[str, np.ndarray]]:
    """Area-weighted class centres after ignore precedence and conflict removal.

    An intersecting patch can contribute the same descriptor to two classes.
    This is explicitly reported and can make small-residue distances misleading.
    No all-classes claim is made if a class has no surviving draft pixels.
    """
    if features.ndim != 3 or not np.isfinite(features).all():
        raise ValueError("invalid patch features")
    padded_shape = tuple(int(n * patch_size) for n in features.shape[:2])
    if any(np.asarray(mask).shape != image_shape for mask in masks.values()):
        raise ValueError("draft masks must match original image dimensions")
    empty = np.zeros(image_shape, dtype=bool)
    ignore = np.asarray(masks.get("ignore", empty), dtype=bool)
    unions = {label: np.asarray(masks.get(label, empty), dtype=bool) for label in CLASSES}
    coverage = np.stack(list(unions.values())).sum(axis=0)
    conflicts = (coverage > 1) & ~ignore
    valid = {label: mask & ~ignore & ~conflicts for label, mask in unions.items()}
    flat = unit_rows(features.reshape(-1, features.shape[-1]))
    class_rows, centers, weights = {}, {}, {}
    for label in CLASSES:
        counts = patch_counts(valid[label], padded_shape, patch_size)
        weights[label] = counts
        weight = counts.ravel()
        total = float(weight.sum())
        if not total:
            class_rows[label] = {
                "status": "unavailable", "reason": "no draft pixels after ignore/conflict exclusion",
                "raw_draft_pixels": int(unions[label].sum()), "valid_draft_pixels": 0,
                "contributing_patches": 0, "center": None, "within_cosine_dispersion": None,
            }
            continue
        center = (flat * weight[:, None]).sum(axis=0) / total
        direction = unit_rows(center)
        cosine = np.clip(1 - flat @ direction, 0, 2)
        dispersion = float((cosine * weight).sum() / total)
        purity = weight[weight > 0] / (patch_size ** 2)
        centers[label] = direction
        class_rows[label] = {
            "status": "available", "raw_draft_pixels": int(unions[label].sum()),
            "valid_draft_pixels": int(total), "contributing_patches": int((weight > 0).sum()),
            "effective_patch_count": float(total ** 2 / np.sum(weight ** 2)),
            "patch_equivalent_area": total / (patch_size ** 2),
            "majority_class_patches": int((weight >= 0.5 * patch_size ** 2).sum()),
            "minimum_patch_class_fraction": float(purity.min()),
            "maximum_patch_class_fraction": float(purity.max()),
            "pixel_weighted_patch_class_fraction": float((weight ** 2).sum() / total / (patch_size ** 2)),
            "center": direction.tolist(), "within_cosine_dispersion": dispersion,
        }
    comparisons = []
    for left, right in combinations(CLASSES, 2):
        pair = {"classes": [left, right], "center_cosine_distance": None,
                "mean_within_cosine_dispersion": None, "between_to_within_ratio": None,
                "shared_contributing_patches": int(((weights[left] > 0) & (weights[right] > 0)).sum())}
        if left in centers and right in centers:
            distance = float(np.clip(1 - centers[left] @ centers[right], 0, 2))
            within = (class_rows[left]["within_cosine_dispersion"] +
                      class_rows[right]["within_cosine_dispersion"]) / 2
            pair.update(center_cosine_distance=distance, mean_within_cosine_dispersion=within,
                        between_to_within_ratio=distance / within if within > 1e-12 else None,
                        status="available", reason="relative descriptor geometry, not recognition accuracy")
        else:
            pair.update(status="unavailable", reason="one or both draft classes have no valid pixels")
        comparisons.append(pair)
    missing = [label for label in CLASSES if label not in centers]
    statistics = {
        "annotation_status": "draft", "human_verified": False, "test_ground_truth_allowed": False,
        "semantics": "Labels were assigned to SAM2 drafts; neither material identity nor class truth is verified.",
        "aggregation": "Unit-normalize descriptors; weight each patch by surviving class pixels; normalize weighted centre.",
        "within_definition": "Pixel-weighted mean (1 - cosine(patch, normalized class centre)).",
        "between_definition": "1 - cosine(normalized class centres); ratio divides by mean of both within dispersions.",
        "ignore_pixels": int(ignore.sum()), "conflicting_nonignore_pixels": int(conflicts.sum()),
        "unknown_pixels": int((~ignore & (coverage == 0)).sum()),
        "ignore_precedence": True, "unlabelled_is_substrate": False,
        "classes": class_rows, "comparisons": comparisons, "missing_classes": missing,
        "three_class_separability_status": "not_assessable" if missing else "exploratory_only",
        "limitations": [
            "Unlabelled pixels are unknown and never treated as substrate.",
            "A 14-pixel patch may mix narrow residue, structure, shadows and unknown pixels.",
            "DINO descriptors use global attention, so a patch descriptor is not purely local material response.",
            "Drafts from another model are not independent human ground truth; no accuracy or class classifier is produced.",
            "Per-image centres describe these two images only; no generalization or statistical significance is inferred.",
        ],
    }
    return statistics, weights
