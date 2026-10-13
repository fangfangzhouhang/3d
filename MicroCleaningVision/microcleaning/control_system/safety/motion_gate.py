"""Stage 2 步进运动的安全关卡（成员 C 的控制仿真模块）。

与泵的 Human Gate 对称：

- ``evaluate_motion`` 只给 DENY 或 HUMAN，从不直接 ALLOW；
- ``approve_motion_gate(confirmed=True)`` 把 HUMAN 转成一次性 ALLOW（令牌 + 摘要 + 有效期）；
- ``require_motion_allow`` 由 ``Stage2SerialLink`` 在打开 COM 前核对，令牌只能用一次。

复用 ``contracts.SafetyDecision``，共享合同字段不改。``MotionRequest`` 是 C 自己的
数据类，不进共享合同；运动回执合同化见 mcl-v0.2 提案。

主机不再用整份名单的步数合计、也不用人工零点两侧的步数边界拦住移动。
一条 MOVEXY 仍不能超过固件报文的 20000 步。位置账本仍是相对人工零点的计数，不是回零：
丢步或断电后会漂，每次上电都要人重新对位后归零。
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from microcleaning.contracts import SafetyDecision, SafetyOutcome
from microcleaning.control_system.planning.stage2_axes import MAX_PULSE_STEPS, parse_movexy_line


STAGE2_MOTION_GATE_VERSION = "stage2-motion-gate-v0"
STAGE2_RUN_STEP_CAP = MAX_PULSE_STEPS
STAGE2_LEG_STEP_CAP = MAX_PULSE_STEPS
DEFAULT_SOFT_LIMIT_STEPS = 10000
START_REFERENCES = frozenset({"image_center", "nozzle_px"})

_consumed_tokens: set[str] = set()
_consumed_lock = threading.Lock()


@dataclass(frozen=True)
class MotionLimits:
    """关卡持有的限制。候选动作和命令行都不能放宽步数上限。"""

    max_abs_steps_per_axis: int = STAGE2_RUN_STEP_CAP
    max_steps_per_leg: int = STAGE2_LEG_STEP_CAP
    soft_min_steps: tuple[int, int] = (-DEFAULT_SOFT_LIMIT_STEPS, -DEFAULT_SOFT_LIMIT_STEPS)
    soft_max_steps: tuple[int, int] = (DEFAULT_SOFT_LIMIT_STEPS, DEFAULT_SOFT_LIMIT_STEPS)
    require_calibration: bool = True
    approval_ttl_seconds: int = 30
    version: str = STAGE2_MOTION_GATE_VERSION

    def validate(self) -> None:
        if not 0 < self.max_abs_steps_per_axis <= STAGE2_RUN_STEP_CAP:
            raise ValueError(f"每轴累计步数上限必须在 1～{STAGE2_RUN_STEP_CAP}")
        if not 0 < self.max_steps_per_leg <= STAGE2_LEG_STEP_CAP:
            raise ValueError(f"单段步数上限必须在 1～{STAGE2_LEG_STEP_CAP}")
        for low, high in zip(self.soft_min_steps, self.soft_max_steps):
            if not low <= 0 <= high:
                raise ValueError("软限位必须包住人工零点")
        if self.approval_ttl_seconds <= 0:
            raise ValueError("审批有效期必须为正")


@dataclass(frozen=True)
class MotionRequest:
    """一次 Stage 2 运动申请：要发的 MOVEXY 行和判断它所需的事实。不是硬件命令。"""

    request_id: str
    task_id: str
    lines: tuple[str, ...]
    position_before_steps: tuple[int, int] | None
    start_reference: str
    calibration_ref: str | None = None
    calibration_sha256: str | None = None
    calibration_warnings: tuple[str, ...] = ()
    dispatch_truncated: bool = False
    rule_version: str = STAGE2_MOTION_GATE_VERSION


@dataclass(frozen=True)
class MotionPlan:
    """由 MOVEXY 行推出的计数，给关卡判断，也给人确认时看。"""

    line_count: int
    malformed_lines: tuple[str, ...]
    abs_steps: tuple[int, int]
    net_steps: tuple[int, int]
    max_leg_steps: int
    position_before: tuple[int, int] | None
    position_after: tuple[int, int] | None
    soft_min_steps: tuple[int, int]
    soft_max_steps: tuple[int, int]
    leaves_soft_limits: bool

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in ("abs_steps", "net_steps", "position_before", "position_after", "soft_min_steps", "soft_max_steps"):
            value = payload[key]
            payload[key] = None if value is None else list(value)
        payload["malformed_lines"] = list(self.malformed_lines)
        payload["coordinate_frame"] = "stage2_steps_from_human_zero"
        return payload


def new_motion_request_id() -> str:
    return f"motion_{uuid4().hex[:12]}"


def motion_request_digest(request: MotionRequest) -> str:
    """动作内容摘要，用于发现审批后的行被改动。"""

    payload = asdict(request)
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def summarize_motion(request: MotionRequest, limits: MotionLimits = MotionLimits()) -> MotionPlan:
    """逐行累加步数；位置已知时检查每一段终点都在软限位内。"""

    malformed: list[str] = []
    deltas: list[tuple[int, int]] = []
    for line in request.lines:
        if "MCV1" in line or "PUMP" in line:
            malformed.append(line)
            continue
        try:
            deltas.append(parse_movexy_line(line))
        except ValueError:
            malformed.append(line)
    abs_x = sum(abs(dx) for dx, _dy in deltas)
    abs_y = sum(abs(dy) for _dx, dy in deltas)
    net = (sum(dx for dx, _dy in deltas), sum(dy for _dx, dy in deltas))
    max_leg = max((max(abs(dx), abs(dy)) for dx, dy in deltas), default=0)

    before = request.position_before_steps
    after = None
    leaves = False
    if before is not None:
        cursor = (int(before[0]), int(before[1]))
        leaves = not _inside(cursor, limits)
        for dx, dy in deltas:
            cursor = (cursor[0] + dx, cursor[1] + dy)
            if not _inside(cursor, limits):
                leaves = True
        after = cursor
    return MotionPlan(
        line_count=len(request.lines),
        malformed_lines=tuple(malformed),
        abs_steps=(abs_x, abs_y),
        net_steps=net,
        max_leg_steps=max_leg,
        position_before=None if before is None else (int(before[0]), int(before[1])),
        position_after=after,
        soft_min_steps=limits.soft_min_steps,
        soft_max_steps=limits.soft_max_steps,
        leaves_soft_limits=leaves,
    )


def evaluate_motion(request: MotionRequest, limits: MotionLimits = MotionLimits()) -> SafetyDecision:
    """硬拒绝优先；全部通过也只给 HUMAN，必须人确认后才可能 ALLOW。"""

    limits.validate()
    plan = summarize_motion(request, limits)
    denied: list[str] = []
    human: list[str] = []
    if plan.malformed_lines:
        denied.append("MALFORMED_OR_UNSAFE_LINE")
    if not request.lines:
        denied.append("NO_MOTION")
    if request.rule_version != limits.version:
        denied.append("RULE_VERSION_MISMATCH")
    if plan.max_leg_steps > limits.max_steps_per_leg:
        denied.append("LEG_STEPS_OVER_CAP")
    if request.start_reference not in START_REFERENCES:
        denied.append("UNSUPPORTED_START_REFERENCE")
    if limits.require_calibration:
        if not request.calibration_ref or not request.calibration_sha256:
            denied.append("CALIBRATION_MISSING")
        elif request.calibration_warnings:
            denied.append("CALIBRATION_HAS_WARNINGS")
    if request.position_before_steps is None:
        denied.append("POSITION_UNKNOWN")
    if request.dispatch_truncated:
        human.append("PATH_TRUNCATED_BY_BUDGET")
    human.append("STAGE2_MOTION_REQUIRES_HUMAN")

    outcome = SafetyOutcome.DENY if denied else SafetyOutcome.HUMAN
    return SafetyDecision(
        request.request_id,
        outcome,
        tuple(denied + human),
        None,
        None,
        policy_version=limits.version,
    )


def format_side_clearance(position: tuple[int, int], limits: MotionLimits = MotionLimits()) -> str:
    """账本上的当前计数。主机不再用两侧步数边界拦住移动。"""

    del limits
    x, y = int(position[0]), int(position[1])
    return f"账本位置 X={x} 步，Y={y} 步，相对人工零点计数。主机不再按步数边界拦住这一步。"


def explain_travel_block(
    outbound: MotionRequest,
    returning: MotionRequest,
    limits: MotionLimits = MotionLimits(),
) -> str | None:
    """去程或回程会越出边界、或单次步数放不下时，给出中文拦截说明。能发令时返回 None。"""

    outbound_decision = evaluate_motion(outbound, limits)
    return_decision = evaluate_motion(returning, limits)
    blocked = (
        outbound_decision.outcome is SafetyOutcome.DENY
        or return_decision.outcome is SafetyOutcome.DENY
        or outbound.dispatch_truncated
        or returning.dispatch_truncated
    )
    if not blocked:
        return None
    sentences = ["已拦住，电机不会动，也不会喷水。"]
    if outbound.position_before_steps is None:
        sentences.append("当前位置未知，所以不能发移动命令。")
    else:
        x, y = int(outbound.position_before_steps[0]), int(outbound.position_before_steps[1])
        sentences.append(f"账本位置 X={x} 步，Y={y} 步，相对人工零点计数。")
    sentences.extend(_leg_block_sentences("去程", outbound, limits))
    sentences.extend(_leg_block_sentences("回程", returning, limits))
    return "".join(sentences)


def _leg_block_sentences(label: str, request: MotionRequest, limits: MotionLimits) -> list[str]:
    plan = summarize_motion(request, limits)
    sentences: list[str] = []
    if request.dispatch_truncated:
        sentences.append(f"{label}有一段超过这条报文允许写入的步数，整段没有放进移动命令。")
    elif plan.max_leg_steps > limits.max_steps_per_leg:
        sentences.append(
            f"{label}单条有一轴要走 {plan.max_leg_steps} 步，超过固件一条报文能写的 {limits.max_steps_per_leg} 步，没有放进移动命令。"
        )
    return sentences


def approve_motion_gate(
    request: MotionRequest,
    decision: SafetyDecision,
    *,
    confirmed: bool,
    limits: MotionLimits = MotionLimits(),
) -> SafetyDecision:
    """把 HUMAN 转为一次性 ALLOW；未确认时保持 HUMAN，DENY 不能被改写。"""

    if decision.outcome is SafetyOutcome.DENY:
        raise PermissionError("DENY 不能被人工关卡改成 ALLOW")
    if decision.outcome is SafetyOutcome.ALLOW:
        raise PermissionError("已经是 ALLOW，不需要再次人工批准")
    if decision.action_id != request.request_id:
        raise PermissionError("人工关卡与运动申请不匹配")
    if not confirmed:
        return decision

    current = evaluate_motion(request, limits)
    if current.outcome is SafetyOutcome.DENY:
        return current
    if current.outcome is not SafetyOutcome.HUMAN:
        raise PermissionError("重新评估后不再是 HUMAN，拒绝发令牌")

    issued = datetime.now(timezone.utc)
    return SafetyDecision(
        request.request_id,
        SafetyOutcome.ALLOW,
        ("HUMAN_GATE_CONFIRMED",) + current.reason_codes,
        f"motion_approval_{uuid4().hex[:12]}",
        None,
        motion_request_digest(request),
        limits.version,
        issued.isoformat(),
        (issued + timedelta(seconds=limits.approval_ttl_seconds)).isoformat(),
    )


def require_motion_allow(
    request: MotionRequest,
    decision: SafetyDecision,
    lines: tuple[str, ...],
    *,
    now: datetime | None = None,
) -> None:
    """发送前最后一次核对。通过即消耗令牌；任何不符都抛 PermissionError，不打开 COM。"""

    if decision.outcome is not SafetyOutcome.ALLOW or not decision.approval_token:
        raise PermissionError("Stage 2 运动未获 ALLOW，拒绝发送")
    if decision.action_id != request.request_id:
        raise PermissionError("审批与运动申请不匹配")
    if decision.policy_version != STAGE2_MOTION_GATE_VERSION:
        raise PermissionError("审批来自其他关卡版本")
    if decision.request_digest != motion_request_digest(request):
        raise PermissionError("运动申请在审批后被改动")
    if tuple(lines) != tuple(request.lines):
        raise PermissionError("要发的 MOVEXY 与审批内容不一致")
    if not decision.expires_at:
        raise PermissionError("审批缺少有效期")
    current = now or datetime.now(timezone.utc)
    if current >= datetime.fromisoformat(decision.expires_at):
        raise PermissionError("审批已过期，请重新确认")
    with _consumed_lock:
        if decision.approval_token in _consumed_tokens:
            raise PermissionError("审批令牌已用过，不能重放")
        _consumed_tokens.add(decision.approval_token)


def _inside(position: tuple[int, int], limits: MotionLimits) -> bool:
    return all(
        low <= value <= high
        for value, low, high in zip(position, limits.soft_min_steps, limits.soft_max_steps)
    )
