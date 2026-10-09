"""无需额外依赖的 HTML/CSV 报告，使用完整证据快照并按版本保存。"""

from __future__ import annotations

import csv
import hashlib
import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .evidence_reader import read_run, resolve_asset


LABELS = {
    "algorithm": "算法候选", "manual": "人工框选", "unrecorded": "未记录",
    "APPROVED": "确认处理", "EXCLUDED": "人工排除", "PENDING_REVIEW": "待审核", "UNRECORDED": "未记录",
    "EXECUTED": "收到喷洗 DONE", "NOT_STARTED": "未完成喷洗", "FAILED": "执行失败",
    "NOT_ASSESSED": "未评估", "UNCERTAIN": "待质量复核", "CLEANED": "复检通过", "NOT_CLEANED": "存在残留",
    "PASS": "合格", "FAIL": "有不合格目标", "REVIEW_REQUIRED": "需要复核", "INCOMPLETE": "任务未完成",
    "COMPLETED": "流程结束", "CANCELLED": "任务取消", "RUNNING": "执行中", "DRAFT": "候选审核中", "LOCKED": "已锁定",
    "WAITING_CONFIRMATION": "等待当步人工确认", "automatic_evidence": "证据规则", "operator_review": "人工复核",
}

REASONS = {
    "NO_FOREGROUND_IS_NOT_VERIFIED_DISAPPEARANCE": "未检出前景，仍需核对目标是否真正消失",
    "TARGET_IDENTITY_NOT_MATCHED": "前后图无法唯一对应同一污渍",
    "COMPARABILITY_NOT_ESTABLISHED": "前后视野或拍摄条件尚未证实可比较",
    "PHYSICAL_ACCEPTANCE_CRITERION_NOT_VALIDATED": "实物洁净判据尚未验收",
    "PUMP_DONE_NOT_RECORDED": "未记录可靠的喷洗输出结束回执",
    "VERIFICATION_NOT_RECORDED": "缺少复检记录", "REMOVAL_RATE_NOT_RECORDED": "未得到可采用的去除率",
    "MANUAL_REGION_IS_NOT_STAIN_BASELINE": "人工框面积是处理区域，不能作为污渍面积基线",
    "MANUAL_REVIEW_INSUFFICIENT_EVIDENCE": "人工复核依据不足，未改变有效质量结论",
    "EMPTY_EXECUTION_LIST": "没有可执行的已确认目标，请先审核名单",
    "TASK_IS_LOCKED": "名单已锁定；修改目标需要新建任务",
    "INVALID_EXECUTION_GEOMETRY": "执行定位尚未验证，不能锁定",
    "LOCKED_LIST_EXCEEDS_TASK_STEP_BUDGET": "整份名单的去程和回程超过任务步数预算",
    "OVERLAPS_EXISTING_TARGET": "人工区域与现有目标重叠，需要复核",
    "MANUAL_PLAN_NOT_VALIDATED": "人工框选区域尚未完成规划校验",
    "CURRENT_LOCATION_NOT_VERIFIED": "当前视野未可靠定位下一目标，已阻止后续动作",
    "REVIEW_REQUIRES_OPERATOR_CONCLUSION_REASON": "人工复核须填写操作员、结论和依据",
    "REVIEW_REQUIRES_EVIDENCE_REFERENCES": "人工复核须引用本任务的实际图像证据",
    "PRE_IMAGE_FILE_MISSING": "处理前原图缺失", "POST_IMAGE_FILE_MISSING": "处理后原图缺失",
    "PRE_IMAGE_HASH_MISMATCH": "处理前图像哈希不一致", "POST_IMAGE_HASH_MISMATCH": "处理后图像哈希不一致",
    "SERIAL_DONE_EVIDENCE_MISSING": "缺少与该动作编号对应的串口 DONE 原文",
    "EPISODE_HASH_MISSING_OR_MISMATCH": "Episode 哈希缺失或校验未通过",
}


def explain(reason: str) -> str:
    return REASONS.get(reason, reason)


def display_time(value):
    if not value:
        return "未记录"
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return str(value) + "（时区未记录）"
        return parsed.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S 北京时间")
    except (ValueError, TypeError):
        return str(value)


def label(value) -> str:
    return LABELS.get(value, str(value) if value is not None else "未记录")


def _h(value) -> str:
    return html.escape(str(value) if value is not None else "未记录", quote=True)


def _csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        rows = [{"记录": "无记录"}]
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            safe = {}
            for key, value in row.items():
                if isinstance(value, (dict, list, tuple)):
                    value = json.dumps(value, ensure_ascii=False)
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
                    value = "'" + value
                safe[key] = value
            writer.writerow(safe)


def _asset(root: Path, report: Path, reference: str | None, name: str, bbox=None) -> str | None:
    relative, error = resolve_asset(root, reference)
    if error:
        return None
    source = root / relative
    output = report / "assets" / (name + ".png")
    output.parent.mkdir(parents=True, exist_ok=True)
    import cv2
    import numpy as np
    image = cv2.imdecode(np.frombuffer(source.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None:
        return None
    if bbox and len(bbox) == 4:
        x, y, w, h = map(int, bbox)
        margin = 14
        image = image[max(0, y - margin):min(image.shape[0], y + h + margin), max(0, x - margin):min(image.shape[1], x + w + margin)]
        if not image.size:
            return None
    okay, buffer = cv2.imencode(".png", image)
    if not okay:
        return None
    output.write_bytes(buffer.tobytes())
    return output.relative_to(report).as_posix()


def _picture(reference, caption):
    visual = f'<img src="{_h(reference)}" alt="{_h(caption)}">' if reference else '<div class="missing">图像未记录或证据不可用</div>'
    return f'<figure>{visual}<figcaption>{_h(caption)}</figcaption></figure>'


def export_report(folder: str | Path, *, formats: tuple[str, ...] = ("html", "csv")) -> Path:
    if not formats or any(format not in {"html", "csv"} for format in formats):
        raise ValueError("SUPPORTED_REPORT_FORMATS: html, csv")
    root = Path(folder).resolve()
    snapshot = read_run(root)
    report = root / "reports" / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    report.mkdir(parents=True, exist_ok=False)
    try:
        target_csv, attempt_csv, sections = [], [], []
        summary_rows = []
        for target in snapshot["targets"]:
            stable = target["target_id"]
            # 历史数据的 ID 不用作路径，避免任意路径字符。
            number = len(target_csv) + 1
            instance = target["instance"]
            last = target["attempts"][-1] if target["attempts"] else {}
            bbox = instance.get("bbox")
            assets = {}
            for phase in ("pre", "post"):
                assets[phase] = _asset(root, report, last.get(phase + "_image"), f"target_{number}_{phase}")
                assets[phase + "_crop"] = _asset(root, report, last.get(phase + "_image"), f"target_{number}_{phase}_crop", bbox=bbox)
                assets[phase + "_mask"] = _asset(root, report, last.get(phase + "_mask"), f"target_{number}_{phase}_mask")
            # 未处理候选也保留首次参考图，不能伪造后图。
            if not target["attempts"]:
                ref = target.get("initial_image")
                assets["pre"] = _asset(root, report, ref, f"target_{number}_pre")
                assets["pre_crop"] = _asset(root, report, ref, f"target_{number}_pre_crop", bbox=bbox)
            evidence = target["quality_evidence"]
            rates = (last.get("verification") or {})
            row = {"target_id": stable, "source": target["source"], "decision": target["decision"],
                   "execution": target["execution"], "quality": target["quality"],
                   "automatic_quality": evidence["automatic_quality"], "effective_source": evidence["effective_source"],
                   "area_px": instance.get("area_px"), "area_kind": target["area_kind"], "bbox": bbox,
                   "centroid_px": instance.get("centroid_px"), "attempts": len(target["attempts"]),
                   "pump_tx_attempts": sum(a["pump_tx"] for a in target["attempts"]),
                   "pump_done_attempts": sum(a["pump_done"] for a in target["attempts"]),
                   "requested_duration_ms": last.get("requested_duration_ms"), "measured_duration_ms": None,
                   "removal_rate": rates.get("removal_rate"), "signed_area_change": evidence["signed_area_change"],
                   "reasons": evidence["reasons"], "note": target["note"], "manual_review": target["manual_review"],
                   "pre_image": assets["pre"], "post_image": assets["post"]}
            target_csv.append(row)
            summary_rows.append('<tr>' + ''.join(f'<td>{_h(value)}</td>' for value in
                (stable, label(target["source"]), label(target["decision"]), label(target["execution"]), label(target["quality"]), len(target["attempts"]))) + '</tr>')
            details = f'<p>位置（原图像素）：{_h(instance.get("centroid_px"))}；区域：{_h(bbox)}；面积类型：{_h(target["area_kind"])}。</p>'
            details += f'<p>原算法结论：{_h(label(evidence["automatic_quality"]))}；有效结论来源：{_h(evidence["effective_source"])}；匹配：{_h(rates.get("match_status"))}；去除率：{_h(row["removal_rate"])}；有符号面积变化率：{_h(row["signed_area_change"])}。</p>'
            details += f'<p>请求喷洗时长：{_h(row["requested_duration_ms"])} ms；实测时长：未记录。喷洗发送尝试 {_h(row["pump_tx_attempts"])} 次；收到 DONE {_h(row["pump_done_attempts"])} 次。压力参数没有可确认的物理单位。</p>'
            details += f'<p>不确定/异常原因：{_h("；".join(explain(reason) for reason in evidence["reasons"]) or "无记录问题")}。备注：{_h(target["note"] or "未记录")}</p>'
            if target["manual_review"]:
                details += f'<p>人工复核（原算法保留）：{_h(json.dumps(target["manual_review"], ensure_ascii=False))}</p>'
            references = [f'{a["record_ref"]}；action_id={a["action_id"]}；Episode={a["episode_refs"]}' for a in target["attempts"]]
            details += '<p class="refs">证据：' + _h(' | '.join(references) or '未执行，无 Episode') + '</p>'
            pictures = '<div class="pair">' + _picture(assets["pre"], "处理前全图") + _picture(assets["post"], "处理后全图") + '</div>'
            pictures += '<div class="pair crops">' + _picture(assets["pre_crop"], "处理前区域") + _picture(assets["post_crop"], "处理后区域") + '</div>'
            pictures += '<details><summary>显示分割 Mask</summary><div class="pair">' + _picture(assets["pre_mask"], "前 Mask") + _picture(assets["post_mask"], "后 Mask") + '</div></details>'
            sections.append(f'<section><h2>{_h(stable)} · {_h(label(target["quality"]))}</h2>{details}{pictures}</section>')
        for attempt in snapshot["attempts"]:
            attempt_csv.append({key: attempt.get(key) for key in ("target_id", "cycle", "action_id", "pump_tx", "pump_ack", "pump_done", "requested_duration_ms", "measured_duration_ms", "record_ref", "serial_ref", "episode_refs", "issues")})
        if "csv" in formats:
            _csv(report / "task.csv", [{"task_id": snapshot["task_id"], "mode": snapshot["mode"],
                "workflow_status": snapshot["workflow_status"], "quality_status": snapshot["quality_status"], **snapshot["metadata"], **snapshot["statistics"]}])
            _csv(report / "targets.csv", target_csv)
            _csv(report / "attempts.csv", attempt_csv)
        if "html" in formats:
            stats = snapshot["statistics"]
            cards = ''.join(f'<div class="stat"><b>{_h(stats[key])}</b><span>{name}</span></div>' for key, name in
                (("algorithm_candidates", "算法候选"), ("manual_candidates", "人工新增"), ("approved", "确认处理"), ("excluded", "排除"), ("executed_targets", "输出完成目标"), ("cleaned", "复检通过"), ("not_cleaned", "存在残留"), ("uncertain", "待质量复核")))
            metadata = snapshot["metadata"]
            document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>MicroCleaningVision · {_h(snapshot["task_id"])}</title>
<style>body{{font:15px/1.7 "Microsoft YaHei",sans-serif;color:#163436;background:#edf3f2;margin:0}}main{{max-width:1060px;margin:32px auto;background:white;padding:36px}}h1{{font-size:28px}}h2{{font-size:21px}}.eyebrow{{color:#167d78;letter-spacing:2px}}.badge{{padding:6px 12px;background:#d8eeea;border-radius:8px}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.stat{{background:#f0f6f5;padding:15px}}.stat b{{font-size:24px;display:block}}.pair{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}figure{{margin:0;background:#f5f7f7;padding:10px;text-align:center}}img{{max-width:100%;max-height:360px}}figcaption{{color:#476767}}.crops img{{max-height:220px}}.missing{{padding:40px;color:#846547;border:1px dashed #aaa}}table{{width:100%;border-collapse:collapse}}th,td{{border-bottom:1px solid #d9e6e4;padding:9px;text-align:left}}section{{margin-top:30px;padding-top:15px;border-top:2px solid #d8e8e4}}.refs{{font-size:12px;overflow-wrap:anywhere}}button{{background:#157b73;color:white;padding:10px 18px;border:0;border-radius:7px;cursor:pointer}}@media print{{body{{background:white}}main{{margin:0;padding:0;max-width:none}}.no-print{{display:none}}section{{break-inside:avoid}}img{{max-height:240px}}}}</style>
<main><div class="eyebrow">MICROCLEANINGVISION / QUALITY EVIDENCE</div><h1>显微清洗质量报告</h1><p><span class="badge">{_h(snapshot["quality_status"])} · {_h(label(snapshot["quality_status"]))}</span>　模式：{_h(snapshot["mode"])}　流程：{_h(label(snapshot["workflow_status"]))}</p>
<button class="no-print" onclick="window.print()">打印 / 另存为 PDF</button><script>if(location.hash==="#print"){{window.addEventListener("load",()=>window.print());}}</script><p>任务：{_h(snapshot["task_id"])}；样品：{_h(metadata.get("sample_id"))}；操作员：{_h(metadata.get("operator"))}</p><p>开始：{_h(display_time(snapshot["started_at"]))}；结束：{_h(display_time(snapshot["ended_at"]))}；Git：{_h(metadata.get("git_commit"))}；软件：{_h(snapshot["software_version"])}</p>
<p>判据：{_h(snapshot["policy"]["version"])}，回放阈值 0.80，实物科学验收未确认。Mock PASS 只表示合成测试判据通过。流程结束、泵输出完成与质量合格分别统计；READXY 为脉冲计数；表面损伤未评估。</p><div class="stats">{cards}</div>
<h2>完整候选名单</h2><table><thead><tr><th>目标</th><th>来源</th><th>操作决定</th><th>执行</th><th>质量</th><th>尝试</th></tr></thead><tbody>{''.join(summary_rows)}</tbody></table>{''.join(sections)}
<section><h2>档案与判定依据</h2><p>总体原因：{_h(snapshot["quality_reasons"])}；原流程原因：{_h(snapshot["summary_reasons"])}</p><p>档案问题：{_h(snapshot["issues"] or "无")}</p><p class="refs">原始目录：{_h(root)}</p><details><summary>查看冻结配置与设备证据</summary><pre class="refs">{_h(json.dumps({"configuration":snapshot["configuration"],"device_evidence":snapshot["device_evidence"]}, ensure_ascii=False, indent=2))}</pre></details></section></main></html>'''
            (report / "index.html").write_text(document, encoding="utf-8")
        manifest = {"report_id": report.name, "created_at": datetime.now().astimezone().isoformat(), "formats": formats,
                    "task_id": snapshot["task_id"], "quality_status": snapshot["quality_status"], "policy": snapshot["policy"],
                    "statistics": snapshot["statistics"], "source_issues": snapshot["issues"],
                    "files": {path.relative_to(report).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in report.rglob("*") if path.is_file()}}
        (report / "report_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report
    except Exception as exc:
        (report / "report_error.json").write_text(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False), encoding="utf-8")
        raise
