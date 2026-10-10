"""无需额外依赖的 HTML/CSV 报告，使用完整证据快照并按版本保存。"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import re
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
    "rewash": "人工选择复洗", "next": "不复洗，继续下一目标", "pause": "暂停并保留档案", "retake": "重拍后图并再次复检",
    "real": "实物模式", "mock": "软件模拟", "matched": "已对应", "unmatched": "未对应", "ambiguous": "存在多个对应区域",
    "READY": "位置已建立参考", "UNHOMED": "尚未建立零点", "MOVING": "平台移动中",
    "POSITION_UNCERTAIN": "位置不可信", "FAULT": "设备故障", "SUCCESS": "流程成功结束",
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
    "POSITION_PERSISTENCE_FAILED": "位置账本未能可靠保存，已禁止继续移动",
    "POSITION_UNKNOWN_BEFORE_MOTION": "发令前位置不可信，已禁止移动",
    "POSITION_CHANGED_BEFORE_MOTION": "发令前位置记录发生变化，需要重新检查规划",
    "POSITION_MOTION_ID_AND_LINES_REQUIRED": "移动缺少动作编号或规划报文，不能登记执行",
    "POSITION_PENDING_MISMATCH": "待执行移动与当前动作不一致，位置不能确认",
    "POSITION_PENDING_INVALID": "待执行移动记录无效，位置不能确认",
    "POSITION_COMPLETION_MISMATCH": "移动完成回执与登记动作不一致",
    "POSITION_PENDING_MISSING": "缺少发令前登记的移动记录，不能补写可信位置",
    "POSITION_EXPECTED_END_MISMATCH": "移动后的预计终点与位置账本不一致",
    "ORIGINAL_OBSERVATION_POSITION_CHANGED": "平台尚未确认回到首次观察位置，停止采用复检数值",
    "HUMAN_DECLINED_RECHECK": "操作员拒绝本次复检请求，未继续动作",
    "HUMAN_DECLINED_RETRY": "操作员未授权下一次清洗",
    "FINAL_RECHECK_NOT_ACCEPTED": "末目标的复检尚未获认可，暂不进入报告打印",
    "INVALID_RECHECK_DECISION": "复检选择无效，等待明确的人工决定",
    "ROI_COMPARABILITY_NOT_PROVEN": "原污渍区域的前后视野尚未证明可比",
    "REPLAY_THRESHOLD_MET": "当前证据达到已配置的回放判据",
    "REPLAY_RESIDUE_REMAINS": "当前复检仍有残留，由人工决定是否复洗",
    "RESIDUAL_REMAINS": "当前复检仍有残留，由人工决定是否复洗",
    "HUMAN_PAUSED_AFTER_RECHECK": "操作员在复检后暂停，完整证据已保留",
    "ROI_REFERENCE_EMPTY": "首次区域没有有效污渍，显示0%，无法建立清洗依据",
    "ROI_PRE_EMPTY": "清洗前原区域未检出污渍，显示0%，不能计算有效清洗率",
    "ROI_POST_EMPTY": "清洗后原区域未检出污渍，按要求显示0%；须人工确认，不能据此自动认定洗净",
    "ROI_IMAGES_SIZE_MISMATCH": "前后图尺寸不一致，不能比较同一像素区域",
    "ROI_MASK_SIZE_MISMATCH": "掩膜尺寸与原图不一致，停止采用清洗率",
    "ROI_INPUT_QUALITY_FLAGGED": "相机记录存在失焦、光照或置信度问题",
    "ROI_PIXEL_QUALITY_LOW": "实际图像质量未通过当前复检门槛",
    "ROI_DUPLICATE_FRAME_ID": "前后帧编号相同，未取得新的复检帧",
    "ROI_DUPLICATE_PIXELS": "前后像素完全相同，无法排除旧帧或冻结画面",
    "ROI_BACKGROUND_UNOBSERVABLE": "背景纹理不足，无法证明已回到原观察位",
    "ROI_BACKGROUND_FEATURES_LOW": "可追踪背景特征不足，位置校验不可信",
    "ROI_ALIGNMENT_LOW_CONFIDENCE": "背景追踪不一致或覆盖不足，位置校验不可信",
    "ROI_POSITION_SHIFTED": "前后背景位移超出容差；请先复位再重拍，不能平移后图掩盖偏移",
    "ROI_ILLUMINATION_CHANGED": "稳定背景亮度变化过大，可能产生虚假的面积变化",
    "ROI_BACKGROUND_CHANGED": "稳定背景像素变化过大，可能存在形变、反光、液滴或位置偏差",
    "ROI_LOCAL_ILLUMINATION_CHANGED": "污渍周围亮度变化过大，不能把阴影或反光误当清洗效果",
    "ROI_FOCUS_CHANGED": "背景清晰度变化过大，前后分割结果不能直接比较",
    "ROI_REFERENCE_POSITION_UNCERTAIN": "本轮清洗前图也未通过首次观察位校验",
    "ROI_FOREGROUND_IDENTITY_UNCERTAIN": "后图前景与原区域几乎不重合，无法确认仍是同一污渍",
    "ROI_NEIGHBOR_INTRUSION": "相邻污渍进入本区域，无法独立测量当前污渍",
    "ROI_FOREGROUND_AT_BOUNDARY": "污渍前景碰到固定区域边缘，面积可能被裁掉，不能采用清洗率",
    "ROI_NEIGHBOR_SECONDARY_CONFIRMATION_REQUIRED": "附近污渍像素发生变化，须二次确认没有侵入本区域",
    "ROI_SEGMENTER_UNSUPPORTED_ALGORITHM": "当前分割算法不支持固定区域复检",
    "ROI_SEGMENTER_POLICY_MISSING": "首次分割参数缺失，无法冻结本目标的复检判据",
    "ROI_SEGMENTER_TARGET_MASK_MISMATCH": "本目标分割掩膜与原图不对应",
    "ROI_SEGMENTER_ROI_INVALID": "固定污渍区域坐标无效",
    "ROI_SEGMENTER_ROI_OUTSIDE_IMAGE": "固定污渍区域超出原图范围",
    "ROI_SEGMENTER_EXCLUDED_MASK_MISMATCH": "相邻排除区域与原图尺寸不一致",
    "ROI_SEGMENTER_EXCLUSION_COVERS_TARGET": "排除区域覆盖了当前污渍，不能建立可靠基线",
    "ROI_SEGMENTER_IMAGE_SIZE_MISMATCH": "复检图尺寸改变，不能复用首次分割参数",
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
        header = {key: CSV_HEADERS.get(key, key) for key in columns}
        writer.writerow(header)
        for row in rows:
            safe = {}
            for key, value in row.items():
                if isinstance(value, (dict, list, tuple)):
                    value = json.dumps(value, ensure_ascii=False)
                elif isinstance(value, str) and value in LABELS:
                    value = label(value)
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
                    value = "'" + value
                safe[key] = value
            writer.writerow(safe)


CSV_HEADERS = {
    "task_id": "任务编号", "target_id": "污渍编号", "mode": "运行模式", "workflow_status": "流程状态",
    "quality_status": "任务质量", "source": "来源", "decision": "操作决定", "execution": "执行状态",
    "quality": "有效质量结论", "automatic_quality": "自动质量结论", "effective_source": "结论依据",
    "area_px": "首次面积_像素", "area_kind": "面积类型", "bbox": "原图区域坐标", "centroid_px": "原图中心_像素",
    "attempts": "清洗轮数", "attempt": "目标清洗轮次", "cycle": "任务清洗序号", "recheck_index": "本轮复检序号",
    "action_id": "动作编号", "pump_tx": "已发送喷洗", "pump_ack": "收到喷洗接受回执", "pump_done": "收到喷洗结束回执",
    "pump_tx_attempts": "喷洗发送次数", "pump_done_attempts": "喷洗结束回执次数", "requested_duration_ms": "请求喷洗时长_毫秒",
    "measured_duration_ms": "实测出水时长_毫秒", "pre_area_px": "清洗前面积_像素", "post_area_px": "复检残留面积_像素",
    "removal_rate": "本轮清洗率", "cumulative_removal_rate": "相对首次累计清洗率", "signed_area_change": "有符号面积变化率",
    "iou": "掩膜交并重合度_IoU", "dice": "掩膜重合度_Dice", "intersection_px": "掩膜交集_像素", "union_px": "掩膜合并面积_像素",
    "analysis_valid": "固定区域验算有效", "alignment_shift_px": "前后视野位移_像素", "alignment_confidence": "配准可信分数",
    "alignment_valid": "前后视野校验通过", "recheck_decision": "复检后人工选择", "operator": "操作员", "decision_reason": "人工选择依据",
    "decision_at": "人工选择时间", "reasons": "结果原因", "outbound_plan_steps": "去程规划步数", "return_plan_steps": "回程规划步数",
    "outbound_pulse_feedback": "去程已发脉冲回执", "return_pulse_feedback": "回程已发脉冲回执", "pre_image": "清洗前图像",
    "post_image": "复检后图像", "roi_evidence": "区域对比证据", "comparability": "图像可比性记录", "record_ref": "原始轮次档案",
    "serial_ref": "原始串口档案", "episode_refs": "完整过程档案", "issues": "档案问题", "note": "备注", "manual_review": "人工复核记录",
    "sample_id": "样品编号", "git_commit": "源码提交", "git_dirty": "源码存在未提交改动", "ui_version": "工作台版本",
    "firmware_identity": "固件身份", "source_sha256": "源码内容摘要", "source_files_sha256": "源码文件摘要",
    "algorithm_candidates": "算法候选数", "manual_candidates": "人工新增数", "excluded": "排除数", "approved": "确认处理数",
    "pending_review": "待审核数", "executed_targets": "喷洗输出结束目标数", "cleaned": "复检通过数", "not_cleaned": "残留数",
    "uncertain": "待质量复核数", "failed_targets": "执行失败数",
}


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


def _rate(value) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return "未得到可靠数值"
    return f"{value * 100:.2f}%"


def _motion_lines(lines) -> str:
    from microcleaning.control_system.planning.stage2_axes import parse_movexy_line
    summaries = []
    if lines is not None and not isinstance(lines, (list, tuple)):
        return "规划报文格式有误，不能采用"
    for number, line in enumerate(lines or [], 1):
        try:
            dx, dy = parse_movexy_line(line)
            summaries.append(f"第{number}段：X {dx:+d} 步，Y {dy:+d} 步")
        except (ValueError, TypeError):
            summaries.append(f"第{number}段：无法解析；原文 {line}")
    return "；".join(summaries) or "未记录"


def _motion_feedback(execution: dict) -> dict:
    session = execution.get("session") or {}
    session = session if isinstance(session, dict) else {}
    results = {"去程": execution.get("outbound_result") or session.get("motion_result") or session.get("motion_error"),
               "回程": execution.get("return_result") or execution.get("return_error")}
    text = {}
    for phase, result in results.items():
        if not isinstance(result, dict):
            text[phase] = "未记录脉冲回执"
            continue
        replies = []
        source = result.get("replies") or []
        source = source if isinstance(source, (list, tuple)) else []
        for reply in source:
            matched = re.fullmatch(r"STEP2 X=(\d+) BX=(\d+) Y=(\d+) BY=(\d+)", str(reply).strip())
            if matched:
                x, bx, y, by = map(int, matched.groups())
                replies.append(f"X已发{x}步（{'运动中' if bx else '停止'}），Y已发{y}步（{'运动中' if by else '停止'}）")
        text[phase] = "；".join(replies) or "未记录 READXY 计数"
    return text


def _attempt_section(root: Path, report: Path, target: dict, attempt: dict, target_number: int,
                     attempt_number: int) -> tuple[str, list[dict]]:
    """每次清洗和每次重拍都可见；最终结论不能抹掉中间失败。"""
    rows, sections = [], []
    geometry, execution = attempt.get("geometry") or {}, attempt.get("execution") or {}
    feedback = _motion_feedback(execution)
    prefix = f"target_{target_number}_attempt_{attempt_number}"
    checks = attempt.get("rechecks") or [{"index": 1, "pre_image": attempt.get("pre_image"),
        "post_image": attempt.get("post_image"), "verification": attempt.get("verification"),
        "comparability": attempt.get("comparability"), "roi_analysis": attempt.get("roi_analysis"),
        "roi_evidence": attempt.get("roi_evidence") or {}, "decision": attempt.get("recheck_decision"),
        "issues": attempt.get("issues") or []}]
    for ordinal, check in enumerate(checks, 1):
        analysis = check.get("roi_analysis") or {}
        verification = check.get("verification") or {}
        result = verification.get("result") or verification
        result = result if isinstance(result, dict) else {}
        decision = check.get("decision") or {}
        alignment = analysis.get("alignment") or {}
        alignment = alignment if isinstance(alignment, dict) else {}
        rate = analysis.get("removal_rate") if analysis else verification.get("removal_rate", result.get("removal_rate"))
        valid = analysis.get("valid") if analysis else None
        refs = check.get("roi_evidence") or {}
        assets = {}
        for phase in ("pre", "post"):
            reference = check.get(phase + "_image")
            assets[phase] = _asset(root, report, reference, prefix + f"_recheck_{ordinal}_{phase}")
            # 已冻结 ROI 的放大图优先，旧档案才从原图按同一坐标裁剪。
            assets[phase + "_crop"] = _asset(root, report, refs.get(phase + "_crop"), prefix + f"_recheck_{ordinal}_{phase}_crop") if phase + "_crop" in refs else _asset(root, report, reference, prefix + f"_recheck_{ordinal}_{phase}_crop", bbox=(target.get("instance") or {}).get("bbox"))
        assets["overlay"] = _asset(root, report, refs.get("overlay"), prefix + f"_recheck_{ordinal}_overlay")
        assets["contact_sheet"] = _asset(root, report, refs.get("contact_sheet"), prefix + f"_recheck_{ordinal}_contact_sheet")
        codes = analysis.get("reason_codes", result.get("reason_codes", [])) or []
        codes = codes if isinstance(codes, (list, tuple)) else [str(codes)]
        messages = analysis.get("messages_zh")
        reasons = messages if isinstance(messages, (list, tuple)) and messages else [explain(str(code)) for code in codes]
        row = {"target_id": target["target_id"], "attempt": attempt_number, "cycle": attempt.get("cycle"),
            "recheck_index": check.get("index", ordinal), "action_id": attempt.get("action_id"),
            "pump_tx": attempt.get("pump_tx"), "pump_ack": attempt.get("pump_ack"), "pump_done": attempt.get("pump_done"),
            "requested_duration_ms": attempt.get("requested_duration_ms"), "measured_duration_ms": attempt.get("measured_duration_ms"),
            "pre_area_px": analysis.get("pre_area_px", verification.get("pre_area_px")),
            "post_area_px": analysis.get("post_area_px", verification.get("post_area_px")),
            "removal_rate": rate, "cumulative_removal_rate": analysis.get("cumulative_removal_rate"),
            "iou": analysis.get("iou"), "dice": analysis.get("dice"), "intersection_px": analysis.get("intersection_px"),
            "union_px": analysis.get("union_px"), "analysis_valid": valid, "alignment_shift_px": alignment.get("shift_px"),
            "alignment_confidence": alignment.get("confidence"), "alignment_valid": alignment.get("valid"),
            "recheck_decision": label(decision.get("choice")), "operator": decision.get("operator"),
            "decision_reason": decision.get("reason"), "decision_at": decision.get("at"), "reasons": reasons,
            "outbound_plan_steps": _motion_lines((geometry.get("outbound") or {}).get("lines")),
            "return_plan_steps": _motion_lines((geometry.get("returning") or {}).get("lines")),
            "outbound_pulse_feedback": feedback["去程"], "return_pulse_feedback": feedback["回程"],
            "pre_image": check.get("pre_image"), "post_image": check.get("post_image"), "roi_evidence": refs,
            "comparability": check.get("comparability"), "record_ref": attempt.get("record_ref"),
            "serial_ref": attempt.get("serial_ref"), "episode_refs": attempt.get("episode_refs"),
            "issues": list(dict.fromkeys([*(attempt.get("issues") or []), *(check.get("issues") or [])]))}
        rows.append(row)
        approved = "数值有效" if valid is True else "数值不可作为合格依据" if valid is False else "旧档案：未记录固定区域验算"
        before_area = row["pre_area_px"]
        formula = f"本轮清洗率 =（前面积 {_h(before_area)} − 后面积 {_h(row['post_area_px'])}）÷ 前面积；本轮 {_h(_rate(rate))}，相对首次累计 {_h(_rate(row['cumulative_removal_rate']))}。" if isinstance(before_area, (int, float)) and before_area > 0 else f"没有可靠的清洗前面积基线，不进行面积除法。界面记录 {_h(_rate(rate))}，不能作为洗净依据。"
        details = f'<h4>第{attempt_number}次清洗 · 第{_h(row["recheck_index"])}次复检</h4><p>{formula}</p>'
        details += f'<p>固定区域：{_h(analysis.get("roi"))}；重合度 IoU：{_h(_rate(row["iou"]))}；Dice：{_h(_rate(row["dice"]))}；交集 {_h(row["intersection_px"])} px，合并面积 {_h(row["union_px"])} px。</p>'
        details += f'<p>视野位移：{_h(row["alignment_shift_px"])} px；配准可信分数 {_h(row["alignment_confidence"])}；{approved}。原因：{_h("；".join(map(str, reasons)) or "未记录异常")}。</p>'
        segmentation = analysis.get("segmentation") or {}
        if segmentation:
            details += '<details><summary>本轮固定分割参数与基准校验</summary><pre>' + _h(json.dumps(segmentation, ensure_ascii=False, indent=2)) + '</pre></details>'
        details += f'<p>人工选择：{_h(row["recheck_decision"])}；操作员 {_h(row["operator"])}；依据 {_h(row["decision_reason"])}；{_h(display_time(row["decision_at"]))}。</p>'
        details += f'<p>图像/档案问题：{_h("；".join(row["issues"]) or "无记录问题")}。清洗率为零且数值无效时表示本次未检出可靠目标，不等于已经洗净；未得到可比图像时不计算合格清洗率。</p>'
        pictures = '<div class="pair">' + _picture(assets["pre"], "本轮处理前全图") + _picture(assets["post"], "本次复检后全图") + '</div>'
        pictures += '<div class="pair crops">' + _picture(assets["pre_crop"], "同一原图坐标 · 前污渍放大") + _picture(assets["post_crop"], "同一原图坐标 · 后污渍放大") + '</div>'
        pictures += '<div class="pair crops">' + _picture(assets["overlay"], "掩膜重合：保留 / 去除 / 新增") + _picture(assets["contact_sheet"], "前后污渍与重合可视化") + '</div>'
        sections.append(f'<article class="attempt" data-cycle="{_h(attempt.get("cycle"))}" data-recheck="{_h(row["recheck_index"])}">{details}{pictures}</article>')
    motion = f'<p>去程规划：{_h(_motion_lines((geometry.get("outbound") or {}).get("lines")))}。回程规划：{_h(_motion_lines((geometry.get("returning") or {}).get("lines")))}。</p>'
    motion += f'<p>去程计数：{_h(feedback["去程"])}。回程计数：{_h(feedback["回程"])}。以上为已发脉冲，不是实际机械位移。</p>'
    motion += f'<p>请求喷洗 {_h(attempt.get("requested_duration_ms"))} ms；收到喷洗结束回执：{"是" if attempt.get("pump_done") else "否"}；实测出水时长未记录。动作编号 {_h(attempt.get("action_id"))}。</p>'
    return '<div class="cleaning-round">' + motion + ''.join(sections) + '</div>', rows


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
            details += f'<p>原算法结论：{_h(label(evidence["automatic_quality"]))}；有效结论来源：{_h(label(evidence["effective_source"]))}；匹配：{_h(label(rates.get("match_status")))}；清洗率：{_h(_rate(row["removal_rate"]))}；有符号面积变化率：{_h(_rate(row["signed_area_change"]))}。</p>'
            details += f'<p>请求喷洗时长：{_h(row["requested_duration_ms"])} ms；实测时长：未记录。喷洗发送尝试 {_h(row["pump_tx_attempts"])} 次；收到 DONE {_h(row["pump_done_attempts"])} 次。压力参数没有可确认的物理单位。</p>'
            details += f'<p>不确定/异常原因：{_h("；".join(explain(reason) for reason in evidence["reasons"]) or "无记录问题")}。备注：{_h(target["note"] or "未记录")}</p>'
            if target["manual_review"]:
                details += f'<p>人工复核（原算法保留）：{_h(json.dumps(target["manual_review"], ensure_ascii=False))}</p>'
            references = [f'{a["record_ref"]}；action_id={a["action_id"]}；Episode={a["episode_refs"]}' for a in target["attempts"]]
            details += '<p class="refs">证据：' + _h(' | '.join(references) or '未执行，无 Episode') + '</p>'
            pictures = '<div class="pair">' + _picture(assets["pre"], "处理前全图") + _picture(assets["post"], "处理后全图") + '</div>'
            pictures += '<div class="pair crops">' + _picture(assets["pre_crop"], "处理前区域") + _picture(assets["post_crop"], "处理后区域") + '</div>'
            pictures += '<details><summary>显示分割 Mask</summary><div class="pair">' + _picture(assets["pre_mask"], "前 Mask") + _picture(assets["post_mask"], "后 Mask") + '</div></details>'
            rounds = []
            for ordinal, attempt in enumerate(target["attempts"], 1):
                content, check_rows = _attempt_section(root, report, target, attempt, number, ordinal)
                rounds.append(content)
                attempt_csv.extend(check_rows)
            sections.append(f'<section><h2>{_h(stable)} · {_h(label(target["quality"]))}</h2>{details}{pictures}<h3>完整清洗与复检历史</h3>{"".join(rounds) or "<p>未执行清洗；没有后图或复检记录。</p>"}</section>')
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
            printing = '<button class="no-print" onclick="window.print()">打印 / 另存为 PDF</button><script>if(location.hash==="#print"){window.addEventListener("load",()=>window.print());}</script>' if snapshot.get("report_ready") else '<p class="no-print">当前为档案查看：末目标尚未通过，或证据存在问题。原始数据与所有轮次均保留；报告打印尚未放行。</p>'
            document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>MicroCleaningVision · {_h(snapshot["task_id"])}</title>
<style>body{{font:15px/1.7 "Microsoft YaHei",sans-serif;color:#163436;background:#edf3f2;margin:0}}main{{max-width:1060px;margin:32px auto;background:white;padding:36px}}h1{{font-size:28px}}h2{{font-size:21px}}.eyebrow{{color:#167d78;letter-spacing:2px}}.badge{{padding:6px 12px;background:#d8eeea;border-radius:8px}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.stat{{background:#f0f6f5;padding:15px}}.stat b{{font-size:24px;display:block}}.pair{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}figure{{margin:0;background:#f5f7f7;padding:10px;text-align:center}}img{{max-width:100%;max-height:360px}}figcaption{{color:#476767}}.crops img{{max-height:220px}}.missing{{padding:40px;color:#846547;border:1px dashed #aaa}}table{{width:100%;border-collapse:collapse}}th,td{{border-bottom:1px solid #d9e6e4;padding:9px;text-align:left}}section{{margin-top:30px;padding-top:15px;border-top:2px solid #d8e8e4}}.refs{{font-size:12px;overflow-wrap:anywhere}}button{{background:#157b73;color:white;padding:10px 18px;border:0;border-radius:7px;cursor:pointer}}@media print{{body{{background:white}}main{{margin:0;padding:0;max-width:none}}.no-print{{display:none}}figure,tr{{break-inside:avoid}}h2,h3,h4{{break-after:avoid}}.pair{{break-inside:avoid}}img{{max-height:240px}}}}</style>
<main><div class="eyebrow">MICROCLEANINGVISION / 清洗质量证据</div><h1>显微清洗质量报告</h1><p><span class="badge">{_h(snapshot["quality_status"])} · {_h(label(snapshot["quality_status"]))}</span>　模式：{_h(label(snapshot["mode"]))}　流程：{_h(label(snapshot["workflow_status"]))}</p>
{printing}<p>任务：{_h(snapshot["task_id"])}；样品：{_h(metadata.get("sample_id"))}；操作员：{_h(metadata.get("operator"))}</p><p>开始：{_h(display_time(snapshot["started_at"]))}；结束：{_h(display_time(snapshot["ended_at"]))}；Git：{_h(metadata.get("git_commit"))}；软件：{_h(snapshot["software_version"])}</p>
<p>判据：{_h(snapshot["policy"]["version"])}，回放阈值 {_h(_rate(snapshot["policy"].get("threshold")))}，实物科学验收未确认。模拟模式合格只表示合成测试判据通过。流程结束、泵输出完成与质量合格分别统计；READXY 为已发脉冲计数；表面损伤未评估。</p><div class="stats">{cards}</div>
<h2>完整候选名单</h2><table><thead><tr><th>目标</th><th>来源</th><th>操作决定</th><th>执行</th><th>质量</th><th>尝试</th></tr></thead><tbody>{''.join(summary_rows)}</tbody></table>{''.join(sections)}
<section><h2>档案与判定依据</h2><p>总体原因：{_h(snapshot["quality_reasons"])}；原流程原因：{_h(snapshot["summary_reasons"])}</p><p>档案问题：{_h(snapshot["issues"] or "无")}</p><p class="refs">原始目录：{_h(root)}</p><details><summary>查看冻结配置与设备证据</summary><pre class="refs">{_h(json.dumps({"configuration":snapshot["configuration"],"device_evidence":snapshot["device_evidence"]}, ensure_ascii=False, indent=2))}</pre></details></section></main></html>'''
            (report / "index.html").write_text(document, encoding="utf-8")
        manifest = {"report_id": report.name, "created_at": datetime.now().astimezone().isoformat(), "formats": formats,
                    "task_id": snapshot["task_id"], "quality_status": snapshot["quality_status"], "policy": snapshot["policy"],
                    "statistics": snapshot["statistics"], "source_issues": snapshot["issues"],
                    "evidence_issues": snapshot["evidence_issues"], "report_ready": snapshot["report_ready"],
                    "recorded_report_ready": snapshot["recorded_report_ready"], "workflow_status": snapshot["workflow_status"],
                    "csv_schema": {"attempts": "每次清洗的每次复检一行，保留所有重拍与人工选择", "headers": CSV_HEADERS},
                    "files": {path.relative_to(report).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in report.rglob("*") if path.is_file()}}
        (report / "report_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report
    except Exception as exc:
        (report / "report_error.json").write_text(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False), encoding="utf-8")
        raise
