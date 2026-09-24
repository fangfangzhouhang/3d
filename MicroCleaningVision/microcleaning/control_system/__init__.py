"""成员 C 独占的目标规划与控制仿真模块。

当前保留固定动作申请、定点短喷申请、最低安全边界、FakeSerial、STM32 串口适配器
和软件回放。XY/MOVE 与像素到毫米标定仍未接入。
"""

from .cleaning_plan import (
    CleaningPlan,
    CleaningPlanPolicy,
    CleaningStrategy,
    plan_cleaning,
    simulate_first_action,
)

__all__ = [
    "CleaningPlan",
    "CleaningPlanPolicy",
    "CleaningStrategy",
    "plan_cleaning",
    "simulate_first_action",
]
