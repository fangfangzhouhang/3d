"""软件回放的最低安全边界（成员 C 的控制仿真模块）。

治理器持有不可由候选动作修改的限制。ALLOW 只授权 FakeSerial；它永远不是连接
真实泵、电机或 STM32 的许可。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from microcleaning.contracts import ActionRequest, SafetyDecision, SafetyOutcome, StateEstimate


@dataclass(frozen=True)
class ReplaySafetyLimits:
    workspace_min_mm: float = 0.0
    workspace_max_mm: float = 100.0
    min_duration_ms: int = 100
    max_duration_ms: int = 500
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
    if not state.calibration_valid:
        denied.append("CALIBRATION_INVALID")
    if request.coordinate_frame != "work_mm":
        denied.append("UNSUPPORTED_COORDINATE_FRAME")
    if request.primitive != "SPRAY_AT_POINT":
        denied.append("UNSUPPORTED_PRIMITIVE")
    if not malformed and (not limits.workspace_min_mm <= x <= limits.workspace_max_mm or not limits.workspace_min_mm <= y <= limits.workspace_max_mm):
        denied.append("TARGET_OUT_OF_WORKSPACE")
    if not malformed and not limits.min_duration_ms <= request.duration_ms <= limits.max_duration_ms:
        denied.append("DURATION_OUT_OF_BOUNDS")
    if not malformed and not 0.0 < request.pressure <= limits.max_pressure:
        denied.append("PRESSURE_OUT_OF_BOUNDS")
    if not state.device_state.get("observation_quality_ok"):
        human.append("OBSERVATION_LOW_QUALITY")
    if state.uncertainty_mm > limits.max_uncertainty_mm:
        human.append("LOCALIZATION_UNCERTAIN")

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
