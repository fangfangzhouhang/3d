"""全项目共享的软硬件端口契约（ports）。

这些接口为当前回放适配器和未来真实相机/控制器提供同一契约入口。
目前只有 ReplayCamera 和 FakeSerial 实现；它们不构成真实硬件证据。

设计原则（来自 AGENTS.md 第四节"核心责任边界"）：
- 感知层不直接控制硬件。
- 决策层只能提出动作。
- Safety Governor 决定 ALLOW / DENY / HUMAN。
- 确定性控制器才允许驱动 STM32、泵、电机和喷头。

因此：
- CameraPort 只产出 Observation，不产出动作。
- ControllerPort 只接受已批准的 ActionRequest + SafetyDecision，不自行决策。

何时实现这些接口：
1. 人工关卡完成（样品、安全清单、操作员签署）。
2. 真实相机已标定并保留证据。
3. 真实控制器协议已确认。
在此之前，只允许 MockMCLRunner 与 ReplayMCLRunner 运行。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from microcleaning.contracts import (
    ActionRequest,
    ExecutionReceipt,
    Observation,
    SafetyDecision,
)


class CameraPort(ABC):
    """真实相机适配器接口。

    职责：采集一帧图像，产出不可变的 Observation 证据。
    禁止：直接调用控制器、规划器或任何硬件执行方法。
    """

    @abstractmethod
    def capture(self, task_id: str, phase: str) -> Observation:
        """采集一帧图像并返回 Observation。

        参数:
            task_id: 当前任务标识。
            phase: 采集阶段，如 "pre"（动作前）或 "post"（动作后）。

        返回:
            包含 raw_image_ref、质量指标和标定版本的 Observation。
        """
        raise NotImplementedError


class ControllerPort(ABC):
    """真实确定性控制器适配器接口。

    职责：把已获 ALLOW 的 ActionRequest 翻译为 STM32 / 泵 / 电机命令，并回传 ExecutionReceipt。
    禁止：接受未经安全治理器批准的请求；自行选择动作参数。
    """

    @abstractmethod
    def execute(
        self,
        request: ActionRequest,
        decision: SafetyDecision,
    ) -> ExecutionReceipt:
        """执行已批准的动作申请，回传执行回执。

        参数:
            request: 已获安全治理器批准的 ActionRequest。
            decision: 与 request 绑定的 SafetyDecision（outcome 必须为 ALLOW）。

        返回:
            记录实际目标、时长、压力、控制器状态和联锁状态的 ExecutionReceipt。

        异常:
            PermissionError: 当 decision 不是 ALLOW、令牌已用、或请求摘要不匹配时。
        """
        raise NotImplementedError
