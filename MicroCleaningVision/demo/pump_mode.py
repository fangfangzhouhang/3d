"""从原 Demo 抽取的 demo.pump_mode；保持既有单帧行为。"""

from __future__ import annotations

from uuid import uuid4
from microcleaning.contracts import Episode
from microcleaning.contracts import FailureRecord
from microcleaning.contracts import NextRoute
from microcleaning.contracts import Observation
from microcleaning.contracts import SafetyOutcome
from microcleaning.contracts import StateEstimate
from microcleaning.contracts import VerificationResult
from microcleaning.control_system.safety.fixed_rule import FixedActionPolicy
from microcleaning.control_system.safety.fixed_rule import PUMP_IN_PLACE_RULE_VERSION
from microcleaning.control_system.safety.fixed_rule import propose_pump_in_place
from microcleaning.control_system.safety.governor import approve_human_gate
from microcleaning.control_system.safety.governor import evaluate_action
from microcleaning.control_system.serial.fake_serial import FakeSerialController
from microcleaning.control_system.serial.stm32_protocol import encode_ping
from microcleaning.control_system.serial.stm32_protocol import encode_status
from microcleaning.control_system.serial.stm32_serial import STM32SerialController
from demo import DEMO_VERSION, SerialFactory
from demo.reporting import _analysis_episode


def _arm_pump_episode(
    *,
    run_id: str,
    observation: Observation,
    state: StateEstimate,
    confirm_pump: bool,
    controller_kind: str,
    arm_pump: bool,
    serial_port: str | None,
    baudrate: int,
    serial_timeout: float,
    serial_factory: SerialFactory | None,
    pump_duration_ms: int,
) -> tuple[Episode, str]:
    request = propose_pump_in_place(
        state,
        FixedActionPolicy(duration_ms=pump_duration_ms, version=PUMP_IN_PLACE_RULE_VERSION),
    )
    if request is None:
        episode = _analysis_episode(
            run_id=run_id,
            observation=observation,
            state=state,
            mode_name="pump_in_place_no_target",
            reason_codes=("NO_TARGET", "NO_PUMP_SENT"),
            recovery="视野内没有可喷目标，未申请定点短喷",
        )
        return episode, "未发现目标，未申请定点短喷，未发送PUMP"

    decision = evaluate_action(state, request)
    if confirm_pump:
        try:
            decision = approve_human_gate(state, request, decision, confirmed=True)
        except PermissionError as exc:
            episode = Episode(
                episode_id=f"episode_{uuid4().hex[:12]}",
                task_id=run_id,
                mode="pump_in_place_human_gate_refused",
                protocol_version=DEMO_VERSION,
                observation_pre=observation,
                state=state,
                action_request=request,
                safety_decision=decision,
                execution_receipt=None,
                observation_post=None,
                verification=VerificationResult(
                    run_id,
                    observation.observation_id,
                    None,
                    None,
                    None,
                    False,
                    NextRoute.STOP,
                    ("HUMAN_GATE_REFUSED",),
                ),
                failures=[
                    FailureRecord(
                        f"failure_{uuid4().hex[:12]}",
                        run_id,
                        "safety",
                        "warning",
                        ("HUMAN_GATE_REFUSED",),
                        True,
                        str(exc),
                    )
                ],
            )
            return episode, f"人工关卡拒绝改写决策：{exc}"

    if decision.outcome is not SafetyOutcome.ALLOW:
        route = NextRoute.HUMAN if decision.outcome is SafetyOutcome.HUMAN else NextRoute.STOP
        episode = Episode(
            episode_id=f"episode_{uuid4().hex[:12]}",
            task_id=run_id,
            mode="pump_in_place_human_gate",
            protocol_version=DEMO_VERSION,
            observation_pre=observation,
            state=state,
            action_request=request,
            safety_decision=decision,
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
                decision.reason_codes,
            ),
            failures=[
                FailureRecord(
                    f"failure_{uuid4().hex[:12]}",
                    run_id,
                    "safety",
                    "info",
                    decision.reason_codes,
                    True,
                    "未过人工关卡或被拒绝，未发送PUMP",
                )
            ],
        )
        evidence = (
            "已申请定点短喷但未过人工关卡；未发送PUMP"
            if decision.outcome is SafetyOutcome.HUMAN
            else "定点短喷申请被拒绝；未发送PUMP"
        )
        return episode, evidence

    receipt = None
    execute_error: str | None = None
    try:
        if controller_kind == "fake":
            receipt = FakeSerialController().execute(request, decision)
            evidence_boundary = "人工确认后的FakeSerial定点短喷回放；未打开COM口，不代表真实喷洗"
            episode_mode = "pump_in_place_fake_serial"
        else:
            controller = STM32SerialController(
                port=serial_port,
                baudrate=baudrate,
                timeout=serial_timeout,
                arm_pump=arm_pump,
                serial_factory=serial_factory,
            )
            try:
                receipt = controller.execute(request, decision)
            finally:
                controller.close()
            evidence_boundary = (
                "人工确认且--arm-pump后向STM32发送限时PUMP；未接12V时不能写成真实喷洗有效"
                if arm_pump
                else "审批后控制器未武装；拒绝发送PUMP"
            )
            episode_mode = "pump_in_place_stm32"
    except PermissionError as exc:
        execute_error = str(exc)
        evidence_boundary = "控制器拒绝执行（未武装、非ALLOW或令牌无效）；未发送PUMP"
        episode_mode = "pump_in_place_refused"

    failures = []
    if execute_error:
        failures.append(
            FailureRecord(
                f"failure_{uuid4().hex[:12]}",
                run_id,
                "execution",
                "warning",
                ("PUMP_REFUSED",),
                True,
                execute_error,
            )
        )
    elif receipt is not None and not receipt.success:
        failures.append(
            FailureRecord(
                f"failure_{uuid4().hex[:12]}",
                run_id,
                "execution",
                "warning",
                (receipt.error_code or "EXECUTION_FAILED",),
                True,
                "检查串口回执；ACK不等于清洗成功",
            )
        )

    next_route = NextRoute.HUMAN
    reason_codes: tuple[str, ...] = ("POST_OBSERVATION_REQUIRED",)
    if execute_error:
        next_route = NextRoute.STOP
        reason_codes = ("PUMP_REFUSED",)
    elif receipt is not None and receipt.success:
        next_route = NextRoute.HUMAN
        reason_codes = ("POST_OBSERVATION_REQUIRED", "RECEIPT_IS_NOT_CLEANING_PROOF")

    episode = Episode(
        episode_id=f"episode_{uuid4().hex[:12]}",
        task_id=run_id,
        mode=episode_mode,
        protocol_version=DEMO_VERSION,
        observation_pre=observation,
        state=state,
        action_request=request,
        safety_decision=decision,
        execution_receipt=receipt,
        observation_post=None,
        verification=VerificationResult(
            run_id,
            observation.observation_id,
            None,
            None,
            None,
            False,
            next_route,
            reason_codes,
        ),
        failures=failures,
    )
    return episode, evidence_boundary


def _controller_device_facts(
    *,
    controller_kind: str,
    serial_port: str | None,
    baudrate: int,
    serial_timeout: float,
    serial_factory: SerialFactory | None,
) -> tuple[dict[str, object] | None, bool, bool]:
    if controller_kind == "fake":
        return {"opened": False, "mode": "fake_serial", "pump_sent": False}, True, False
    return _probe_serial(
        serial_port=serial_port,
        baudrate=baudrate,
        serial_timeout=serial_timeout,
        serial_factory=serial_factory,
    )


def _probe_serial(
    *,
    serial_port: str | None,
    baudrate: int,
    serial_timeout: float,
    serial_factory: SerialFactory | None,
) -> tuple[dict[str, object], bool, bool]:
    preview = {
        "ping": encode_ping().decode("ascii").rstrip(),
        "status": encode_status().decode("ascii").rstrip(),
        "pump_sent": False,
    }
    if serial_factory is None and not serial_port:
        return (
            {
                **preview,
                "opened": False,
                "reason": "SERIAL_NOT_OPENED",
            },
            False,
            False,
        )
    controller = STM32SerialController(
        port=serial_port,
        baudrate=baudrate,
        timeout=serial_timeout,
        arm_pump=False,
        serial_factory=serial_factory,
    )
    try:
        pong = controller.ping()
        status = controller.status()
        return (
            {
                **preview,
                "opened": True,
                "pong": pong.raw,
                "status": status.raw,
                "estop_active": status.estop_active,
                "pump_active": status.pump_active,
            },
            True,
            bool(status.estop_active),
        )
    except Exception as exc:
        return (
            {
                **preview,
                "opened": False,
                "reason": type(exc).__name__,
                "detail": str(exc),
            },
            False,
            False,
        )
    finally:
        controller.close()
