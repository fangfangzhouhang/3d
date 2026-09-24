"""软件回放闭环编排（成员 C 的目标规划与控制仿真模块）。

输入已经由 A/B 生成的前后 Observation 与 StateEstimate，依次完成动作申请、安全审批、
FakeSerial 回执和视觉复检。它不读取 COM 口，不声称发生了真实清洗。
"""

from __future__ import annotations

from uuid import uuid4

from microcleaning.contracts import Episode, FailureRecord, NextRoute, Observation, SafetyOutcome, StateEstimate, VerificationResult
from microcleaning.control_system.fixed_rule import propose_action
from microcleaning.control_system.fake_serial import FakeSerialController
from microcleaning.control_system.governor import evaluate_action
from microcleaning.vision.verification import verify_area_change


class ReplayMCLRunner:
    """把三个成员的模块串成一次可测试的软件回放。"""

    def __init__(self, controller: FakeSerialController | None = None) -> None:
        self.controller = controller or FakeSerialController()

    def run(
        self,
        *,
        pre: Observation,
        state: StateEstimate,
        post: Observation | None,
        post_area_px: float | None,
        action_target_mm: tuple[float, float] | None = None,
    ) -> Episode:
        if state.task_id != pre.task_id or state.observation_id != pre.observation_id:
            raise ValueError("StateEstimate 必须来自本次动作前 Observation")
        if not state.device_state.get("observation_quality_ok"):
            verification = VerificationResult(
                pre.task_id,
                pre.observation_id,
                None,
                None,
                None,
                False,
                NextRoute.HUMAN,
                ("OBSERVATION_LOW_QUALITY",),
            )
            failure = FailureRecord(
                _uid("failure"),
                pre.task_id,
                "imaging",
                "warning",
                verification.reason_codes,
                True,
                "replace or recapture the input image",
            )
            return Episode(_uid("episode"), pre.task_id, "software_replay", "replay-v0", pre, state, None, None, None, None, verification, [failure])
        request = propose_action(state, target_centroid_mm=action_target_mm)
        if request is None:
            verification = VerificationResult(pre.task_id, pre.observation_id, getattr(post, "observation_id", None), post_area_px, None, False, NextRoute.STOP, ("NO_TARGET",))
            return Episode(_uid("episode"), pre.task_id, "software_replay", "replay-v0", pre, state, None, None, None, post, verification)
        decision = evaluate_action(state, request)
        if decision.outcome is not SafetyOutcome.ALLOW:
            route = NextRoute.HUMAN if decision.outcome is SafetyOutcome.HUMAN else NextRoute.STOP
            verification = VerificationResult(pre.task_id, pre.observation_id, None, None, None, False, route, decision.reason_codes)
            failure = FailureRecord(_uid("failure"), pre.task_id, "safety", "warning", decision.reason_codes, True, "review input or state")
            return Episode(_uid("episode"), pre.task_id, "software_replay", "replay-v0", pre, state, request, decision, None, None, verification, [failure])
        receipt = self.controller.execute(request, decision)
        verification = verify_area_change(
            task_id=pre.task_id,
            pre=pre,
            post=post,
            pre_area_px=state.target_area_px,
            post_area_px=post_area_px,
            receipt=receipt,
        )
        failures: list[FailureRecord] = []
        if not receipt.success or verification.next_route is NextRoute.HUMAN:
            failures.append(FailureRecord(_uid("failure"), pre.task_id, "execution_or_verification", "warning", verification.reason_codes, True, "inspect replay evidence"))
        return Episode(_uid("episode"), pre.task_id, "software_replay", "replay-v0", pre, state, request, decision, receipt, post, verification, failures)


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"
