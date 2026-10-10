"""复用现有模块的有界闭环；相机、分割与执行器由入口注入。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from itertools import count
from typing import Any, Callable
from uuid import uuid4

from microcleaning.contracts import Episode, FailureRecord, NextRoute, VerificationResult
from microcleaning.control_system.orchestration.target_adapter import TargetIdentityError, TargetLedger
from microcleaning.control_system.orchestration.task_model import TaskModel
from microcleaning.control_system.orchestration.result_evidence import QUALITY_POLICY, evaluate_target, final_quality
from microcleaning.control_system.orchestration.workbench_events import OperationCancelled
from microcleaning.control_system.planning.cleaning_plan import plan_cleaning
from microcleaning.control_system.planning.path_preview import draw_path_overlay, resolve_plan_policy
from microcleaning.control_system.planning.sequence_planner import STRATEGIES, plan_sequence
from microcleaning.control_system.planning.stage2_geometry import build_cycle_geometry, stage2_placeholders
from microcleaning.control_system.planning.stage2_position import load_position
from microcleaning.control_system.safety.motion_gate import STAGE2_RUN_STEP_CAP, explain_travel_block
from microcleaning.control_system.replay.episode_store import write_episode
from microcleaning.data_learning.image_quality import build_observation, measure_image_quality
from microcleaning.vision.contamination import ContaminationMeasurement
from microcleaning.vision.target_instance import extract_target_instances, extract_target_mask, match_target_instance, verify_single_target
from microcleaning.vision.target_instance import TargetVerification
from microcleaning.vision.roi_verification import build_roi_reference, validate_roi_location, verify_roi_pair, save_roi_evidence, requires_neighbor_confirmation
from microcleaning.vision.roi_segmentation import FrozenRoiSegmenter


VERSION = "single-entry-cleaning-v1"

_STAGE_TEXT = {
    "CAPTURE_PRE": "按空格抓前图。",
    "SEGMENT_PRE": "正在分析这一帧。",
    "EXTRACT_TARGETS": "正在读取第一次画面的编号。",
    "SELECT_TARGET": "按编号选择下一块。",
    "PLAN_TARGET": "正在规划这一块。",
    "BUILD_MOTION": "正在换成步进脉冲。",
    "AUTHORIZE": "看下面的说明，再确认是否移动。",
    "MOVE": "去程开始。黄十字是显微镜中心。",
    "ALIGN_AT_NOZZLE": "去程结束。确认后才喷水。",
    "AUTHORIZE_RETURN": "喷水结束。确认后才回显微镜。",
    "RETURN": "正在回到观察位置。",
    "PROBE_DEVICE": "正在询问急停和泵。",
    "MOVE_THEN_PUMP": "去程开始。",
    "PUMP": "短喷开始。DONE 只表示输出结束。",
    "CAPTURE_POST": "按空格抓后图。",
    "SEGMENT_POST": "正在分析后图。",
    "VERIFY_TARGET": "正在核对这一块。",
    "STAIN_RECORDED": "这一块已记录。",
    "SUCCESS": "编号名单已处理完。这不是洁净验收。",
    "HUMAN": "已停止。",
    "ERROR": "因错误停止。脉冲和 DONE 不是清洗有效。",
    "RETRY": "按你的确认，再试同一块。",
    "REVIEW_CANDIDATES": "请审核首次候选；仅确认处理且定位有效的目标进入锁定名单。",
    "CANCELLED": "任务已取消；请查看停止回执和中止记录。",
}


@dataclass(frozen=True)
class CapturedFrame:
    image: Any
    frame_id: str
    captured_at: str
    source_id: str
    settings: dict


@dataclass(frozen=True)
class LoopConfig:
    max_cycles: int = 3
    max_retries_per_target: int = 0
    sequence_strategy: str = "nearest_neighbor"
    step_budget: int = STAGE2_RUN_STEP_CAP
    real: bool = False

    def validate(self):
        for name, value, low in (("max_cycles", self.max_cycles, 1), ("max_retries_per_target", self.max_retries_per_target, 0), ("step_budget", self.step_budget, 1)):
            if not isinstance(value, int) or isinstance(value, bool) or value < low:
                raise ValueError(f"INVALID_{name.upper()}")
        if self.step_budget > STAGE2_RUN_STEP_CAP or self.sequence_strategy not in STRATEGIES:
            raise ValueError("INVALID_BUDGET_OR_SEQUENCE_STRATEGY")


class CleaningLoop:
    def __init__(self, *, output_dir: str | Path, source, segmenter, executor,
                 placeholders, calibration, offset, config: LoopConfig = LoopConfig(),
                 compare: Callable[[dict], bool] | None = None, review: Callable | None = None,
                 recheck_decision: Callable[[dict], dict] | None = None,
                 metadata: dict | None = None, on_event: Callable[[dict], None] | None = None) -> None:
        config.validate()
        self.config = config
        self.folder = Path(output_dir).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        if (self.folder / "summary.json").exists():
            raise FileExistsError("RUN_DIRECTORY_ALREADY_USED")
        self.task_id = f"cleaning_{uuid4().hex[:12]}"
        self.source, self.segmenter, self.executor = source, segmenter, executor
        self.placeholders, self.calibration, self.offset = placeholders, calibration, offset
        self.compare = compare
        self.review, self.metadata, self.on_event = review, metadata or {}, on_event
        self.recheck_decision = recheck_decision
        self.interactive_recheck = recheck_decision is not None
        self.report_ready = False
        self._initial_frame = None
        self._roi_refs = {}
        self._roi_segmenters = {}
        self.task: TaskModel | None = None
        self.cancellation = getattr(executor, "cancellation", None)
        self.current_target_id: str | None = None
        self.policy_hash = segmenter.sha256
        self.ledger = None
        self.roster_ids: tuple[str, ...] = ()
        self.roster_mask = None
        self.roster_plan: dict = {}
        self.ignored_new_total = 0
        self.boundary_notice: str | None = None
        self.used = (0, 0)
        self.cycles: list[dict] = []
        self.events: list[dict] = []
        self._cycle = 0
        self._seen_frames: set[str] = set()
        self.observation_position = load_position(executor.position_path).xy()
        self.position_zero_set_at = load_position(executor.position_path).zero_set_at
        self._reference_settings = None
        write_json(self.folder / "run_config.json", {"task_id": self.task_id, "version": VERSION,
            "config": asdict(config), "segmentation": segmenter.configuration, "segmentation_sha256": self.policy_hash,
            "offset": offset.to_dict(), "placeholders": placeholders.to_dict(),
            "motor_calibration": None if calibration is None else calibration.to_dict(),
            "metadata": self.metadata, "quality_policy": QUALITY_POLICY,
            "task_manifest_ref": "task_manifest.json" if review is not None else None,
            "evidence_boundary": "Mock 是软件证据；实物需独立记录"})

    def stage(self, phase: str) -> None:
        event = {"phase": phase, "cycle": self._cycle, "at": now()}
        self.events.append(event)
        if len(self.events) > 100:
            self.events.pop(0)
        with (self.folder / "stage_events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        write_json(self.folder / "progress.json", {"task_id": self.task_id, "events": self.events,
            "used_abs_steps": self.used, "completed_cycles": len(self.cycles)})
        print(_STAGE_TEXT.get(phase, phase), flush=True)
        tell = getattr(self.source, "tell", None)
        if callable(tell):
            tell(_STAGE_TEXT.get(phase, phase))
        self._emit("stage", phase=phase, explanation=_STAGE_TEXT.get(phase, phase), cycle=self._cycle,
                   current_target_id=self.current_target_id)

    def _emit(self, kind: str, **payload) -> None:
        if self.on_event is not None:
            self.on_event({"kind": kind, **payload,
                "task": None if self.task is None else self.task.ui_snapshot()})

    def _check_cancel(self) -> None:
        if self.cancellation is not None:
            self.cancellation.check()

    def run(self) -> dict:
        status, reasons = "ERROR", ("LOOP_NOT_STARTED",)
        active: dict | None = None
        outcome, pre, post, verification = None, None, None, None
        episode_written = True
        try:
            # 每一轮（含 RETRY）都重新拍前图；不复用旧动作和旧令牌。
            for number in (count(1) if self.interactive_recheck else range(1, self.config.max_cycles + 1)):
                self._check_cancel()
                self._cycle = number
                outcome, pre, post, verification = None, None, None, None
                episode_written = False
                cycle_dir = self.folder / f"cycle_{number:03d}"
                cycle_dir.mkdir()
                position = load_position(self.executor.position_path)
                if position.xy() != self.observation_position or position.zero_set_at != self.position_zero_set_at:
                    status, reasons = "HUMAN", ("ORIGINAL_OBSERVATION_POSITION_CHANGED",)
                    break
                self.stage("CAPTURE_PRE")
                pre = self._observe("pre", cycle_dir)
                if pre["observation"].quality_flags:
                    self._say(f"图像质量提示 {list(pre['observation'].quality_flags)}。不因此停止，仍逐次确认。")
                self.stage("EXTRACT_TARGETS")
                targets = extract_target_instances(pre["mask"], mask_ref=str(cycle_dir / "pre_mask.png"))
                if self.ledger is None:
                    self.ledger = TargetLedger(pre["observation"].observation_id, pre["mask"], targets)
                    self._publish_roster(pre)
                    if self.review is not None:
                        self._review_candidates(pre)
                elif not self.interactive_recheck:
                    self._advance_closed(pre["observation"].observation_id, pre["mask"])
                self.stage("SELECT_TARGET")
                selected = self._next_roster_id()
                h, w = pre["frame"].image.shape[:2]
                sequence = plan_sequence(self.ledger.pending(), strategy=self.config.sequence_strategy, start_px=(w // 2, h // 2))
                sequence_record = asdict(sequence)
                sequence_record["visit_order"] = list(self.roster_ids)
                sequence_record["selected_target_id"] = selected
                write_json(cycle_dir / "sequence.json", sequence_record)
                if selected is None:
                    if self.cycles and self.roster_ids:
                        self._say(f"第一次画面标注的 {len(self.roster_ids)} 块都已处理过。不再重复清洗。这次结束不是洁净验收。")
                    status, reasons = "SUCCESS", ("ALL_OBSERVED_TARGETS_COMPLETED",) if self.cycles else ("NO_TARGET",)
                    break
                entry = self.ledger.entries.get(selected)
                if entry is None and self.task is not None:
                    entry = self.task.targets[selected]
                instance = self.roster_plan[selected]
                self.current_target_id = selected
                if self.task is not None:
                    item = self.task.targets[selected]
                    if item.source == "algorithm" and not self.interactive_recheck:
                        match = match_target_instance(pre_target=instance, pre_mask=self.roster_mask, post_mask=pre["mask"])
                        matched = next((target for target in targets if target.component_label == match.post_label), None)
                        drift = None if matched is None else sum((a - b) ** 2 for a, b in zip(matched.centroid_px, instance.centroid_px)) ** 0.5
                        if match.status != "matched" or matched is None or drift > 2.0:
                            raise TargetIdentityError(f"{selected}:CURRENT_LOCATION_NOT_VERIFIED")
                    elif item.source == "manual" and number > 1 and not self.executor.confirm({"phase": "manual_location", "target_id": selected,
                            "centroid_px": instance.centroid_px, "bbox": instance.bbox}):
                        raise TargetIdentityError(f"{selected}:MANUAL_LOCATION_NOT_CONFIRMED")
                    item.execution = "RUNNING"
                    self.task.persist()
                    self._emit("target")
                self.stage("PLAN_TARGET")
                target_mask = self.task.manual_masks[selected] if self.task is not None and selected in self.task.manual_masks else extract_target_mask(self.roster_mask, instance)
                if self.interactive_recheck:
                    reference = self._roi_refs.get(selected)
                    if reference is None:
                        reference = build_roi_reference(self._initial_frame.image, target_mask,
                            all_target_mask=self.roster_mask | target_mask, target_id=selected, frame_id=self._initial_frame.frame_id)
                        frozen = FrozenRoiSegmenter(self.segmenter, self._initial_frame.image, reference.roi, target_mask,
                            excluded_mask=reference.all_target_mask & ~reference.target_mask)
                        baseline = frozen.mask(self._initial_frame.image)
                        baseline.setflags(write=False)
                        import numpy as np
                        subject = (baseline > 0) & reference.measurement_domain
                        subject.setflags(write=False)
                        all_targets = reference.all_target_mask | subject
                        all_targets.setflags(write=False)
                        reference = replace(reference, target_mask=subject, all_target_mask=all_targets,
                            initial_area_px=int(np.count_nonzero(subject)))
                        self._roi_segmenters[selected] = frozen
                        write_json(self.folder / f"roi_reference_{selected}.json", {"roi": reference.roi,
                            "initial_area_px": reference.initial_area_px, "segmentation": frozen.to_dict()})
                        write_png(self.folder / f"roi_reference_{selected}_mask.png", baseline)
                        self._roi_refs[selected] = reference
                    pre["mask"] = self._roi_segmenters[selected].mask(pre["frame"].image)
                    write_png(cycle_dir / "pre_mask.png", pre["mask"])
                    pre["metadata"]["roi_segmentation"] = self._roi_segmenters[selected].to_dict()
                    pre["metadata"]["initial_scan_measurement"] = pre["metadata"]["measurement"]
                    import numpy as np
                    ys, xs = np.nonzero(pre["mask"])
                    pre["metadata"]["measurement"] = asdict(ContaminationMeasurement(len(xs),
                        None if not len(xs) else (float(xs.mean()), float(ys.mean())), 1.0 if len(xs) else 0.0,
                        0.0, str(cycle_dir / "pre_mask.png"), len(extract_target_instances(pre["mask"])), "frozen-roi-segmentation-v1"))
                    write_json(cycle_dir / "pre.json", pre["metadata"])
                    location = validate_roi_location(reference, pre["frame"].image, pre["mask"])
                    write_json(cycle_dir / "location_check.json", asdict(location))
                    if not location.valid:
                        raise TargetIdentityError(f"{selected}:ROI_REFERENCE_POSITION_UNCERTAIN:{','.join(location.reason_codes)}")
                    # 原像素区域不变；本轮规划用当前残留，而不是重放首次动作。
                    target_mask = (pre["mask"] > 0) & reference.measurement_domain
                    import numpy as np
                    target_mask = target_mask.astype(np.uint8) * 255
                    if not np.any(target_mask):
                        raise TargetIdentityError(f"{selected}:ROI_PRE_EMPTY:原区域未检出，禁止盲目复喷")
                    # 不重新配对新的 T 编号；允许污渍减少导致质心在冻结区域内变化。
                    current = extract_target_instances(target_mask)
                    if not current:
                        raise TargetIdentityError(f"{selected}:ROI_FOREGROUND_IDENTITY_UNCERTAIN")
                    instance = max(current, key=lambda target: target.area_px)
                    if len(current) > 1:
                        self._say(f"{selected} 原区域剩余 {len(current)} 片，仍保留同一污渍编号；本轮规划最大残片，复检统计全部残留。")
                        target_mask = extract_target_mask(target_mask, instance)
                verification_mask = target_mask if self.task is not None and selected in self.task.manual_masks else self.roster_mask
                write_png(cycle_dir / "selected_target_mask.png", target_mask)
                normalized, _ = stage2_placeholders(self.placeholders, self.roster_mask.shape, self.calibration)
                plan = plan_cleaning(target_mask, policy=resolve_plan_policy(normalized))
                self.stage("BUILD_MOTION")
                geometry = build_cycle_geometry(plan, base=self.placeholders, calibration=self.calibration,
                    offset=self.offset, observation_position=self.observation_position,
                    task_id=self.task_id, used_abs_steps=(0, 0) if self.interactive_recheck else self.used,
                    budget=self.config.step_budget, real=self.config.real)
                write_json(cycle_dir / "geometry.json", geometry.to_dict())
                write_json(cycle_dir / "path_preview.json", geometry.preview.to_dict())
                write_png(cycle_dir / "path_overlay.png", draw_path_overlay(pre["frame"].image, geometry.preview))
                active = {"cycle": number, "target_id": selected, "frame_target_id": instance.target_id,
                    "component_label": instance.component_label, "retry_count": entry.retry_count,
                    "area_px": instance.area_px, "centroid_px": instance.centroid_px,
                    "roster_index": self.roster_ids.index(selected) + 1, "roster_total": len(self.roster_ids),
                    "roster_ids": list(self.roster_ids), "next_target_id": self._following_id(selected),
                    "pre_observation": asdict(pre["observation"]), "sequence": sequence_record,
                    "geometry": geometry.to_dict(), "segmentation_sha256": self.policy_hash,
                    "offset": self.offset.to_dict(), "position_zero_set_at": self.position_zero_set_at,
                    "path_overlay": str(cycle_dir / "path_overlay.png"), "used_abs_steps_before": self.used}
                write_json(cycle_dir / "cycle.json", active)
                self._emit("geometry", geometry=geometry.to_dict(), offset=self.offset.to_dict(),
                    motor_calibration=None if self.calibration is None else self.calibration.to_dict(),
                    calibration_valid=not geometry.reasons, real=self.config.real)
                if hasattr(self.source, "select_target"):
                    self.source.select_target(instance)  # Mock 的图像替身只模拟被选中的目标。
                measurement = ContaminationMeasurement(instance.area_px, instance.centroid_px,
                    pre["measurement"].uncertainty_px, pre["measurement"].confidence,
                    str(cycle_dir / "selected_target_mask.png"), 1, pre["measurement"].algorithm_version)
                outcome = self.executor.execute(geometry=geometry, observation=pre["observation"],
                    measurement=measurement, preview=active, stage=self.stage)
                notice = explain_travel_block(geometry.outbound_request, geometry.return_request)
                if notice and outcome.status not in {"MOVED", "RETURNED"}:
                    self.boundary_notice = notice
                    self._say(notice)
                self.used = tuple(a + b for a, b in zip(self.used, outcome.used_abs_steps))
                active["execution"] = outcome.to_dict()
                write_json(cycle_dir / "execution.json", outcome.to_dict())
                post, verification = None, None
                if outcome.status == "MOVED":
                    self._say("去程和回程已走完。这一轮没有喷水，也没有复检。")
                    self._episode(cycle_dir, pre, post, outcome, verification)
                    episode_written = True
                    active["used_abs_steps_after"] = self.used
                    self.cycles.append(active)
                    if self.task is not None:
                        self.task.update_attempt(selected, active, quality="UNCERTAIN", finished=False)
                    active = None
                    status, reasons = "SUCCESS", outcome.reasons
                    break
                if outcome.status == "RETURNED":
                    if not self.executor.confirm({**active, "phase": "recheck"}):
                        self._episode(cycle_dir, pre, post, outcome, verification, failure=("recheck", ("HUMAN_DECLINED_RECHECK",)))
                        episode_written = True
                        active["used_abs_steps_after"] = self.used
                        active["failure"] = {"status": "HUMAN", "reasons": ["HUMAN_DECLINED_RECHECK"]}
                        self.cycles.append(active)
                        write_json(cycle_dir / "cycle.json", active)
                        active = None
                        status, reasons = "HUMAN", ("HUMAN_DECLINED_RECHECK",)
                        break
                    if self.interactive_recheck:
                        post, verification, route = self._interactive_recheck(active, pre, outcome, entry, cycle_dir)
                    else:
                        self.stage("CAPTURE_POST")
                        post = self._observe("post", cycle_dir, after=outcome.returned_at)
                        self.stage("VERIFY_TARGET")
                        comparable, evidence = self._comparable(pre, post, outcome)
                        active["comparability"] = evidence
                        verification = verify_single_target(task_id=self.task_id, pre=pre["observation"], post=post["observation"],
                            pre_target=instance, pre_mask=verification_mask, post_mask=post["mask"],
                            receipt=outcome.receipt, images_comparable=comparable)
                        active["verification"] = asdict(verification)
                        if self.task is not None:
                            quality = evaluate_target(source=self.task.targets[selected].source, execution=active["execution"],
                                verification=active["verification"], comparability=evidence, mode="real" if self.config.real else "mock")
                            active["quality_evidence"] = quality.to_dict()
                            self.task.targets[selected].quality = quality.quality
                            self._emit("verification", target_id=selected, verification=active["verification"],
                                       quality_evidence=quality.to_dict(), pre=pre["metadata"], post=post["metadata"])
                        write_json(cycle_dir / "verification.json", asdict(verification))
                        _say_recheck(self, verification)
                        route = self._after_recheck(active, verification, entry, post)
                    failure = None if route in {"next", "retry", "success_done", "cycle_limit"} else ("human_gate", route)
                    self._episode(cycle_dir, pre, post, outcome, verification, failure=failure)
                    episode_written = True
                    active["used_abs_steps_after"] = self.used
                    self.cycles.append(active)
                    if self.task is not None:
                        self.task.update_attempt(selected, active, quality=active["quality_evidence"]["quality"],
                            finished=route in {"next", "success_done", "cycle_limit"})
                    write_json(cycle_dir / "cycle.json", active)
                    active = None
                    if route in {"next", "retry"}:
                        continue
                    if route == "success_done":
                        status, reasons = "SUCCESS", ("ALL_OBSERVED_TARGETS_COMPLETED",)
                        break
                    if route == "cycle_limit":
                        status, reasons = "HUMAN", ("MAX_CYCLES_REACHED",)
                        break
                    status, reasons = "HUMAN", route
                    break
                self._episode(cycle_dir, pre, post, outcome, verification)
                episode_written = True
                active["used_abs_steps_after"] = self.used
                self.cycles.append(active)
                if self.task is not None:
                    self.task.update_attempt(selected, active, quality="UNCERTAIN", finished=False)
                write_json(cycle_dir / "cycle.json", active)
                active = None
                status, reasons = outcome.status, outcome.reasons
                break
            else:
                status, reasons = "HUMAN", ("MAX_CYCLES_REACHED",)
        except TargetIdentityError as exc:
            status, reasons = "HUMAN", ("TARGET_IDENTITY_UNCERTAIN", str(exc))
        except OperationCancelled as exc:
            status, reasons = "CANCELLED", (str(exc),)
        except (Exception, KeyboardInterrupt) as exc:
            status, reasons = "ERROR", ("INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else getattr(exc, "reason_code", str(exc) or type(exc).__name__),)
        finally:
            cleanup_errors = []
            for resource, code in ((self.executor, "EXECUTOR_CLOSE_FAILED"), (self.source, "FRAME_SOURCE_CLOSE_FAILED")):
                try:
                    resource.close()
                except (Exception, KeyboardInterrupt) as exc:
                    cleanup_errors.append({"reason": code, "type": type(exc).__name__, "message": str(exc)})
            if cleanup_errors:
                reasons = (() if status == "SUCCESS" else reasons) + tuple(error["reason"] for error in cleanup_errors)
                status = "ERROR"
            if active is not None:
                if outcome is not None and not episode_written:
                    self._episode(cycle_dir, pre, post, outcome, verification, failure=(self.events[-1]["phase"], reasons))
                active["failure"] = {"status": status, "reasons": reasons}
                self.cycles.append(active)
                if self.task is not None:
                    self.task.update_attempt(active["target_id"], active, quality="UNCERTAIN", finished=False)
                write_json(self.folder / f"cycle_{self._cycle:03d}" / "cycle.json", active)
            self.stage(status)
            if self.task is not None:
                self.task.finish("CANCELLED" if status == "CANCELLED" else "COMPLETED" if status == "SUCCESS" else "FAILED")
            if status != "SUCCESS":
                write_json(self.folder / "failure.json", {"status": status, "reasons": reasons, "cycle": self._cycle,
                    "position": load_position(self.executor.position_path).to_dict()})
            summary = {"task_id": self.task_id, "version": VERSION, "mode": "real" if self.config.real else "mock",
                "status": status, "reasons": reasons, "cycles": self.cycles, "used_abs_steps": self.used,
                "cleanup_errors": cleanup_errors,
                "targets": {} if self.ledger is None else self.ledger.to_dict(),
                "position": load_position(self.executor.position_path).to_dict(),
                "segmentation_sha256": self.policy_hash, "events": self.events,
                "evidence_boundary": "规则复检通过不是已验收洁净标准；MCV1 DONE 只表示输出完成",
                "initial_target_ids": list(self.task.initial_ids if self.task is not None else self.roster_ids),
                "ignored_new_components": self.ignored_new_total,
                "boundary_notice": self.boundary_notice}
            summary.update(report_ready=self.report_ready and status == "SUCCESS", rewash_limit=None if self.interactive_recheck else self.config.max_retries_per_target,
                stage_events_ref="stage_events.jsonl", recheck_mode="frozen_roi_human_decision" if self.interactive_recheck else "legacy_bounded_cli")
            if self.task is not None:
                rows = [item.to_dict() for item in self.task.targets.values()]
                quality, quality_reasons = final_quality(rows, workflow_status=self.task.state, mode=summary["mode"])
                summary.update(workflow_status=self.task.state, quality_status=quality, quality_reasons=quality_reasons,
                    task_manifest_ref="task_manifest.json", review_log_ref="review_log.jsonl",
                    candidate_target_ids=list(self.task.targets), execution_target_ids=list(self.task.execution_ids),
                    cancellation=None if self.cancellation is None else {"requested_at": self.cancellation.requested_at,
                        "reason": self.cancellation.reason if self.cancellation.event.is_set() else None})
            write_json(self.folder / "serial.json", {"events": self.executor.session.serial_events,
                "preserved_replies": [line.decode("ascii", errors="replace").strip() for line in self.executor.session.preserved_replies]})
            write_json(self.folder / "summary.json", summary)
            self._emit("completed", summary=summary, folder=str(self.folder.resolve()))
        return summary

    def _observe(self, phase, folder, after=None):
        frame = self.source.capture(phase, after=after)
        self.stage("SEGMENT_PRE" if phase == "pre" else "SEGMENT_POST")
        if frame.frame_id in self._seen_frames:
            raise ValueError("REUSED_FRAME_ID")
        self._seen_frames.add(frame.frame_id)
        if not frame.captured_at or datetime.fromisoformat(frame.captured_at).tzinfo is None:
            raise ValueError("CAPTURE_TIMESTAMP_INVALID")
        if after is not None and datetime.fromisoformat(frame.captured_at) <= datetime.fromisoformat(after):
            raise ValueError("POST_CAPTURE_NOT_AFTER_RETURN")
        if phase == "pre":
            snapshot = (frame.source_id, frame.image.shape, dict(frame.settings))
            if self._reference_settings is None:
                self._reference_settings = snapshot
            elif self._reference_settings != snapshot:
                raise TargetIdentityError("PRE_CAPTURE_SETTINGS_CHANGED")
        if self.segmenter.sha256 != self.policy_hash:
            raise ValueError("SEGMENTATION_POLICY_CHANGED")
        path = folder / f"{phase}.png"
        write_png(path, frame.image)
        metrics, quality = measure_image_quality(frame.image)
        observation = build_observation(task_id=self.task_id, frame_id=frame.frame_id, raw_image_ref=str(path), quality=quality, software_version=VERSION)
        if phase == "post" and self.interactive_recheck:
            # 后图只采用首次冻结阈值与原像素ROI；全图目标扫描仅为可选二次确认。
            import numpy as np
            mask = self._roi_segmenters[self.current_target_id].mask(frame.image)
            area = int(np.count_nonzero(mask))
            ys, xs = np.nonzero(mask)
            measurement = ContaminationMeasurement(area, None if not area else (float(xs.mean()), float(ys.mean())),
                0.0 if not area else 1.0, 0.0, str(folder / f"{phase}_mask.png"),
                len(extract_target_instances(mask)), "frozen-roi-segmentation-v1")
        else:
            segmentation = self.segmenter(frame.image)
            mask, measurement = segmentation.mask, segmentation.measurement
        write_png(folder / f"{phase}_mask.png", mask)
        metadata = {"observation": asdict(observation), "captured_at": frame.captured_at, "source_id": frame.source_id,
            "settings": frame.settings, "quality_metrics": asdict(metrics), "quality": asdict(quality),
            "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "segmentation_sha256": self.policy_hash,
            "measurement": asdict(measurement)}
        if phase == "post" and self.interactive_recheck:
            metadata["roi_segmentation"] = self._roi_segmenters[self.current_target_id].to_dict()
        write_json(folder / f"{phase}.json", metadata)
        return {"frame": frame, "observation": observation, "mask": mask, "measurement": measurement, "metadata": metadata}

    def _comparable(self, pre, post, outcome):
        a, b = pre["frame"], post["frame"]
        evidence = {"same_size": a.image.shape == b.image.shape, "same_source": a.source_id == b.source_id,
            "same_settings": a.settings == b.settings, "same_policy": pre["metadata"]["segmentation_sha256"] == post["metadata"]["segmentation_sha256"],
            "returned_to_overview": outcome.status == "RETURNED" and outcome.returned_at is not None and load_position(self.executor.position_path).xy() == self.observation_position,
            "quality_ok": not pre["observation"].quality_flags and not post["observation"].quality_flags,
            "fresh_after_return": datetime.fromisoformat(b.captured_at) > datetime.fromisoformat(outcome.returned_at)}
        # execute 已核对原观察位；这里再记录证据。禁止无条件使用 images_comparable=True。
        evidence["pair_confirmed"] = False
        if all(evidence[key] for key in evidence if key != "pair_confirmed") and self.compare is not None:
            evidence["pair_confirmed"] = bool(self.compare({"pre": pre["metadata"], "post": post["metadata"]}))
        comparable = all(evidence.values())
        evidence["pair_evidence_kind"] = "operator" if self.config.real else "declared_mock_view"
        return comparable, evidence

    def _after_recheck(self, active, verification, entry, post):
        """复检数字先显示。结束这一块并开始下一块，必须再等一次 yes。"""

        result = verification.result
        selected = active["target_id"]
        if result.next_route is NextRoute.STOP and "REPLAY_THRESHOLD_MET" in result.reason_codes:
            if not self.executor.confirm({**active, "phase": "next", "verification": asdict(verification),
                    "cycles_left": self.config.max_cycles - self._cycle, "next_target_id": self._following_id(selected)}):
                return ("HUMAN_STOPPED_AFTER_RECHECK",) + result.reason_codes
            return self._finish_stain_and_continue(post, selected, cleaned=True)
        if result.next_route is NextRoute.RETRY:
            if entry.retry_count >= self.config.max_retries_per_target:
                if self.task is not None:
                    if self.executor.confirm({**active, "phase": "next_incomplete", "verification": asdict(verification),
                            "stop_reasons": ("MAX_RETRIES_PER_TARGET_REACHED",) + result.reason_codes,
                            "cycles_left": self.config.max_cycles - self._cycle, "next_target_id": self._following_id(selected)}):
                        return self._finish_stain_and_continue(post, selected, cleaned=False)
                    return ("HUMAN_STOPPED_AFTER_RESIDUE",) + result.reason_codes
                self.executor.confirm({**active, "phase": "stop", "verification": asdict(verification),
                    "stop_reasons": ("MAX_RETRIES_PER_TARGET_REACHED",) + result.reason_codes})
                return ("MAX_RETRIES_PER_TARGET_REACHED",) + result.reason_codes
            if not self.executor.confirm({**active, "phase": "retry", "verification": asdict(verification)}):
                return ("HUMAN_DECLINED_RETRY",) + result.reason_codes
            self.stage("RETRY")
            entry.retry_count += 1
            self._say(f"再试 {selected}。这还不是下一块。")
            return "retry"
        accepted = self.executor.confirm({**active, "phase": "next_incomplete", "verification": asdict(verification),
            "stop_reasons": result.reason_codes, "cycles_left": self.config.max_cycles - self._cycle,
            "next_target_id": self._following_id(selected),
            "quality_flags": list((active.get("pre_observation") or {}).get("quality_flags") or ())})
        if not accepted:
            return ("HUMAN_STOPPED_AFTER_INCONCLUSIVE_RECHECK",) + result.reason_codes
        return self._finish_stain_and_continue(post, selected, cleaned=False)

    def _interactive_recheck(self, active, pre, outcome, entry, cycle_dir):
        """每次复检都落盘后才请求人决定；重拍不产生新运动或喷洗。"""
        selected = active["target_id"]
        active["rechecks"] = []
        for index in count(1):
            self._check_cancel()
            directory = cycle_dir / f"recheck_{index:03d}"
            directory.mkdir()
            self.stage("CAPTURE_POST")
            post = self._observe("post", directory, after=outcome.returned_at)
            self.stage("VERIFY_TARGET")
            comparable, evidence = self._comparable(pre, post, outcome)
            secondary = None
            if requires_neighbor_confirmation(self._roi_refs[selected], pre["frame"].image, post["frame"].image):
                self._say(f"{selected} 周围区域有变化，进行一次全图二次确认；当前污渍面积仍按原固定区域计算。")
                secondary = self.segmenter(post["frame"].image).mask
                write_png(directory / "secondary_mask.png", secondary)
                evidence["secondary_mask_ref"] = (directory / "secondary_mask.png").relative_to(self.folder).as_posix()
                evidence["secondary_mask_sha256"] = hashlib.sha256((directory / "secondary_mask.png").read_bytes()).hexdigest()
            roi = verify_roi_pair(reference=self._roi_refs[selected],
                pre_image=pre["frame"].image, post_image=post["frame"].image,
                pre_mask=pre["mask"], post_mask=post["mask"],
                pre_frame_id=pre["frame"].frame_id, post_frame_id=post["frame"].frame_id,
                pre_quality_flags=pre["observation"].quality_flags, post_quality_flags=post["observation"].quality_flags,
                secondary_post_mask=secondary)
            evidence["roi_alignment_valid"] = roi.valid
            valid = comparable and roi.valid
            passed = valid and roi.removal_rate is not None and roi.removal_rate >= QUALITY_POLICY["threshold"]
            reasons = ("REPLAY_THRESHOLD_MET",) if passed else ("REPLAY_RESIDUE_REMAINS",) if valid else tuple(dict.fromkeys((*roi.reason_codes, "ROI_COMPARABILITY_NOT_PROVEN")))
            result = VerificationResult(self.task_id, pre["observation"].observation_id, post["observation"].observation_id,
                roi.post_area_px, roi.removal_rate if valid or roi.removal_rate == 0 else None, False,
                NextRoute.STOP if passed else NextRoute.RETRY if valid else NextRoute.HUMAN, reasons)
            verification = TargetVerification(selected, roi.pre_area_px, roi.post_area_px, result.removal_rate,
                roi.alignment_confidence, result, "matched" if valid else "unmatched")
            raw = asdict(verification)
            quality = evaluate_target(source=self.task.targets[selected].source, execution=active["execution"],
                verification=raw, comparability=evidence, mode="real" if self.config.real else "mock")
            refs = save_roi_evidence(roi, directory / "roi")
            refs = {name: (directory / "roi" / path).relative_to(self.folder).as_posix() for name, path in refs.items()}
            hashes = {name: hashlib.sha256((self.folder / path).read_bytes()).hexdigest() for name, path in refs.items()}
            analysis = {**roi.to_dict(), "segmentation": self._roi_segmenters[selected].to_dict()}
            check = {"index": index, "pre": pre["metadata"], "post": post["metadata"],
                "verification": raw, "comparability": evidence, "roi_analysis": analysis,
                "roi_evidence": refs, "roi_evidence_sha256": hashes, "quality_evidence": quality.to_dict()}
            active["rechecks"].append(check)
            active.update(verification=raw, comparability=evidence, roi_analysis=analysis,
                roi_evidence=refs, roi_evidence_sha256=hashes, quality_evidence=quality.to_dict())
            self.task.targets[selected].quality = quality.quality
            # 兼容旧读档键，同时保留每次重拍的独立目录。
            for name in ("post.png", "post_mask.png", "post.json"):
                (cycle_dir / name).write_bytes((directory / name).read_bytes())
            write_json(directory / "verification.json", raw)
            write_json(cycle_dir / "verification.json", raw)
            write_json(cycle_dir / "cycle.json", active)
            self._emit("verification", target_id=selected, verification=raw, quality_evidence=quality.to_dict(),
                roi_analysis=analysis, pre=pre["metadata"], post=post["metadata"],
                pre_crop=roi.pre_crop, post_crop=roi.post_crop, overlay=roi.overlay)
            _say_recheck(self, verification)
            # 末块不能把未通过的“不复洗”解释为打印许可；仍可暂停留档。
            manual_accept_allowed = comparable and roi.alignment.valid and roi.reference_alignment.valid and (
                roi.valid or set(roi.reason_codes).issubset({"ROI_POST_EMPTY"}))
            choices = ("rewash", "next", "retake", "pause") if self._following_id(selected) or passed or manual_accept_allowed else ("rewash", "retake", "pause")
            decision = self.recheck_decision({"target_id": selected, "cycle": self._cycle, "recheck_index": index,
                "verification": raw, "roi_analysis": analysis, "quality_evidence": quality.to_dict(),
                "next_target_id": self._following_id(selected), "last_target": self._following_id(selected) is None,
                "choices": choices, "passed": passed, "manual_accept_allowed": manual_accept_allowed,
                "require_reason": not self._following_id(selected) and not passed})
            if not isinstance(decision, dict) or decision.get("choice") not in choices:
                raise ValueError("INVALID_RECHECK_DECISION")
            self._check_cancel()
            decision = {**decision, "at": now(), "operator": self.metadata.get("operator", "未记录")}
            check["decision"] = decision
            active["recheck_decision"] = decision
            self.task.audit("recheck_decision", target_id=selected, cycle=self._cycle, recheck_index=index, **decision)
            write_json(directory / "decision.json", decision)
            write_json(cycle_dir / "cycle.json", active)
            if decision["choice"] == "retake":
                self._say(f"{selected} 仅重拍复检，不移动、不喷水；先确认复位和成像条件。")
                continue
            if decision["choice"] == "rewash":
                entry.retry_count += 1
                self.task.targets[selected].retry_count = entry.retry_count
                self.stage("RETRY")
                self._say(f"人工要求第 {entry.retry_count} 次复洗 {selected}；重新采图、规划及授权。")
                return post, verification, "retry"
            if decision["choice"] == "pause":
                return post, verification, ("HUMAN_PAUSED_AFTER_RECHECK",)
            human_final_pass = not self._following_id(selected) and manual_accept_allowed and bool(decision.get("reason", "").strip())
            if not self._following_id(selected) and not passed and not human_final_pass:
                return post, verification, ("FINAL_RECHECK_NOT_ACCEPTED",)
            if passed or human_final_pass:
                # 实物自动标准尚未验证；此处只有明确的人认可才登记人工结论。
                manual = {"operator": decision["operator"], "reason": decision.get("reason") or "操作员核对本轮固定区域复检后认可结果",
                    "conclusion": "CLEANED", "evidence_refs": list(refs.values())}
                quality = evaluate_target(source=self.task.targets[selected].source, execution=active["execution"],
                    verification=raw, comparability=evidence, mode="real" if self.config.real else "mock", manual=manual)
                active["quality_evidence"] = quality.to_dict()
                check["quality_evidence"] = quality.to_dict()
                self.task.targets[selected].quality = quality.quality
            if not self._following_id(selected):
                self.report_ready = passed or human_final_pass
            return post, verification, self._finish_stain_and_continue(post, selected, cleaned=passed or human_final_pass)

    def _finish_stain_and_continue(self, post, selected, *, cleaned: bool):
        if self.interactive_recheck:
            if selected in self.ledger.entries:
                self.ledger.entries[selected].completed = True
        else:
            self._advance_closed(post["observation"].observation_id, post["mask"], completed_id=selected if selected in self.ledger.entries else None)
        if self.task is not None:
            self.task.targets[selected].finished = True
        self.stage("STAIN_RECORDED")
        finished = sum(1 for item in self.ledger.entries.values() if item.completed)
        nxt = self._following_id(selected)
        if nxt is None:
            if cleaned:
                self._say(f"{_ordinal(finished)}污渍已清洗完成。编号名单已全部处理，不再重复清洗。这次结束不是洁净验收。")
            else:
                self._say(f"{_ordinal(finished)}污渍的本次处理已结束，规则没有把它记成洗净。编号名单已全部处理。")
            return "success_done"
        if not self.interactive_recheck and self._cycle >= self.config.max_cycles:
            ending = "已清洗完成" if cleaned else "的本次处理已结束，规则没有把它记成洗净"
            self._say(f"{_ordinal(finished)}污渍{ending}。允许的轮数已经用完，{nxt} 还在名单里，但不会开始。")
            return "cycle_limit"
        if cleaned:
            self._say(f"{_ordinal(finished)}污渍已清洗完成，现在进行{_ordinal(finished + 1)} {nxt}。")
        else:
            self._say(f"{_ordinal(finished)}污渍的本次处理已结束，规则没有把它记成洗净。现在进行{_ordinal(finished + 1)} {nxt}。")
        return "next"

    def _next_roster_id(self) -> str | None:
        for stable in self.roster_ids:
            entry = self.task.targets.get(stable) if self.task is not None else self.ledger.entries.get(stable)
            if self.task is not None:
                if entry is not None and not entry.finished:
                    return stable
                continue
            if entry is not None and not entry.completed:
                return stable
        return None

    def _following_id(self, selected: str) -> str | None:
        seen = False
        for stable in self.roster_ids:
            if stable == selected:
                seen = True
                continue
            unfinished = not self.task.targets[stable].finished if self.task is not None else not self.ledger.entries[stable].completed
            if seen and unfinished:
                return stable
        return None

    def _say(self, message: str) -> None:
        print(message, flush=True)
        tell = getattr(self.source, "tell", None)
        if callable(tell):
            tell(message)

    def _publish_roster(self, pre) -> None:
        self.roster_ids = tuple(self.ledger.entries)
        self.roster_mask = pre["mask"].copy()
        self._initial_frame = pre["frame"]
        self.roster_plan = {stable: entry.instance for stable, entry in self.ledger.entries.items()}
        if not self.roster_ids:
            return
        details = "，".join(
            f"{stable}（{entry.instance.area_px:.0f} px）" for stable, entry in self.ledger.entries.items()
        )
        self._say(f"第一次画面共 {len(self.roster_ids)} 块，按编号清洗：{details}。不漏，也不多洗。")
        view = _draw_roster(pre["frame"].image, self.ledger.entries)
        write_png(self.folder / "initial_roster.png", view)
        show = getattr(self.source, "show_roster", None)
        if callable(show):
            show(view)

    def _review_candidates(self, pre) -> None:
        self.task = TaskModel(folder=self.folder, task_id=self.task_id, image_shape=pre["mask"].shape,
            entries=self.ledger.entries, metadata={**self.metadata, "mode": "real" if self.config.real else "mock"},
            references={"initial_image": pre["observation"].raw_image_ref, "initial_mask": str(Path(pre["observation"].raw_image_ref).with_name("pre_mask.png")),
                "initial_image_sha256": pre["metadata"]["raw_sha256"], "segmentation_sha256": self.policy_hash,
                "motor_calibration": None if self.calibration is None else self.calibration.to_dict(), "offset": self.offset.to_dict()},
            policy={**QUALITY_POLICY, "max_location_drift_px": 2.0, "step_budget": self.config.step_budget,
                "pump_duration_ms": self.executor.pump_duration_ms, "max_cycles": self.config.max_cycles})
        for stable, candidate in self.task.targets.items():
            self.prepare_candidate(stable)
        self.task.persist()
        self.stage("REVIEW_CANDIDATES")
        self.review(self.task, pre, self.prepare_candidate)
        self._check_cancel()
        if self.task.state != "RUNNING":
            raise PermissionError("HUMAN_LOCK_AND_START_REQUIRED")
        self.roster_ids = self.task.execution_ids
        self.roster_plan.update({stable: item.instance for stable, item in self.task.targets.items()})

    def prepare_candidate(self, stable: str) -> None:
        from microcleaning.contracts import SafetyOutcome
        from microcleaning.control_system.safety.motion_gate import evaluate_motion
        candidate = self.task.targets[stable]
        mask = self.task.manual_masks[stable] if candidate.source == "manual" else extract_target_mask(self.roster_mask, candidate.instance)
        normalized, _ = stage2_placeholders(self.placeholders, mask.shape, self.calibration)
        plan = plan_cleaning(mask, policy=resolve_plan_policy(normalized))
        geometry = build_cycle_geometry(plan, base=self.placeholders, calibration=self.calibration, offset=self.offset,
            observation_position=self.observation_position, task_id=self.task_id, budget=self.config.step_budget, real=self.config.real)
        reasons = list(geometry.reasons)
        for request in (geometry.outbound_request, geometry.return_request):
            decision = evaluate_motion(request)
            if decision.outcome is SafetyOutcome.DENY:
                reasons.extend(decision.reason_codes)
        if candidate.source == "manual":
            if "OVERLAPS_EXISTING_TARGET" in candidate.eligibility_reasons:
                reasons.append("OVERLAPS_EXISTING_TARGET")
            write_png(self.folder / f"manual_{stable}_mask.png", mask)
        self.task.set_plan(stable, {"action": plan.strategy.value, "geometry": geometry.to_dict(),
            "centroid_px": candidate.instance.centroid_px, "pump_duration_ms": self.executor.pump_duration_ms}, tuple(dict.fromkeys(reasons)))

    def _advance_closed(self, observation_id: str, mask, *, completed_id: str | None = None) -> None:
        try:
            self.ledger.advance(observation_id, mask, extract_target_instances(mask), completed_id=completed_id, allow_new=False)
        except TargetIdentityError as exc:
            if completed_id is not None:
                self.ledger.entries[completed_id].completed = True
            nxt = None if completed_id is None else self._following_id(completed_id)
            if nxt:
                self._say(f"{completed_id} 这一帧对不上。按编号继续 {nxt}。")
            else:
                self._say(f"这一帧对不上第一次标注（{exc}）。名单不因此漏掉下一块。")
            return
        self.ignored_new_total += self.ledger.ignored_new
        if self.ledger.ignored_new:
            self._say(f"新分出 {self.ledger.ignored_new} 个区域，不加入名单。")

    def _episode(self, folder, pre, post, outcome, verification, failure=None):
        failures = []
        if outcome.status != "RETURNED":
            failures.append(FailureRecord(f"failure_{uuid4().hex[:12]}", self.task_id, "execute", "error" if outcome.status == "ERROR" else "info", outcome.reasons, True, "核对本轮记录；未知位置需人工重新对位"))
        if failure:
            failures.append(FailureRecord(f"failure_{uuid4().hex[:12]}", self.task_id, failure[0], "error", failure[1], True, "检查本轮图像及执行回执；不得重放旧动作"))
        result = verification.result if verification else VerificationResult(self.task_id, pre["observation"].observation_id, None, None, None, False, NextRoute.HUMAN, failure[1] if failure else outcome.reasons)
        episode = Episode(f"episode_{uuid4().hex[:12]}", self.task_id, "closed_loop_real" if self.config.real else "closed_loop_mock", VERSION,
            pre["observation"], outcome.state, outcome.request, outcome.pump_decision, outcome.receipt,
            None if post is None else post["observation"], result, failures)
        write_episode(episode, folder / "episodes")


def _ordinal(number: int) -> str:
    names = {1: "第一个", 2: "第二个", 3: "第三个", 4: "第四个", 5: "第五个", 6: "第六个", 7: "第七个", 8: "第八个", 9: "第九个"}
    return names.get(number, f"第{number}个")


def _say_recheck(loop, verification) -> None:
    from microcleaning.control_system.reporting.quality_report import explain
    result = verification.result
    rate = "无有效数值（请检查复位和成像证据）" if verification.removal_rate is None else f"{verification.removal_rate:.1%}"
    match = {"matched": "原区域可以比较", "unmatched": "证据不足，不能判定", "ambiguous": "区域对应不唯一"}.get(verification.match_status, "待人工复核")
    reasons = "；".join(explain(code) for code in result.reason_codes)
    loop._say(
        f"复检结果：污渍 {verification.target_id}，{match}，清洗率 {rate}。{reasons}。请人工选择复洗或结束本目标。"
    )


def _draw_roster(image, entries):
    import cv2

    view = image.copy()
    for stable, entry in entries.items():
        instance = entry.instance
        if instance is None:
            continue
        x, y = int(round(instance.centroid_px[0])), int(round(instance.centroid_px[1]))
        cv2.circle(view, (x, y), 14, (0, 255, 255), 2, cv2.LINE_8)
        cv2.putText(view, stable, (min(view.shape[1] - 48, x + 8), max(16, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_8)
    return view


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_png(path: Path, image) -> None:
    import cv2
    okay, buffer = cv2.imencode(".png", image)
    if not okay:
        raise OSError("IMAGE_WRITE_FAILED")
    path.write_bytes(buffer.tobytes())
