"""从原 Demo 抽取的 demo.single_frame；保持既有单帧行为。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from datetime import timezone
from pathlib import Path
from uuid import uuid4
from microcleaning.control_system.planning.cleaning_plan import CleaningPlan
from microcleaning.control_system.planning.cleaning_plan import plan_cleaning
from microcleaning.control_system.planning.cleaning_plan import simulate_first_action
from microcleaning.control_system.planning.path_preview import build_path_preview
from microcleaning.control_system.planning.path_preview import draw_path_overlay
from microcleaning.control_system.planning.path_preview import load_path_placeholders
from microcleaning.control_system.planning.path_preview import resolve_plan_policy
from microcleaning.control_system.planning.stage2_axes import dispatch_motion
from microcleaning.control_system.planning.stage2_geometry import stage2_placeholders as _stage2_placeholders
from microcleaning.control_system.planning.stage2_position import DEFAULT_POSITION_PATH
from microcleaning.control_system.planning.work_frame import load_motor_calibration
from microcleaning.control_system.replay.replay_mcl import ReplayMCLRunner
from microcleaning.control_system.safety.fixed_rule import DEFAULT_IN_PLACE_DURATION_MS
from microcleaning.control_system.safety.fixed_rule import MAX_IN_PLACE_DURATION_MS
from microcleaning.control_system.safety.motion_gate import STAGE2_RUN_STEP_CAP
from microcleaning.data_learning.image_quality import build_observation
from microcleaning.data_learning.image_quality import inspect_image_file
from microcleaning.vision.state_estimator import estimate_state
from demo import CaptureFactory, DEMO_MODES, DEMO_VERSION, MotionConfirm, SIMULATION_CALIBRATION_VERSION, SerialFactory, VISION_ALGORITHMS
from demo.image_ops import _draw_contamination, _load_demo_image, _load_dependencies, segment_demo_image
from demo.motion_mode import Stage2MotionFailed, Stage2MotionOutcome, _stage2_episode, _stage2_move
from demo.pump_mode import _arm_pump_episode, _controller_device_facts, _probe_serial
from demo.reporting import _analysis_episode, write_demo_report


def _write_image(path, image, cv2):
    """Windows 中文目录以 Python 写字节，避免 imwrite 的路径编码限制。"""
    okay, encoded = cv2.imencode(Path(path).suffix or ".png", image)
    if okay:
        Path(path).write_bytes(encoded.tobytes())
    return okay


def run_demo(
    *,
    input_path: str | Path | None = None,
    generate_sample: bool = False,
    from_camera: bool = False,
    mode: str = "analyze",
    output_root: str | Path = Path("output") / "demo",
    camera_index: int = 0,
    warmup_frames: int = 5,
    camera_width: int | None = None,
    camera_height: int | None = None,
    camera_backend: int | None = None,
    capture_factory: CaptureFactory | None = None,
    serial_port: str | None = None,
    baudrate: int = 115200,
    serial_timeout: float = 2.0,
    arm_pump: bool = False,
    confirm_pump: bool = False,
    controller_kind: str = "fake",
    serial_factory: SerialFactory | None = None,
    pump_duration_ms: int = DEFAULT_IN_PLACE_DURATION_MS,
    algorithm: str = "local",
    source_kind_override: str | None = None,
    input_source_override: str | None = None,
    policy_path: str | Path | None = None,
    use_tuned_policy: bool | None = None,
    path_placeholders: str | Path | None = None,
    stage2_xy: bool = False,
    arm_stage2_xy: bool = False,
    stage2_max_steps: int = STAGE2_RUN_STEP_CAP,
    stage2_calibration: str | Path | None = None,
    stage2_position_path: str | Path | None = None,
    motion_confirm: MotionConfirm | None = None,
) -> Path:
    """运行一次Demo并返回本次不可覆盖的输出目录。

    ``stage2-move`` 发送中途失败时，记录写完后抛 ``Stage2MotionFailed``。
    ``motion_confirm`` 为空时运动停在 HUMAN，不打开步进串口。
    """

    _validate_demo_args(
        input_path=input_path,
        generate_sample=generate_sample,
        from_camera=from_camera,
        mode=mode,
        arm_pump=arm_pump,
        confirm_pump=confirm_pump,
        controller_kind=controller_kind,
        pump_duration_ms=pump_duration_ms,
        algorithm=algorithm,
        arm_stage2_xy=arm_stage2_xy,
        stage2_max_steps=stage2_max_steps,
    )
    if mode == "stage2-move":
        stage2_xy = True
    calibration = load_motor_calibration(stage2_calibration) if stage2_calibration is not None else None
    position_path = Path(stage2_position_path) if stage2_position_path is not None else DEFAULT_POSITION_PATH

    cv2, np = _load_dependencies()
    run_id = f"demo_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
    run_dir = Path(output_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    input_output = run_dir / "input.png"

    image, input_source, source_kind = _load_demo_image(
        input_path=input_path,
        generate_sample=generate_sample,
        from_camera=from_camera,
        run_id=run_id,
        run_dir=run_dir,
        camera_index=camera_index,
        warmup_frames=warmup_frames,
        camera_width=camera_width,
        camera_height=camera_height,
        camera_backend=camera_backend,
        capture_factory=capture_factory,
        cv2=cv2,
        np=np,
    )
    if source_kind_override:
        source_kind = source_kind_override
    if input_source_override:
        input_source = input_source_override
    if not _write_image(input_output, image, cv2):
        raise OSError(f"无法写入Demo输入副本：{input_output}")

    inspection = inspect_image_file(input_output)
    observation = build_observation(
        task_id=run_id,
        frame_id="pre",
        raw_image_ref=input_output.relative_to(run_dir).as_posix(),
        quality=inspection.quality,
        software_version=f"{DEMO_VERSION}/{inspection.algorithm_version}",
    )

    segmentation = segment_demo_image(
        image,
        algorithm,
        policy_path=policy_path,
        use_tuned_policy=use_tuned_policy,
    )
    mask_path = run_dir / "mask.png"
    if not _write_image(mask_path, segmentation.mask, cv2):
        raise OSError(f"无法写入mask：{mask_path}")
    measurement = replace(segmentation.measurement, mask_ref=mask_path.relative_to(run_dir).as_posix())
    placeholders = load_path_placeholders(path_placeholders)
    start_reference = None
    if stage2_xy:
        placeholders, start_reference = _stage2_placeholders(placeholders, image.shape, calibration)
    plan = plan_cleaning(segmentation.mask, policy=resolve_plan_policy(placeholders))
    path_preview = build_path_preview(plan, placeholders=placeholders)

    contamination_overlay = _draw_contamination(image, segmentation.mask, measurement.centroid_px, cv2)
    path_overlay = draw_path_overlay(contamination_overlay, path_preview)
    _write_image(run_dir / "contamination_overlay.png", contamination_overlay, cv2)
    _write_image(run_dir / "path_overlay.png", path_overlay, cv2)
    (run_dir / "path_narrative.txt").write_text("\n".join(path_preview.narrative) + "\n", encoding="utf-8")
    stage2_dispatch = None
    stage2_outcome: Stage2MotionOutcome | None = None
    if stage2_xy:
        stage2_dispatch = dispatch_motion(path_preview.motion, budget=stage2_max_steps)
        xy_text = "\n".join(stage2_dispatch.lines) + ("\n" if stage2_dispatch.lines else "")
        (run_dir / "stage2_xy_pulses.txt").write_text(xy_text, encoding="utf-8")

    serial_probe: dict[str, object] | None = None
    if mode == "stage2-move":
        stage2_outcome = _stage2_move(
            run_id=run_id,
            run_dir=run_dir,
            dispatch=stage2_dispatch,
            start_reference=start_reference,
            calibration=calibration,
            position_path=position_path,
            motion_confirm=motion_confirm,
            arm_stage2_xy=arm_stage2_xy,
            serial_port=serial_port,
            baudrate=baudrate,
            serial_timeout=serial_timeout,
            serial_factory=serial_factory,
        )
        state = estimate_state(observation, measurement)
        episode, evidence_boundary = _stage2_episode(
            run_id=run_id,
            observation=observation,
            state=state,
            outcome=stage2_outcome,
        )
    elif mode == "simulate":
        state, action_target_mm = _build_simulation_state(observation, measurement, plan)
        post_mask = simulate_first_action(segmentation.mask, plan)
        post_mask_path = run_dir / "post_mask.png"
        _write_image(post_mask_path, post_mask, cv2)
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
        evidence_boundary = "归一化虚拟标定+FakeSerial+模拟post mask；不代表真实清洗"
    elif mode == "ping-only":
        serial_probe, controller_connected, estop_active = _probe_serial(
            serial_port=serial_port,
            baudrate=baudrate,
            serial_timeout=serial_timeout,
            serial_factory=serial_factory,
        )
        state = estimate_state(
            observation,
            measurement,
            device_state={
                "controller_connected": controller_connected,
                "interlock_ok": controller_connected and not estop_active,
                "e_stop_active": estop_active,
            },
        )
        episode = _analysis_episode(
            run_id=run_id,
            observation=observation,
            state=state,
            mode_name="serial_ping_only",
            reason_codes=("SERIAL_PING_ONLY", "NO_PUMP_SENT"),
            recovery="通信探测不申请泵；要申请定点短喷请使用 --mode arm-pump",
        )
        evidence_boundary = "仅PING/STATUS通信探测；不发送PUMP，未接12V不能写成泵已工作"
    elif mode == "arm-pump":
        serial_probe, controller_connected, estop_active = _controller_device_facts(
            controller_kind=controller_kind,
            serial_port=serial_port,
            baudrate=baudrate,
            serial_timeout=serial_timeout,
            serial_factory=serial_factory,
        )
        state = estimate_state(
            observation,
            measurement,
            device_state={
                "controller_connected": controller_connected,
                "interlock_ok": controller_connected and not estop_active,
                "e_stop_active": estop_active,
            },
        )
        episode, evidence_boundary = _arm_pump_episode(
            run_id=run_id,
            observation=observation,
            state=state,
            confirm_pump=confirm_pump,
            controller_kind=controller_kind,
            arm_pump=arm_pump,
            serial_port=serial_port,
            baudrate=baudrate,
            serial_timeout=serial_timeout,
            serial_factory=serial_factory,
            pump_duration_ms=pump_duration_ms,
        )
    else:
        state = estimate_state(observation, measurement)
        episode_mode = "camera_image_analysis" if mode == "camera-analyze" or from_camera else "real_image_analysis"
        episode = _analysis_episode(
            run_id=run_id,
            observation=observation,
            state=state,
            mode_name=episode_mode,
            reason_codes=("CALIBRATION_AND_POST_OBSERVATION_REQUIRED", "NO_PUMP_SENT"),
            recovery="提供真实像素到工作台标定和动作后图片后再申请动作；本模式默认不发送PUMP",
        )
        if from_camera or mode == "camera-analyze" or source_kind == "camera":
            evidence_boundary = "USB相机或文件像素分析；不发送PUMP，不代表标定或清洗"
        else:
            evidence_boundary = "真实像素分析；没有真实标定和动作后图像，不执行动作"

    write_demo_report(
        algorithm=algorithm,
        arm_pump=arm_pump,
        camera_height=camera_height,
        camera_index=camera_index,
        camera_width=camera_width,
        confirm_pump=confirm_pump,
        controller_kind=controller_kind,
        episode=episode,
        evidence_boundary=evidence_boundary,
        from_camera=from_camera,
        input_source=input_source,
        measurement=measurement,
        mode=mode,
        observation=observation,
        path_preview=path_preview,
        plan=plan,
        run_dir=run_dir,
        run_id=run_id,
        serial_probe=serial_probe,
        source_kind=source_kind,
        stage2_dispatch=stage2_dispatch,
        stage2_outcome=stage2_outcome,
        state=state,
        warmup_frames=warmup_frames,
    )
    if stage2_outcome is not None and stage2_outcome.status == "failed":
        raise Stage2MotionFailed(run_dir, stage2_outcome.error.reason_code)
    return run_dir


def _validate_demo_args(
    *,
    input_path: str | Path | None,
    generate_sample: bool,
    from_camera: bool,
    mode: str,
    arm_pump: bool,
    confirm_pump: bool,
    controller_kind: str,
    pump_duration_ms: int,
    algorithm: str,
    arm_stage2_xy: bool = False,
    stage2_max_steps: int = STAGE2_RUN_STEP_CAP,
) -> None:
    if mode not in DEMO_MODES:
        raise ValueError(f"mode必须是{'/'.join(DEMO_MODES)}")
    if algorithm not in VISION_ALGORITHMS:
        raise ValueError(f"algorithm必须是{'/'.join(VISION_ALGORITHMS)}")
    selected = sum((input_path is not None, generate_sample, from_camera))
    if selected != 1:
        raise ValueError("必须且只能选择--input、--generate-sample或--from-camera之一")
    if mode == "camera-analyze" and not from_camera:
        raise ValueError("camera-analyze 必须使用 --from-camera")
    if mode == "simulate" and from_camera:
        raise ValueError("simulate 不能与 --from-camera 同时使用；相机像素不要混入虚拟毫米标定")
    if controller_kind not in {"fake", "stm32"}:
        raise ValueError("controller 必须是 fake 或 stm32")
    if arm_pump and mode != "arm-pump":
        raise ValueError("--arm-pump 只能与 --mode arm-pump 一起使用")
    if confirm_pump and mode != "arm-pump":
        raise ValueError("--confirm-pump 只能与 --mode arm-pump 一起使用")
    if not 100 <= pump_duration_ms <= MAX_IN_PLACE_DURATION_MS:
        raise ValueError(f"--pump-duration-ms 必须在 100 到 {MAX_IN_PLACE_DURATION_MS} 之间")
    if arm_stage2_xy and mode != "stage2-move":
        raise ValueError("只有 --mode stage2-move 能发步进；analyze/camera-analyze 只写 stage2_xy_pulses.txt")
    if not 0 <= stage2_max_steps <= STAGE2_RUN_STEP_CAP:
        raise ValueError(f"--stage2-max-steps 必须在 0 到 {STAGE2_RUN_STEP_CAP} 之间（代码常量，不能调大）")


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
