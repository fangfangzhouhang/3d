"""邻域差异污染分割（成员 B）。

污渍不预先规定成某一种颜色。先估计每个像素周围的背景，再看亮度/颜色差得
够不够大；够大的连通块再丢掉又细又长的线（电路铜线一类）。

这仍是 OpenCV，不是深度学习。演示可以选用本算法；看见污渍不会发泵。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from microcleaning.vision.contamination import ContaminationMeasurement
from microcleaning.vision.hsv_baseline import SegmentationResult


LOCAL_CONTRAST_VERSION = "local-contrast-v0.1"
ACTIVE_LOCAL_CONTRAST_POLICY_PATH = Path("data") / "models" / "local_contrast_policy.json"
_INT_POLICY_FIELDS = frozenset(
    {"blur_kernel_px", "min_component_area_px", "morphology_kernel_px"}
)


@dataclass(frozen=True)
class LocalContrastPolicy:
    blur_kernel_px: int = 0
    lightness_weight: float = 0.55
    color_weight: float = 1.0
    min_residual: float = 8.0
    min_component_area_px: int = 20
    max_aspect_ratio: float = 6.0
    max_area_ratio: float = 0.20
    morphology_kernel_px: int = 3

    def validate(self) -> None:
        if self.blur_kernel_px < 0 or (
            self.blur_kernel_px > 0 and self.blur_kernel_px % 2 == 0
        ):
            raise ValueError("blur_kernel_px必须是0（按图自适应）或正奇数")
        if self.lightness_weight < 0 or self.color_weight < 0:
            raise ValueError("权重不能为负")
        if self.min_residual < 0:
            raise ValueError("min_residual不能为负")
        if self.min_component_area_px <= 0:
            raise ValueError("min_component_area_px必须大于0")
        if self.max_aspect_ratio < 1.0:
            raise ValueError("max_aspect_ratio必须≥1")
        if not 0.0 < self.max_area_ratio < 1.0:
            raise ValueError("max_area_ratio必须位于0～1之间")
        if self.morphology_kernel_px <= 0 or self.morphology_kernel_px % 2 == 0:
            raise ValueError("形态学核必须是正奇数")


def local_contrast_policy_from_mapping(payload: dict[str, Any]) -> LocalContrastPolicy:
    """从 JSON 字典恢复策略；只读取已知字段。"""

    if not isinstance(payload, dict):
        raise ValueError("策略JSON必须是对象")
    body = payload.get("policy")
    if isinstance(body, dict):
        payload = body
    known = {item.name for item in fields(LocalContrastPolicy)}
    kwargs: dict[str, Any] = {}
    for name, value in payload.items():
        if name not in known:
            continue
        if name in _INT_POLICY_FIELDS:
            kwargs[name] = int(value)
        else:
            kwargs[name] = float(value)
    policy = LocalContrastPolicy(**kwargs)
    policy.validate()
    return policy


def load_local_contrast_policy(path: str | Path) -> LocalContrastPolicy:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"策略文件不存在：{source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"策略JSON无法解析：{source}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"策略JSON必须是对象：{source}")
    return local_contrast_policy_from_mapping(payload)


def save_local_contrast_policy(
    policy: LocalContrastPolicy,
    path: str | Path,
    *,
    extra: dict[str, Any] | None = None,
) -> Path:
    policy.validate()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "algorithm": "local",
        "base_version": LOCAL_CONTRAST_VERSION,
        "feeds_action_request": False,
        "policy": asdict(policy),
        "note": "仅OpenCV邻域差异策略；不是语义分割训练，也不是清洗有效证据",
    }
    if extra:
        payload.update(extra)
        payload["policy"] = asdict(policy)
        payload["feeds_action_request"] = False
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def resolve_local_contrast_policy(
    *,
    policy_path: str | Path | None = None,
    use_tuned_policy: bool | None = None,
    active_path: str | Path | None = None,
) -> LocalContrastPolicy | None:
    """显式路径优先；True 必须读到生效文件；None 则文件存在才用；False 忽略。"""

    if policy_path is not None:
        return load_local_contrast_policy(policy_path)
    if use_tuned_policy is False:
        return None
    target = Path(active_path) if active_path is not None else ACTIVE_LOCAL_CONTRAST_POLICY_PATH
    if target.is_file():
        return load_local_contrast_policy(target)
    if use_tuned_policy is True:
        raise FileNotFoundError(f"没有已调参策略文件：{target}。请先运行 train_entry --apply")
    return None


def segment_contamination(
    image: Any,
    *,
    policy: LocalContrastPolicy = LocalContrastPolicy(),
) -> SegmentationResult:
    """把相对周围差得足够多的块标成污染。"""

    cv2, np = _load_dependencies()
    policy.validate()
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.size == 0:
        raise ValueError("image必须是非空uint8 NumPy图像")
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("邻域差异基线要求BGR三通道图像")

    height, width = image.shape[:2]
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    kernel = _neighborhood_ksize(height, width, policy.blur_kernel_px)
    background = cv2.GaussianBlur(lab, (kernel, kernel), 0)
    delta = lab - background
    residual = np.sqrt(
        (policy.lightness_weight * delta[:, :, 0]) ** 2
        + (policy.color_weight * delta[:, :, 1]) ** 2
        + (policy.color_weight * delta[:, :, 2]) ** 2
    )
    peak = float(residual.max())
    if peak < policy.min_residual:
        mask = np.zeros((height, width), dtype=np.uint8)
        kept_components = 0
        residual_u8 = np.zeros((height, width), dtype=np.uint8)
    else:
        residual_u8 = np.clip(residual * (255.0 / max(peak, 1.0)), 0, 255).astype(np.uint8)
        _, raw_mask = cv2.threshold(residual_u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        morph = np.ones((policy.morphology_kernel_px, policy.morphology_kernel_px), dtype=np.uint8)
        cleaned = cv2.morphologyEx(raw_mask, cv2.MORPH_OPEN, morph)
        cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, morph)
        mask, kept_components = _keep_components(
            cleaned,
            min_area_px=max(policy.min_component_area_px, (height * width) // 8000),
            max_area_px=int(height * width * policy.max_area_ratio),
            max_aspect_ratio=policy.max_aspect_ratio,
            cv2=cv2,
        )

    area_px = float(cv2.countNonZero(mask))
    if area_px > 0:
        moments = cv2.moments(mask, binaryImage=True)
        centroid = (float(moments["m10"] / moments["m00"]), float(moments["m01"] / moments["m00"]))
        score = residual_u8[mask > 0]
        confidence = float(np.clip(float(score.mean()) / 255.0, 0.0, 1.0))
        uncertainty_px = max(1.0, policy.morphology_kernel_px / 2.0)
    else:
        centroid = None
        confidence = 0.0
        uncertainty_px = 0.0
    measurement = ContaminationMeasurement(
        area_px=area_px,
        centroid_px=centroid,
        uncertainty_px=uncertainty_px,
        confidence=confidence,
        component_count=kept_components,
        algorithm_version=(
            LOCAL_CONTRAST_VERSION
            if policy == LocalContrastPolicy()
            else f"{LOCAL_CONTRAST_VERSION}+auto"
        ),
    )
    measurement.validate()
    return SegmentationResult(measurement=measurement, mask=mask)


def _neighborhood_ksize(height: int, width: int, configured: int) -> int:
    if configured > 0:
        return configured
    size = max(15, (min(height, width) // 8) | 1)
    if size % 2 == 0:
        size += 1
    return int(size)


def _keep_components(cleaned, *, min_area_px: int, max_area_px: int, max_aspect_ratio: float, cv2):
    import numpy as np

    count, labels, stats, _ = cv2.connectedComponentsWithStats(cleaned, connectivity=8)
    mask = np.zeros(cleaned.shape, dtype=np.uint8)
    kept = 0
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area_px or area > max_area_px:
            continue
        box_w = max(int(stats[label, cv2.CC_STAT_WIDTH]), 1)
        box_h = max(int(stats[label, cv2.CC_STAT_HEIGHT]), 1)
        aspect = max(box_w, box_h) / min(box_w, box_h)
        if aspect > max_aspect_ratio:
            continue
        mask[labels == label] = 255
        kept += 1
    return mask, kept


def _load_dependencies() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "邻域差异分割需要感知依赖；请在项目.venv安装requirements/perception-opencv.txt"
        ) from exc
    return cv2, np
