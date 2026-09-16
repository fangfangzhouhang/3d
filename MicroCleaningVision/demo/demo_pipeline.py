"""MicroCleaningVision Demo v0.2：真实像素分析、相机抓帧与软件/串口一拍闭环。

运行示例：
    python -m demo.demo_pipeline --generate-sample --mode simulate
    python -m demo.demo_pipeline --input path/to/image.png --mode analyze
    python -m demo.demo_pipeline --from-camera --mode analyze
    python -m demo.demo_pipeline --from-camera --live --camera-index 1
    python -m demo.demo_pipeline --from-camera --live --mode arm-pump --confirm-pump --arm-pump --controller stm32 --serial-port COM5
    python -m demo.demo_pipeline --from-camera --mode camera-analyze
    python -m demo.demo_pipeline --generate-sample --mode ping-only
    python -m demo.demo_pipeline --generate-sample --mode arm-pump --confirm-pump --controller fake

``analyze`` / ``camera-analyze`` 只输出像素测量和路线，默认不发送 PUMP。
``--live`` 打开带 B 分割叠加的实时窗口，空格冻结当前帧；默认走 analyze。
``--live --mode arm-pump --confirm-pump --arm-pump --controller stm32 --serial-port COMx``
时，空格在识别到目标后发送限时 PUMP。预览循环不会自动喷。
``simulate`` 使用明确标记的归一化虚拟标定，只授权 FakeSerial，不访问COM口。
``ping-only`` 在视觉结果之外只探测 PING/STATUS。
``arm-pump`` 可申请定点短喷，但第一次泵动作必须经过人工关卡；STM32 路径还要 ``--arm-pump``。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from microcleaning.contracts import (
    Episode,
    FailureRecord,
    NextRoute,
    Observation,
    SafetyOutcome,
    StateEstimate,
    VerificationResult,
)
from microcleaning.control_system.cleaning_plan import (
    CleaningPlan,
    plan_cleaning,
    simulate_first_action,
)
from microcleaning.control_system.episode_store import write_episode
from microcleaning.control_system.fake_serial import FakeSerialController
from microcleaning.control_system.fixed_rule import (
    DEFAULT_IN_PLACE_DURATION_MS,
    MAX_IN_PLACE_DURATION_MS,
    FixedActionPolicy,
    PUMP_IN_PLACE_RULE_VERSION,
    propose_pump_in_place,
)
from microcleaning.control_system.governor import approve_human_gate, evaluate_action
from microcleaning.control_system.replay_mcl import ReplayMCLRunner
from microcleaning.control_system.stm32_protocol import encode_ping, encode_status
from microcleaning.control_system.stm32_serial import STM32SerialController
from microcleaning.data_learning.image_quality import build_observation, inspect_image_file
from microcleaning.vision.exg_baseline import (
    segment_contamination as segment_exg,
    segment_excess_red as segment_exr,
)
from microcleaning.vision.hsv_baseline import read_bgr_image, segment_contamination as segment_hsv
from microcleaning.vision.local_contrast_baseline import segment_contamination as segment_local
from microcleaning.vision.otsu_baseline import segment_contamination as segment_otsu
from microcleaning.vision.state_estimator import estimate_state


DEMO_VERSION = "microcleaning-demo-v0.2"
SIMULATION_CALIBRATION_VERSION = "simulation-normalized-v0"
DEMO_MODES = ("analyze", "simulate", "camera-analyze", "ping-only", "arm-pump")
VISION_ALGORITHMS = ("hsv", "otsu", "exg", "exr", "local")
CaptureFactory = Callable[..., Any]
SerialFactory = Callable[[], Any]


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
) -> Path:
    """运行一次Demo并返回本次不可覆盖的输出目录。"""

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
    )

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
    if not cv2.imwrite(str(input_output), image):
        raise OSError(f"无法写入Demo输入副本：{input_output}")

    inspection = inspect_image_file(input_output)
    observation = build_observation(
        task_id=run_id,
        frame_id="pre",
        raw_image_ref=input_output.relative_to(run_dir).as_posix(),
        quality=inspection.quality,
        software_version=f"{DEMO_VERSION}/{inspection.algorithm_version}",
    )

    segmentation = segment_demo_image(image, algorithm)
    mask_path = run_dir / "mask.png"
    if not cv2.imwrite(str(mask_path), segmentation.mask):
        raise OSError(f"无法写入mask：{mask_path}")
    measurement = replace(segmentation.measurement, mask_ref=mask_path.relative_to(run_dir).as_posix())
    plan = plan_cleaning(segmentation.mask)

    contamination_overlay = _draw_contamination(image, segmentation.mask, measurement.centroid_px, cv2)
    path_overlay = _draw_plan(contamination_overlay, plan, cv2)
    cv2.imwrite(str(run_dir / "contamination_overlay.png"), contamination_overlay)
    cv2.imwrite(str(run_dir / "path_overlay.png"), path_overlay)

    serial_probe: dict[str, object] | None = None
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
    print(f"模式：{mode}；视觉算法：{algorithm}；证据边界：{evidence_boundary}")
    print(f"污染面积：{measurement.area_px:.0f} px")
    print(f"污染中心：{measurement.centroid_px}")
    print(f"清洗策略：{plan.strategy.value}，路径点数：{len(plan.path_px)}")
    print(f"ActionRequest：{'已生成' if episode.action_request else '未生成'}")
    if episode.safety_decision is not None:
        print(f"SafetyDecision：{episode.safety_decision.outcome.value}")
    print(f"ExecutionReceipt：{'已生成' if episode.execution_receipt else '未生成'}")
    print(f"下一路由：{episode.verification.next_route.value if episode.verification else 'UNKNOWN'}")
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


def _load_demo_image(
    *,
    input_path: str | Path | None,
    generate_sample: bool,
    from_camera: bool,
    run_id: str,
    run_dir: Path,
    camera_index: int,
    warmup_frames: int,
    camera_width: int | None,
    camera_height: int | None,
    camera_backend: int | None,
    capture_factory: CaptureFactory | None,
    cv2: Any,
    np: Any,
) -> tuple[Any, str, str]:
    if generate_sample:
        return _generate_sample(np, cv2), "program-generated-red-marker", "generated"
    if input_path is not None:
        source_path = Path(input_path)
        return read_bgr_image(source_path), str(source_path.resolve()), "file"
    from microcleaning.data_learning.usb_camera import USBCamera, USBCameraConfig, USBCameraError

    camera = USBCamera(
        USBCameraConfig(
            device_index=camera_index,
            output_root=run_dir / "camera",
            width=camera_width,
            height=camera_height,
            warmup_frames=warmup_frames,
            backend=camera_backend,
        ),
        capture_factory=capture_factory,
    )
    try:
        camera.capture(run_id, "pre")
    except USBCameraError:
        raise
    captured = camera.capture_path("pre")
    return read_bgr_image(captured), f"usb-camera:index={camera_index}", "camera"


def segment_demo_image(image, algorithm: str = "local"):
    """按 Demo 当前选择的 B 算法分割；默认是邻域差异 local。HSV 只作对照。"""

    if algorithm == "otsu":
        return segment_otsu(image)
    if algorithm == "hsv":
        return segment_hsv(image)
    if algorithm == "exg":
        return segment_exg(image)
    if algorithm == "exr":
        return segment_exr(image)
    if algorithm == "local":
        return segment_local(image)
    raise ValueError(f"algorithm必须是{'/'.join(VISION_ALGORITHMS)}")


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


def _arm_pump_episode(
    *,
    run_id: str,
    observation: Observation,
    state: StateEstimate,
    confirm_pump: bool,
    controller_kind: str,
    arm_pump: bool,
    serial_port: str | None,
    baudrate: int,
    serial_timeout: float,
    serial_factory: SerialFactory | None,
    pump_duration_ms: int,
) -> tuple[Episode, str]:
    request = propose_pump_in_place(
        state,
        FixedActionPolicy(duration_ms=pump_duration_ms, version=PUMP_IN_PLACE_RULE_VERSION),
    )
    if request is None:
        episode = _analysis_episode(
            run_id=run_id,
            observation=observation,
            state=state,
            mode_name="pump_in_place_no_target",
            reason_codes=("NO_TARGET", "NO_PUMP_SENT"),
            recovery="视野内没有可喷目标，未申请定点短喷",
        )
        return episode, "未发现目标，未申请定点短喷，未发送PUMP"

    decision = evaluate_action(state, request)
    if confirm_pump:
        try:
            decision = approve_human_gate(state, request, decision, confirmed=True)
        except PermissionError as exc:
            episode = Episode(
                episode_id=f"episode_{uuid4().hex[:12]}",
                task_id=run_id,
                mode="pump_in_place_human_gate_refused",
                protocol_version=DEMO_VERSION,
                observation_pre=observation,
                state=state,
                action_request=request,
                safety_decision=decision,
                execution_receipt=None,
                observation_post=None,
                verification=VerificationResult(
                    run_id,
                    observation.observation_id,
                    None,
                    None,
                    None,
                    False,
                    NextRoute.STOP,
                    ("HUMAN_GATE_REFUSED",),
                ),
                failures=[
                    FailureRecord(
                        f"failure_{uuid4().hex[:12]}",
                        run_id,
                        "safety",
                        "warning",
                        ("HUMAN_GATE_REFUSED",),
                        True,
                        str(exc),
                    )
                ],
            )
            return episode, f"人工关卡拒绝改写决策：{exc}"

    if decision.outcome is not SafetyOutcome.ALLOW:
        route = NextRoute.HUMAN if decision.outcome is SafetyOutcome.HUMAN else NextRoute.STOP
        episode = Episode(
            episode_id=f"episode_{uuid4().hex[:12]}",
            task_id=run_id,
            mode="pump_in_place_human_gate",
            protocol_version=DEMO_VERSION,
            observation_pre=observation,
            state=state,
            action_request=request,
            safety_decision=decision,
            execution_receipt=None,
            observation_post=None,
            verification=VerificationResult(
                run_id,
                observation.observation_id,
                None,
                None,
                None,
                False,
                route,
                decision.reason_codes,
            ),
            failures=[
                FailureRecord(
                    f"failure_{uuid4().hex[:12]}",
                    run_id,
                    "safety",
                    "info",
                    decision.reason_codes,
                    True,
                    "未过人工关卡或被拒绝，未发送PUMP",
                )
            ],
        )
        evidence = (
            "已申请定点短喷但未过人工关卡；未发送PUMP"
            if decision.outcome is SafetyOutcome.HUMAN
            else "定点短喷申请被拒绝；未发送PUMP"
        )
        return episode, evidence

    receipt = None
    execute_error: str | None = None
    try:
        if controller_kind == "fake":
            receipt = FakeSerialController().execute(request, decision)
            evidence_boundary = "人工确认后的FakeSerial定点短喷回放；未打开COM口，不代表真实喷洗"
            episode_mode = "pump_in_place_fake_serial"
        else:
            controller = STM32SerialController(
                port=serial_port,
                baudrate=baudrate,
                timeout=serial_timeout,
                arm_pump=arm_pump,
                serial_factory=serial_factory,
            )
            try:
                receipt = controller.execute(request, decision)
            finally:
                controller.close()
            evidence_boundary = (
                "人工确认且--arm-pump后向STM32发送限时PUMP；未接12V时不能写成真实喷洗有效"
                if arm_pump
                else "审批后控制器未武装；拒绝发送PUMP"
            )
            episode_mode = "pump_in_place_stm32"
    except PermissionError as exc:
        execute_error = str(exc)
        evidence_boundary = "控制器拒绝执行（未武装、非ALLOW或令牌无效）；未发送PUMP"
        episode_mode = "pump_in_place_refused"

    failures = []
    if execute_error:
        failures.append(
            FailureRecord(
                f"failure_{uuid4().hex[:12]}",
                run_id,
                "execution",
                "warning",
                ("PUMP_REFUSED",),
                True,
                execute_error,
            )
        )
    elif receipt is not None and not receipt.success:
        failures.append(
            FailureRecord(
                f"failure_{uuid4().hex[:12]}",
                run_id,
                "execution",
                "warning",
                (receipt.error_code or "EXECUTION_FAILED",),
                True,
                "检查串口回执；ACK不等于清洗成功",
            )
        )

    next_route = NextRoute.HUMAN
    reason_codes: tuple[str, ...] = ("POST_OBSERVATION_REQUIRED",)
    if execute_error:
        next_route = NextRoute.STOP
        reason_codes = ("PUMP_REFUSED",)
    elif receipt is not None and receipt.success:
        next_route = NextRoute.HUMAN
        reason_codes = ("POST_OBSERVATION_REQUIRED", "RECEIPT_IS_NOT_CLEANING_PROOF")

    episode = Episode(
        episode_id=f"episode_{uuid4().hex[:12]}",
        task_id=run_id,
        mode=episode_mode,
        protocol_version=DEMO_VERSION,
        observation_pre=observation,
        state=state,
        action_request=request,
        safety_decision=decision,
        execution_receipt=receipt,
        observation_post=None,
        verification=VerificationResult(
            run_id,
            observation.observation_id,
            None,
            None,
            None,
            False,
            next_route,
            reason_codes,
        ),
        failures=failures,
    )
    return episode, evidence_boundary


def _controller_device_facts(
    *,
    controller_kind: str,
    serial_port: str | None,
    baudrate: int,
    serial_timeout: float,
    serial_factory: SerialFactory | None,
) -> tuple[dict[str, object] | None, bool, bool]:
    if controller_kind == "fake":
        return {"opened": False, "mode": "fake_serial", "pump_sent": False}, True, False
    return _probe_serial(
        serial_port=serial_port,
        baudrate=baudrate,
        serial_timeout=serial_timeout,
        serial_factory=serial_factory,
    )


def _probe_serial(
    *,
    serial_port: str | None,
    baudrate: int,
    serial_timeout: float,
    serial_factory: SerialFactory | None,
) -> tuple[dict[str, object], bool, bool]:
    preview = {
        "ping": encode_ping().decode("ascii").rstrip(),
        "status": encode_status().decode("ascii").rstrip(),
        "pump_sent": False,
    }
    if serial_factory is None and not serial_port:
        return (
            {
                **preview,
                "opened": False,
                "reason": "SERIAL_NOT_OPENED",
            },
            False,
            False,
        )
    controller = STM32SerialController(
        port=serial_port,
        baudrate=baudrate,
        timeout=serial_timeout,
        arm_pump=False,
        serial_factory=serial_factory,
    )
    try:
        pong = controller.ping()
        status = controller.status()
        return (
            {
                **preview,
                "opened": True,
                "pong": pong.raw,
                "status": status.raw,
                "estop_active": status.estop_active,
                "pump_active": status.pump_active,
            },
            True,
            bool(status.estop_active),
        )
    except Exception as exc:
        return (
            {
                **preview,
                "opened": False,
                "reason": type(exc).__name__,
                "detail": str(exc),
            },
            False,
            False,
        )
    finally:
        controller.close()


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
    parser = argparse.ArgumentParser(description="MicroCleaningVision Demo v0.2")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="jpg/png真实图片路径")
    source.add_argument("--generate-sample", action="store_true", help="生成可复现的红色模拟污染图")
    source.add_argument("--from-camera", action="store_true", help="用 USBCamera 抓一帧；默认不发泵")
    parser.add_argument(
        "--mode",
        choices=DEMO_MODES,
        default="analyze",
        help="analyze/camera-analyze 只分析；ping-only 只探测通信；arm-pump 需人工确认才可能发泵；可与 --live 组合",
    )
    parser.add_argument("--output-root", default=str(Path("output") / "demo"))
    parser.add_argument("--camera-index", type=int, default=0, help="OpenCV VideoCapture 设备序号")
    parser.add_argument("--warmup-frames", type=int, default=5, help="抓目标帧前丢弃的预热帧数")
    parser.add_argument("--camera-width", type=int, default=None)
    parser.add_argument("--camera-height", type=int, default=None)
    parser.add_argument("--camera-backend", type=int, default=None, help="可选 OpenCV backend 整数")
    parser.add_argument("--serial-port", help="STM32 COM 口，如 COM5；省略时不打开串口")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--serial-timeout", type=float, default=2.0)
    parser.add_argument("--controller", choices=("fake", "stm32"), default="fake")
    parser.add_argument("--arm-pump", action="store_true", help="允许 STM32SerialController 翻译已批准的 PUMP")
    parser.add_argument("--confirm-pump", action="store_true", help="人工关卡：把 HUMAN 转为一次性 ALLOW")
    parser.add_argument(
        "--pump-duration-ms",
        type=int,
        default=DEFAULT_IN_PLACE_DURATION_MS,
        help=f"定点短喷时长，100～{MAX_IN_PLACE_DURATION_MS} ms，默认 {DEFAULT_IN_PLACE_DURATION_MS}",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="打开实时窗口：B 分割叠加辅助对焦，空格抓帧；默认不发泵。与 --mode arm-pump 组合后，空格在有目标时发泵",
    )
    parser.add_argument(
        "--algorithm",
        choices=VISION_ALGORITHMS,
        default="local",
        help="B 分割算法；默认 local=邻域差异。实时窗口 H/O/G/E/L 可切换。HSV 只作对照，不是主算法",
    )
    parser.add_argument(
        "--wait-usb",
        action="store_true",
        help="实时会话：先等待指定 camera-index 可读取再打开预览；Q暂停后按其他键重开",
    )
    args = parser.parse_args(argv)
    if args.live:
        if not args.from_camera:
            parser.error("--live 必须与 --from-camera 一起使用")
        pump_on_analyze = False
        if args.mode in {"analyze", "camera-analyze"}:
            if args.arm_pump or args.confirm_pump:
                parser.error(
                    "默认 --live 不发泵。要实喷请使用 --mode arm-pump --confirm-pump "
                    "--arm-pump --controller stm32 --serial-port COMx"
                )
        elif args.mode == "arm-pump":
            if not args.confirm_pump:
                parser.error("--live --mode arm-pump 必须加 --confirm-pump（空格=人在场确认这一帧）")
            if args.controller == "stm32":
                if not args.arm_pump:
                    parser.error("STM32 实喷还必须加 --arm-pump")
                if not args.serial_port:
                    parser.error("STM32 实喷必须指定 --serial-port，例如 COM5")
            pump_on_analyze = True
        else:
            parser.error("--live 只能与 analyze/camera-analyze 或 arm-pump 一起使用")
        from demo.live_station import run_live_session
        from microcleaning.data_learning.usb_camera import USBCameraError

        try:
            run_live_session(
                camera_index=args.camera_index,
                algorithm=args.algorithm,
                output_root=args.output_root,
                warmup_frames=args.warmup_frames,
                camera_width=args.camera_width,
                camera_height=args.camera_height,
                camera_backend=args.camera_backend,
                wait_usb=args.wait_usb,
                pump_on_analyze=pump_on_analyze,
                confirm_pump=args.confirm_pump,
                arm_pump=args.arm_pump,
                controller_kind=args.controller,
                serial_port=args.serial_port,
                baudrate=args.baudrate,
                serial_timeout=args.serial_timeout,
                pump_duration_ms=args.pump_duration_ms,
            )
        except USBCameraError as exc:
            print(f"相机采集失败：{exc}", file=sys.stderr)
            return 2
        return 0
    try:
        run_demo(
            input_path=args.input,
            generate_sample=args.generate_sample,
            from_camera=args.from_camera,
            mode=args.mode,
            output_root=args.output_root,
            camera_index=args.camera_index,
            warmup_frames=args.warmup_frames,
            camera_width=args.camera_width,
            camera_height=args.camera_height,
            camera_backend=args.camera_backend,
            serial_port=args.serial_port,
            baudrate=args.baudrate,
            serial_timeout=args.serial_timeout,
            arm_pump=args.arm_pump,
            confirm_pump=args.confirm_pump,
            controller_kind=args.controller,
            pump_duration_ms=args.pump_duration_ms,
            algorithm=args.algorithm,
        )
    except Exception as exc:
        from microcleaning.data_learning.usb_camera import USBCameraError

        if isinstance(exc, USBCameraError):
            print(f"相机采集失败：{exc}", file=sys.stderr)
            return 2
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
