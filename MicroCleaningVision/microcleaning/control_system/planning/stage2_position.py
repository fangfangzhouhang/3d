"""Stage 2 位置账本：相对人工零点累计步数，存在本机 ``output/stage2/position.json``。

这不是回零（homing），也不是台面绝对坐标：步进开环，丢步或断电后计数会漂。
每次上电或发送失败后，都要人把台面对到参考位置，再执行 ``--stage2-set-zero``。
账本缺失、损坏或被标记未知时，运动关卡一律拒发（POSITION_UNKNOWN）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
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
    status: str = "READY"
    pending_motion: dict[str, object] | None = None
    reference_epoch: str | None = None

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
            "status": self.status,
            "pending_motion": self.pending_motion,
            "reference_epoch": self.reference_epoch or self.zero_set_at,
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
    status = str(payload.get("status", "READY" if payload.get("known") is True else "POSITION_UNCERTAIN"))
    pending = payload.get("pending_motion")
    if pending is not None and not isinstance(pending, dict):
        return _unknown("在途运动记录格式不符，按未知处理")
    # MOVING 文件是重启时的故障证据：旧坐标不能作为新的自动运动起点。
    known = payload.get("known") is True and status == "READY" and pending is None
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
        status=status,
        pending_motion=pending,
        reference_epoch=payload.get("reference_epoch") or payload.get("zero_set_at"),
    )


def set_zero(path: str | Path = DEFAULT_POSITION_PATH, *, run_id: str | None = None) -> Stage2Position:
    """人已经把台面对到参考位置：把当前位置记为 (0, 0)。"""

    now = _now()
    position = Stage2Position(True, 0, 0, now, now, run_id, "人工对位后归零；只是计数起点，不是回零",
                              reference_epoch=uuid4().hex)
    _write(path, position)
    return position


class PositionPersistenceError(RuntimeError):
    reason_code = "POSITION_PERSISTENCE_FAILED"


class PositionLifecycleError(RuntimeError):
    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def begin_motion(path: str | Path, lines: tuple[str, ...], *, run_id: str,
                 motion_id: str, expected_before: tuple[int, int] | None = None) -> Stage2Position:
    """在任何运动字节前写入在途记录；失败时调用方必须拒发。"""
    current = load_position(path)
    before = current.xy()
    if before is None:
        raise PositionLifecycleError("POSITION_UNKNOWN_BEFORE_MOTION")
    if expected_before is not None and before != tuple(expected_before):
        raise PositionLifecycleError("POSITION_CHANGED_BEFORE_MOTION")
    if not motion_id or not lines:
        raise PositionLifecycleError("POSITION_MOTION_ID_AND_LINES_REQUIRED")
    expected = list(before)
    for line in lines:
        dx, dy = parse_movexy_line(line)
        expected[0] += dx
        expected[1] += dy
    now = _now()
    pending = {"motion_id": motion_id, "run_id": run_id, "lines": list(lines),
               "before_steps": list(before), "expected_steps": expected,
               "started_at": now, "reference_epoch": current.reference_epoch or current.zero_set_at}
    moving = replace(current, known=False, x_steps=None, y_steps=None, status="MOVING",
                     pending_motion=pending, updated_at=now, last_run_id=run_id,
                     note="运动已登记、尚未提交完成回执；重新启动后必须重新建立位置参考")
    _write(path, moving)
    return moving


def record_not_started(path: str | Path, *, run_id: str, motion_id: str) -> Stage2Position:
    """发送器已证明没有尝试 MOVEXY 时撤销在途记录；不能用于部分运动。"""
    current = load_position(path)
    pending = current.pending_motion
    if (current.status != "MOVING" or pending is None or pending.get("motion_id") != motion_id
            or pending.get("run_id") != run_id):
        raise PositionLifecycleError("POSITION_PENDING_MISMATCH")
    before = pending.get("before_steps")
    if not _pair(before):
        raise PositionLifecycleError("POSITION_PENDING_INVALID")
    position = replace(current, known=True, x_steps=before[0], y_steps=before[1], status="READY",
                       pending_motion=None, updated_at=_now(), last_run_id=run_id,
                       note="发送器确认未尝试运动，保留原计数参考")
    _write(path, position)
    return position


def record_completed(
    path: str | Path,
    lines: tuple[str, ...],
    *,
    run_id: str,
    motion_id: str | None = None,
) -> Stage2Position:
    """把已经走完的 MOVEXY 行累加进账本。账本本来未知时保持未知。"""

    current = load_position(path)
    pending = current.pending_motion
    if pending is not None:
        if (current.status != "MOVING" or pending.get("run_id") != run_id
            or (motion_id is not None and pending.get("motion_id") != motion_id)
            or tuple(pending.get("lines", ())) != tuple(lines)
            or not _pair(pending.get("before_steps")) or not _pair(pending.get("expected_steps"))):
            raise PositionLifecycleError("POSITION_COMPLETION_MISMATCH")
        x, y = pending["before_steps"]
    elif motion_id is not None:
        raise PositionLifecycleError("POSITION_PENDING_MISSING")
    elif current.xy() is None:
        return mark_unknown(path, run_id=run_id, reason="发送前位置已未知，不累加")
    else:
        x, y = current.xy()
    for line in lines:
        dx, dy = parse_movexy_line(line)
        x += dx
        y += dy
    if pending is not None and (x, y) != tuple(pending["expected_steps"]):
        raise PositionLifecycleError("POSITION_EXPECTED_END_MISMATCH")
    position = Stage2Position(True, x, y, current.zero_set_at, _now(), run_id,
                              "按固件确认走完的 MOVEXY 累加；不是位移实测",
                              reference_epoch=current.reference_epoch)
    _write(path, position)
    return position


def mark_unknown(path: str | Path, *, run_id: str | None, reason: str) -> Stage2Position:
    current = load_position(path)
    position = Stage2Position(False, None, None, current.zero_set_at, _now(), run_id, reason,
                              status="POSITION_UNCERTAIN", pending_motion=current.pending_motion,
                              reference_epoch=current.reference_epoch)
    _write(path, position)
    return position


def _unknown(note: str) -> Stage2Position:
    return Stage2Position(False, None, None, None, None, None, note, status="UNHOMED")


def _write(path: str | Path, position: Stage2Position) -> None:
    target = Path(path)
    temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(position.to_dict(), ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except OSError as exc:
        raise PositionPersistenceError(f"位置账本无法持久化：{exc}") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _pair(value: object) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 2 and all(_is_int(v) for v in value)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
