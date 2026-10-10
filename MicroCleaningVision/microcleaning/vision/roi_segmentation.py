"""冻结每个原始污渍的分割阈值和上下文，供面积复检使用。

任务级参数冻结不等于阈值冻结：Otsu（根据当帧分布自动选阈值）会在其他
污渍改变后改变结果。本模块只从首次图确定阈值、归一化峰值、核尺寸和分支。
后续只替换原 ROI 的像素，ROI 外仍使用首次上下文，不重新估全图阈值或编号。
这一明确的新测量策略仍需要真实显微图验证，不宣称通用分割准确率。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any


ROI_SEGMENTATION_VERSION = "frozen-roi-segmentation-v1"


class FrozenRoiSegmenter:
    """接收 FrozenSegmenter 的 algorithm/policy；不导入 Demo 或访问设备。

``roi`` 是原图 (x,y,width,height)，``target_mask`` 是首次该目标的原尺寸
掩膜。初始和后续图片都调用 mask()，保证比较的是同一个冻结测量策略。
"""

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_locked", False):
            raise AttributeError("ROI分割参数已冻结；新参数必须建立新的参考与证据")
        object.__setattr__(self, name, value)

    def __init__(self, segmenter: Any, reference_image: Any,
                 roi: tuple[int, int, int, int], target_mask: Any, *, excluded_mask: Any = None) -> None:
        cv2, np = _deps()
        image = _image(reference_image)
        algorithm = getattr(segmenter, "algorithm", None)
        if algorithm not in {"local", "hsv", "otsu", "exg", "exr"}:
            raise ValueError("ROI_SEGMENTER_UNSUPPORTED_ALGORITHM")
        policy = getattr(segmenter, "policy", None)
        if not is_dataclass(policy) or not callable(getattr(policy, "validate", None)):
            raise ValueError("ROI_SEGMENTER_POLICY_MISSING")
        policy.validate()
        if not isinstance(target_mask, np.ndarray) or target_mask.ndim != 2 or target_mask.dtype not in (np.dtype("uint8"), np.dtype("bool")) or target_mask.shape != image.shape[:2]:
            raise ValueError("ROI_SEGMENTER_TARGET_MASK_MISMATCH")
        if not isinstance(roi, (tuple, list)) or len(roi) != 4 or any(isinstance(v, bool) or not isinstance(v, int) for v in roi):
            raise ValueError("ROI_SEGMENTER_ROI_INVALID")
        x, y, width, height = roi
        frame_h, frame_w = image.shape[:2]
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > frame_w or y + height > frame_h:
            raise ValueError("ROI_SEGMENTER_ROI_OUTSIDE_IMAGE")
        self.algorithm = algorithm
        self.policy = type(policy)(**asdict(policy))
        self.roi = (x, y, width, height)
        self.shape = tuple(image.shape)
        self._reference = image.copy()
        self._reference.setflags(write=False)
        self._target_mask = (target_mask > 0).copy()
        self._target_mask.setflags(write=False)
        if excluded_mask is None:
            excluded = np.zeros(image.shape[:2], dtype=bool)
        elif not isinstance(excluded_mask, np.ndarray) or excluded_mask.ndim != 2 or excluded_mask.shape != image.shape[:2] or excluded_mask.dtype not in (np.dtype("uint8"), np.dtype("bool")):
            raise ValueError("ROI_SEGMENTER_EXCLUDED_MASK_MISMATCH")
        else:
            excluded = excluded_mask > 0
        if np.any(excluded & self._target_mask):
            raise ValueError("ROI_SEGMENTER_EXCLUSION_COVERS_TARGET")
        self._excluded_mask = excluded.copy()
        self._excluded_mask.setflags(write=False)
        update_domain = np.zeros(image.shape[:2], dtype=bool)
        update_domain[y:y + height, x:x + width] = True
        update_domain &= ~excluded
        self._update_domain = update_domain
        self._update_domain.setflags(write=False)
        self.reference_sha256 = _pixel_hash(image)
        self.target_mask_sha256 = _pixel_hash(self._target_mask)
        self.threshold: float | None = None
        self.normalization_peak: float | None = None
        self.blur_kernel_px: int | None = None
        self.branch = "fixed"
        self._adaptive_threshold_map = None
        self._configuration: dict[str, Any] = {}
        self._prepare()
        initial_mask = self.mask(self._reference)
        self.initial_area_px = int(np.count_nonzero(initial_mask))
        self.initial_target_overlap_px = int(np.count_nonzero((initial_mask > 0) & self._target_mask))
        self._configuration = self._metadata()
        self.sha256 = hashlib.sha256(json.dumps(self._configuration, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()
        self._locked = True

    def _prepare(self) -> None:
        cv2, np = _deps()
        height, width = self.shape[:2]
        if self.algorithm == "local":
            configured = self.policy.blur_kernel_px
            self.blur_kernel_px = configured if configured else max(15, (min(height, width) // 8) | 1)
            residual = self._local_residual(self._reference)
            self.normalization_peak = float(residual.max())
            if self.normalization_peak < self.policy.min_residual:
                self.branch = "no_initial_signal"
                self.threshold = None
            else:
                score = self._normalize_local(residual)
                self.threshold = float(cv2.threshold(score, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0])
        elif self.algorithm == "otsu":
            self.blur_kernel_px = self.policy.blur_kernel_px
            value = cv2.cvtColor(self._reference, cv2.COLOR_BGR2HSV)[:, :, 2]
            blurred = cv2.GaussianBlur(value, (self.blur_kernel_px, self.blur_kernel_px), 0)
            self.threshold = float(cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[0])
            raw = cv2.threshold(blurred, self.threshold, 255, cv2.THRESH_BINARY_INV)[1]
            cleaned = self._clean(raw)
            filtered = self._filter(cleaned)
            if not np.any((filtered > 0) & self._target_mask):
                # 初始目标只由 fallback 检出时，冻结其背景阈值图，不在每帧切换分支。
                self.branch = "frozen_initial_adaptive_background"
                mean = cv2.GaussianBlur(value, (self.policy.adaptive_block_size, self.policy.adaptive_block_size), 0,
                                        borderType=cv2.BORDER_REPLICATE)
                self._adaptive_threshold_map = mean.astype(np.int16) - self.policy.adaptive_c
                self._adaptive_threshold_map.setflags(write=False)
            else:
                self.branch = "frozen_initial_otsu"
        elif self.algorithm in {"exg", "exr"}:
            score = self._color_index(self._reference)
            if int(score.max()) == 0:
                self.branch = "no_initial_signal"
            self.threshold = float(cv2.threshold(score, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0])

    def mask(self, image: Any) -> Any:
        """返回原图尺寸 uint8，只有原 ROI 可有前景；从不动态调用原分割器。"""
        cv2, np = _deps()
        image = _image(image)
        if tuple(image.shape) != self.shape:
            raise ValueError("ROI_SEGMENTER_IMAGE_SIZE_MISMATCH")
        x, y, width, height = self.roi
        canvas = self._reference.copy()
        canvas[self._update_domain] = image[self._update_domain]
        if self.branch == "no_initial_signal":
            raw = np.zeros(self.shape[:2], np.uint8)
        elif self.algorithm == "local":
            score = self._normalize_local(self._local_residual(canvas))
            raw = cv2.threshold(score, self.threshold, 255, cv2.THRESH_BINARY)[1]
        elif self.algorithm == "hsv":
            hsv = cv2.cvtColor(canvas, cv2.COLOR_BGR2HSV)
            p = self.policy
            first = cv2.inRange(hsv, np.uint8((p.hue_low_1, p.saturation_min, p.value_min)), np.uint8((p.hue_high_1, 255, 255)))
            second = cv2.inRange(hsv, np.uint8((p.hue_low_2, p.saturation_min, p.value_min)), np.uint8((p.hue_high_2, 255, 255)))
            raw = cv2.bitwise_or(first, second)
        elif self.algorithm == "otsu":
            value = cv2.cvtColor(canvas, cv2.COLOR_BGR2HSV)[:, :, 2]
            if self._adaptive_threshold_map is not None:
                raw = np.where(value.astype(np.int16) <= self._adaptive_threshold_map, np.uint8(255), np.uint8(0))
            else:
                blurred = cv2.GaussianBlur(value, (self.blur_kernel_px, self.blur_kernel_px), 0)
                raw = cv2.threshold(blurred, self.threshold, 255, cv2.THRESH_BINARY_INV)[1]
        else:
            score = self._color_index(canvas)
            raw = cv2.threshold(score, self.threshold, 255, cv2.THRESH_BINARY)[1]
        # 所有形态学/面积过滤均保持首次全帧尺度，避免裁图后最大面积阈值改变。
        filtered = self._filter(self._clean(raw))
        result = np.zeros(self.shape[:2], np.uint8)
        result[y:y + height, x:x + width] = filtered[y:y + height, x:x + width]
        return result

    def _local_residual(self, canvas: Any) -> Any:
        cv2, np = _deps()
        lab = cv2.cvtColor(canvas, cv2.COLOR_BGR2LAB).astype(np.float32)
        background = cv2.GaussianBlur(lab, (self.blur_kernel_px, self.blur_kernel_px), 0)
        delta = lab - background
        return np.sqrt((self.policy.lightness_weight * delta[:, :, 0]) ** 2
                       + (self.policy.color_weight * delta[:, :, 1]) ** 2
                       + (self.policy.color_weight * delta[:, :, 2]) ** 2)

    def _normalize_local(self, residual: Any) -> Any:
        _, np = _deps()
        return np.clip(residual * (255.0 / max(float(self.normalization_peak or 0), 1.0)), 0, 255).astype(np.uint8)

    def _color_index(self, image: Any) -> Any:
        _, np = _deps()
        blue, green, red = (image[:, :, channel].astype(np.float32) for channel in range(3))
        raw = 2 * green - red - blue if self.algorithm == "exg" else 1.4 * red - green - blue
        return np.clip(raw, 0, 255).astype(np.uint8)

    def _clean(self, raw: Any) -> Any:
        cv2, np = _deps()
        size = self.policy.morphology_kernel_px
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)) if self.algorithm == "otsu" else np.ones((size, size), np.uint8)
        opened = cv2.morphologyEx(raw, cv2.MORPH_OPEN, kernel)
        return cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel)

    def _filter(self, raw: Any) -> Any:
        cv2, np = _deps()
        count, labels, stats, _ = cv2.connectedComponentsWithStats(raw, connectivity=8)
        result = np.zeros(raw.shape, np.uint8)
        height, width = self.shape[:2]
        min_area = self.policy.min_component_area_px
        max_area = None
        aspect = getattr(self.policy, "max_aspect_ratio", None)
        edge_margin = 0
        if self.algorithm == "local":
            min_area = max(min_area, height * width // 8000)
            max_area = int(height * width * self.policy.max_area_ratio)
        elif self.algorithm == "otsu":
            max_area = height * width * self.policy.max_area_ratio
            edge_margin = self.policy.edge_margin_px
            if self.branch == "frozen_initial_adaptive_background":
                min_area = self.policy.fallback_min_component_area_px
        for label in range(1, int(count)):
            left, top, box_w, box_h, area = (int(v) for v in stats[label])
            if area < min_area or max_area is not None and area > max_area:
                continue
            if aspect is not None and max(box_w / max(1, box_h), box_h / max(1, box_w)) > aspect:
                continue
            if edge_margin and (left < edge_margin or top < edge_margin or left + box_w > width - edge_margin or top + box_h > height - edge_margin):
                continue
            result[labels == label] = 255
        return result

    def _metadata(self) -> dict[str, Any]:
        data = {
            "algorithm_version": ROI_SEGMENTATION_VERSION,
            "algorithm": self.algorithm,
            "policy_type": type(self.policy).__name__,
            "policy": asdict(self.policy),
            "roi": self.roi,
            "processing_shape": self.shape,
            "branch": self.branch,
            "threshold": self.threshold,
            "normalization_peak": self.normalization_peak,
            "blur_kernel_px": self.blur_kernel_px,
            "reference_sha256": self.reference_sha256,
            "target_mask_sha256": self.target_mask_sha256,
            "excluded_context_sha256": _pixel_hash(self._excluded_mask),
            "initial_area_px": self.initial_area_px,
            "initial_target_overlap_px": self.initial_target_overlap_px,
            "context_rule": "固定全帧尺寸和首次外围/已知邻居上下文，仅原ROI非邻居像素替换当前图；后续不重新求Otsu或峰值。邻居实际变化必须另做二次确认。",
            "quality_boundary": "阈值和形态学策略为分割面积估计；真实科学标准和误差仍需标注验证。",
            "pixel_hash_semantics": "SHA256(str(shape)+原像素)，不是PNG文件字节哈希。",
        }
        if self._adaptive_threshold_map is not None:
            data["adaptive_threshold_map_sha256"] = _pixel_hash(self._adaptive_threshold_map)
            data["adaptive_threshold_map_min"] = int(self._adaptive_threshold_map.min())
            data["adaptive_threshold_map_max"] = int(self._adaptive_threshold_map.max())
        return data

    def to_dict(self) -> dict[str, Any]:
        """返回初始冻结参数与哈希；复制返回值不允许更改运行中的测量参数。"""
        data = json.loads(json.dumps(self._configuration, ensure_ascii=False, allow_nan=False))
        data["sha256"] = self.sha256
        return data


def _pixel_hash(array: Any) -> str:
    return hashlib.sha256(str(array.shape).encode("ascii") + array.tobytes()).hexdigest()


def _image(image: Any) -> Any:
    _, np = _deps()
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.size == 0 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("冻结ROI分割只接受非空uint8 BGR图像")
    return image


def _deps() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("冻结ROI分割需要 requirements/perception-opencv.txt") from exc
    return cv2, np
