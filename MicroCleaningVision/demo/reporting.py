"""从原 Demo 抽取的 demo.reporting；保持既有单帧行为。"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4
from microcleaning.contracts import Episode
from microcleaning.contracts import FailureRecord
from microcleaning.contracts import NextRoute
from microcleaning.contracts import Observation
from microcleaning.contracts import StateEstimate
from microcleaning.contracts import VerificationResult
from microcleaning.control_system.replay.episode_store import write_episode
from demo import DEMO_VERSION
from demo.image_ops import _plan_as_dict


def _analysis_episode(
    *,
    run_id: str,
    observation: Observation,
    state: StateEstimate,
    mode_name: str,
    reason_codes: tuple[str, ...],
    recovery: str,
) -> Episode:
    verification = VerificationResult(
        task_id=run_id,
        pre_observation_id=observation.observation_id,
        post_observation_id=None,
        residual_area_px=None,
        removal_rate=None,
        damage_flag=False,
        next_route=NextRoute.HUMAN,
        reason_codes=reason_codes,
    )
    return Episode(
        episode_id=f"episode_{uuid4().hex[:12]}",
        task_id=run_id,
        mode=mode_name,
        protocol_version=DEMO_VERSION,
        observation_pre=observation,
        state=state,
        action_request=None,
        safety_decision=None,
        execution_receipt=None,
        observation_post=None,
        verification=verification,
        failures=[
            FailureRecord(
                failure_id=f"failure_{uuid4().hex[:12]}",
                task_id=run_id,
                stage="calibration" if "CALIBRATION" in reason_codes[0] else "control",
                severity="info",
                reason_codes=verification.reason_codes,
                reproducible=True,
                recovery=recovery,
            )
        ],
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_demo_report(*,
    algorithm,
    arm_pump,
    camera_height,
    camera_index,
    camera_width,
    confirm_pump,
    controller_kind,
    episode,
    evidence_boundary,
    from_camera,
    input_source,
    measurement,
    mode,
    observation,
    path_preview,
    plan,
    run_dir,
    run_id,
    serial_probe,
    source_kind,
    stage2_dispatch,
    stage2_outcome,
    state,
    warmup_frames,
):
    episode_path = write_episode(episode, run_dir)
    summary = {
        "demo_version": DEMO_VERSION,
        "run_id": run_id,
        "mode": mode,
        "vision_algorithm": algorithm,
        "source_kind": source_kind,
        "input_source": input_source,
        "evidence_boundary": evidence_boundary,
        "camera": (
            {
                "device_index": camera_index,
                "warmup_frames": warmup_frames,
                "width": camera_width,
                "height": camera_height,
            }
            if from_camera or source_kind == "camera"
            else None
        ),
        "pump_armed": arm_pump,
        "human_confirmed": confirm_pump,
        "controller_kind": controller_kind if mode in {"ping-only", "arm-pump"} else None,
        "serial_probe": serial_probe,
        "observation": asdict(observation),
        "contamination": asdict(measurement),
        "cleaning_plan": _plan_as_dict(plan),
        "path_preview": path_preview.to_dict(),
        "stage2_dispatch": None if stage2_dispatch is None else stage2_dispatch.to_dict(),
        "stage2_transmit": (
            None
            if stage2_outcome is None or stage2_outcome.transmit is None
            else stage2_outcome.transmit.to_dict()
        ),
        "stage2_motion": None if stage2_outcome is None else stage2_outcome.to_summary(),
        "hardware_actions": {
            "stm32_pump_attempted": (
                mode == "arm-pump" and controller_kind == "stm32" and episode.execution_receipt is not None
            ),
            "stage2_transmit_attempted": stage2_outcome is not None and stage2_outcome.receipt_written,
            "stage2_lines_sent": 0 if stage2_outcome is None else stage2_outcome.lines_sent(),
            "stage2_in_flight_line": (
                None if stage2_outcome is None or stage2_outcome.error is None else stage2_outcome.error.in_flight_line
            ),
            "stage2_stopped": (
                None if stage2_outcome is None or stage2_outcome.error is None else stage2_outcome.error.stopped
            ),
            "note": "path_preview.send_to_controller 只描述预览对象本身；是否真的发过以本字段为准",
        },
        "state": asdict(state),
        "action_request": asdict(episode.action_request) if episode.action_request else None,
        "safety_decision": asdict(episode.safety_decision) if episode.safety_decision else None,
        "execution_receipt": asdict(episode.execution_receipt) if episode.execution_receipt else None,
        "verification": asdict(episode.verification) if episode.verification else None,
        "episode_file": episode_path.name,
        "artifacts": {
            "input": "input.png",
            "mask": "mask.png",
            "contamination_overlay": "contamination_overlay.png",
            "path_overlay": "path_overlay.png",
            "path_narrative": "path_narrative.txt",
            "post_mask": "post_mask.png" if mode == "simulate" else None,
            "stage2_xy_pulses": "stage2_xy_pulses.txt" if stage2_dispatch is not None else None,
            "stage2_intent": "stage2_intent.json" if stage2_outcome is not None else None,
            "stage2_receipt": (
                "stage2_receipt.json" if stage2_outcome is not None and stage2_outcome.receipt_written else None
            ),
        },
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(f"Demo完成：{run_dir}")
    print(f"模式：{mode}；视觉算法：{algorithm}；证据边界：{evidence_boundary}")
    print(f"污染面积：{measurement.area_px:.0f} px")
    print(f"污染中心：{measurement.centroid_px}")
    print(f"清洗策略：{plan.strategy.value}，路径点数：{len(plan.path_px)}")
    print(f"ActionRequest：{'已生成' if episode.action_request else '未生成'}")
    if episode.safety_decision is not None:
        print(f"SafetyDecision：{episode.safety_decision.outcome.value}")
    print(f"ExecutionReceipt：{'已生成' if episode.execution_receipt else '未生成'}")
    print(f"下一路由：{episode.verification.next_route.value if episode.verification else 'UNKNOWN'}")
    for line in path_preview.narrative:
        print(line)
    if stage2_dispatch is not None:
        print("---------- Stage 2：XY 双轴规划 ----------")
        print(
            f"X 计划 |步|={stage2_dispatch.planned_abs_steps_x}，"
            f"本次可发 |步|={stage2_dispatch.transmit_abs_steps_x}"
        )
        print(
            f"Y 计划 |步|={stage2_dispatch.planned_abs_steps_y}，"
            f"本次可发 |步|={stage2_dispatch.transmit_abs_steps_y}，"
            f"每轴预算={stage2_dispatch.budget}，超出预算已截住={stage2_dispatch.truncated}"
        )
        for line in stage2_dispatch.lines:
            print(f"  计划 MOVEXY：{line}")
        if not stage2_dispatch.lines:
            print("  没有可发脉冲。")
        if stage2_outcome is None:
            print("  分析模式只写 stage2_xy_pulses.txt，不打开步进串口。要转动请用 --mode stage2-move。")
        else:
            reasons = "、".join(stage2_outcome.final_decision.reason_codes)
            print(f"  运动关卡：{stage2_outcome.final_decision.outcome.value}（{reasons}）；状态：{stage2_outcome.status}")
            if stage2_outcome.transmit is not None:
                print(f"  已发送 {len(stage2_outcome.transmit.sent_lines)} 条 MOVEXY 命令。")
                for reply in stage2_outcome.transmit.replies:
                    print(f"  STM32：{reply}")
            if stage2_outcome.error is not None:
                stop_text = "已确认 STOP" if stage2_outcome.error.stopped else "STOP 未确认，请立即手断 24V"
                print(f"  发送失败：{stage2_outcome.error.reason_code}；{stop_text}；位置账本已标记未知。")
            print(f"  位置账本：{stage2_outcome.position_after.to_dict()}")
