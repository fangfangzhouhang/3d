"""Stage 2 轴位。X/Y 均已接线，每段生成一条 MOVEXY 双轴同时出串口。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from microcleaning.control_system.planning.stepper_preview import (
    MotionLeg,
    MotionPreview,
    StepperConfig,
)


MAX_PULSE_STEPS = 20000
DEFAULT_TRANSMIT_BUDGET = 1600

_MOVEXY_LINE = re.compile(r"^MOVEXY \d+ (?:FWD|REV) \d+ (?:FWD|REV)$")


@dataclass(frozen=True)
class AxisSlot:
    """一根轴的铭牌和是否允许出串口。"""

    name: str
    wired: bool
    transmit: bool
    steps_per_rev: int = 1600
    microstep: int = 1
    lead_mm_per_rev: float = 5.0

    def validate(self) -> None:
        if self.name not in {"x", "y"}:
            raise ValueError("轴名只能是 x 或 y")
        if self.steps_per_rev <= 0 or self.microstep <= 0 or self.lead_mm_per_rev <= 0:
            raise ValueError("步/圈、细分、导程必须为正")
        if self.transmit and not self.wired:
            raise ValueError(f"{self.name} 未接线，不能发送")

    def steps_per_mm(self) -> float:
        self.validate()
        return (self.steps_per_rev * self.microstep) / self.lead_mm_per_rev


def stage2_axis_slots() -> tuple[AxisSlot, AxisSlot]:
    return (
        AxisSlot("x", wired=True, transmit=True),
        AxisSlot("y", wired=True, transmit=True),
    )


def stage2_stepper() -> StepperConfig:
    """X/Y 都用 1600 脉冲/转、5 mm/转，双轴均可发送。"""

    x_slot, y_slot = stage2_axis_slots()
    stepper = StepperConfig(
        version="stage2-xy-wired-v0",
        steps_per_rev=x_slot.steps_per_rev,
        microstep=x_slot.microstep,
        lead_mm_per_rev_x=x_slot.lead_mm_per_rev,
        lead_mm_per_rev_y=y_slot.lead_mm_per_rev,
        send_to_controller=False,
    )
    stepper.validate()
    return stepper


@dataclass(frozen=True)
class Stage2Dispatch:
    lines: tuple[str, ...]
    planned_abs_steps_x: int
    planned_abs_steps_y: int
    transmit_abs_steps_x: int
    transmit_abs_steps_y: int
    budget: int
    truncated: bool

    def wire_text(self) -> str:
        return "".join(f"{line}\r\n" for line in self.lines)

    def to_dict(self) -> dict[str, object]:
        return {
            "lines": list(self.lines),
            "planned_abs_steps_x": self.planned_abs_steps_x,
            "planned_abs_steps_y": self.planned_abs_steps_y,
            "transmit_abs_steps_x": self.transmit_abs_steps_x,
            "transmit_abs_steps_y": self.transmit_abs_steps_y,
            "budget": self.budget,
            "truncated": self.truncated,
            "y_on_wire": True,
        }


def dispatch_motion(
    motion: MotionPreview,
    *,
    budget: int = DEFAULT_TRANSMIT_BUDGET,
) -> Stage2Dispatch:
    """每个路径段一行 MOVEXY，双轴同时发送；两轴累计步数都不超过预算。"""

    slots = {slot.name: slot for slot in stage2_axis_slots()}
    for slot in slots.values():
        slot.validate()
    if budget < 0:
        raise ValueError("发送预算不能为负")

    lines: list[str] = []
    planned_x = 0
    planned_y = 0
    tx_x = 0
    tx_y = 0
    truncated = False
    for leg in motion.legs:
        ax = abs(leg.steps_x)
        ay = abs(leg.steps_y)
        planned_x += ax
        planned_y += ay
        if ax == 0 and ay == 0:
            continue
        if ax > MAX_PULSE_STEPS or ay > MAX_PULSE_STEPS:
            truncated = True
            break
        if tx_x + ax > budget or tx_y + ay > budget:
            truncated = True
            break
        lines.append(_xy_line(leg))
        tx_x += ax
        tx_y += ay

    text = "\n".join(lines)
    if "MCV1" in text or "PUMP" in text:
        raise RuntimeError("双轴报文混入了禁止字段")
    for line in lines:
        if not _MOVEXY_LINE.match(line):
            raise RuntimeError(f"报文行格式非法：{line}")
    return Stage2Dispatch(
        tuple(lines),
        planned_x,
        planned_y,
        tx_x,
        tx_y,
        budget,
        truncated,
    )


def _xy_line(leg: MotionLeg) -> str:
    dir_x = "FWD" if leg.steps_x >= 0 else "REV"
    dir_y = "FWD" if leg.steps_y >= 0 else "REV"
    return f"MOVEXY {abs(leg.steps_x)} {dir_x} {abs(leg.steps_y)} {dir_y}"
