"""复用现有模块的有界闭环；相机、分割与执行器由入口注入。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from microcleaning.contracts import Episode, FailureRecord, NextRoute, VerificationResult
from microcleaning.control_system.orchestration.target_adapter import TargetIdentityError, TargetLedger
from microcleaning.control_system.planning.cleaning_plan import plan_cleaning
from microcleaning.control_system.planning.path_preview import draw_path_overlay, resolve_plan_policy
from microcleaning.control_system.planning.sequence_planner import STRATEGIES, plan_sequence
from microcleaning.control_system.planning.stage2_geometry import build_cycle_geometry, stage2_placeholders
from microcleaning.control_system.planning.stage2_position import load_position
from microcleaning.control_system.replay.episode_store import write_episode
from microcleaning.data_learning.image_quality import build_observation, measure_image_quality
from microcleaning.vision.contamination import ContaminationMeasurement
from microcleaning.vision.target_instance import extract_target_instances, extract_target_mask, verify_single_target


VERSION = "single-entry-cleaning-v1"


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
    step_budget: int = 1600
    real: bool = False

    def validate(self):
        for name, value, low in (("max_cycles", self.max_cycles, 1), ("max_retries_per_target", self.max_retries_per_target, 0), ("step_budget", self.step_budget, 1)):
            if not isinstance(value, int) or isinstance(value, bool) or value < low:
                raise ValueError(f"INVALID_{name.upper()}")
        if self.step_budget > 1600 or self.sequence_strategy not in STRATEGIES:
            raise ValueError("INVALID_BUDGET_OR_SEQUENCE_STRATEGY")


class CleaningLoop:
    def __init__(self, *, output_dir: str | Path, source, segmenter, executor,
                 placeholders, calibration, offset, config: LoopConfig = LoopConfig(),
                 compare: Callable[[dict], bool] | None = None) -> None:
        config.validate()
        self.config = config
        self.folder = Path(output_dir)
        self.folder.mkdir(parents=True, exist_ok=True)
        if (self.folder / "summary.json").exists():
            raise FileExistsError("RUN_DIRECTORY_ALREADY_USED")
        self.task_id = f"cleaning_{uuid4().hex[:12]}"
        self.source, self.segmenter, self.executor = source, segmenter, executor
        self.placeholders, self.calibration, self.offset = placeholders, calibration, offset
        self.compare = compare
        self.policy_hash = segmenter.sha256
        self.ledger = None
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
            "evidence_boundary": "Mock 是软件证据；实物需独立记录"})

    def stage(self, phase: str) -> None:
        event = {"phase": phase, "cycle": self._cycle, "at": now()}
        self.events.append(event)
        write_json(self.folder / "progress.json", {"task_id": self.task_id, "events": self.events,
            "used_abs_steps": self.used, "completed_cycles": len(self.cycles)})

    def run(self) -> dict:
        status, reasons = "ERROR", ("LOOP_NOT_STARTED",)
        active: dict | None = None
        outcome, pre, post, verification = None, None, None, None
        episode_written = True
        try:
            # 每一轮（含 RETRY）都重新拍前图；不复用旧动作和旧令牌。
            for number in range(1, self.config.max_cycles + 1):
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
                    status, reasons = "HUMAN", ("PRE_IMAGE_QUALITY_LOW",)
                    break
                self.stage("EXTRACT_TARGETS")
                targets = extract_target_instances(pre["mask"], mask_ref=str(cycle_dir / "pre_mask.png"))
                if self.ledger is None:
                    self.ledger = TargetLedger(pre["observation"].observation_id, pre["mask"], targets)
                else:
                    self.ledger.advance(pre["observation"].observation_id, pre["mask"], targets)
                self.stage("SELECT_TARGET")
                h, w = pre["frame"].image.shape[:2]
                sequence = plan_sequence(self.ledger.pending(), strategy=self.config.sequence_strategy, start_px=(w // 2, h // 2))
                write_json(cycle_dir / "sequence.json", asdict(sequence))
                selected = sequence.selected_target_id
                if selected is None:
                    status, reasons = "SUCCESS", ("ALL_OBSERVED_TARGETS_COMPLETED",) if self.cycles else ("NO_TARGET",)
                    break
                entry = self.ledger.entries[selected]
                self.stage("PLAN_TARGET")
                target_mask = extract_target_mask(pre["mask"], entry.instance)
                write_png(cycle_dir / "selected_target_mask.png", target_mask)
                normalized, _ = stage2_placeholders(self.placeholders, pre["frame"].image.shape, self.calibration)
                plan = plan_cleaning(target_mask, policy=resolve_plan_policy(normalized))
                self.stage("BUILD_MOTION")
                geometry = build_cycle_geometry(plan, base=self.placeholders, calibration=self.calibration,
                    offset=self.offset, observation_position=self.observation_position,
                    task_id=self.task_id, used_abs_steps=self.used, budget=self.config.step_budget, real=self.config.real)
                write_json(cycle_dir / "geometry.json", geometry.to_dict())
                write_json(cycle_dir / "path_preview.json", geometry.preview.to_dict())
                write_png(cycle_dir / "path_overlay.png", draw_path_overlay(pre["frame"].image, geometry.preview))
                active = {"cycle": number, "target_id": selected, "frame_target_id": entry.instance.target_id,
                    "component_label": entry.instance.component_label, "retry_count": entry.retry_count,
                    "area_px": entry.instance.area_px, "centroid_px": entry.instance.centroid_px,
                    "pre_observation": asdict(pre["observation"]), "sequence": asdict(sequence),
                    "geometry": geometry.to_dict(), "segmentation_sha256": self.policy_hash,
                    "offset": self.offset.to_dict(), "position_zero_set_at": self.position_zero_set_at,
                    "path_overlay": str(cycle_dir / "path_overlay.png"), "used_abs_steps_before": self.used}
                write_json(cycle_dir / "cycle.json", active)
                if hasattr(self.source, "select_target"):
                    self.source.select_target(entry.instance)  # Mock 的图像替身只模拟被选中的目标。
                measurement = ContaminationMeasurement(entry.instance.area_px, entry.instance.centroid_px,
                    pre["measurement"].uncertainty_px, pre["measurement"].confidence,
                    str(cycle_dir / "selected_target_mask.png"), 1, pre["measurement"].algorithm_version)
                outcome = self.executor.execute(geometry=geometry, observation=pre["observation"],
                    measurement=measurement, preview=active, stage=self.stage)
                self.used = tuple(a + b for a, b in zip(self.used, outcome.used_abs_steps))
                active["execution"] = outcome.to_dict()
                write_json(cycle_dir / "execution.json", outcome.to_dict())
                post, verification = None, None
                if outcome.status == "RETURNED":
                    self.stage("CAPTURE_POST")
                    post = self._observe("post", cycle_dir, after=outcome.returned_at)
                    self.stage("VERIFY_TARGET")
                    comparable, evidence = self._comparable(pre, post, outcome)
                    active["comparability"] = evidence
                    verification = verify_single_target(task_id=self.task_id, pre=pre["observation"], post=post["observation"],
                        pre_target=entry.instance, pre_mask=pre["mask"], post_mask=post["mask"],
                        receipt=outcome.receipt, images_comparable=comparable)
                    active["verification"] = asdict(verification)
                    write_json(cycle_dir / "verification.json", asdict(verification))
                self._episode(cycle_dir, pre, post, outcome, verification)
                episode_written = True
                active["used_abs_steps_after"] = self.used
                self.cycles.append(active)
                write_json(cycle_dir / "cycle.json", active)
                active = None
                if outcome.status != "RETURNED":
                    status, reasons = outcome.status, outcome.reasons
                    break
                result = verification.result
                success = result.next_route is NextRoute.STOP and "REPLAY_THRESHOLD_MET" in result.reason_codes
                if success:
                    self.ledger.advance(post["observation"].observation_id, post["mask"], extract_target_instances(post["mask"]), completed_id=selected)
                    self.stage("SUCCESS")
                    if not self.ledger.pending():
                        status, reasons = "SUCCESS", ("ALL_OBSERVED_TARGETS_COMPLETED",)
                        break
                elif result.next_route is NextRoute.RETRY:
                    self.stage("RETRY")
                    if entry.retry_count >= self.config.max_retries_per_target:
                        status, reasons = "HUMAN", ("MAX_RETRIES_PER_TARGET_REACHED",) + result.reason_codes
                        break
                    self.ledger.advance(post["observation"].observation_id, post["mask"], extract_target_instances(post["mask"]))
                    self.ledger.entries[selected].retry_count += 1
                else:
                    status, reasons = "HUMAN", result.reason_codes
                    break
            else:
                status, reasons = "HUMAN", ("MAX_CYCLES_REACHED",)
        except TargetIdentityError as exc:
            status, reasons = "HUMAN", ("TARGET_IDENTITY_UNCERTAIN", str(exc))
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
                write_json(self.folder / f"cycle_{self._cycle:03d}" / "cycle.json", active)
            self.stage(status)
            if status != "SUCCESS":
                write_json(self.folder / "failure.json", {"status": status, "reasons": reasons, "cycle": self._cycle,
                    "position": load_position(self.executor.position_path).to_dict()})
            summary = {"task_id": self.task_id, "version": VERSION, "mode": "real" if self.config.real else "mock",
                "status": status, "reasons": reasons, "cycles": self.cycles, "used_abs_steps": self.used,
                "cleanup_errors": cleanup_errors,
                "targets": {} if self.ledger is None else self.ledger.to_dict(),
                "position": load_position(self.executor.position_path).to_dict(),
                "segmentation_sha256": self.policy_hash, "events": self.events,
                "evidence_boundary": "规则复检通过不是已验收洁净标准；MCV1 DONE 只表示输出完成"}
            write_json(self.folder / "serial.json", {"events": self.executor.session.serial_events,
                "preserved_replies": [line.decode("ascii", errors="replace").strip() for line in self.executor.session.preserved_replies]})
            write_json(self.folder / "summary.json", summary)
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
        segmentation = self.segmenter(frame.image)
        write_png(folder / f"{phase}_mask.png", segmentation.mask)
        metadata = {"observation": asdict(observation), "captured_at": frame.captured_at, "source_id": frame.source_id,
            "settings": frame.settings, "quality_metrics": asdict(metrics), "quality": asdict(quality),
            "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "segmentation_sha256": self.policy_hash,
            "measurement": asdict(segmentation.measurement)}
        write_json(folder / f"{phase}.json", metadata)
        return {"frame": frame, "observation": observation, "mask": segmentation.mask, "measurement": segmentation.measurement, "metadata": metadata}

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
