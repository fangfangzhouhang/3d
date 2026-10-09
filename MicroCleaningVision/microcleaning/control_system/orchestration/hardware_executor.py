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
from microcleaning.control_system.safety.fixed_rule import DEFAULT_IN_PLACE_DURATION_MS, FixedActionPolicy, PUMP_IN_PLACE_RULE_VERSION, propose_pump_in_place
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
    outbound_result: Stage2TransmitResult | None = None
    stop_evidence: dict | None = None

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
            "outbound_result": None if self.outbound_result is None else self.outbound_result.to_dict(),
            "stop_evidence": self.stop_evidence,
        }


class HardwareExecutor:
    def __init__(self, *, session: F103SerialSession, link: Stage2SerialLink,
                 controller: STM32SerialController, position_path: str | Path,
                 confirm: Callable[[dict], bool], pump_duration_ms: int = DEFAULT_IN_PLACE_DURATION_MS) -> None:
        self.session, self.link, self.controller = session, link, controller
        self.position_path = Path(position_path)
        self.confirm = confirm
        self.pump_duration_ms = pump_duration_ms
        self._motion_uncertain = False
        self.cancellation = getattr(session, "cancellation", None)

    def _check_cancel(self) -> None:
        if self.cancellation is not None:
            self.cancellation.check()

    def execute(self, *, geometry: CycleGeometry, observation: Observation,
                measurement: ContaminationMeasurement, preview: dict,
                stage: Callable[[str], None]) -> CycleExecution:
        self._check_cancel()
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
        if not self.link.armed:
            result.reasons = ("EXPLICIT_MOTION_ARM_REQUIRED",)
            return result
        if not self._same_reference(geometry.observation_position, preview):
            result.reasons = ("POSITION_CHANGED_BEFORE_AUTHORIZATION",)
            return result
        if not self.controller.arm_pump:
            self._run_motion_only(result, geometry, observation, preview, stage, request)
            return result
        self._run_spray_cycle(result, geometry, observation, preview, stage, request, outbound, returning)
        return result

    def _run_spray_cycle(self, result, geometry, observation, preview, stage, request, outbound, returning) -> None:
        """去程、针头重合、短喷、回原位各自等人确认。重合确认之前不发 PUMP。"""

        stage("AUTHORIZE")
        facts = {**preview, "geometry": geometry.to_dict(), "include_pump": True, "pump_request": asdict(request)}
        if not self.confirm({**facts, "phase": "move"}):
            result.reasons = ("HUMAN_DECLINED",)
            return
        result.motion_decision = approve_motion_gate(geometry.outbound_request, outbound, confirmed=True)
        try:
            stage("PROBE_DEVICE")
            self._probe(result)
            looked = evaluate_action(result.state, request)
            if looked.outcome is SafetyOutcome.DENY:
                result.pump_decision = looked
                result.reasons = looked.reason_codes
                return
            if not self._same_reference(geometry.observation_position, preview):
                result.reasons = ("POSITION_CHANGED_AFTER_AUTHORIZATION",)
                return
            stage("MOVE")
            self._motion_uncertain = True
            sent = self.link.transmit(geometry.outbound, request=geometry.outbound_request, decision=result.motion_decision)
            result.outbound_result = sent
            position = record_completed(self.position_path, sent.sent_lines, run_id=observation.task_id)
            result.used_abs_steps = _absolute(sent.sent_lines)
            if tuple(sent.sent_lines) != tuple(geometry.outbound.lines) or position.xy() != geometry.execution_position:
                self._motion_uncertain = True
                raise RuntimeError("EXECUTION_POSITION_MISMATCH")
            self._motion_uncertain = False
            stage("ALIGN_AT_NOZZLE")
            if not self.confirm({**facts, "phase": "align"}):
                self._return_without_spray(result, geometry, observation, preview, stage, facts, returning)
                return
            stage("PROBE_DEVICE")
            self._probe(result)
            pump_human = evaluate_action(result.state, request)
            result.pump_decision = pump_human
            if pump_human.outcome is SafetyOutcome.DENY:
                result.reasons = pump_human.reason_codes
                return
            result.pump_decision = approve_human_gate(result.state, request, pump_human, confirmed=True)
            self.controller.validate_allow(request, result.pump_decision)
            result.return_decision = approve_motion_gate(geometry.return_request, returning, confirmed=True)
            _require_current_return_approval(geometry.return_request, result.return_decision)
            stage("PUMP")
            # 即使等待 ACK/DONE 中取消，仍保留已完成去程及调用事实；TX/ACK 由 serial.json 证明。
            result.session_result = F103StepThenPumpResult(self.session.is_open, True, self.session.pump_bytes,
                False, False, motion_result=sent, pump_reason="OUTPUT_NOT_CONFIRMED")
            receipt = self.controller.execute(request, result.pump_decision)
            output_finished = bool(receipt.success and receipt.controller_state == "DONE")
            stopped = False
            if not output_finished:
                try:
                    done = self.controller.stop()
                    stopped = done.kind == "DONE" and done.action_id == "STOP"
                except Exception:
                    stopped = False
            result.session_result = F103StepThenPumpResult(
                serial_opened=self.session.is_open, pump_called=True, pump_bytes=self.session.pump_bytes,
                output_finished=output_finished, stopped=stopped, receipt=receipt, motion_result=sent,
                pump_reason=None if output_finished else (receipt.error_code or "PUMP_OUTPUT_NOT_FINISHED"),
            )
            self.controller.close()
            if not output_finished:
                result.status = "ERROR"
                result.reasons = (result.session_result.pump_reason or "PUMP_OUTPUT_NOT_FINISHED",)
                return
            stage("AUTHORIZE_RETURN")
            if not self.confirm({**facts, "phase": "return"}):
                result.status = "HUMAN"
                result.reasons = ("HUMAN_DECLINED_RETURN",)
                return
            self._ensure_return_token(result, geometry, returning)
            stage("RETURN")
            self._transmit_return(result, geometry, observation)
            result.status, result.reasons = "RETURNED", ("PUMP_DONE_AND_RETURN_CONFIRMED",)
        except (Exception, KeyboardInterrupt) as exc:
            if isinstance(exc, Stage2TransmitError):
                self._motion_uncertain = exc.motion_attempted
            result.status = "CANCELLED" if self.cancellation is not None and self.cancellation.event.is_set() else "ERROR"
            result.reasons = ("INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else getattr(exc, "reason_code", str(exc) or type(exc).__name__),)
            result.stop_evidence = self._stop()
            if isinstance(exc, Stage2TransmitError):
                result.stop_evidence["motion_stop_confirmed"] = exc.stopped
            if self._motion_uncertain:
                mark_unknown(self.position_path, run_id=observation.task_id, reason=result.reasons[0])
            self.session.failed = True
            self.session.close()
        finally:
            self.controller.close()
            if result.status != "RETURNED":
                self.session.close()

    def _return_without_spray(self, result, geometry, observation, preview, stage, facts, returning) -> None:
        """人没有确认重合时不喷水。仍可单独确认是否回到原观察位。"""

        result.reasons = ("HUMAN_DECLINED_SPRAY",)
        if not self.confirm({**facts, "phase": "return_without_spray"}):
            result.status = "HUMAN"
            result.reasons = ("HUMAN_DECLINED_SPRAY", "HUMAN_DECLINED_RETURN")
            return
        stage("RETURN")
        self._ensure_return_token(result, geometry, returning)
        self._transmit_return(result, geometry, observation)
        result.status = "HUMAN"
        result.reasons = ("HUMAN_DECLINED_SPRAY", "RETURNED_WITHOUT_SPRAY")

    def _ensure_return_token(self, result, geometry, evaluated) -> None:
        decision = result.return_decision
        if decision is not None and decision.outcome is SafetyOutcome.ALLOW:
            try:
                _require_current_return_approval(geometry.return_request, decision)
                return
            except PermissionError:
                pass
        result.return_decision = approve_motion_gate(geometry.return_request, evaluated, confirmed=True)

    def _transmit_return(self, result, geometry, observation) -> None:
        if load_position(self.position_path).xy() != geometry.return_request.position_before_steps:
            self._motion_uncertain = True
            raise RuntimeError("POSITION_CHANGED_BEFORE_RETURN")
        self._motion_uncertain = True
        try:
            back = self.link.transmit(geometry.returning, request=geometry.return_request, decision=result.return_decision)
        except Stage2TransmitError as exc:
            result.return_error = exc
            self._motion_uncertain = exc.motion_attempted
            raise
        result.return_result = back
        position = record_completed(self.position_path, back.sent_lines, run_id=observation.task_id)
        result.used_abs_steps = tuple(a + b for a, b in zip(result.used_abs_steps, _absolute(back.sent_lines)))
        if tuple(back.sent_lines) != geometry.returning.lines or position.xy() != geometry.observation_position:
            raise RuntimeError("RETURN_NOT_COMPLETED_AT_ORIGINAL_OVERVIEW")
        self._motion_uncertain = False
        result.returned_at = datetime.now(timezone.utc).isoformat()

    def _probe(self, result) -> None:
        pong = self.controller.ping()
        status = self.controller.status()
        result.device_probe = {"pong": pong.raw, "status": status.raw,
            "interlock_evidence": "MCV1_STATUS_ONLY; physical wiring remains field acceptance"}
        facts = {**result.state.device_state, "controller_connected": pong.kind == "PONG" and status.kind == "STATUS",
            "interlock_ok": not status.estop_active and not status.pump_active,
            "e_stop_active": bool(status.estop_active), "pump_active": bool(status.pump_active)}
        result.state = replace(result.state, device_state=facts)
        self.controller.close()

    def _run_motion_only(self, result, geometry, observation, preview, stage, request) -> None:
        """只走去程和回程。没有喷水武装时不发 PUMP，也不把脉冲计数写成清洗结果。"""

        stage("AUTHORIZE")
        if not self.confirm({**preview, "phase": "move", "geometry": geometry.to_dict(), "include_pump": False, "pump_request": asdict(request)}):
            result.reasons = ("HUMAN_DECLINED",)
            return
        result.motion_decision = approve_motion_gate(geometry.outbound_request, result.motion_decision, confirmed=True)
        result.return_decision = approve_motion_gate(geometry.return_request, result.return_decision, confirmed=True)
        try:
            stage("MOVE")
            self._motion_uncertain = True
            sent = self.link.transmit(geometry.outbound, request=geometry.outbound_request, decision=result.motion_decision)
            result.outbound_result = sent
            position = record_completed(self.position_path, sent.sent_lines, run_id=observation.task_id)
            result.used_abs_steps = _absolute(sent.sent_lines)
            if tuple(sent.sent_lines) != tuple(geometry.outbound.lines) or position.xy() != geometry.execution_position:
                self._motion_uncertain = True
                raise RuntimeError("EXECUTION_POSITION_MISMATCH")
            self._motion_uncertain = False
            stage("RETURN")
            self._motion_uncertain = True
            back = self.link.transmit(geometry.returning, request=geometry.return_request, decision=result.return_decision)
            result.return_result = back
            position = record_completed(self.position_path, back.sent_lines, run_id=observation.task_id)
            result.used_abs_steps = tuple(a + b for a, b in zip(result.used_abs_steps, _absolute(back.sent_lines)))
            if tuple(back.sent_lines) != tuple(geometry.returning.lines) or position.xy() != geometry.observation_position:
                raise RuntimeError("RETURN_NOT_COMPLETED_AT_ORIGINAL_OVERVIEW")
            self._motion_uncertain = False
            result.returned_at = datetime.now(timezone.utc).isoformat()
            result.status, result.reasons = "MOVED", ("MOTION_COMPLETED_NO_PUMP",)
        except (Exception, KeyboardInterrupt) as exc:
            result.status = "CANCELLED" if self.cancellation is not None and self.cancellation.event.is_set() else "ERROR"
            result.reasons = ("INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else getattr(exc, "reason_code", str(exc) or type(exc).__name__),)
            result.stop_evidence = self._stop()
            if isinstance(exc, Stage2TransmitError):
                result.stop_evidence["motion_stop_confirmed"] = exc.stopped
            if self._motion_uncertain:
                mark_unknown(self.position_path, run_id=observation.task_id, reason=result.reasons[0])
            self.session.failed = True
            self.session.close()
        finally:
            if result.status != "MOVED":
                self.session.close()

    def _stop(self) -> dict:
        evidence = {"requested_at": datetime.now(timezone.utc).isoformat(), "pump_stop_confirmed": False,
                    "serial_was_open": self.session.is_open}
        if self.session.is_open:
            try:
                done = self.controller.stop()
                evidence.update(pump_stop_confirmed=done.kind == "DONE" and done.action_id == "STOP",
                                replies=list(self.controller.stop_replies))
            except Exception as exc:
                evidence["error"] = str(exc)
            finally:
                self.controller.close()
        return evidence

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
