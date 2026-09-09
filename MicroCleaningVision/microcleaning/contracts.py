"""MicroCleaningVision 最小闭环的版本化接口契约。

这些对象承载“申请”和“证据”，不是硬件命令。只有执行适配器才能把已批准的
``ActionRequest`` 翻译为确定性控制器命令；当前提供的适配器仅用于模拟。

中文阅读提示：Observation 是观测记录，StateEstimate 是状态判断，
ActionRequest 是动作申请单，SafetyDecision 是安全审批，
ExecutionReceipt 是执行回执，VerificationResult 是结果复查。
代码名是稳定接口，不能为了中文化而重命名。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


INTERFACE_VERSION = "mcl-v0.1"


class SafetyOutcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    HUMAN = "HUMAN"


class NextRoute(str, Enum):
    STOP = "STOP"
    RETRY = "RETRY"
    HUMAN = "HUMAN"


@dataclass(frozen=True)
class Observation:
    observation_id: str
    task_id: str
    timestamp: str
    frame_id: str
    raw_image_ref: str
    focus_quality: float
    illumination_quality: float
    confidence: float
    quality_flags: tuple[str, ...]
    software_version: str


@dataclass(frozen=True)
class StateEstimate:
    state_id: str
    task_id: str
    observation_id: str
    target_centroid_mm: tuple[float, float] | None
    target_area_px: float
    coordinate_frame: str
    uncertainty_mm: float | None
    device_state: dict[str, Any]
    calibration_version: str
    calibration_valid: bool
    prior_actions: tuple[str, ...] = ()
    target_centroid_px: tuple[float, float] | None = None
    uncertainty_px: float | None = None


@dataclass(frozen=True)
class ActionRequest:
    """软件建议的下一步动作；不是硬件命令。

    ``target_centroid_mm`` 保持必填，避免把“没有坐标”伪装成可执行目标：
    - ``coordinate_frame="work_mm"``：工作台毫米坐标，必须来自有效标定。
    - ``coordinate_frame="nozzle_fixed"`` 且目标 ``(0, 0)``：喷头不动的定点脉冲，
      不是伪造的 ``work_mm`` 标定。约束里必须写明禁止 XY。

    合法 ``primitive``：``SPRAY_AT_POINT``（需 ``work_mm``）、``PUMP_IN_PLACE``（无 XY）。

    消费者：``fixed_rule.py``、``governor.py``、``fake_serial.py``、
    ``stm32_serial.py``、``replay_mcl.py``、``mock_mcl.py``、``demo/demo_pipeline.py``。
    字段未改，接口版本仍为 ``mcl-v0.1``。失败测试见
    ``test/control_system/test_pump_in_place.py`` 与 ``test_stm32_serial.py``。
    """

    action_id: str
    task_id: str
    state_id: str
    target_centroid_mm: tuple[float, float]
    coordinate_frame: str
    primitive: str
    duration_ms: int
    pressure: float
    constraints: dict[str, Any]
    expected_effect: str
    rule_version: str


@dataclass(frozen=True)
class SafetyDecision:
    action_id: str
    outcome: SafetyOutcome
    reason_codes: tuple[str, ...]
    approval_token: str | None
    state_id: str | None = None
    request_digest: str | None = None
    policy_version: str = "safety-policy-v0"
    issued_at: str | None = None
    expires_at: str | None = None
    interface_version: str = INTERFACE_VERSION


@dataclass(frozen=True)
class ExecutionReceipt:
    action_id: str
    mode: str
    started_at: str
    ended_at: str
    actual_target_mm: tuple[float, float] | None
    actual_duration_ms: int
    actual_pressure: float
    controller_state: str
    interlock_state: str
    success: bool
    error_code: str | None = None


@dataclass(frozen=True)
class VerificationResult:
    task_id: str
    pre_observation_id: str
    post_observation_id: str | None
    residual_area_px: float | None
    removal_rate: float | None
    damage_flag: bool
    next_route: NextRoute
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class FailureRecord:
    failure_id: str
    task_id: str
    stage: str
    severity: str
    reason_codes: tuple[str, ...]
    reproducible: bool
    recovery: str


@dataclass
class Episode:
    episode_id: str
    task_id: str
    mode: str
    protocol_version: str
    observation_pre: Observation
    state: StateEstimate
    action_request: ActionRequest | None
    safety_decision: SafetyDecision | None
    execution_receipt: ExecutionReceipt | None
    observation_post: Observation | None
    verification: VerificationResult | None
    failures: list[FailureRecord] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe, nested evidence record."""
        return asdict(self)
