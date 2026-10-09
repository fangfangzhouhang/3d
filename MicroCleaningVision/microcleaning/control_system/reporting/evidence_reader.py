"""兼容工作台、旧 closed_loop、旧单帧 Episode 的只读规范化。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from microcleaning.control_system.orchestration.result_evidence import QUALITY_POLICY, evaluate_target, final_quality, target_statistics


def _json(path: Path, issues: list[str], *, required: bool = False) -> dict:
    if not path.exists():
        if required:
            issues.append(f"MISSING_JSON:{path.name}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("expected object")
        return payload
    except (ValueError, OSError) as exc:
        issues.append(f"INVALID_JSON:{path.name}:{type(exc).__name__}")
        return {}


def resolve_asset(root: Path, reference: str | None, digest: str | None = None) -> tuple[str | None, str | None]:
    """只读取本任务内文件；迁移后的绝对引用须有原哈希验证身份。"""
    if not reference:
        return None, "IMAGE_REFERENCE_MISSING"
    root = root.resolve()
    path = Path(reference)
    candidate = path if path.is_absolute() else root / path
    if not path.is_absolute() and not candidate.is_file():
        old_relative = path.resolve()
        if old_relative.is_relative_to(root):
            candidate = old_relative
    if not candidate.is_file() and digest:
        suffix = next((path.parts[index:] for index, part in enumerate(path.parts) if part.startswith("cycle_") or part.startswith("draft_")), None)
        if suffix is not None:
            candidate = root.joinpath(*suffix)
    try:
        candidate.resolve().relative_to(root)
    except ValueError:
        if not digest:
            return None, "IMAGE_OUTSIDE_RUN_OR_UNVERIFIED_MOVE"
        # 迁移时保留 cycle_NNN/... 等原目录后缀，不跨任务搜索文件。
        parts = path.parts
        suffix = next((parts[index:] for index, part in enumerate(parts) if part.startswith("cycle_") or part.startswith("draft_")), None)
        if suffix is None:
            return None, "IMAGE_OUTSIDE_RUN"
        candidate = root.joinpath(*suffix)
    try:
        relative = candidate.resolve().relative_to(root).as_posix()
        if not candidate.is_file():
            return None, "IMAGE_FILE_MISSING"
        if digest and hashlib.sha256(candidate.read_bytes()).hexdigest() != digest:
            return None, "IMAGE_HASH_MISMATCH"
        return relative, None
    except (ValueError, OSError):
        return None, "IMAGE_NOT_READABLE"


def _reviews(root: Path, issues: list[str]) -> list[dict]:
    path = root / "review_log.jsonl"
    if not path.exists():
        return []
    result = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        issues.append("REVIEW_LOG_NOT_READABLE")
        return []
    for index, line in enumerate(lines, 1):
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError()
            result.append(event)
        except ValueError:
            issues.append(f"REVIEW_LOG_INVALID_LINE:{index}")
    return result


def read_run(folder: str | Path) -> dict:
    root, issues = Path(folder).resolve(), []
    summary = _json(root / "summary.json", issues, required=True)
    config = _json(root / "run_config.json", issues)
    manifest = _json(root / "task_manifest.json", issues)
    serial = _json(root / "serial.json", issues)
    reviews = _reviews(root, issues)
    if manifest.get("locked_snapshot"):
        digest = hashlib.sha256(json.dumps(manifest["locked_snapshot"], ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if digest != manifest.get("lock_sha256"):
            issues.append("LOCK_MANIFEST_HASH_MISMATCH")
        if tuple(manifest.get("execution_ids") or ()) != tuple(manifest["locked_snapshot"].get("execution_ids") or ()):
            issues.append("LOCKED_QUEUE_CHANGED")
    mode = summary.get("mode") or (manifest.get("metadata") or {}).get("mode") or "unrecorded"
    workflow = summary.get("workflow_status") or manifest.get("state") or ("COMPLETED" if summary.get("status") == "SUCCESS" else "FAILED" if summary.get("status") else "UNRECORDED")
    records = summary.get("cycles") or []
    if not records:
        records = [_json(path, issues) for path in sorted(root.glob("cycle_*/cycle.json"))]
    if not records and (summary.get("observation") or summary.get("execution_receipt")):
        # 旧单帧：它没有人工批准名单，不补造批准或稳定 S 身份。
        episode_ref, episode_error = resolve_asset(root, summary.get("episode_file"))
        episode = _json(root / episode_ref, issues) if episode_ref else {}
        if episode_error:
            issues.append("LEGACY_EPISODE_NOT_AVAILABLE")
        records = [{"cycle": 1, "target_id": "UNRECORDED", "pre_observation": summary.get("observation"),
                    "post_observation": episode.get("observation_post"), "legacy_episode_ref": episode_ref,
                    "verification": summary.get("verification") or episode.get("verification"),
                    "execution": {"receipt": summary.get("execution_receipt") or episode.get("execution_receipt"),
                                  "pump_request": summary.get("action_request") or episode.get("action_request")}}]
    attempts = []
    events = serial.get("events") or []
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            issues.append("INVALID_CYCLE_RECORD")
            continue
        local_issues = []
        number = record.get("cycle", index)
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            issues.append("INVALID_CYCLE_NUMBER")
            continue
        cycle_dir = root / f"cycle_{number:03d}"
        execution = record.get("execution") or _json(cycle_dir / "execution.json", local_issues)
        raw_verification = record.get("verification") or _json(cycle_dir / "verification.json", local_issues) or None
        pre, post = _json(cycle_dir / "pre.json", local_issues), _json(cycle_dir / "post.json", local_issues)
        pre_observation = pre.get("observation") or record.get("pre_observation") or {}
        post_observation = post.get("observation") or record.get("post_observation") or summary.get("post_observation") or {}
        pre_image, err = resolve_asset(root, pre_observation.get("raw_image_ref"), pre.get("raw_sha256"))
        if err:
            local_issues.append("PRE_" + err)
        post_image, err = resolve_asset(root, post_observation.get("raw_image_ref"), post.get("raw_sha256"))
        if err:
            local_issues.append("POST_" + err)
        receipt = ((execution.get("session") or {}).get("receipt") or execution.get("receipt") or {})
        request = execution.get("pump_request") or {}
        action_id = receipt.get("action_id") or request.get("action_id")
        relevant = [event for event in events if action_id and action_id in str(event.get("line", "")).split("|")]
        pump_tx = any(str(e.get("line", "")).startswith(f"MCV1|PUMP|{action_id}|") and e.get("direction") == "tx" for e in relevant)
        ack = any(e.get("line") == f"MCV1|ACK|{action_id}" and e.get("direction") == "rx" for e in relevant)
        done = any(e.get("line") == f"MCV1|DONE|{action_id}" and e.get("direction") == "rx" for e in relevant)
        receipt_done = receipt.get("success") is True and receipt.get("controller_state") == "DONE"
        if receipt_done and not done:
            local_issues.append("SERIAL_DONE_EVIDENCE_MISSING")
        if done and not receipt_done:
            local_issues.append("DONE_RECEIVED_BUT_RECEIPT_NOT_FINALIZED")
        episode_refs = []
        episode_dir = cycle_dir / "episodes"
        episode_paths = sorted(episode_dir.glob("*.json")) if episode_dir.is_dir() else []
        if record.get("legacy_episode_ref"):
            episode_paths.append(root / record["legacy_episode_ref"])
        for path in episode_paths:
            episode = _json(path, local_issues)
            digest_path = path.with_suffix(".sha256")
            # 原 Episode 写入器在 Windows 使用文本换行，却按写入前 LF 文本计算 SHA。
            # 同时支持原文本哈希与字节哈希；不把 CRLF 兼容误判为证据损坏。
            raw_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            text_hash = hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            if not digest_path.exists() or digest_path.read_text(encoding="ascii").strip() not in {raw_hash, text_hash}:
                local_issues.append("EPISODE_HASH_MISSING_OR_MISMATCH")
            if episode.get("task_id") != (summary.get("task_id") or summary.get("run_id")):
                local_issues.append("EPISODE_TASK_ID_CONFLICT")
            episode_refs.append(path.relative_to(root).as_posix())
        attempts.append({"cycle": number, "target_id": record.get("target_id") or "UNRECORDED", "action_id": action_id,
            "execution": execution, "verification": raw_verification, "comparability": record.get("comparability"),
            "pump_tx": pump_tx, "pump_ack": ack, "pump_done": done and receipt_done,
            "requested_duration_ms": request.get("duration_ms"), "measured_duration_ms": None,
            "pre_image": pre_image, "post_image": post_image, "pre_mask": f"cycle_{number:03d}/pre_mask.png" if (cycle_dir / "pre_mask.png").is_file() else None,
            "post_mask": f"cycle_{number:03d}/post_mask.png" if (cycle_dir / "post_mask.png").is_file() else None,
            "episode_refs": episode_refs, "serial_ref": "serial.json" if events else None,
            "record_ref": record.get("legacy_episode_ref") or f"cycle_{number:03d}/cycle.json", "issues": list(dict.fromkeys(local_issues))})
    candidates = manifest.get("targets") or summary.get("targets") or {}
    if not candidates and attempts:
        candidates = {attempt["target_id"]: {} for attempt in attempts}
    rows = []
    initial_refs = manifest.get("references") or {}
    initial_image, _ = resolve_asset(root, initial_refs.get("initial_image"), initial_refs.get("initial_image_sha256"))
    latest_reviews = {}
    for event in reviews:
        if event.get("action") == "quality_review":
            latest_reviews[event.get("target_id")] = event if event.get("conclusion") != "REVOKED" else None
    for stable, candidate in candidates.items():
        candidate = candidate if isinstance(candidate, dict) else {}
        related = [attempt for attempt in attempts if attempt["target_id"] == stable]
        last = related[-1] if related else None
        source = candidate.get("source") or ("algorithm" if candidate.get("instance") else "unrecorded")
        quality = evaluate_target(source=source, execution={} if last is None else last["execution"],
            verification=None if last is None else last["verification"], comparability=None if last is None else last["comparability"],
            mode=mode, evidence_issues=tuple(issues + ([] if last is None else last["issues"])), manual=latest_reviews.get(stable))
        executed = any(attempt["pump_done"] for attempt in related)
        failed = any(attempt["execution"].get("status") == "ERROR" for attempt in related)
        row = {"target_id": stable, "source": source, "decision": candidate.get("decision", "UNRECORDED"),
               "execution": "EXECUTED" if executed else "FAILED" if failed else "NOT_STARTED",
               "finished": candidate.get("finished", candidate.get("completed", False)),
               "quality": quality.quality if related else "NOT_ASSESSED", "quality_evidence": quality.to_dict(),
               "instance": deepcopy(candidate.get("instance") or {}), "area_kind": candidate.get("area_kind") or "unrecorded",
               "initial_image": initial_image, "note": candidate.get("note") or "", "attempts": related, "manual_review": latest_reviews.get(stable)}
        rows.append(row)
    quality_status, quality_reasons = final_quality(rows, workflow_status=workflow, mode=mode)
    if issues and quality_status == "PASS":
        quality_status, quality_reasons = "REVIEW_REQUIRED", ("TASK_EVIDENCE_ISSUES",)
    metadata = manifest.get("metadata") or config.get("metadata") or summary.get("metadata") or {}
    statistics = target_statistics(rows, attempts)
    if not manifest:
        for key in ("manual_candidates", "excluded", "approved", "pending_review", "cleaned", "not_cleaned"):
            statistics[key] = None
        if not summary.get("targets"):
            statistics["algorithm_candidates"] = None
    start = manifest.get("created_at") or next((event.get("at") for event in summary.get("events", []) if event.get("at")), None)
    end = manifest.get("ended_at") or next((event.get("at") for event in reversed(summary.get("events", [])) if event.get("at")), None)
    return {"schema": "quality-report-v1", "task_id": summary.get("task_id") or summary.get("run_id") or manifest.get("task_id") or "未记录",
            "folder": str(root), "mode": mode, "workflow_status": workflow, "legacy_status": summary.get("status", "未记录"),
            "quality_status": quality_status, "quality_reasons": quality_reasons, "statistics": statistics,
            "targets": rows, "attempts": attempts, "issues": list(dict.fromkeys(issues)), "reviews": reviews,
            "metadata": metadata, "started_at": start, "ended_at": end, "software_version": summary.get("version") or summary.get("demo_version") or "未记录",
            "configuration": config, "policy": manifest.get("policy") or QUALITY_POLICY, "summary_reasons": summary.get("reasons") or [],
            "device_evidence": {"position": summary.get("position"), "firmware_identity": metadata.get("firmware_identity") or "未确认",
                                "motion_feedback": "READXY 脉冲计数，非编码器位移", "damage_assessment": "未评估"}}


def record_quality_review(folder: str | Path, *, target_id: str, operator: str, conclusion: str,
                          reason: str, evidence_refs: list[str]) -> dict:
    root = Path(folder).resolve()
    snapshot = read_run(root)
    if target_id not in {row["target_id"] for row in snapshot["targets"]}:
        raise ValueError("UNKNOWN_REVIEW_TARGET")
    if not operator.strip() or not reason.strip() or conclusion not in {"CLEANED", "NOT_CLEANED", "UNCERTAIN", "REVOKED"}:
        raise ValueError("REVIEW_REQUIRES_OPERATOR_CONCLUSION_REASON")
    if conclusion != "REVOKED" and not evidence_refs:
        raise ValueError("REVIEW_REQUIRES_EVIDENCE_REFERENCES")
    safe_refs = []
    for reference in evidence_refs:
        relative, error = resolve_asset(root, reference)
        if error:
            raise ValueError(error)
        safe_refs.append(relative)
    event = {"review_id": uuid4().hex, "task_id": snapshot["task_id"], "action": "quality_review",
             "target_id": target_id, "at": datetime.now(timezone.utc).isoformat(), "operator": operator.strip(),
             "conclusion": conclusion, "reason": reason.strip(), "evidence_refs": safe_refs}
    with (root / "review_log.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
    return event
