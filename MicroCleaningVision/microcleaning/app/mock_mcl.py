"""无依赖、仅模拟的最小闭环。

它只证明软件接口和失败路由连通，不证明相机、标定、串口、泵、运动、清洗或
硬件安全性能。真实适配器只能在人工批准硬件规程后引入，并必须沿用同一套契约。
换句话说：这里的 ``mock://`` 是流程演练，不是实验结果。
"""

from __future__ import annotations

import json
import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from microcleaning.contracts import (
    ActionRequest,
    Episode,
    ExecutionReceipt,
    FailureRecord,
    NextRoute,
    Observation,
    SafetyDecision,
    SafetyOutcome,
    StateEstimate,
    VerificationResult,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def canonical_request_digest(request: ActionRequest) -> str:
    """动作申请进入安全边界前，先生成不可伪造的内容摘要。"""
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
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MockSafetyPolicy:
    """由安全治理器持有的限制；候选申请不能自行修改这些上限。"""

    max_duration_ms: int = 500
    min_duration_ms: int = 100
    max_pressure: float = 0.80
    max_uncertainty_mm: float = 0.50
    minimum_quality: float = 0.70
    max_prior_actions: int = 0
    workspace_min_mm: float = 0.0
    workspace_max_mm: float = 100.0
    approval_ttl_seconds: int = 30
    version: str = "mock-safety-policy-v0"


@dataclass(frozen=True)
class MockScenario:
    """仅供测试和干运行演示使用的可控合成场景。"""

    contamination_area_px: float = 100.0
    centroid_mm: tuple[float, float] = (50.0, 50.0)
    confidence: float = 0.95
    focus_quality: float = 0.95
    illumination_quality: float = 0.95
    calibration_valid: bool = True
    interlock_ok: bool = True
    e_stop_active: bool = False
    controller_connected: bool = True
    communication_failure: bool = False
    cleaning_efficiency: float = 0.90
    damage_flag: bool = False
    prior_action_count: int = 0


class MockCamera:
    """Synthetic observation source; it does not access a camera device."""

    def capture(self, task_id: str, phase: str, scenario: MockScenario, area_px: float) -> tuple[Observation, float]:
        quality_flags = tuple(
            flag
            for score, flag in (
                (scenario.focus_quality, "FOCUS_LOW"),
                (scenario.illumination_quality, "ILLUMINATION_LOW"),
                (scenario.confidence, "CONFIDENCE_LOW"),
            )
            if score < MockSafetyPolicy.minimum_quality
        )
        observation = Observation(
            observation_id=uid("obs"),
            task_id=task_id,
            timestamp=utc_now(),
            frame_id=f"mock-{phase}-{uuid4().hex[:8]}",
            raw_image_ref=f"mock://{task_id}/{phase}",
            focus_quality=scenario.focus_quality,
            illumination_quality=scenario.illumination_quality,
            confidence=scenario.confidence,
            quality_flags=quality_flags,
            software_version="mock-mcl-v0",
        )
        return observation, area_px


class RuleDecision:
    """Smallest deterministic policy: one fixed spray primitive for one target."""

    def propose(self, state: StateEstimate) -> ActionRequest | None:
        if state.target_centroid_mm is None or state.target_area_px <= 0:
            return None
        return ActionRequest(
            action_id=uid("action"),
            task_id=state.task_id,
            state_id=state.state_id,
            target_centroid_mm=state.target_centroid_mm,
            coordinate_frame=state.coordinate_frame,
            primitive="SPRAY_AT_POINT",
            duration_ms=200,
            pressure=0.30,
            constraints={"max_duration_ms": 500, "max_pressure": 0.80, "retry_budget": 1},
            expected_effect="reduce synthetic contamination area",
            rule_version="fixed-spray-rule-v0",
        )


class MockSafetyGovernor:
    """Independent, deterministic safety gate for structured action proposals."""

    def __init__(self, policy: MockSafetyPolicy = MockSafetyPolicy()) -> None:
        self.policy = policy

    def evaluate(self, state: StateEstimate, request: ActionRequest, scenario: MockScenario) -> SafetyDecision:
        hard_denials: list[str] = []
        human_reasons: list[str] = []
        target_x: float | None = None
        target_y: float | None = None
        malformed = False
        try:
            target_x, target_y = request.target_centroid_mm
            numeric_fields = (target_x, target_y, request.duration_ms, request.pressure)
            malformed = not all(isinstance(value, (int, float)) and value == value for value in numeric_fields)
        except (TypeError, ValueError):
            malformed = True
        if malformed:
            hard_denials.append("MALFORMED_ACTION_REQUEST")
        if state.device_state.get("e_stop_active"):
            hard_denials.append("ESTOP_ACTIVE")
        if not state.device_state.get("interlock_ok"):
            hard_denials.append("INTERLOCK_OPEN")
        if not state.device_state.get("controller_connected"):
            hard_denials.append("CONTROLLER_UNAVAILABLE")
        if not state.calibration_valid:
            hard_denials.append("CALIBRATION_EXPIRED")
        if state.uncertainty_mm > self.policy.max_uncertainty_mm:
            human_reasons.append("LOCALIZATION_UNCERTAIN")
        if scenario.confidence < self.policy.minimum_quality or scenario.focus_quality < self.policy.minimum_quality or scenario.illumination_quality < self.policy.minimum_quality:
            human_reasons.append("OBSERVATION_LOW_QUALITY")
        if not malformed and not (self.policy.workspace_min_mm <= target_x <= self.policy.workspace_max_mm and self.policy.workspace_min_mm <= target_y <= self.policy.workspace_max_mm):
            hard_denials.append("TARGET_OUT_OF_WORKSPACE")
        if request.coordinate_frame != "work_mm":
            hard_denials.append("UNSUPPORTED_COORDINATE_FRAME")
        if request.primitive != "SPRAY_AT_POINT":
            hard_denials.append("UNSUPPORTED_PRIMITIVE")
        if not malformed and not (self.policy.min_duration_ms <= request.duration_ms <= self.policy.max_duration_ms):
            hard_denials.append("DURATION_OUT_OF_BOUNDS")
        if not malformed and not (0.0 < request.pressure <= self.policy.max_pressure):
            hard_denials.append("PRESSURE_OUT_OF_BOUNDS")
        if len(state.prior_actions) > self.policy.max_prior_actions:
            human_reasons.append("RETRY_BUDGET_EXHAUSTED")

        if hard_denials:
            return SafetyDecision(
                action_id=request.action_id,
                outcome=SafetyOutcome.DENY,
                reason_codes=tuple(hard_denials + human_reasons),
                approval_token=None,
                state_id=state.state_id,
                request_digest=None,
                policy_version=self.policy.version,
            )
        if human_reasons:
            return SafetyDecision(
                action_id=request.action_id,
                outcome=SafetyOutcome.HUMAN,
                reason_codes=tuple(human_reasons),
                approval_token=None,
                state_id=state.state_id,
                request_digest=None,
                policy_version=self.policy.version,
            )
        issued = datetime.now(timezone.utc)
        return SafetyDecision(
            action_id=request.action_id,
            outcome=SafetyOutcome.ALLOW,
            reason_codes=("ALL_CHECKS_PASSED",),
            approval_token=uid("approval"),
            state_id=state.state_id,
            request_digest=canonical_request_digest(request),
            policy_version=self.policy.version,
            issued_at=issued.isoformat(),
            expires_at=(issued + timedelta(seconds=self.policy.approval_ttl_seconds)).isoformat(),
        )


class MockController:
    """Mock executor.  It refuses anything other than an ALLOW decision."""

    mode = "mock"

    def __init__(self) -> None:
        self._used_tokens: set[str] = set()

    def execute(self, request: ActionRequest, decision: SafetyDecision, scenario: MockScenario) -> ExecutionReceipt:
        if decision.outcome is not SafetyOutcome.ALLOW or not decision.approval_token:
            raise PermissionError("mock controller requires a current ALLOW decision")
        if decision.action_id != request.action_id or decision.state_id != request.state_id:
            raise PermissionError("approval does not bind this action and state")
        if decision.request_digest != canonical_request_digest(request):
            raise PermissionError("approved request has been modified")
        if decision.policy_version != "mock-safety-policy-v0":
            raise PermissionError("unknown safety policy")
        if not decision.expires_at or datetime.fromisoformat(decision.expires_at) <= datetime.now(timezone.utc):
            raise PermissionError("approval has expired")
        if decision.approval_token in self._used_tokens:
            raise PermissionError("approval token may be used only once")
        self._used_tokens.add(decision.approval_token)
        started = utc_now()
        if scenario.communication_failure:
            return ExecutionReceipt(
                action_id=request.action_id,
                mode=self.mode,
                started_at=started,
                ended_at=utc_now(),
                actual_target_mm=None,
                actual_duration_ms=0,
                actual_pressure=0.0,
                controller_state="UNKNOWN",
                interlock_state="SIMULATED_OK",
                success=False,
                error_code="COMMUNICATION_TIMEOUT",
            )
        return ExecutionReceipt(
            action_id=request.action_id,
            mode=self.mode,
            started_at=started,
            ended_at=utc_now(),
            actual_target_mm=request.target_centroid_mm,
            actual_duration_ms=request.duration_ms,
            actual_pressure=request.pressure,
            controller_state="SIMULATED_COMPLETE",
            interlock_state="SIMULATED_OK",
            success=True,
        )


class SimpleVerifier:
    def verify(
        self,
        task_id: str,
        pre: Observation,
        post: Observation | None,
        pre_area: float,
        post_area: float | None,
        receipt: ExecutionReceipt | None,
        scenario: MockScenario,
    ) -> VerificationResult:
        if receipt is None or not receipt.success or post is None or post_area is None:
            return VerificationResult(task_id, pre.observation_id, None, None, None, False, NextRoute.HUMAN, ("EXECUTION_UNCONFIRMED",))
        removal_rate = 0.0 if pre_area <= 0 else (pre_area - post_area) / pre_area
        if scenario.damage_flag:
            return VerificationResult(task_id, pre.observation_id, post.observation_id, post_area, removal_rate, True, NextRoute.HUMAN, ("POTENTIAL_DAMAGE",))
        if post_area <= 15.0:
            return VerificationResult(task_id, pre.observation_id, post.observation_id, post_area, removal_rate, False, NextRoute.STOP, ("RESIDUAL_BELOW_MOCK_THRESHOLD",))
        return VerificationResult(task_id, pre.observation_id, post.observation_id, post_area, removal_rate, False, NextRoute.HUMAN, ("RESIDUAL_REQUIRES_HUMAN_REVIEW",))


class MockMCLRunner:
    """Compose the mock vertical slice while preserving every evidence object."""

    def __init__(self) -> None:
        self.camera = MockCamera()
        self.decision = RuleDecision()
        self.safety = MockSafetyGovernor()
        self.controller = MockController()
        self.verifier = SimpleVerifier()

    def run(self, scenario: MockScenario = MockScenario(), task_id: str | None = None) -> Episode:
        task_id = task_id or uid("task")
        pre, pre_area = self.camera.capture(task_id, "pre", scenario, scenario.contamination_area_px)
        state = StateEstimate(
            state_id=uid("state"), task_id=task_id, observation_id=pre.observation_id,
            target_centroid_mm=scenario.centroid_mm if pre_area > 0 else None,
            target_area_px=pre_area, coordinate_frame="work_mm", uncertainty_mm=0.10,
            device_state={"interlock_ok": scenario.interlock_ok, "e_stop_active": scenario.e_stop_active, "controller_connected": scenario.controller_connected},
            calibration_version="mock-calibration-v0", calibration_valid=scenario.calibration_valid,
            prior_actions=tuple(f"prior_{number}" for number in range(scenario.prior_action_count)),
        )
        request = self.decision.propose(state)
        if request is None:
            verification = VerificationResult(task_id, pre.observation_id, None, 0.0, 1.0, False, NextRoute.STOP, ("NO_TARGET",))
            return Episode(uid("episode"), task_id, "mock", "mock-protocol-v0", pre, state, None, None, None, None, verification)

        safety = self.safety.evaluate(state, request, scenario)
        if safety.outcome is not SafetyOutcome.ALLOW:
            failure = FailureRecord(uid("failure"), task_id, "safety", "high", safety.reason_codes, True, "human review or correct prerequisite")
            verification = VerificationResult(task_id, pre.observation_id, None, None, None, False, NextRoute.HUMAN if safety.outcome is SafetyOutcome.HUMAN else NextRoute.STOP, safety.reason_codes)
            return Episode(uid("episode"), task_id, "mock", "mock-protocol-v0", pre, state, request, safety, None, None, verification, [failure])

        receipt = self.controller.execute(request, safety, scenario)
        if not receipt.success:
            failure = FailureRecord(uid("failure"), task_id, "execution", "high", (receipt.error_code or "EXECUTION_FAILED",), True, "safe stop and inspect controller link")
            verification = self.verifier.verify(task_id, pre, None, pre_area, None, receipt, scenario)
            return Episode(uid("episode"), task_id, "mock", "mock-protocol-v0", pre, state, request, safety, receipt, None, verification, [failure])

        post_area = max(0.0, pre_area * (1.0 - scenario.cleaning_efficiency))
        post, _ = self.camera.capture(task_id, "post", scenario, post_area)
        verification = self.verifier.verify(task_id, pre, post, pre_area, post_area, receipt, scenario)
        failures: list[FailureRecord] = []
        if verification.next_route is not NextRoute.STOP:
            failures.append(FailureRecord(uid("failure"), task_id, "verification", "medium", verification.reason_codes, True, "human review before another action"))
        return Episode(uid("episode"), task_id, "mock", "mock-protocol-v0", pre, state, request, safety, receipt, post, verification, failures)


def write_episode(episode: Episode, output_dir: str | Path) -> Path:
    """Persist an immutable, atomically-written JSON record plus SHA-256 sidecar."""
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"{episode.episode_id}.json"
    digest_path = folder / f"{episode.episode_id}.sha256"
    if output.exists():
        raise FileExistsError(f"refusing to overwrite episode: {output}")
    if digest_path.exists():
        raise FileExistsError(f"refusing to overwrite evidence digest: {digest_path}")
    payload = json.dumps(episode.as_dict(), ensure_ascii=False, indent=2) + "\n"
    temporary = folder / f".{episode.episode_id}.{uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if output.exists():
            raise FileExistsError(f"refusing to overwrite episode: {output}")
        os.replace(temporary, output)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        digest_path.write_text(f"{digest}  {output.name}\n", encoding="ascii", newline="\n")
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


if __name__ == "__main__":
    episode = MockMCLRunner().run(task_id="mock-demo")
    path = write_episode(episode, Path("output") / "episodes")
    print(f"Mock-only episode written to {path}")
    print(f"Route: {episode.verification.next_route.value if episode.verification else 'UNKNOWN'}")
