"""Stage 2 位置账本：相对人工零点累计步数，存在本机 ``output/stage2/position.json``。

这不是回零（homing），也不是台面绝对坐标：步进开环，丢步或断电后计数会漂。
每次上电或发送失败后，都要人把台面对到参考位置，再执行 ``--stage2-set-zero``。
账本缺失、损坏或被标记未知时，运动关卡一律拒发（POSITION_UNKNOWN）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from microcleaning.control_system.planning.stage2_axes import parse_movexy_line


DEFAULT_POSITION_PATH = Path("output") / "stage2" / "position.json"
POSITION_FORMAT = "stage2-position-v0"


@dataclass(frozen=True)
class Stage2Position:
    known: bool
    x_steps: int | None
    y_steps: int | None
    zero_set_at: str | None
    updated_at: str | None
    last_run_id: str | None
    note: str

    def xy(self) -> tuple[int, int] | None:
        if not self.known or self.x_steps is None or self.y_steps is None:
            return None
        return (self.x_steps, self.y_steps)

    def to_dict(self) -> dict[str, object]:
        return {
            "format": POSITION_FORMAT,
            "known": self.known,
            "x_steps": self.x_steps,
            "y_steps": self.y_steps,
            "zero_set_at": self.zero_set_at,
            "updated_at": self.updated_at,
            "last_run_id": self.last_run_id,
            "note": self.note,
            "coordinate_frame": "stage2_steps_from_human_zero",
        }


def load_position(path: str | Path = DEFAULT_POSITION_PATH) -> Stage2Position:
    source = Path(path)
    if not source.is_file():
        return _unknown("没有位置账本：先由人把台面对到参考位置，再执行 --stage2-set-zero")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _unknown("位置账本无法读取或不是 JSON，按未知处理")
    if not isinstance(payload, dict) or payload.get("format") != POSITION_FORMAT:
        return _unknown("位置账本格式不符，按未知处理")
    known = payload.get("known") is True
    x_steps = payload.get("x_steps")
    y_steps = payload.get("y_steps")
    if known and not (_is_int(x_steps) and _is_int(y_steps)):
        return _unknown("位置账本缺少整数步数，按未知处理")
    return Stage2Position(
        known=known,
        x_steps=x_steps if known else None,
        y_steps=y_steps if known else None,
        zero_set_at=payload.get("zero_set_at"),
        updated_at=payload.get("updated_at"),
        last_run_id=payload.get("last_run_id"),
        note=str(payload.get("note", "")),
    )


def set_zero(path: str | Path = DEFAULT_POSITION_PATH, *, run_id: str | None = None) -> Stage2Position:
    """人已经把台面对到参考位置：把当前位置记为 (0, 0)。"""

    now = _now()
    position = Stage2Position(True, 0, 0, now, now, run_id, "人工对位后归零；只是计数起点，不是回零")
    _write(path, position)
    return position


def record_completed(
    path: str | Path,
    lines: tuple[str, ...],
    *,
    run_id: str,
) -> Stage2Position:
    """把已经走完的 MOVEXY 行累加进账本。账本本来未知时保持未知。"""

    current = load_position(path)
    if current.xy() is None:
        return mark_unknown(path, run_id=run_id, reason="发送前位置已未知，不累加")
    x, y = current.xy()
    for line in lines:
        dx, dy = parse_movexy_line(line)
        x += dx
        y += dy
    position = Stage2Position(True, x, y, current.zero_set_at, _now(), run_id, "按固件确认走完的 MOVEXY 累加；不是位移实测")
    _write(path, position)
    return position


def mark_unknown(path: str | Path, *, run_id: str | None, reason: str) -> Stage2Position:
    current = load_position(path)
    position = Stage2Position(False, None, None, current.zero_set_at, _now(), run_id, reason)
    _write(path, position)
    return position


def _unknown(note: str) -> Stage2Position:
    return Stage2Position(False, None, None, None, None, None, note)


def _write(path: str | Path, position: Stage2Position) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    temporary.write_text(json.dumps(position.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
