"""P6 workbench adapter: frozen full-size inputs, optional Tk simulation only."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from demo.coverage_session import CoverageDataset
from microcleaning.control_system.planning.coverage_model import Evidence, FootprintProfile, canonical_json, content_hash
from microcleaning.control_system.planning.coverage_planner import CoverageInput
from microcleaning.control_system.reporting.coverage_evidence import export_bound_preview


@dataclass(frozen=True, eq=False)
class WorkbenchCoverageSource:
    dataset: CoverageDataset
    folder: str
    binding_json: str

    @property
    def binding(self):
        return json.loads(self.binding_json)

    @property
    def identity(self):
        return content_hash(self.binding)

    def __deepcopy__(self, memo):
        # Dataset arrays are bytes-backed immutable; events may safely share them.
        return self


def freeze_source(*, task_id, folder, target_id, stage, cycle, recheck, frame_id,
                  image_bgr, mask, mask_kind, metadata, input_valid=True, input_reasons=()):
    """Use the complete supplied mask, never its largest connected component."""
    root = Path(folder).resolve()
    observation = metadata["observation"]
    image_path = Path(observation["raw_image_ref"])
    if not image_path.is_absolute():
        image_path = root / image_path
    image_ref = image_path.resolve().relative_to(root).as_posix()
    source_ref = "cycle_001/pre.json" if stage == "initial" else (
        f"cycle_{cycle:03d}/pre.json" if stage == "pre" else f"cycle_{cycle:03d}/recheck_{recheck:03d}/post.json")
    input = CoverageInput(mask, target_id, mask_kind, source_ref, frame_id=frame_id,
        image_ref=image_ref, image_sha256=metadata["raw_sha256"],
        segmentation_sha256=metadata["segmentation_sha256"])
    dataset = CoverageDataset(np.asarray(image_bgr)[..., ::-1], input)
    binding = {**input.to_dict(), "task_id": task_id, "stage": stage, "cycle": cycle, "recheck": recheck,
        "observation_id": observation["observation_id"], "captured_at": metadata.get("captured_at"),
        "roi_segmentation_sha256": (metadata.get("roi_segmentation") or {}).get("sha256"),
        "input_valid": bool(input_valid), "input_reasons": list(input_reasons)}
    return WorkbenchCoverageSource(dataset, str(root), canonical_json(binding))


class WorkbenchCoverageHub:
    """Tk-thread owner. Keep initial + latest per target, not all rewash images.

    A target/frame replacement closes the old worker/panel. Late callbacks also
    check the frozen source identity and panel identity before updating a page.
    No execution command, confirmation broker or hardware object is referenced.
    """
    def __init__(self, owner):
        self.owner = owner
        self.task_id = None
        self.sources = {}
        self.panel = None
        self.worker = None
        self.next_generation = 0
        self.source = None
        self.preview = None
        self.profile = FootprintProfile("workbench-virtual-circle-v1", None, "px",
            Evidence("assumed", "explicit-workbench-simulation"))
        self.policy = None
        self.view_options = None
        self.closed = False

    def reset(self, task_id=None):
        self._close_panel()
        self.task_id = task_id
        self.sources.clear()
        self.source = self.preview = None
        self._notify()

    def ingest(self, source):
        if self.closed or not isinstance(source, WorkbenchCoverageSource):
            return
        binding = source.binding
        if self.task_id != binding["task_id"]:
            # Only the current task may establish/reset identity; stale events
            # from a former task are ignored after start_task resets the hub.
            current = (self.owner.task or {}).get("task_id")
            if current != binding["task_id"]:
                return
            self.reset(binding["task_id"])
        target, stage = binding["target_id"], binding["stage"]
        slot = "initial" if stage == "initial" else "latest"
        versions = self.sources.setdefault(target, {})
        previous = versions.get(slot)
        if previous is not None:
            old = previous.binding
            if (binding["cycle"], binding["recheck"] or 0, 1 if stage == "post" else 0) <= (
                    old["cycle"], old["recheck"] or 0, 1 if old["stage"] == "post" else 0):
                return
        versions[slot] = source
        if self.source is not None and self.source.binding["target_id"] == target and self.source.identity != source.identity:
            if (self.source.binding["stage"] == "initial") == (stage == "initial"):
                opened = self.panel is not None and not self.panel.closed
                self._close_panel()
                self.source, self.preview = source, None
                if opened:
                    self.open(target, latest=stage != "initial")
        self._notify()

    def get(self, target, *, latest=False):
        versions = self.sources.get(target) or {}
        return (versions.get("latest") or versions.get("initial")) if latest else versions.get("initial")

    def select(self, target):
        if self.source is not None and self.source.binding["target_id"] != target:
            self._close_panel()
            self.source = self.preview = None
        self._notify()

    def _close_panel(self):
        panel, self.panel = self.panel, None
        if panel is not None and not panel.closed:
            self._remember(panel)
            panel.close()

    def _remember(self, panel):
        try:
            self.profile, self.policy = panel.parameter_snapshot()
        except (ValueError, TypeError):
            self.profile, self.policy = panel.profile, panel.policy
        self.next_generation = max(self.next_generation, panel.generation)
        self.view_options = panel._options()

    def open(self, target, *, latest=False, worker=None):
        source = self.get(target, latest=latest)
        if source is None:
            return False
        if self.panel is not None and self.source is source and not self.panel.closed:
            self.panel.root.lift()
            return True
        self._close_panel()
        self.source, self.preview = source, None
        from tkinter import Toplevel
        from demo.coverage_panel import StandaloneCoveragePanel
        from demo.coverage_session import LatestCoverageWorker
        from microcleaning.control_system.planning.coverage_planner import CoveragePolicy
        identity = source.identity
        def current(panel):
            return not self.closed and self.panel is panel and not panel.closed and self.source is not None and self.source.identity == identity
        def view(panel):
            if current(panel) and panel.current_plan is not None:
                self.preview = (identity, panel.current_plan, panel.current_view)
                self._notify()
        def invalid(panel):
            if current(panel):
                self.preview = None
                self._notify()
        def closed(panel):
            if self.panel is panel:
                self._remember(panel)
                self.panel = None
        def exporter(panel):
            # Called on Tk at click time; nothing mutable is read by save().
            if not current(panel):
                raise ValueError("COVERAGE_SOURCE_SUPERSEDED")
            binding, generation = source.binding, panel.accepted_generation
            def save(rgb, plan, destination, *, constraints, options, step_count):
                return export_bound_preview(rgb, plan, source.folder, binding, generation=generation,
                    constraints=constraints, options=options, step_count=step_count)
            return save
        popup = Toplevel(self.owner.root)
        if self.worker is None:
            self.worker = worker if worker is not None else LatestCoverageWorker()
        elif worker is not None and worker is not self.worker:
            popup.destroy()
            raise ValueError("COVERAGE_HUB_WORKER_ALREADY_BOUND")
        self.panel = StandaloneCoveragePanel(source.dataset, self.profile, root=popup, worker=self.worker,
            policy=self.policy or CoveragePolicy(candidate_stride_px=2), options=self.view_options, input_locked=True,
            on_view=view, on_invalidate=invalid, on_close=closed, exporter_factory=exporter,
            on_exported=self._saved, initial_generation=self.next_generation, owns_worker=False,
            layout_scale=self.owner.scale,
            source_description=f"{source.binding['stage']} / {source.binding['frame_id']}\n" + (
                "输入位置/可比性未通过：" + ", ".join(source.binding["input_reasons"])
                if not source.binding["input_valid"] else "输入绑定原图坐标，仅供模拟"))
        self.panel.root.title(f"{target} · {source.binding['stage']} · 模拟覆盖预览")
        self._notify()
        return True

    def _saved(self, path):
        if self.closed:
            return
        self.owner.write(f"模拟覆盖已保存：{path}")
        runtime = self.owner.runtime
        if not runtime.active:
            runtime.load_results(self.source.folder, auto_navigate=False)

    def _notify(self):
        if self.closed:
            return
        callback = getattr(self.owner, "coverage_changed", None)
        if callback is not None:
            callback()

    def close(self):
        self.closed = True
        self._close_panel()
        if self.worker is not None:
            self.worker.close()
        self.sources.clear()
        self.source = self.preview = None
        self.owner = None
