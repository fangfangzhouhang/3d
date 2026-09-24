"""软件回放与定点短喷的最低安全边界（成员 C 的控制仿真模块）。

治理器持有不可由候选动作修改的限制。``SPRAY_AT_POINT`` 的 ALLOW 只授权 FakeSerial。
``PUMP_IN_PLACE`` 不会从 ``evaluate_action`` 直接得到 ALLOW，第一次泵动作必须经过
Human Gate；即使批准，STM32 适配器仍需显式 ``--arm-pump`` 才会翻译 PUMP。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from microcleaning.contracts import ActionRequest, SafetyDecision, SafetyOutcome, StateEstimate
from microcleaning.control_system.fixed_rule import (
    IN_PLACE_TARGET_MM,
    MAX_IN_PLACE_DURATION_MS,
    NOZZLE_FIXED_FRAME,
    PUMP_IN_PLACE,
)


@dataclass(frozen=True)
class ReplaySafetyLimits:
    workspace_min_mm: float = 0.0
    workspace_max_mm: float = 100.0
    min_duration_ms: int = 100
    max_duration_ms: int = 500
    max_in_place_duration_ms: int = MAX_IN_PLACE_DURATION_MS
    max_pressure: float = 0.80
    max_uncertainty_mm: float = 0.50
    approval_ttl_seconds: int = 30
    version: str = "replay-safety-v0"


def request_digest(request: ActionRequest) -> str:
    """生成动作内容摘要，用于发现审批后的参数篡改。"""
    payload = {
        "action_id": request.action_id,
        "task_id": request.task_id,
        "state_id": request.state_id,
        "target_centroid_mm": request.target_centroid_mm,
        "coordinate_frame": request.coordinate_frame,
        "primitive": request.primitive,
        "duration_ms": request.duration_ms,
        "pressure": request.pressure,
        "constraints": request.constraints,
        "expected_effect": request.expected_effect,
        "rule_version": request.rule_version,
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evaluate_action(
    state: StateEstimate,
    request: ActionRequest,
    limits: ReplaySafetyLimits = ReplaySafetyLimits(),
) -> SafetyDecision:
    """按“硬拒绝优先、测量不确定转人工”的顺序审批动作。"""
    denied: list[str] = []
    human: list[str] = []
    try:
        x, y = request.target_centroid_mm
        numeric = (x, y, request.duration_ms, request.pressure)
        malformed = not all(isinstance(value, (int, float)) and value == value for value in numeric)
    except (TypeError, ValueError):
        x = y = 0.0
        malformed = True
    if malformed:
        denied.append("MALFORMED_ACTION_REQUEST")
    if request.task_id != state.task_id or request.state_id != state.state_id:
        denied.append("REQUEST_STATE_MISMATCH")
    if state.device_state.get("e_stop_active"):
        denied.append("ESTOP_ACTIVE")
    if not state.device_state.get("controller_connected"):
        denied.append("CONTROLLER_UNAVAILABLE")
    if not state.device_state.get("interlock_ok"):
        denied.append("INTERLOCK_OPEN")
    in_place = request.primitive == PUMP_IN_PLACE
    if in_place:
        if request.coordinate_frame != NOZZLE_FIXED_FRAME:
            denied.append("UNSUPPORTED_COORDINATE_FRAME")
        if not malformed and (x, y) != IN_PLACE_TARGET_MM and (x, y) != (0, 0):
            denied.append("IN_PLACE_TARGET_NOT_NOZZLE_ORIGIN")
        if request.constraints.get("xy_motion", True):
            denied.append("XY_MOTION_NOT_FORBIDDEN")
        if not malformed and not limits.min_duration_ms <= request.duration_ms <= limits.max_in_place_duration_ms:
            denied.append("DURATION_OUT_OF_BOUNDS")
        human.append("PUMP_IN_PLACE_REQUIRES_HUMAN")
    else:
        if not state.calibration_valid:
            denied.append("CALIBRATION_INVALID")
        if request.coordinate_frame != "work_mm":
            denied.append("UNSUPPORTED_COORDINATE_FRAME")
        if request.primitive != "SPRAY_AT_POINT":
            denied.append("UNSUPPORTED_PRIMITIVE")
        if not malformed and (
            not limits.workspace_min_mm <= x <= limits.workspace_max_mm
            or not limits.workspace_min_mm <= y <= limits.workspace_max_mm
        ):
            denied.append("TARGET_OUT_OF_WORKSPACE")
        if not malformed and not limits.min_duration_ms <= request.duration_ms <= limits.max_duration_ms:
            denied.append("DURATION_OUT_OF_BOUNDS")
        if state.uncertainty_mm is None:
            denied.append("MILLIMETRE_UNCERTAINTY_MISSING")
        elif state.uncertainty_mm > limits.max_uncertainty_mm:
            human.append("LOCALIZATION_UNCERTAIN")
    if not malformed and not 0.0 < request.pressure <= limits.max_pressure:
        denied.append("PRESSURE_OUT_OF_BOUNDS")
    if not state.device_state.get("observation_quality_ok"):
        human.append("OBSERVATION_LOW_QUALITY")

    if denied:
        return SafetyDecision(request.action_id, SafetyOutcome.DENY, tuple(denied + human), None, state.state_id, policy_version=limits.version)
    if human:
        return SafetyDecision(request.action_id, SafetyOutcome.HUMAN, tuple(human), None, state.state_id, policy_version=limits.version)
    issued = datetime.now(timezone.utc)
    return SafetyDecision(
        request.action_id,
        SafetyOutcome.ALLOW,
        ("REPLAY_CHECKS_PASSED",),
        f"approval_{uuid4().hex[:12]}",
        state.state_id,
        request_digest(request),
        limits.version,
        issued.isoformat(),
        (issued + timedelta(seconds=limits.approval_ttl_seconds)).isoformat(),
    )


def approve_human_gate(
    state: StateEstimate,
    request: ActionRequest,
    decision: SafetyDecision,
    *,
    confirmed: bool,
    limits: ReplaySafetyLimits = ReplaySafetyLimits(),
) -> SafetyDecision:
    """把 HUMAN 转为一次性 ALLOW；未确认时保持 HUMAN，DENY 不能被改写。"""

    if decision.outcome is SafetyOutcome.DENY:
        raise PermissionError("DENY 不能被人工关卡改成 ALLOW")
    if decision.outcome is SafetyOutcome.ALLOW:
        raise PermissionError("已经是 ALLOW，不需要再次人工批准")
    if decision.outcome is not SafetyOutcome.HUMAN:
        raise PermissionError("只有 HUMAN 决策可以进入人工关卡")
    if decision.action_id != request.action_id or decision.state_id != request.state_id:
        raise PermissionError("人工关卡与动作或状态不匹配")
    if not confirmed:
        return decision

    current = evaluate_action(state, request, limits)
    if current.outcome is SafetyOutcome.DENY:
        return current
    if current.outcome is not SafetyOutcome.HUMAN:
        raise PermissionError("重新评估后不再是 HUMAN，拒绝发令牌")

    issued = datetime.now(timezone.utc)
    return SafetyDecision(
        request.action_id,
        SafetyOutcome.ALLOW,
        ("HUMAN_GATE_CONFIRMED",) + current.reason_codes,
        f"approval_{uuid4().hex[:12]}",
        state.state_id,
        request_digest(request),
        limits.version,
        issued.isoformat(),
        (issued + timedelta(seconds=limits.approval_ttl_seconds)).isoformat(),
    )
