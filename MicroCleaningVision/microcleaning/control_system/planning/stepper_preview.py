"""毫米位移 → 步进脉冲预览。不发送 MOVE。

丝杆公式（与常见 3D 打印机 steps/mm 相同）：

    steps_per_mm = (steps_per_rev * microstep) / lead_mm_per_rev

也可以直接填写 ``steps_per_mm_x/y`` 覆盖公式。转数 = 步数 / (步/圈 × 细分)。
``homed=false`` 时结果只供对照；``send_to_controller`` 必须为 false。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StepperConfig:
    """电机铭牌参数的更改位置。未测导程时使用 assumed_lead_mm_per_rev。"""

    version: str = "stepper-placeholder-v0"
    steps_per_rev: int = 200
    microstep: int = 16
    lead_mm_per_rev_x: float | None = None
    lead_mm_per_rev_y: float | None = None
    assumed_lead_mm_per_rev: float = 8.0
    steps_per_mm_x: float | None = None
    steps_per_mm_y: float | None = None
    homed: bool = False
    send_to_controller: bool = False

    def validate(self) -> None:
        if self.send_to_controller:
            raise ValueError("当前版本禁止 send_to_controller=true；步进结果不得发往 STM32")
        if self.steps_per_rev <= 0 or self.microstep <= 0:
            raise ValueError("steps_per_rev与microstep必须为正")
        if self.assumed_lead_mm_per_rev <= 0:
            raise ValueError("assumed_lead_mm_per_rev必须大于0")
        for value in (self.lead_mm_per_rev_x, self.lead_mm_per_rev_y, self.steps_per_mm_x, self.steps_per_mm_y):
            if value is not None and value <= 0:
                raise ValueError("导程和steps_per_mm若填写必须大于0")

    def steps_per_mm(self, axis: str) -> tuple[float, str]:
        self.validate()
        explicit = self.steps_per_mm_x if axis == "x" else self.steps_per_mm_y
        if explicit is not None:
            return float(explicit), "explicit_steps_per_mm"
        lead = self.lead_mm_per_rev_x if axis == "x" else self.lead_mm_per_rev_y
        if lead is not None:
            return (self.steps_per_rev * self.microstep) / float(lead), "lead_formula"
        return (
            (self.steps_per_rev * self.microstep) / self.assumed_lead_mm_per_rev,
            "assumed_lead_formula",
        )

    def microsteps_per_rev(self) -> int:
        return self.steps_per_rev * self.microstep

    def to_dict(self) -> dict[str, object]:
        steps_x, source_x = self.steps_per_mm("x")
        steps_y, source_y = self.steps_per_mm("y")
        return {
            "version": self.version,
            "steps_per_rev": self.steps_per_rev,
            "microstep": self.microstep,
            "lead_mm_per_rev_x": self.lead_mm_per_rev_x,
            "lead_mm_per_rev_y": self.lead_mm_per_rev_y,
            "assumed_lead_mm_per_rev": self.assumed_lead_mm_per_rev,
            "steps_per_mm_x": self.steps_per_mm_x,
            "steps_per_mm_y": self.steps_per_mm_y,
            "resolved_steps_per_mm_x": steps_x,
            "resolved_steps_per_mm_y": steps_y,
            "steps_per_mm_source_x": source_x,
            "steps_per_mm_source_y": source_y,
            "homed": self.homed,
            "send_to_controller": False,
            "evidence_boundary": "步进预览；未HOME、无MCV2 MOVE，不发送串口",
        }


@dataclass(frozen=True)
class MotionLeg:
    index: int
    role: str
    pump_on: bool
    from_mm: tuple[float, float]
    to_mm: tuple[float, float]
    delta_mm: tuple[float, float]
    steps_x: int
    steps_y: int
    revolutions_x: float
    revolutions_y: float

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "role": self.role,
            "pump_on": self.pump_on,
            "from_mm": list(self.from_mm),
            "to_mm": list(self.to_mm),
            "delta_mm": list(self.delta_mm),
            "steps_x": self.steps_x,
            "steps_y": self.steps_y,
            "revolutions_x": self.revolutions_x,
            "revolutions_y": self.revolutions_y,
        }


@dataclass(frozen=True)
class MotionPreview:
    start_mm: tuple[float, float]
    legs: tuple[MotionLeg, ...]
    total_spray_mm: float
    total_travel_mm: float
    total_abs_steps_x: int
    total_abs_steps_y: int
    homed: bool
    send_to_controller: bool
    out_of_travel: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "start_mm": list(self.start_mm),
            "legs": [leg.to_dict() for leg in self.legs],
            "total_spray_mm": self.total_spray_mm,
            "total_travel_mm": self.total_travel_mm,
            "total_abs_steps_x": self.total_abs_steps_x,
            "total_abs_steps_y": self.total_abs_steps_y,
            "homed": self.homed,
            "send_to_controller": False,
            "out_of_travel": self.out_of_travel,
        }


def mm_delta_to_steps(
    delta_mm: tuple[float, float],
    stepper: StepperConfig,
) -> tuple[int, int, float, float]:
    """位移毫米 → 有符号步数和转数。"""

    stepper.validate()
    steps_x_per_mm, _ = stepper.steps_per_mm("x")
    steps_y_per_mm, _ = stepper.steps_per_mm("y")
    steps_x = int(round(delta_mm[0] * steps_x_per_mm))
    steps_y = int(round(delta_mm[1] * steps_y_per_mm))
    per_rev = float(stepper.microsteps_per_rev())
    return steps_x, steps_y, steps_x / per_rev, steps_y / per_rev


def preview_motion(
    waypoints_mm: tuple[tuple[float, float], ...],
    segment_start_indices: tuple[int, ...],
    stepper: StepperConfig,
    *,
    start_mm: tuple[float, float] = (0.0, 0.0),
    out_of_travel: bool = False,
) -> MotionPreview:
    """路径点 → 段间关泵、段内开泵的步进对照表。"""

    stepper.validate()
    if not waypoints_mm:
        return MotionPreview(start_mm, (), 0.0, 0.0, 0, 0, stepper.homed, False, out_of_travel)

    starts = set(segment_start_indices)
    legs: list[MotionLeg] = []
    cursor = start_mm
    spray_mm = 0.0
    travel_mm = 0.0
    abs_x = 0
    abs_y = 0

    def add_leg(role: str, pump_on: bool, destination: tuple[float, float]) -> None:
        nonlocal cursor, spray_mm, travel_mm, abs_x, abs_y
        delta = (destination[0] - cursor[0], destination[1] - cursor[1])
        steps_x, steps_y, rev_x, rev_y = mm_delta_to_steps(delta, stepper)
        length = (delta[0] ** 2 + delta[1] ** 2) ** 0.5
        if pump_on:
            spray_mm += length
        else:
            travel_mm += length
        abs_x += abs(steps_x)
        abs_y += abs(steps_y)
        legs.append(
            MotionLeg(
                index=len(legs),
                role=role,
                pump_on=pump_on,
                from_mm=cursor,
                to_mm=destination,
                delta_mm=delta,
                steps_x=steps_x,
                steps_y=steps_y,
                revolutions_x=rev_x,
                revolutions_y=rev_y,
            )
        )
        cursor = destination

    add_leg("home_to_first", False, waypoints_mm[0])
    for index in range(1, len(waypoints_mm)):
        if index in starts:
            add_leg("segment_travel", False, waypoints_mm[index])
        else:
            add_leg("spray", True, waypoints_mm[index])

    return MotionPreview(
        start_mm,
        tuple(legs),
        spray_mm,
        travel_mm,
        abs_x,
        abs_y,
        stepper.homed,
        False,
        out_of_travel,
    )


def stepper_from_dict(payload: dict[str, Any]) -> StepperConfig:
    if payload.get("send_to_controller"):
        raise ValueError("Stepper JSON 禁止 send_to_controller=true")
    stepper = StepperConfig(
        version=str(payload.get("version", "stepper-placeholder-v0")),
        steps_per_rev=int(payload.get("steps_per_rev", 200)),
        microstep=int(payload.get("microstep", 16)),
        lead_mm_per_rev_x=_optional_float(payload.get("lead_mm_per_rev_x")),
        lead_mm_per_rev_y=_optional_float(payload.get("lead_mm_per_rev_y")),
        assumed_lead_mm_per_rev=float(payload.get("assumed_lead_mm_per_rev", 8.0)),
        steps_per_mm_x=_optional_float(payload.get("steps_per_mm_x")),
        steps_per_mm_y=_optional_float(payload.get("steps_per_mm_y")),
        homed=bool(payload.get("homed", False)),
        send_to_controller=False,
    )
    stepper.validate()
    return stepper


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
