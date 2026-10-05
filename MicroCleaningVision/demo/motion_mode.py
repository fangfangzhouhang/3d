"""从原 Demo 抽取的 demo.motion_mode；保持既有单帧行为。"""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from microcleaning.contracts import Episode
from microcleaning.contracts import FailureRecord
from microcleaning.contracts import NextRoute
from microcleaning.contracts import Observation
from microcleaning.contracts import SafetyOutcome
from microcleaning.contracts import StateEstimate
from microcleaning.contracts import VerificationResult
from microcleaning.control_system.planning.stage2_axes import Stage2Dispatch
from microcleaning.control_system.planning.stage2_position import Stage2Position
from microcleaning.control_system.planning.stage2_position import load_position
from microcleaning.control_system.planning.stage2_position import mark_unknown
from microcleaning.control_system.planning.stage2_position import record_completed
from microcleaning.control_system.planning.work_frame import MotorCalibration
from microcleaning.control_system.serial.stage2_link import Stage2SerialLink
from microcleaning.control_system.serial.stage2_link import Stage2TransmitError
from microcleaning.control_system.serial.stage2_link import Stage2TransmitResult
from microcleaning.control_system.safety.motion_gate import MotionRequest
from microcleaning.control_system.safety.motion_gate import approve_motion_gate
from microcleaning.control_system.safety.motion_gate import evaluate_motion
from microcleaning.control_system.safety.motion_gate import new_motion_request_id
from microcleaning.control_system.safety.motion_gate import summarize_motion
from demo import DEMO_VERSION, MotionConfirm, SerialFactory
from demo.reporting import _write_json


class Stage2MotionFailed(RuntimeError):
    """步进发送中途失败。记录已经写完，``run_dir`` 指向本次输出目录。"""

    def __init__(self, run_dir: Path, reason_code: str) -> None:
        super().__init__(f"Stage 2 发送失败（{reason_code}），记录见 {run_dir}")
        self.run_dir = run_dir
        self.reason_code = reason_code


@dataclass(frozen=True)
class Stage2MotionOutcome:
    status: str
    request: MotionRequest
    plan: dict
    gate_decision: Any
    final_decision: Any
    human_confirmed: bool
    position_before: Stage2Position
    position_after: Stage2Position
    transmit: Stage2TransmitResult | None = None
    error: Stage2TransmitError | None = None
    receipt_written: bool = False

    def lines_sent(self) -> int:
        if self.transmit is not None:
            return len(self.transmit.sent_lines)
        if self.error is not None:
            return len(self.error.sent_lines)
        return 0

    def to_summary(self) -> dict[str, object]:
        return {
            "status": self.status,
            "request_id": self.request.request_id,
            "gate_decision": asdict(self.gate_decision),
            "final_decision": asdict(self.final_decision),
            "human_confirmed": self.human_confirmed,
            "plan": self.plan,
            "position_before": self.position_before.to_dict(),
            "position_after": self.position_after.to_dict(),
            "lines_sent": self.lines_sent(),
            "error": None if self.error is None else self.error.to_dict(),
            "intent_file": "stage2_intent.json",
            "receipt_file": "stage2_receipt.json" if self.receipt_written else None,
        }


def _stage2_move(
    *,
    run_id: str,
    run_dir: Path,
    dispatch: Stage2Dispatch,
    start_reference: str,
    calibration: MotorCalibration | None,
    position_path: Path,
    motion_confirm: MotionConfirm | None,
    arm_stage2_xy: bool,
    serial_port: str | None,
    baudrate: int,
    serial_timeout: float,
    serial_factory: SerialFactory | None,
) -> Stage2MotionOutcome:
    """运动关卡 → 人确认 → 武装 → 发送。打开 COM 前先落盘意图，发送后无论成败都落盘回执。"""

    position = load_position(position_path)
    request = MotionRequest(
        request_id=new_motion_request_id(),
        task_id=run_id,
        lines=dispatch.lines,
        position_before_steps=position.xy(),
        start_reference=start_reference,
        calibration_ref=None if calibration is None else calibration.ref,
        calibration_sha256=None if calibration is None else calibration.sha256,
        calibration_warnings=() if calibration is None else calibration.warnings,
        dispatch_truncated=dispatch.truncated,
    )
    plan = summarize_motion(request).to_dict()
    plan["path_overlay"] = str(run_dir / "path_overlay.png")
    gate_decision = evaluate_motion(request)
    decision = gate_decision
    human_confirmed = False
    if gate_decision.outcome is SafetyOutcome.DENY:
        status = "denied"
    elif not arm_stage2_xy:
        status = "not_armed"
    elif motion_confirm is None:
        status = "human_pending"
    else:
        human_confirmed = bool(motion_confirm(request, plan))
        if human_confirmed:
            decision = approve_motion_gate(request, gate_decision, confirmed=True)
            status = "approved" if decision.outcome is SafetyOutcome.ALLOW else "denied"
        else:
            status = "human_declined"

    _write_json(
        run_dir / "stage2_intent.json",
        {
            "status": status,
            "written_at": datetime.now(timezone.utc).isoformat(),
            "request": asdict(request),
            "plan": plan,
            "gate_decision": asdict(gate_decision),
            "final_decision": asdict(decision),
            "human_confirmed": human_confirmed,
            "armed": arm_stage2_xy,
            "serial_port": serial_port,
            "calibration": None if calibration is None else calibration.to_dict(),
            "position_before": position.to_dict(),
            "evidence_boundary": "发送意图；写于打开步进串口之前。不是执行回执",
        },
    )
    if status != "approved":
        return Stage2MotionOutcome(status, request, plan, gate_decision, decision, human_confirmed, position, position)

    result: Stage2TransmitResult | None = None
    error: Stage2TransmitError | None = None
    position_after = position
    link = Stage2SerialLink(
        port=serial_port,
        baudrate=baudrate,
        timeout=serial_timeout,
        armed=True,
        serial_factory=serial_factory,
    )
    try:
        result = link.transmit(dispatch, request=request, decision=decision)
        position_after = record_completed(position_path, result.sent_lines, run_id=run_id)
        status = "sent"
    except Stage2TransmitError as exc:
        error = exc
        status = "failed"
        if exc.motion_attempted:
            position_after = mark_unknown(
                position_path,
                run_id=run_id,
                reason=f"发送失败 {exc.reason_code}：位置不可信，人重新对位后执行 --stage2-set-zero",
            )
    except PermissionError:
        # 链路在打开 COM 前拒绝了审批：没有发出任何东西，位置不变。
        status = "refused_by_link"
        raise
    except BaseException:
        status = "aborted"
        try:
            position_after = mark_unknown(position_path, run_id=run_id, reason="发送过程意外中断：位置不可信")
        except Exception:
            pass
        raise
    finally:
        _write_json(
            run_dir / "stage2_receipt.json",
            {
                "status": status,
                "written_at": datetime.now(timezone.utc).isoformat(),
                "request_id": request.request_id,
                "transmit": None if result is None else result.to_dict(),
                "error": None if error is None else error.to_dict(),
                "position_before": position.to_dict(),
                "position_after": position_after.to_dict(),
                "evidence_boundary": "固件回执只证明计数与握手，不证明台面位移、对准或清洗",
            },
        )
    return Stage2MotionOutcome(
        status,
        request,
        plan,
        gate_decision,
        decision,
        human_confirmed,
        position,
        position_after,
        transmit=result,
        error=error,
        receipt_written=True,
    )


def _stage2_episode(
    *,
    run_id: str,
    observation: Observation,
    state: StateEstimate,
    outcome: Stage2MotionOutcome,
) -> tuple[Episode, str]:
    mode_name, route, failure_stage = _STAGE2_EPISODE_ROUTES[outcome.status]
    if outcome.status == "sent":
        reason_codes: tuple[str, ...] = (
            "STAGE2_MOTION_SENT",
            "NO_PUMP_SENT",
            "RECEIPT_IS_NOT_POSITION_PROOF",
            "POST_OBSERVATION_REQUIRED",
        )
        evidence = "人工确认后向 Stage 2 发送 MOVEXY；回执只证明固件计数，不证明台面位移、对准或清洗；未发 PUMP"
    elif outcome.status == "failed":
        stop_code = "STAGE2_STOP_CONFIRMED" if outcome.error.stopped else "STAGE2_STOP_UNCONFIRMED"
        reason_codes = (outcome.error.reason_code, stop_code, "POSITION_UNKNOWN_AFTER_FAILURE", "NO_PUMP_SENT")
        evidence = "Stage 2 发送中途失败，已尝试 STOP；位置不可信；未发 PUMP"
    else:
        reason_codes = outcome.final_decision.reason_codes + ("NO_MOTION_SENT", "NO_PUMP_SENT")
        if outcome.status == "not_armed":
            reason_codes = ("STAGE2_NOT_ARMED",) + reason_codes
        evidence = f"Stage 2 运动未发送（{outcome.status}）；未打开步进串口；未发 PUMP"

    failures = []
    if failure_stage is not None:
        failures.append(
            FailureRecord(
                f"failure_{uuid4().hex[:12]}",
                run_id,
                failure_stage,
                "warning" if outcome.status == "failed" else "info",
                reason_codes,
                True,
                _STAGE2_RECOVERY[outcome.status],
            )
        )
    episode = Episode(
        episode_id=f"episode_{uuid4().hex[:12]}",
        task_id=run_id,
        mode=mode_name,
        protocol_version=DEMO_VERSION,
        observation_pre=observation,
        state=state,
        action_request=None,
        safety_decision=outcome.final_decision,
        execution_receipt=None,
        observation_post=None,
        verification=VerificationResult(
            run_id,
            observation.observation_id,
            None,
            None,
            None,
            False,
            route,
            reason_codes,
        ),
        failures=failures,
    )
    return episode, evidence


def _cli_motion_confirm(request: MotionRequest, plan: dict) -> bool:
    """命令行人工关卡：打印计划，要求人输入 YES。没有终端输入时视为未确认。"""

    abs_x, abs_y = plan["abs_steps"]
    print("========== Stage 2 运动确认 ==========")
    print(f"将发送 {plan['line_count']} 条 MOVEXY；X 累计 |步|={abs_x}，Y 累计 |步|={abs_y}")
    print(
        f"位置账本（相对人工零点，不是绝对坐标）：{plan['position_before']} → {plan['position_after']}；"
        f"软限位 {plan['soft_min_steps']}～{plan['soft_max_steps']}"
    )
    print(f"标定：{request.calibration_ref}；起点：{request.start_reference}")
    print(f"路径叠加图：{plan['path_overlay']}")
    print("人必须在电机旁，手能立刻断 24V。MOVEXY 两轴同频，不是直线插补。")
    try:
        answer = input("确认发送请输入 YES：")
    except EOFError:
        return False
    return answer.strip() == "YES"


_STAGE2_EPISODE_ROUTES = {
    "denied": ("stage2_motion_denied", NextRoute.STOP, "safety"),
    "not_armed": ("stage2_motion_not_armed", NextRoute.HUMAN, "control"),
    "human_pending": ("stage2_motion_human_pending", NextRoute.HUMAN, "safety"),
    "human_declined": ("stage2_motion_human_declined", NextRoute.HUMAN, "safety"),
    "sent": ("stage2_motion_sent", NextRoute.HUMAN, None),
    "failed": ("stage2_motion_failed", NextRoute.STOP, "execution"),
}


_STAGE2_RECOVERY = {
    "denied": "看 stage2_intent.json 的 reason_codes：位置未知先人工对位后 --stage2-set-zero；缺标定先跑 scripts/calibrate_motor_mm_per_px.py",
    "not_armed": "人在电机旁、24V 已接时再加 --arm-stage2-xy --serial-port COMx",
    "human_pending": "运动需要人确认；命令行会要求输入 YES",
    "human_declined": "人没有确认，未打开步进串口",
    "failed": "检查 stage2_receipt.json；STOP 未确认时立即手断 24V；位置已标记未知，需重新对位后 --stage2-set-zero",
}
