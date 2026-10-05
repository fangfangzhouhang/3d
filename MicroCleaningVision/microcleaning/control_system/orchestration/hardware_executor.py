"""调用既有授权/串口实现，执行一轮去程—短喷—回原观察位。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Callable
from pathlib import Path

from microcleaning.contracts import ActionRequest, ExecutionReceipt, Observation, SafetyDecision, SafetyOutcome, StateEstimate
from microcleaning.control_system.planning.stage2_axes import parse_movexy_line
from microcleaning.control_system.planning.stage2_geometry import CycleGeometry
from microcleaning.control_system.planning.stage2_position import load_position, mark_unknown, record_completed
from microcleaning.control_system.safety.fixed_rule import FixedActionPolicy, PUMP_IN_PLACE_RULE_VERSION, propose_pump_in_place
from microcleaning.control_system.safety.governor import approve_human_gate, evaluate_action
from microcleaning.control_system.safety.motion_gate import approve_motion_gate, evaluate_motion, motion_request_digest
from microcleaning.control_system.serial.f103_session import F103SerialSession, F103StepThenPumpResult
from microcleaning.control_system.serial.stage2_link import Stage2SerialLink, Stage2TransmitError, Stage2TransmitResult
from microcleaning.control_system.serial.stm32_serial import STM32SerialController
from microcleaning.vision.contamination import ContaminationMeasurement
from microcleaning.vision.state_estimator import estimate_state


@dataclass
class CycleExecution:
    status: str
    reasons: tuple[str, ...]
    state: StateEstimate
    request: ActionRequest
    pump_decision: SafetyDecision | None = None
    motion_decision: SafetyDecision | None = None
    return_decision: SafetyDecision | None = None
    session_result: F103StepThenPumpResult | None = None
    return_result: Stage2TransmitResult | None = None
    return_error: Stage2TransmitError | None = None
    returned_at: str | None = None
    used_abs_steps: tuple[int, int] = (0, 0)
    device_probe: dict | None = None

    @property
    def receipt(self) -> ExecutionReceipt | None:
        return None if self.session_result is None else self.session_result.receipt

    def to_dict(self) -> dict[str, object]:
        session = self.session_result
        return {
            "status": self.status, "reasons": self.reasons, "state": asdict(self.state),
            "pump_request": asdict(self.request), "pump_decision": None if self.pump_decision is None else asdict(self.pump_decision),
            "motion_decision": None if self.motion_decision is None else asdict(self.motion_decision),
            "return_decision": None if self.return_decision is None else asdict(self.return_decision),
            "session": None if session is None else {
                "serial_opened": session.serial_opened, "pump_called": session.pump_called, "pump_bytes": session.pump_bytes,
                "output_finished": session.output_finished, "stopped": session.stopped,
                "motion_reason": session.motion_reason, "pump_reason": session.pump_reason,
                "motion_result": None if session.motion_result is None else session.motion_result.to_dict(),
                "motion_error": None if session.motion_error is None else session.motion_error.to_dict(),
                "receipt": None if session.receipt is None else asdict(session.receipt),
            },
            "return_result": None if self.return_result is None else self.return_result.to_dict(),
            "return_error": None if self.return_error is None else self.return_error.to_dict(),
            "returned_at": self.returned_at, "used_abs_steps": self.used_abs_steps, "device_probe": self.device_probe,
        }


class HardwareExecutor:
    def __init__(self, *, session: F103SerialSession, link: Stage2SerialLink,
                 controller: STM32SerialController, position_path: str | Path,
                 confirm: Callable[[dict], bool], pump_duration_ms: int = 200) -> None:
        self.session, self.link, self.controller = session, link, controller
        self.position_path = Path(position_path)
        self.confirm = confirm
        self.pump_duration_ms = pump_duration_ms
        self._motion_uncertain = False

    def execute(self, *, geometry: CycleGeometry, observation: Observation,
                measurement: ContaminationMeasurement, preview: dict,
                stage: Callable[[str], None]) -> CycleExecution:
        state = estimate_state(observation, measurement)
        request = propose_pump_in_place(state, FixedActionPolicy(duration_ms=self.pump_duration_ms, version=PUMP_IN_PLACE_RULE_VERSION))
        if request is None:
            raise ValueError("NO_SELECTED_TARGET_FOR_PUMP")
        # 绑定这一轮几何与目标，但仍保留 PUMP_IN_PLACE 的禁止 XY 含义。
        request = replace(request, constraints={**request.constraints, "cycle_target_id": preview["target_id"],
            "outbound_request_id": geometry.outbound_request.request_id, "return_request_id": geometry.return_request.request_id})
        result = CycleExecution("HUMAN", (), state, request)
        outbound = evaluate_motion(geometry.outbound_request)
        returning = evaluate_motion(geometry.return_request)
        result.motion_decision, result.return_decision = outbound, returning
        denied = tuple(outbound.reason_codes if outbound.outcome is SafetyOutcome.DENY else ()) + tuple(returning.reason_codes if returning.outcome is SafetyOutcome.DENY else ())
        if geometry.reasons or denied:
            result.reasons = geometry.reasons + denied
            return result
        if not self.link.armed or not self.controller.arm_pump:
            result.reasons = ("EXPLICIT_MOTION_AND_PUMP_ARM_REQUIRED",)
            return result
        if not self._same_reference(geometry.observation_position, preview):
            result.reasons = ("POSITION_CHANGED_BEFORE_AUTHORIZATION",)
            return result
        stage("AUTHORIZE")
        if not self.confirm({**preview, "geometry": geometry.to_dict(), "pump_request": asdict(request)}):
            result.reasons = ("HUMAN_DECLINED",)
            return result
        result.motion_decision = approve_motion_gate(geometry.outbound_request, outbound, confirmed=True)
        result.return_decision = approve_motion_gate(geometry.return_request, returning, confirmed=True)
        try:
            # 只有运动已获 ALLOW 后才开指定 COM；使用同一会话探测，不另开连接。
            stage("PROBE_DEVICE")
            pong = self.controller.ping()
            status = self.controller.status()
            result.device_probe = {"pong": pong.raw, "status": status.raw,
                "interlock_evidence": "MCV1_STATUS_ONLY; physical wiring remains field acceptance"}
            facts = {**state.device_state, "controller_connected": pong.kind == "PONG" and status.kind == "STATUS",
                "interlock_ok": not status.estop_active and not status.pump_active,
                "e_stop_active": bool(status.estop_active), "pump_active": bool(status.pump_active)}
            result.state = replace(state, device_state=facts)
            self.controller.close()  # 释放 MCV1 租约，物理连接继续由 session 持有。
            pump_human = evaluate_action(result.state, request)
            result.pump_decision = pump_human
            if pump_human.outcome is SafetyOutcome.DENY:
                result.reasons = pump_human.reason_codes
                return result
            result.pump_decision = approve_human_gate(result.state, request, pump_human, confirmed=True)
            self.controller.validate_allow(request, result.pump_decision)
            if not self._same_reference(geometry.observation_position, preview):
                result.reasons = ("POSITION_CHANGED_AFTER_AUTHORIZATION",)
                return result
            stage("MOVE_THEN_PUMP")
            self._motion_uncertain = True
            def before_pump(motion_result):
                # 先记已完成的运动，再检查本轮回程批准是否仍有效；异常不会遗失位置事实。
                if not self._same_reference(geometry.observation_position, preview):
                    raise RuntimeError("POSITION_REFERENCE_CHANGED_DURING_MOTION")
                position = record_completed(self.position_path, motion_result.sent_lines, run_id=observation.task_id)
                result.used_abs_steps = _absolute(motion_result.sent_lines)
                if position.xy() != geometry.execution_position:
                    raise RuntimeError("EXECUTION_POSITION_MISMATCH")
                self._motion_uncertain = False
                _require_current_return_approval(geometry.return_request, result.return_decision)
                stage("PUMP")
            outcome = self.session.run_step_then_pump(self.link, self.controller,
                dispatch=geometry.outbound, motion_request=geometry.outbound_request,
                motion_decision=result.motion_decision, pump_request=request,
                pump_decision=result.pump_decision, close_after=False, before_pump=before_pump)
            result.session_result = outcome
            if outcome.motion_result is not None:
                position = load_position(self.position_path)
                result.used_abs_steps = _absolute(outcome.motion_result.sent_lines)
                self._motion_uncertain = False
                if position.xy() != geometry.execution_position:
                    self._motion_uncertain = True
                    raise RuntimeError("EXECUTION_POSITION_MISMATCH")
            elif outcome.motion_error is not None:
                self._motion_uncertain = outcome.motion_error.motion_attempted
            else:
                self._motion_uncertain = False  # 授权预检拒绝，尚未发 MOVEXY。
            if not outcome.output_finished:
                result.status = "ERROR"
                result.reasons = (outcome.motion_reason or outcome.pump_reason or "PUMP_OUTPUT_NOT_FINISHED",)
                if self._motion_uncertain:
                    mark_unknown(self.position_path, run_id=observation.task_id, reason=result.reasons[0])
                return result
            stage("RETURN")
            if load_position(self.position_path).xy() != geometry.return_request.position_before_steps:
                self._motion_uncertain = True
                raise RuntimeError("POSITION_CHANGED_BEFORE_RETURN")
            self._motion_uncertain = True
            try:
                back = self.link.transmit(geometry.returning, request=geometry.return_request, decision=result.return_decision)
            except Stage2TransmitError as exc:
                result.return_error = exc
                raise
            result.return_result = back
            position = record_completed(self.position_path, back.sent_lines, run_id=observation.task_id)
            result.used_abs_steps = tuple(a + b for a, b in zip(result.used_abs_steps, _absolute(back.sent_lines)))
            if tuple(back.sent_lines) != geometry.returning.lines or position.xy() != geometry.observation_position:
                raise RuntimeError("RETURN_NOT_COMPLETED_AT_ORIGINAL_OVERVIEW")
            self._motion_uncertain = False
            result.returned_at = datetime.now(timezone.utc).isoformat()
            result.status, result.reasons = "RETURNED", ("PUMP_DONE_AND_RETURN_CONFIRMED",)
            return result
        except (Exception, KeyboardInterrupt) as exc:
            result.status = "ERROR"
            result.reasons = ("INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else getattr(exc, "reason_code", str(exc) or type(exc).__name__),)
            self._stop()
            if self._motion_uncertain:
                mark_unknown(self.position_path, run_id=observation.task_id, reason=result.reasons[0])
            self.session.failed = True
            self.session.close()
            return result
        finally:
            self.controller.close()
            if result.status != "RETURNED":
                self.session.close()

    def _stop(self) -> None:
        if self.session.is_open:
            try:
                self.controller.stop()
            except Exception:
                pass
            finally:
                self.controller.close()

    def close(self) -> None:
        try:
            self.link.close()
        finally:
            try:
                self.controller.close()
            finally:
                self.session.close()

    def _same_reference(self, xy, preview) -> bool:
        position = load_position(self.position_path)
        return position.xy() == xy and position.zero_set_at == preview["position_zero_set_at"]


def _absolute(lines: tuple[str, ...]) -> tuple[int, int]:
    deltas = [parse_movexy_line(line) for line in lines]
    return sum(abs(x) for x, _ in deltas), sum(abs(y) for _, y in deltas)


def _require_current_return_approval(request, decision) -> None:
    """不消费回程令牌；最终发送仍由原 require_motion_allow 校验和消费。"""
    if (decision.outcome is not SafetyOutcome.ALLOW or not decision.approval_token
        or decision.action_id != request.request_id or decision.request_digest != motion_request_digest(request)
        or decision.policy_version != request.rule_version):
        raise PermissionError("RETURN_APPROVAL_INVALID")
    expires = datetime.fromisoformat(decision.expires_at or "")
    if expires.tzinfo is None or datetime.now(timezone.utc) >= expires:
        raise PermissionError("RETURN_APPROVAL_EXPIRED_BEFORE_PUMP")
