"""Stage 2 轴位。X 已接线可发送；Y 只保留同名接口，不进入串口字节。"""

from __future__ import annotations

from dataclasses import dataclass

from microcleaning.control_system.planning.stepper_preview import MotionLeg, MotionPreview, StepperConfig


MAX_PULSE_STEPS = 20000
DEFAULT_TRANSMIT_BUDGET = 1600


@dataclass(frozen=True)
class AxisSlot:
    """一根轴的铭牌和是否允许出串口。Y 现在 wired/transmit 都是 false。"""

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
        if self.name == "y" and self.transmit:
            raise ValueError("Y 轴接口已预留，当前 Stage 2 固件只有 X，禁止发送")

    def steps_per_mm(self) -> float:
        self.validate()
        return (self.steps_per_rev * self.microstep) / self.lead_mm_per_rev


def stage2_axis_slots() -> tuple[AxisSlot, AxisSlot]:
    return (
        AxisSlot("x", wired=True, transmit=True),
        AxisSlot("y", wired=False, transmit=False),
    )


def stage2_stepper() -> StepperConfig:
    """X 用 1600 脉冲/转、5 mm/转。Y 用同一铭牌只做对照，不发出去。"""

    x_slot, y_slot = stage2_axis_slots()
    stepper = StepperConfig(
        version="stage2-x-wired-y-reserved-v0",
        steps_per_rev=x_slot.steps_per_rev,
        microstep=x_slot.microstep,
        lead_mm_per_rev_x=x_slot.lead_mm_per_rev,
        lead_mm_per_rev_y=y_slot.lead_mm_per_rev,
        send_to_controller=False,
    )
    stepper.validate()
    return stepper


@dataclass(frozen=True)
class HeldAxisMove:
    axis: str
    steps: int
    direction: str

    def to_dict(self) -> dict[str, object]:
        return {"axis": self.axis, "steps": self.steps, "direction": self.direction, "transmitted": False}


@dataclass(frozen=True)
class Stage2Dispatch:
    x_lines: tuple[str, ...]
    y_held: tuple[HeldAxisMove, ...]
    planned_abs_steps_x: int
    transmit_abs_steps_x: int
    budget: int
    truncated: bool

    def wire_text(self) -> str:
        return "".join(f"{line}\r\n" for line in self.x_lines)

    def to_dict(self) -> dict[str, object]:
        return {
            "x_lines": list(self.x_lines),
            "y_held": [item.to_dict() for item in self.y_held],
            "planned_abs_steps_x": self.planned_abs_steps_x,
            "transmit_abs_steps_x": self.transmit_abs_steps_x,
            "budget": self.budget,
            "truncated": self.truncated,
            "y_on_wire": False,
        }


def dispatch_motion(
    motion: MotionPreview,
    *,
    budget: int = DEFAULT_TRANSMIT_BUDGET,
) -> Stage2Dispatch:
    """XY 对照都保留。串口文本只含 X 的 PULSE，并且不超过预算。"""

    slots = {slot.name: slot for slot in stage2_axis_slots()}
    for slot in slots.values():
        slot.validate()
    if budget < 0:
        raise ValueError("发送预算不能为负")

    x_lines: list[str] = []
    y_held: list[HeldAxisMove] = []
    planned = 0
    transmitted = 0
    truncated = False
    for leg in motion.legs:
        planned += abs(leg.steps_x)
        y_move = _held_move(slots["y"], leg.steps_y)
        if y_move is not None:
            y_held.append(y_move)
        steps = abs(leg.steps_x)
        if steps == 0:
            continue
        if steps > MAX_PULSE_STEPS or transmitted + steps > budget:
            truncated = True
            break
        line = _x_line(slots["x"], leg)
        if line is None:
            continue
        x_lines.append(line)
        transmitted += steps
    text = "".join(x_lines)
    if "MCV1" in text or "\nY" in text or text.startswith("Y"):
        raise RuntimeError("X 轴报文混入了禁止字段")
    return Stage2Dispatch(tuple(x_lines), tuple(y_held), planned, transmitted, budget, truncated)


def _x_line(slot: AxisSlot, leg: MotionLeg) -> str | None:
    if not slot.transmit or slot.name != "x" or leg.steps_x == 0:
        return None
    direction = "FWD" if leg.steps_x > 0 else "REV"
    steps = abs(leg.steps_x)
    if steps > MAX_PULSE_STEPS:
        return None
    return f"PULSE {steps} {direction}"


def _held_move(slot: AxisSlot, steps: int) -> HeldAxisMove | None:
    if steps == 0:
        return None
    if slot.transmit:
        slot.validate()
    return HeldAxisMove(slot.name, abs(steps), "FWD" if steps > 0 else "REV")
