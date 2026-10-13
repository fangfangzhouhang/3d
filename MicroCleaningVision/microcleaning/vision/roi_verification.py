"""固定原像素区域复检；只测量图像，不访问相机或执行运动。

初始 ROI（region of interest，要持续复查的原图区域）始终保留原坐标。
背景光流用于检查是否回到同一视野，绝不移动后图来伪造“已经复位”。
这里的亚像素阈值是明确的工程拒绝规则，尚不是物理零误差保证或科学验收标准。
无前景显示 0%，但 valid=False：未检出不能被解释为清洗成功。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from microcleaning.data_learning.image_quality import measure_image_quality


ROI_ALGORITHM_VERSION = "fixed-roi-verification-v1"


@dataclass(frozen=True)
class RoiVerificationPolicy:
    """暂定图像可比性门槛；真实相机验收时需冻结并验证这套参数。"""

    roi_margin_px: int = 8
    alignment_tolerance_px: float = 0.25
    min_background_features: int = 12
    min_feature_inlier_fraction: float = 0.80
    min_feature_support_fraction: float = 0.10
    max_background_mean_difference: float = 12.0
    max_background_difference_p95: float = 30.0
    min_background_std: float = 3.0
    min_background_laplacian_variance: float = 20.0
    max_focus_change_factor: float = 2.0
    min_roi_overlap_fraction: float = 0.05
    zoom_factor: int = 4

    def __post_init__(self) -> None:
        for name in ("roi_margin_px", "min_background_features", "zoom_factor"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < (0 if name == "roi_margin_px" else 1):
                raise ValueError(f"{name} 必须是有效整数")
        for name in (
            "alignment_tolerance_px", "max_background_mean_difference",
            "max_background_difference_p95", "min_background_std",
            "min_background_laplacian_variance", "max_focus_change_factor",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} 必须是有限正数")
        if self.max_focus_change_factor < 1:
            raise ValueError("max_focus_change_factor 必须至少为 1")
        for name in ("min_feature_inlier_fraction", "min_feature_support_fraction", "min_roi_overlap_fraction"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"{name} 必须位于 (0,1]")


@dataclass(frozen=True, eq=False)
class RoiReference:
    target_id: str
    frame_id: str | None
    roi: tuple[int, int, int, int]
    initial_area_px: int
    image_sha256: str
    image: Any = field(repr=False)
    target_mask: Any = field(repr=False)
    all_target_mask: Any = field(repr=False)
    measurement_domain: Any = field(repr=False)
    policy: RoiVerificationPolicy = RoiVerificationPolicy()


@dataclass(frozen=True)
class AlignmentEvidence:
    valid: bool
    shift_px: tuple[float, float] | None
    confidence: float
    tracked_features: int
    inlier_fraction: float
    feature_support_fraction: float
    displacement_p95_px: float | None
    background_mean_difference: float | None
    background_difference_p95: float | None
    focus_ratio: float | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, eq=False)
class RoiVerification:
    target_id: str
    roi: tuple[int, int, int, int]
    valid: bool
    reason_codes: tuple[str, ...]
    messages_zh: tuple[str, ...]
    initial_area_px: int
    pre_area_px: int
    post_area_px: int
    removal_rate: float | None
    cumulative_removal_rate: float | None
    iou: float | None
    dice: float | None
    intersection_px: int
    union_px: int
    pre_frame_id: str | None
    post_frame_id: str | None
    pre_sha256: str
    post_sha256: str
    reference_sha256: str
    reference_mask_sha256: str
    pre_mask_sha256: str
    post_mask_sha256: str
    secondary_confirmation_used: bool
    secondary_mask_sha256: str | None
    alignment: AlignmentEvidence
    reference_alignment: AlignmentEvidence
    policy: RoiVerificationPolicy
    pre_crop: Any = field(repr=False)
    post_crop: Any = field(repr=False)
    overlay: Any = field(repr=False)
    contact_sheet: Any = field(repr=False)

    @property
    def alignment_shift_px(self) -> tuple[float, float] | None:
        return self.alignment.shift_px

    @property
    def alignment_confidence(self) -> float:
        return self.alignment.confidence

    def to_dict(self) -> dict[str, Any]:
        """只有有限数值和元数据；不把数组嵌入 JSON。"""
        omitted = {"pre_crop", "post_crop", "overlay", "contact_sheet"}
        data = {name: value for name, value in self.__dict__.items() if name not in omitted}
        data.update(
            algorithm_version=ROI_ALGORITHM_VERSION,
            alignment=asdict(self.alignment),
            reference_alignment=asdict(self.reference_alignment),
            policy=asdict(self.policy),
            alignment_shift_px=self.alignment_shift_px,
            alignment_confidence=self.alignment_confidence,
            pixel_sha256_semantics="图像哈希=SHA256(str(shape)+uint8原像素)；掩膜哈希同式且归一化bool；不是PNG文件字节哈希。",
            rate_semantics="每轮：(清洗前面积-清洗后面积)/清洗前面积；负值表示面积增加。未检出显示0但无效。",
            cumulative_rate_semantics="累计：(首次面积-本次后面积)/首次面积；无效时不采用。",
            overlap_semantics="IoU=交集/并集；Dice=2×交集/(前面积+后面积)，不能单独代表清洗率。",
            overlay_legend_zh={"green": "前景已减少", "yellow": "前后重合残留", "red": "后图新增前景"},
            evidence_boundary="仅背景图像在可观察容差内一致；不证明物理零误差、无损伤或真实洗净。",
        )
        return data


_MESSAGES = {
    "ROI_REFERENCE_EMPTY": "首次区域没有有效污渍，显示0%，无法建立清洗依据。",
    "ROI_PRE_EMPTY": "清洗前在原区域未检出污渍，显示0%，不能计算有效清洗率。",
    "ROI_POST_EMPTY": "清洗后在原区域未检出污渍，按要求显示0%；需人工确认，不能据此认定洗净。",
    "ROI_IMAGES_SIZE_MISMATCH": "前后图尺寸不一致，不能比较同一像素区域。",
    "ROI_MASK_SIZE_MISMATCH": "掩膜尺寸与原图不一致，停止采用数值。",
    "ROI_INPUT_QUALITY_FLAGGED": "相机质量记录存在失焦、光照或置信度问题。",
    "ROI_PIXEL_QUALITY_LOW": "真实像素的成像质量未通过当前复检门槛。",
    "ROI_DUPLICATE_FRAME_ID": "前后帧编号相同，未获得新的复检帧。",
    "ROI_DUPLICATE_PIXELS": "前后像素完全相同，无法排除旧帧或冻结帧。",
    "ROI_BACKGROUND_UNOBSERVABLE": "背景缺少足够纹理，无法证明已经回到原观察位。",
    "ROI_BACKGROUND_FEATURES_LOW": "可追踪的背景特征不足，位置校验不可信。",
    "ROI_ALIGNMENT_LOW_CONFIDENCE": "背景追踪不一致或覆盖不足，位置校验不可信。",
    "ROI_POSITION_SHIFTED": "发现背景位移超过容差，不移动后图掩盖偏移；请先复位再重拍。",
    "ROI_ILLUMINATION_CHANGED": "稳定背景亮度变化过大，可能导致虚假的面积变化。",
    "ROI_BACKGROUND_CHANGED": "稳定背景像素变化过大，可能存在形变、反光、液滴或位置偏差。",
    "ROI_LOCAL_ILLUMINATION_CHANGED": "污渍周围的原区域亮度变化过大，不能把局部阴影或反光误当清洗效果。",
    "ROI_FOCUS_CHANGED": "背景清晰度变化过大，前后分割结果不能直接比较。",
    "ROI_REFERENCE_POSITION_UNCERTAIN": "本轮清洗前图也未通过首次观察位校验。",
    "ROI_FOREGROUND_IDENTITY_UNCERTAIN": "后图前景与原污渍区域几乎不重合，不能把其他污渍当成本目标。",
    "ROI_NEIGHBOR_INTRUSION": "相邻污渍进入本目标区域，无法独立测量当前污渍。",
    "ROI_FOREGROUND_AT_BOUNDARY": "污渍前景碰到固定区域边缘，面积可能被裁掉，不能采用清洗率。",
    "ROI_NEIGHBOR_SECONDARY_CONFIRMATION_REQUIRED": "附近已知污渍的真实像素发生变化，需二次确认未侵入本区域，当前清洗率不采用。",
}


def build_roi_reference(
    image: Any, target_mask: Any, *, all_target_mask: Any = None,
    target_id: str = "", frame_id: str | None = None,
    policy: RoiVerificationPolicy = RoiVerificationPolicy(),
) -> RoiReference:
    """锁第一次图的区域和周围目标；数组复制、只读，调用者后续修改不影响它。"""
    _, np = _deps()
    image = _image(image, "image")
    target = _mask(target_mask, "target_mask")
    all_targets = target if all_target_mask is None else _mask(all_target_mask, "all_target_mask")
    if image.shape[:2] != target.shape or target.shape != all_targets.shape:
        raise ValueError("ROI_MASK_SIZE_MISMATCH")
    if np.any(target & ~all_targets):
        raise ValueError("all_target_mask 必须包含 target_mask")
    height, width = target.shape
    ys, xs = np.nonzero(target)
    if len(xs):
        margin = policy.roi_margin_px
        x0, y0 = max(0, int(xs.min()) - margin), max(0, int(ys.min()) - margin)
        x1, y1 = min(width, int(xs.max()) + 1 + margin), min(height, int(ys.max()) + 1 + margin)
    else:
        x0, y0, x1, y1 = 0, 0, width, height
    domain = np.zeros(target.shape, dtype=bool)
    domain[y0:y1, x0:x1] = True
    domain &= ~(all_targets & ~target)
    return RoiReference(
        target_id, frame_id, (x0, y0, x1 - x0, y1 - y0),
        int(np.count_nonzero(target & domain)), _hash(image),
        _readonly(image), _readonly(target), _readonly(all_targets), _readonly(domain), policy,
    )


def verify_roi_pair(
    *, reference: RoiReference, pre_image: Any, post_image: Any,
    pre_mask: Any, post_mask: Any, pre_frame_id: str | None = None,
    post_frame_id: str | None = None, pre_quality_flags: tuple[str, ...] = (),
    post_quality_flags: tuple[str, ...] = (), policy: RoiVerificationPolicy | None = None,
    secondary_post_mask: Any = None,
) -> RoiVerification:
    """测固定 ROI；可比性失效时不计算有效面积去除率，不依赖当帧目标编号。

调用者还必须核对来源、采集设置、分割策略、回程回执、时间与动作身份。
本函数不负责这些设备证据，不提供自动“洗净”或发令路由。
"""
    cv2, np = _deps()
    policy = reference.policy if policy is None else policy
    pre = _image(pre_image, "pre_image")
    post = _image(post_image, "post_image")
    before = _mask(pre_mask, "pre_mask")
    after = _mask(post_mask, "post_mask")
    secondary = None if secondary_post_mask is None else _mask(secondary_post_mask, "secondary_post_mask")
    reasons: list[str] = []
    matching_size = pre.shape == post.shape == reference.image.shape
    matching_masks = before.shape == after.shape == reference.target_mask.shape == pre.shape[:2]
    if not matching_size:
        reasons.append("ROI_IMAGES_SIZE_MISMATCH")
    if not matching_masks:
        reasons.append("ROI_MASK_SIZE_MISMATCH")
    if pre_quality_flags or post_quality_flags:
        reasons.append("ROI_INPUT_QUALITY_FLAGGED")
    for image in (pre, post):
        _, quality = measure_image_quality(image)
        if quality.flags():
            reasons.append("ROI_PIXEL_QUALITY_LOW")
    pre_hash, post_hash = _hash(pre), _hash(post)
    if pre_frame_id is not None and pre_frame_id == post_frame_id:
        reasons.append("ROI_DUPLICATE_FRAME_ID")
    if pre_hash == post_hash:
        reasons.append("ROI_DUPLICATE_PIXELS")
    alignment = _missing_alignment()
    reference_alignment = _missing_alignment()
    pre_domain = np.zeros(reference.target_mask.shape, dtype=bool)
    post_domain = pre_domain.copy()
    if matching_size and matching_masks:
        pre_domain = before & reference.measurement_domain
        post_domain = after & reference.measurement_domain
        exclude = reference.all_target_mask | before | after
        x, y, w, h = reference.roi
        exclude = exclude.copy()
        exclude[y:y + h, x:x + w] = True
        alignment = _alignment(pre, post, exclude, policy)
        reference_alignment = _alignment(reference.image, pre, exclude, policy)
        reasons.extend(alignment.reason_codes)
        if not reference_alignment.valid:
            reasons.extend(reference_alignment.reason_codes)
            reasons.append("ROI_REFERENCE_POSITION_UNCERTAIN")
        if _neighbor_intrudes(reference, before) or _neighbor_intrudes(reference, after):
            reasons.append("ROI_NEIGHBOR_INTRUSION")
        if requires_neighbor_confirmation(reference, pre, post):
            if secondary is None:
                reasons.append("ROI_NEIGHBOR_SECONDARY_CONFIRMATION_REQUIRED")
            else:
                if secondary.shape != reference.target_mask.shape:
                    reasons.append("ROI_MASK_SIZE_MISMATCH")
                elif _neighbor_intrudes(reference, secondary):
                    reasons.append("ROI_NEIGHBOR_INTRUSION")
        roi_foreground = (pre_domain | post_domain)[y:y + h, x:x + w]
        if any(np.any(edge) for edge in (roi_foreground[0], roi_foreground[-1], roi_foreground[:, 0], roi_foreground[:, -1])):
            reasons.append("ROI_FOREGROUND_AT_BOUNDARY")
        # 全幅背景可稳定位姿，但局部灯光/液滴也可能只覆盖污渍区域。
        # 在原 ROI 内利用前后均非前景的背景环作独立光照检查。
        foreground_margin = cv2.dilate(
            (reference.all_target_mask | before | after).astype(np.uint8),
            np.ones((5, 5), np.uint8),
        ) > 0
        local_background = reference.measurement_domain & ~foreground_margin
        if np.count_nonzero(local_background) >= 32:
            local_pre, local_post = _gray(pre)[local_background], _gray(post)[local_background]
            local_mean_delta = abs(float(local_pre.mean()) - float(local_post.mean()))
            local_difference_p95 = float(np.percentile(np.abs(local_pre.astype(np.float32) - local_post.astype(np.float32)), 95))
            if local_mean_delta > policy.max_background_mean_difference or local_difference_p95 > policy.max_background_difference_p95:
                reasons.append("ROI_LOCAL_ILLUMINATION_CHANGED")
    pre_area, post_area = int(np.count_nonzero(pre_domain)), int(np.count_nonzero(post_domain))
    if reference.initial_area_px == 0:
        reasons.append("ROI_REFERENCE_EMPTY")
    if matching_size and matching_masks and pre_area == 0:
        reasons.append("ROI_PRE_EMPTY")
    if matching_size and matching_masks and post_area == 0:
        reasons.append("ROI_POST_EMPTY")
    intersection = int(np.count_nonzero(pre_domain & post_domain))
    union = int(np.count_nonzero(pre_domain | post_domain))
    if pre_area and post_area and intersection / min(pre_area, post_area) < policy.min_roi_overlap_fraction:
        reasons.append("ROI_FOREGROUND_IDENTITY_UNCERTAIN")
    reasons = list(dict.fromkeys(reasons))
    valid = not reasons
    # 空 ROI 的 0 是用户规定的显示语义，不是有效的清洗效果估计。
    no_foreground_observed = matching_size and matching_masks and (pre_area == 0 or post_area == 0)
    rate = (pre_area - post_area) / pre_area if valid else (0.0 if no_foreground_observed else None)
    cumulative = (reference.initial_area_px - post_area) / reference.initial_area_px if valid else None
    iou = intersection / union if valid and union else None
    dice = 2 * intersection / (pre_area + post_area) if valid and pre_area + post_area else None
    pre_crop = _crop_for_display(pre, reference.roi, policy.zoom_factor)
    post_crop = _crop_for_display(post, reference.roi, policy.zoom_factor)
    # 原坐标域可视化；并列图像保持相同比例，invalid 也保留供人排查。
    overlay_raw = _bgr(reference.image).copy()
    overlay_raw = (overlay_raw.astype(np.float32) * 0.45).astype(np.uint8)
    overlay_raw[pre_domain & ~post_domain] = (40, 210, 70)
    overlay_raw[pre_domain & post_domain] = (30, 210, 245)
    overlay_raw[~pre_domain & post_domain] = (60, 60, 235)
    overlay = _crop_for_display(overlay_raw, reference.roi, policy.zoom_factor)
    contact = _contact_sheet(pre_crop, post_crop, overlay)
    return RoiVerification(
        reference.target_id, reference.roi, valid, tuple(reasons),
        tuple(_MESSAGES[reason] for reason in reasons), reference.initial_area_px,
        pre_area, post_area, rate, cumulative, iou, dice, intersection, union,
        pre_frame_id, post_frame_id, pre_hash, post_hash, reference.image_sha256,
        _hash(reference.target_mask), _hash(before), _hash(after), secondary is not None,
        None if secondary is None else _hash(secondary), alignment,
        reference_alignment, policy, pre_crop, post_crop, overlay, contact,
    )


def validate_roi_location(
    reference: RoiReference, image: Any, mask: Any = None,
    *, policy: RoiVerificationPolicy | None = None,
) -> AlignmentEvidence:
    """动作前检查是否仍在首次观察位；初始同帧可通过，不套用后图新帧规则。

mask 只是排除可能变化的前景，缺省仅排除首次目标；不能通过更改mask获得运动授权。
返回的是可观察背景的一致性证据，调用者还需核对位置账本与全部动作关卡。
"""
    _, np = _deps()
    image = _image(image, "image")
    policy = reference.policy if policy is None else policy
    if image.shape != reference.image.shape:
        return _missing_alignment("ROI_IMAGES_SIZE_MISMATCH")
    excluded = reference.all_target_mask.copy()
    if mask is not None:
        foreground = _mask(mask, "mask")
        if foreground.shape != excluded.shape:
            return _missing_alignment("ROI_MASK_SIZE_MISMATCH")
        excluded |= foreground
    x, y, width, height = reference.roi
    excluded[y:y + height, x:x + width] = True
    return _alignment(reference.image, image, excluded, policy)


def requires_neighbor_confirmation(reference: RoiReference, pre_image: Any, post_image: Any) -> bool:
    """比较原区域附近已知邻居的真实像素；不使用冻结上下文伪装其未变化。

可据此按需调用二次区域/全图分割，仅检查侵入，不能替换主面积或目标编号。
"""
    cv2, np = _deps()
    pre, post = _image(pre_image, "pre_image"), _image(post_image, "post_image")
    if pre.shape != post.shape or pre.shape != reference.image.shape:
        return True
    neighbors = (reference.all_target_mask > 0) & ~(reference.target_mask > 0)
    region = np.zeros(reference.target_mask.shape, np.uint8)
    x, y, w, h = reference.roi
    region[y:y+h, x:x+w] = 1
    region = cv2.dilate(region, np.ones((25, 25), np.uint8)) > 0
    nearby = neighbors & region
    if not np.any(nearby):
        return False
    before, after = _bgr(pre)[nearby].astype(np.int16), _bgr(post)[nearby].astype(np.int16)
    delta = np.max(np.abs(before - after), axis=1)
    return bool(np.count_nonzero(delta > reference.policy.max_background_mean_difference) / len(delta) > 0.10)


def save_roi_evidence(result: RoiVerification, directory: str | Path) -> dict[str, str]:
    """保存本轮对比图和元数据；允许中文路径，不覆盖其他轮次文件夹。"""
    cv2, _ = _deps()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    refs: dict[str, str] = {}
    artifact_sha256: dict[str, str] = {}
    intended = [directory / f"{name}.png" for name in ("pre_crop", "post_crop", "overlay", "contact_sheet")]
    intended.append(directory / "roi_verification.json")
    if any(path.exists() for path in intended):
        raise FileExistsError("复检证据目录已有同名文件；每轮/每次重拍须使用新目录")
    for name in ("pre_crop", "post_crop", "overlay", "contact_sheet"):
        path = directory / f"{name}.png"
        ok, encoded = cv2.imencode(".png", getattr(result, name))
        if not ok:
            raise OSError(f"无法保存复检图像：{path}")
        payload = encoded.tobytes()
        path.write_bytes(payload)
        artifact_sha256[name] = hashlib.sha256(payload).hexdigest()
        refs[name] = path.name
    metadata = result.to_dict()
    metadata["artifacts"] = refs.copy()
    metadata["artifact_sha256"] = artifact_sha256
    path = directory / "roi_verification.json"
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    refs["metadata"] = path.name
    return refs


def _alignment(pre: Any, post: Any, exclude: Any, policy: RoiVerificationPolicy) -> AlignmentEvidence:
    cv2, np = _deps()
    before, after = _gray(pre), _gray(post)
    # 两层金字塔中的 21px 窗口不能跨入被清洗的区域。
    # 更深金字塔会把小污渍变化混进远处特征；大位移仍由背景差异门拒绝。
    excluded = cv2.dilate(exclude.astype(np.uint8), np.ones((49, 49), np.uint8)) > 0
    background = ~excluded
    background[:12] = background[-12:] = False
    background[:, :12] = background[:, -12:] = False
    if np.count_nonzero(background) < max(256, policy.min_background_features * 25):
        return _missing_alignment("ROI_BACKGROUND_UNOBSERVABLE")
    pre_bg, post_bg = before[background], after[background]
    lap_pre = cv2.Laplacian(before, cv2.CV_64F)[background]
    lap_post = cv2.Laplacian(after, cv2.CV_64F)[background]
    pre_focus, post_focus = float(lap_pre.var()), float(lap_post.var())
    if min(float(pre_bg.std()), float(post_bg.std())) < policy.min_background_std or min(pre_focus, post_focus) < policy.min_background_laplacian_variance:
        return _missing_alignment("ROI_BACKGROUND_UNOBSERVABLE")
    reasons: list[str] = []
    mean_delta = abs(float(pre_bg.mean()) - float(post_bg.mean()))
    difference_p95 = float(np.percentile(np.abs(pre_bg.astype(np.float32) - post_bg.astype(np.float32)), 95))
    focus_ratio = post_focus / pre_focus
    if mean_delta > policy.max_background_mean_difference:
        reasons.append("ROI_ILLUMINATION_CHANGED")
    if difference_p95 > policy.max_background_difference_p95:
        reasons.append("ROI_BACKGROUND_CHANGED")
    if not 1 / policy.max_focus_change_factor <= focus_ratio <= policy.max_focus_change_factor:
        reasons.append("ROI_FOCUS_CHANGED")
    points = cv2.goodFeaturesToTrack(before, 300, 0.02, 8, mask=background.astype(np.uint8) * 255, blockSize=5)
    if points is None or len(points) < policy.min_background_features:
        return AlignmentEvidence(False, None, 0.0, 0, 0.0, 0.0, None, mean_delta, difference_p95, focus_ratio, tuple(reasons + ["ROI_BACKGROUND_FEATURES_LOW"]))
    flowed, status, errors = cv2.calcOpticalFlowPyrLK(before, after, points, None, winSize=(21, 21), maxLevel=1)
    if flowed is None or status is None:
        return _missing_alignment("ROI_BACKGROUND_FEATURES_LOW")
    returned, back_status, _ = cv2.calcOpticalFlowPyrLK(after, before, flowed, None, winSize=(21, 21), maxLevel=1)
    if returned is None or back_status is None:
        return _missing_alignment("ROI_BACKGROUND_FEATURES_LOW")
    original = points.reshape(-1, 2)
    destination = flowed.reshape(-1, 2)
    roundtrip_error = np.linalg.norm(returned.reshape(-1, 2) - original, axis=1)
    good = status.reshape(-1).astype(bool) & back_status.reshape(-1).astype(bool) & (roundtrip_error <= 0.5)
    good &= errors.reshape(-1) <= 20
    height, width = background.shape
    target_x = np.rint(destination[:, 0]).astype(int).clip(0, width - 1)
    target_y = np.rint(destination[:, 1]).astype(int).clip(0, height - 1)
    good &= background[target_y, target_x]
    good &= (destination[:, 0] >= 12) & (destination[:, 0] < width - 12) & (destination[:, 1] >= 12) & (destination[:, 1] < height - 12)
    tracked = int(np.count_nonzero(good))
    if tracked < policy.min_background_features:
        return AlignmentEvidence(False, None, 0.0, tracked, tracked / len(points), 0.0, None, mean_delta, difference_p95, focus_ratio, tuple(reasons + ["ROI_BACKGROUND_FEATURES_LOW"]))
    deltas = destination[good] - original[good]
    shift = np.median(deltas, axis=0)
    coherent = np.linalg.norm(deltas - shift, axis=1) <= max(0.5, policy.alignment_tolerance_px)
    inlier_fraction = float(np.count_nonzero(coherent) / len(points))
    support = original[good][coherent]
    support_fraction = 0.0
    if len(support) >= 3:
        hull = cv2.convexHull(support.astype(np.float32))
        support_fraction = float(cv2.contourArea(hull) / (width * height))
    displacement_p95 = float(np.percentile(np.linalg.norm(deltas, axis=1), 95))
    if inlier_fraction < policy.min_feature_inlier_fraction or support_fraction < policy.min_feature_support_fraction:
        reasons.append("ROI_ALIGNMENT_LOW_CONFIDENCE")
    # 使用位移分位数，避免旋转/局部形变正负抵消后中位数看起来是零。
    if float(np.linalg.norm(shift)) > policy.alignment_tolerance_px or displacement_p95 > policy.alignment_tolerance_px:
        reasons.append("ROI_POSITION_SHIFTED")
    confidence = min(inlier_fraction, min(1.0, support_fraction / policy.min_feature_support_fraction))
    return AlignmentEvidence(not reasons, (float(shift[0]), float(shift[1])), confidence, tracked, inlier_fraction, support_fraction, displacement_p95, mean_delta, difference_p95, focus_ratio, tuple(reasons))


def _neighbor_intrudes(reference: RoiReference, mask: Any) -> bool:
    cv2, np = _deps()
    # 兼容消费者从PNG恢复的uint8掩膜；索引必须显式bool，不能成为整数行索引。
    neighbors = (reference.all_target_mask > 0) & ~(reference.target_mask > 0)
    if not np.any(neighbors):
        return False
    _, labels = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    neighbor_labels = set(int(value) for value in np.unique(labels[neighbors]) if value)
    # 此连通域检查仅用于二次排除相邻区域侵入；主面积始终在冻结坐标 ROI 内计算。
    return bool(neighbor_labels and np.any(np.isin(labels[reference.measurement_domain], list(neighbor_labels))))


def _missing_alignment(reason: str | None = None) -> AlignmentEvidence:
    return AlignmentEvidence(False, None, 0.0, 0, 0.0, 0.0, None, None, None, None, () if reason is None else (reason,))


def _image(value: Any, name: str) -> Any:
    _, np = _deps()
    if not isinstance(value, np.ndarray) or value.size == 0 or value.dtype != np.uint8 or not (value.ndim == 2 or value.ndim == 3 and value.shape[2] in (3, 4)):
        raise ValueError(f"{name} 必须是非空uint8灰度/BGR/BGRA图像")
    return value


def _mask(value: Any, name: str) -> Any:
    _, np = _deps()
    if not isinstance(value, np.ndarray) or value.size == 0 or value.ndim != 2 or value.dtype not in (np.dtype("uint8"), np.dtype("bool")):
        raise ValueError(f"{name} 必须是非空二维uint8/bool掩膜")
    return value > 0


def _readonly(array: Any) -> Any:
    value = array.copy()
    value.setflags(write=False)
    return value


def _hash(image: Any) -> str:
    return hashlib.sha256(str(image.shape).encode("ascii") + image.tobytes()).hexdigest()


def _gray(image: Any) -> Any:
    cv2, _ = _deps()
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY if image.shape[2] == 4 else cv2.COLOR_BGR2GRAY)


def _bgr(image: Any) -> Any:
    cv2, _ = _deps()
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR) if image.shape[2] == 4 else image


def _crop_for_display(image: Any, roi: tuple[int, int, int, int], zoom: int) -> Any:
    cv2, np = _deps()
    x, y, width, height = roi
    cropped = _bgr(image)[y:y + height, x:x + width]
    if not cropped.size:
        cropped = np.zeros((height, width, 3), np.uint8)
    # 不同尺寸属于无效证据，但仍把画布留到相同大小以便人排查。
    target_w, target_h = min(800, width * zoom), min(800, height * zoom)
    factor = min(target_w / max(1, cropped.shape[1]), target_h / max(1, cropped.shape[0]))
    resized = cv2.resize(cropped, (max(1, round(cropped.shape[1] * factor)), max(1, round(cropped.shape[0] * factor))), interpolation=cv2.INTER_NEAREST)
    canvas = np.zeros((target_h, target_w, 3), np.uint8)
    canvas[:resized.shape[0], :resized.shape[1]] = resized
    return canvas


def _contact_sheet(*images: Any) -> Any:
    _, np = _deps()
    gap = np.full((images[0].shape[0], 8, 3), 240, np.uint8)
    return np.concatenate((images[0], gap, images[1], gap, images[2]), axis=1)


def _deps() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("固定区域复检需要 requirements/perception-opencv.txt") from exc
    return cv2, np
