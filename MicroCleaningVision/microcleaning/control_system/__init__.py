"""成员 C：目标规划与控制仿真。

按功能分子包，不要把业务文件继续堆在这一层：

- ``planning``  路径规则、假设毫米、步进对照
- ``safety``    动作申请与安全闸
- ``serial``    FakeSerial 与 STM32 协议
- ``replay``    软件回放、Episode、Mock 基线
"""

from .planning.cleaning_plan import (
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
