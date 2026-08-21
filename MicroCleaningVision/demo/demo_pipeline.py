"""MicroCleaningVision Demo v0.1：真实像素分析与软件模拟闭环。

运行示例：
    python -m demo.demo_pipeline --generate-sample --mode simulate
    python -m demo.demo_pipeline --input path/to/image.png --mode analyze

``analyze`` 只输出真实图片的像素测量和路线，不伪造毫米坐标。
``simulate`` 使用明确标记的归一化虚拟标定，只授权 FakeSerial，不访问COM口。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from microcleaning.contracts import (
    Episode,
    FailureRecord,
    NextRoute,
    VerificationResult,
)
from microcleaning.control_system.cleaning_plan import (
    CleaningPlan,
    plan_cleaning,
    simulate_first_action,
)
from microcleaning.control_system.episode_store import write_episode
from microcleaning.control_system.replay_mcl import ReplayMCLRunner
from microcleaning.data_learning.image_quality import build_observation, inspect_image_file
from microcleaning.vision.hsv_baseline import read_bgr_image, segment_contamination
from microcleaning.vision.state_estimator import estimate_state


DEMO_VERSION = "microcleaning-demo-v0.1"
SIMULATION_CALIBRATION_VERSION = "simulation-normalized-v0"


def run_demo(
    *,
    input_path: str | Path | None = None,
    generate_sample: bool = False,
    mode: str = "analyze",
    output_root: str | Path = Path("output") / "demo",
) -> Path:
    """运行一次Demo并返回本次不可覆盖的输出目录。"""

    if mode not in {"analyze", "simulate"}:
        raise ValueError("mode必须是analyze或simulate")
    if (input_path is None) == (not generate_sample):
        raise ValueError("必须且只能选择--input或--generate-sample之一")

    cv2, np = _load_dependencies()
    run_id = f"demo_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
    run_dir = Path(output_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    input_output = run_dir / "input.png"

    if generate_sample:
        image = _generate_sample(np, cv2)
        if not cv2.imwrite(str(input_output), image):
            raise OSError(f"无法写入合成输入：{input_output}")
        input_source = "program-generated-red-marker"
    else:
        source_path = Path(input_path)  # type: ignore[arg-type]
        image = read_bgr_image(source_path)
        if not cv2.imwrite(str(input_output), image):
            raise OSError(f"无法写入Demo输入副本：{input_output}")
        input_source = str(source_path.resolve())

    inspection = inspect_image_file(input_output)
    observation = build_observation(
        task_id=run_id,
        frame_id="pre",
        raw_image_ref=input_output.relative_to(run_dir).as_posix(),
        quality=inspection.quality,
        software_version=f"{DEMO_VERSION}/{inspection.algorithm_version}",
    )

    segmentation = segment_contamination(image)
    mask_path = run_dir / "mask.png"
    if not cv2.imwrite(str(mask_path), segmentation.mask):
        raise OSError(f"无法写入mask：{mask_path}")
    measurement = replace(segmentation.measurement, mask_ref=mask_path.relative_to(run_dir).as_posix())
    plan = plan_cleaning(segmentation.mask)

    contamination_overlay = _draw_contamination(image, segmentation.mask, measurement.centroid_px, cv2)
    path_overlay = _draw_plan(contamination_overlay, plan, cv2)
    cv2.imwrite(str(run_dir / "contamination_overlay.png"), contamination_overlay)
    cv2.imwrite(str(run_dir / "path_overlay.png"), path_overlay)

    if mode == "simulate":
        state, action_target_mm = _build_simulation_state(observation, measurement, plan)
        post_mask = simulate_first_action(segmentation.mask, plan)
        post_mask_path = run_dir / "post_mask.png"
        cv2.imwrite(str(post_mask_path), post_mask)
        post_observation = build_observation(
            task_id=run_id,
            frame_id="post-simulated",
            raw_image_ref=post_mask_path.relative_to(run_dir).as_posix(),
            quality=inspection.quality,
            software_version=f"{DEMO_VERSION}/SIMULATED_POST_MASK",
        )
        episode = ReplayMCLRunner().run(
            pre=observation,
            state=state,
            post=post_observation,
            post_area_px=float(cv2.countNonZero(post_mask)),
            action_target_mm=action_target_mm,
        )
    else:
        state = estimate_state(observation, measurement)
        verification = VerificationResult(
            task_id=run_id,
            pre_observation_id=observation.observation_id,
            post_observation_id=None,
            residual_area_px=None,
            removal_rate=None,
            damage_flag=False,
            next_route=NextRoute.HUMAN,
            reason_codes=("CALIBRATION_AND_POST_OBSERVATION_REQUIRED",),
        )
        episode = Episode(
            episode_id=f"episode_{uuid4().hex[:12]}",
            task_id=run_id,
            mode="real_image_analysis",
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
                    stage="calibration",
                    severity="info",
                    reason_codes=verification.reason_codes,
                    reproducible=True,
                    recovery="提供真实像素到工作台标定和动作后图片后再申请动作",
                )
            ],
        )

    episode_path = write_episode(episode, run_dir)
    summary = {
        "demo_version": DEMO_VERSION,
        "run_id": run_id,
        "mode": mode,
        "input_source": input_source,
        "evidence_boundary": (
            "真实像素分析；没有真实标定和动作后图像，不执行动作"
            if mode == "analyze"
            else "归一化虚拟标定+FakeSerial+模拟post mask；不代表真实清洗"
        ),
        "observation": asdict(observation),
        "contamination": asdict(measurement),
        "cleaning_plan": _plan_as_dict(plan),
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
            "post_mask": "post_mask.png" if mode == "simulate" else None,
        },
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(f"Demo完成：{run_dir}")
    print(f"污染面积：{measurement.area_px:.0f} px")
    print(f"污染中心：{measurement.centroid_px}")
    print(f"清洗策略：{plan.strategy.value}，路径点数：{len(plan.path_px)}")
    print(f"ActionRequest：{'已生成（仅模拟）' if episode.action_request else '未生成'}")
    print(f"下一路由：{episode.verification.next_route.value if episode.verification else 'UNKNOWN'}")
    return run_dir


def _build_simulation_state(observation, measurement, plan: CleaningPlan):
    width, height = plan.image_size_px
    if measurement.centroid_px is None:
        return estimate_state(
            observation,
            measurement,
            calibration_version=SIMULATION_CALIBRATION_VERSION,
            calibration_valid=True,
            device_state={"controller_connected": True, "interlock_ok": True},
        ), None

    def to_mm(point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        return (
            100.0 * x / max(1, width - 1),
            100.0 * y / max(1, height - 1),
        )

    centroid_mm = to_mm(measurement.centroid_px)
    scale = max(100.0 / max(1, width - 1), 100.0 / max(1, height - 1))
    uncertainty_mm = measurement.uncertainty_px * scale
    state = estimate_state(
        observation,
        measurement,
        calibration_version=SIMULATION_CALIBRATION_VERSION,
        calibration_valid=True,
        target_centroid_mm=centroid_mm,
        uncertainty_mm=uncertainty_mm,
        device_state={"controller_connected": True, "interlock_ok": True},
    )
    action_target = to_mm(plan.path_px[0]) if plan.path_px else None
    return state, action_target


def _draw_contamination(image, mask, centroid, cv2):
    overlay = image.copy()
    colored = image.copy()
    colored[mask > 0] = (0, 190, 255)
    overlay = cv2.addWeighted(overlay, 0.72, colored, 0.28, 0)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(overlay, contours, -1, (0, 255, 255), 2)
    if centroid is not None:
        cv2.drawMarker(overlay, (round(centroid[0]), round(centroid[1])), (255, 0, 0), cv2.MARKER_CROSS, 18, 2)
    return overlay


def _draw_plan(image, plan: CleaningPlan, cv2):
    overlay = image.copy()
    points = [(round(x), round(y)) for x, y in plan.path_px]
    segment_starts = set(plan.segment_start_indices)
    for end_index in range(1, len(points)):
        if end_index not in segment_starts:
            cv2.line(overlay, points[end_index - 1], points[end_index], (0, 255, 0), 2)
    for index, point in enumerate(points):
        cv2.circle(overlay, point, 4 if index else 7, (255, 0, 255) if index else (0, 0, 255), -1)
    return overlay


def _plan_as_dict(plan: CleaningPlan) -> dict[str, object]:
    return {
        "strategy": plan.strategy.value,
        "coordinate_frame": plan.coordinate_frame,
        "image_size_px": plan.image_size_px,
        "contamination_area_px": plan.contamination_area_px,
        "path_px": plan.path_px,
        "segment_start_indices": plan.segment_start_indices,
        "reason": plan.reason,
    }


def _generate_sample(np, cv2):
    rng = np.random.default_rng(20260820)
    image = np.full((360, 520, 3), (208, 214, 220), dtype=np.uint8)
    texture = rng.normal(0, 5, image.shape[:2]).astype(np.int16)
    for channel in range(3):
        image[:, :, channel] = np.clip(image[:, :, channel].astype(np.int16) + texture, 0, 255).astype(np.uint8)
    cv2.ellipse(image, (250, 178), (92, 54), -12, 0, 360, (18, 28, 225), -1)
    cv2.circle(image, (382, 248), 27, (12, 20, 210), -1)
    return image


def _load_dependencies():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "Demo需要NumPy/OpenCV；请安装requirements/perception-opencv.txt"
        ) from exc
    return cv2, np


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MicroCleaningVision Demo v0.1")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="jpg/png真实图片路径")
    source.add_argument("--generate-sample", action="store_true", help="生成可复现的红色模拟污染图")
    parser.add_argument("--mode", choices=("analyze", "simulate"), default="analyze")
    parser.add_argument("--output-root", default=str(Path("output") / "demo"))
    args = parser.parse_args(argv)
    run_demo(
        input_path=args.input,
        generate_sample=args.generate_sample,
        mode=args.mode,
        output_root=args.output_root,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
