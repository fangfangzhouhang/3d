"""FakeSerial（假串口）控制器（成员 C 的控制仿真模块）。

它模拟 ACK（确认帧）、超时和回执，但绝不打开 COM 口。ACK 是“控制器收到并处理
命令的确认”，不是“清洗成功”的证明。
"""

from __future__ import annotations

from datetime import datetime, timezone

from microcleaning.ports import ControllerPort
from microcleaning.contracts import ActionRequest, ExecutionReceipt, SafetyDecision, SafetyOutcome
from microcleaning.control_system.governor import ReplaySafetyLimits, request_digest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FakeSerialController(ControllerPort):
    """只执行一次性、未过期、与原申请绑定的回放审批。"""

    def __init__(self, *, simulate_timeout: bool = False) -> None:
        self.simulate_timeout = simulate_timeout
        self._used_tokens: set[str] = set()

    def execute(self, request: ActionRequest, decision: SafetyDecision) -> ExecutionReceipt:
        if decision.outcome is not SafetyOutcome.ALLOW or not decision.approval_token:
            raise PermissionError("FakeSerial 只接受 ALLOW 审批")
        if decision.action_id != request.action_id or decision.state_id != request.state_id:
            raise PermissionError("审批与动作或状态不匹配")
        if decision.request_digest != request_digest(request):
            raise PermissionError("动作在审批后被修改")
        if decision.policy_version != ReplaySafetyLimits().version:
            raise PermissionError("无法识别安全策略版本")
        if not decision.expires_at or datetime.fromisoformat(decision.expires_at) <= datetime.now(timezone.utc):
            raise PermissionError("审批已经过期")
        if decision.approval_token in self._used_tokens:
            raise PermissionError("审批令牌不能重复使用")
        self._used_tokens.add(decision.approval_token)
        started = _now()
        if self.simulate_timeout:
            return ExecutionReceipt(request.action_id, "fake_serial", started, _now(), None, 0, 0.0, "UNKNOWN", "SIMULATED", False, "ACK_TIMEOUT")
        return ExecutionReceipt(
            request.action_id,
            "fake_serial",
            started,
            _now(),
            request.target_centroid_mm,
            request.duration_ms,
            request.pressure,
            "ACK_RECEIVED",
            "SIMULATED",
            True,
        )
