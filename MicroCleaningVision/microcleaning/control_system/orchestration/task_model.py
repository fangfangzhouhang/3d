"""执行前人工决策与不可变名单；只由任务线程修改，UI 使用快照。"""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from microcleaning.vision.target_instance import TargetInstance, extract_target_instances


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class Candidate:
    target_id: str
    source: str
    instance: TargetInstance
    decision: str = "PENDING_REVIEW"
    execution: str = "NOT_STARTED"
    quality: str = "NOT_ASSESSED"
    finished: bool = False
    retry_count: int = 0
    note: str = ""
    plan: dict = field(default_factory=dict)
    eligibility_reasons: tuple[str, ...] = ()
    attempts: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        result = asdict(self)
        result["area_kind"] = "segmented_stain_px" if self.source == "algorithm" else "manual_region_px"
        return result


class TaskModel:
    VERSION = "workbench-task-v1"

    def __init__(self, *, folder: Path, task_id: str, image_shape: tuple, entries: dict,
                 metadata: dict, references: dict, policy: dict) -> None:
        self.folder, self.task_id = Path(folder), task_id
        self.image_shape = tuple(image_shape[:2])
        self.metadata, self.references, self.policy = deepcopy(metadata), deepcopy(references), deepcopy(policy)
        self.created_at, self.ended_at = _now(), None
        self.state = "DRAFT"
        self.targets = {stable: Candidate(stable, "algorithm", entry.instance) for stable, entry in entries.items()}
        self.initial_ids = tuple(self.targets)
        self.execution_ids: tuple[str, ...] = ()
        self.locked_snapshot: dict | None = None
        self.lock_sha256: str | None = None
        self.manual_masks: dict = {}
        self._next_id = len(self.targets) + 1
        self.persist()

    def _editable(self) -> None:
        if self.state != "DRAFT":
            raise PermissionError("TASK_IS_LOCKED")

    def audit(self, action: str, *, target_id: str | None = None, reason: str = "", **facts) -> None:
        event = {"review_id": uuid4().hex, "task_id": self.task_id, "at": _now(),
                 "operator": self.metadata.get("operator") or "未记录", "action": action,
                 "target_id": target_id, "reason": reason, **facts}
        with (self.folder / "review_log.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def decide(self, target_id: str, decision: str, reason: str = "") -> None:
        self._editable()
        if decision not in {"APPROVED", "EXCLUDED", "PENDING_REVIEW"}:
            raise ValueError("INVALID_TARGET_DECISION")
        candidate = self.targets[target_id]
        if decision == "APPROVED" and candidate.eligibility_reasons:
            raise ValueError("TARGET_NOT_EXECUTABLE: " + ", ".join(candidate.eligibility_reasons))
        self.audit("decision", target_id=target_id, reason=reason, before=candidate.decision, after=decision)
        candidate.decision, candidate.note = decision, reason
        self.persist()

    def set_plan(self, target_id: str, plan: dict, reasons: tuple[str, ...]) -> None:
        self._editable()
        candidate = self.targets[target_id]
        candidate.plan = deepcopy(plan)
        candidate.eligibility_reasons = tuple(reasons)
        if reasons:
            candidate.decision = "PENDING_REVIEW"

    def add_manual(self, bbox: tuple[int, int, int, int], reason: str = "") -> str:
        self._editable()
        import numpy as np
        x, y, width, height = bbox
        h, w = self.image_shape
        if any(not isinstance(n, int) or isinstance(n, bool) for n in bbox) or min(width, height) <= 0 or x < 0 or y < 0 or x + width > w or y + height > h:
            raise ValueError("MANUAL_REGION_OUTSIDE_OR_EMPTY")
        mask = np.zeros((h, w), dtype=np.uint8)
        mask[y:y + height, x:x + width] = 255
        stable = f"S{self._next_id:04d}"
        instance = extract_target_instances(mask)[0]
        overlaps = []
        for candidate in self.targets.values():
            a, b, c, d = candidate.instance.bbox
            if max(x, a) < min(x + width, a + c) and max(y, b) < min(y + height, b + d):
                overlaps.append(candidate.target_id)
        candidate = Candidate(stable, "manual", instance, note=reason)
        candidate.eligibility_reasons = ("MANUAL_PLAN_NOT_VALIDATED",) + (("OVERLAPS_EXISTING_TARGET",) if overlaps else ())
        self.audit("manual_add", target_id=stable, reason=reason, bbox_px=bbox, overlaps=overlaps)
        self.targets[stable], self.manual_masks[stable] = candidate, mask
        self._next_id += 1
        self.persist()
        return stable

    def lock(self) -> tuple[str, ...]:
        self._editable()
        chosen = tuple(stable for stable, item in self.targets.items() if item.decision == "APPROVED")
        if not chosen:
            raise ValueError("EMPTY_EXECUTION_LIST")
        if any(self.targets[stable].eligibility_reasons or not self.targets[stable].plan for stable in chosen):
            raise ValueError("INVALID_EXECUTION_GEOMETRY")
        snapshot = {"task_id": self.task_id, "locked_at": _now(), "execution_ids": chosen,
                    "targets": {stable: item.to_dict() for stable, item in self.targets.items()},
                    "metadata": deepcopy(self.metadata), "references": deepcopy(self.references), "policy": deepcopy(self.policy)}
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
        # 先原子落盘，再改变内存状态；磁盘失败仍 DRAFT、无执行名单。
        payload = self.to_dict()
        payload.update(state="LOCKED", execution_ids=chosen, locked_snapshot=snapshot, lock_sha256=digest)
        atomic_json(self.folder / "task_manifest.json", payload)
        self.execution_ids, self.locked_snapshot, self.lock_sha256, self.state = chosen, snapshot, digest, "LOCKED"
        self.audit("lock", execution_ids=chosen, pending_count=sum(t.decision == "PENDING_REVIEW" for t in self.targets.values()), lock_sha256=digest)
        return chosen

    def begin(self) -> None:
        if self.state != "LOCKED" or not self.execution_ids:
            raise PermissionError("LOCKED_TASK_REQUIRED")
        self.state = "RUNNING"
        self.persist()

    def update_attempt(self, target_id: str, attempt: dict, *, quality: str, finished: bool) -> None:
        item = self.targets[target_id]
        item.attempts.append(deepcopy(attempt))
        execution = attempt.get("execution") or {}
        receipt = ((execution.get("session") or {}).get("receipt") or {})
        item.execution = "EXECUTED" if receipt.get("success") and receipt.get("controller_state") == "DONE" else ("FAILED" if execution.get("status") == "ERROR" else "NOT_STARTED")
        item.quality, item.finished = quality, finished
        self.persist()

    def finish(self, state: str) -> None:
        if state not in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise ValueError("INVALID_TERMINAL_STATE")
        self.state, self.ended_at = state, _now()
        self.persist()

    def to_dict(self) -> dict:
        return {"version": self.VERSION, "task_id": self.task_id, "state": self.state,
                "created_at": self.created_at, "ended_at": self.ended_at, "metadata": deepcopy(self.metadata),
                "image_shape": self.image_shape, "initial_ids": self.initial_ids, "execution_ids": self.execution_ids,
                "references": deepcopy(self.references), "policy": deepcopy(self.policy),
                "targets": {stable: item.to_dict() for stable, item in self.targets.items()},
                "locked_snapshot": deepcopy(self.locked_snapshot), "lock_sha256": self.lock_sha256}

    def persist(self) -> None:
        atomic_json(self.folder / "task_manifest.json", self.to_dict())

    def ui_snapshot(self) -> dict:
        """主线程只需要候选与当前状态，避免逐阶段搬运无限复洗的全部档案。"""
        targets = {}
        for stable, item in self.targets.items():
            targets[stable] = {name: deepcopy(value) for name, value in vars(item).items() if name != "attempts" and name != "instance"}
            targets[stable].update(instance=asdict(item.instance), attempts=[], attempt_count=len(item.attempts),
                area_kind="segmented_stain_px" if item.source == "algorithm" else "manual_region_px")
        return {"version": self.VERSION, "task_id": self.task_id, "state": self.state,
            "initial_ids": self.initial_ids, "execution_ids": self.execution_ids, "targets": targets,
            "image_shape": self.image_shape, "metadata": deepcopy(self.metadata)}
