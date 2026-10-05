"""视觉实例与序列规划之间的适配；跨帧采用 B 已有匹配规则。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from microcleaning.control_system.planning.sequence_planner import SequenceTarget
from microcleaning.vision.target_instance import TargetInstance, match_target_instance


class TargetIdentityError(ValueError):
    pass


@dataclass
class TaskTarget:
    stable_id: str
    observation_id: str
    instance: TargetInstance | None
    retry_count: int = 0
    completed: bool = False

    def as_sequence_target(self) -> SequenceTarget:
        if self.instance is None or self.completed:
            raise ValueError("TARGET_NOT_PENDING")
        return SequenceTarget(self.stable_id, self.instance.centroid_px, self.instance.area_px, self.retry_count)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class TargetLedger:
    """S0001 等是任务身份；T1/component_label 只用于这一帧。"""

    def __init__(self, observation_id: str, mask: Any, instances: list[TargetInstance]) -> None:
        self.mask = mask.copy()
        self.entries: dict[str, TaskTarget] = {}
        self._next_id = 1
        self._retired_views: dict[str, tuple[TargetInstance, Any]] = {}
        for instance in instances:
            self._add(observation_id, instance)

    def _add(self, observation_id: str, instance: TargetInstance) -> None:
        stable = f"S{self._next_id:04d}"
        self._next_id += 1
        self.entries[stable] = TaskTarget(stable, observation_id, instance)

    def pending(self) -> list[SequenceTarget]:
        return [entry.as_sequence_target() for entry in self.entries.values() if not entry.completed and entry.instance is not None]

    def advance(self, observation_id: str, mask: Any, instances: list[TargetInstance], *, completed_id: str | None = None) -> None:
        """先完成整个一对一检查再写账本；一对多、多对一或未洗目标消失交人工。"""
        by_label = {instance.component_label: instance for instance in instances}
        mapping: dict[str, TargetInstance | None] = {}
        claimed: set[int] = set()
        for stable, entry in self.entries.items():
            if entry.instance is None:
                if stable in self._retired_views:
                    old_instance, old_mask = self._retired_views[stable]
                    match = match_target_instance(pre_target=old_instance, pre_mask=old_mask, post_mask=mask)
                    if match.status != "matched" or match.post_label is not None:
                        raise TargetIdentityError(f"{stable}:COMPLETED_TARGET_REAPPEARED_OR_UNCERTAIN")
                continue
            match = match_target_instance(pre_target=entry.instance, pre_mask=self.mask, post_mask=mask)
            if match.status != "matched":
                raise TargetIdentityError(f"{stable}:{match.reason}")
            if match.post_label is None:
                if not entry.completed and stable != completed_id:
                    raise TargetIdentityError(f"{stable}:UNATTEMPTED_TARGET_DISAPPEARED")
                mapping[stable] = None
            else:
                if match.post_label in claimed:
                    raise TargetIdentityError("MULTIPLE_TARGETS_MATCH_ONE_COMPONENT")
                claimed.add(match.post_label)
                mapping[stable] = by_label[match.post_label]
        for stable, instance in mapping.items():
            entry = self.entries[stable]
            if instance is None and entry.instance is not None:
                self._retired_views[stable] = (entry.instance, self.mask.copy())
            entry.instance = instance
            entry.observation_id = observation_id
        if completed_id is not None:
            self.entries[completed_id].completed = True
        for label, instance in by_label.items():
            if label not in claimed:
                self._add(observation_id, instance)
        self.mask = mask.copy()

    def to_dict(self) -> dict[str, object]:
        return {stable: entry.to_dict() for stable, entry in self.entries.items()}
