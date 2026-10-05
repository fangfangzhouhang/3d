"""单进程入口：抓图、选目标、授权、移动短喷、回原观察位、复检。"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from demo import VISION_ALGORITHMS
from demo.camera_preview import CameraPreview
from demo.closed_loop_fixture import SCENARIOS, MockF103Serial, MockFrames, mock_offset
from demo.image_ops import FrozenSegmenter
from microcleaning.control_system.orchestration.cleaning_loop import CleaningLoop, LoopConfig, write_json
from microcleaning.control_system.orchestration.hardware_executor import HardwareExecutor
from microcleaning.control_system.planning.path_preview import load_path_placeholders
from microcleaning.control_system.planning.sequence_planner import STRATEGIES
from microcleaning.control_system.planning.stage2_geometry import load_nozzle_offset
from microcleaning.control_system.planning.stage2_position import DEFAULT_POSITION_PATH, load_position, set_zero
from microcleaning.control_system.planning.work_frame import load_motor_calibration
from microcleaning.control_system.serial.f103_session import F103SerialSession
from microcleaning.control_system.serial.stage2_link import Stage2SerialLink
from microcleaning.control_system.serial.stm32_serial import STM32SerialController


def _yes(prompt: str) -> bool:
    try:
        return input(prompt).strip() == "YES"
    except EOFError:
        return False


def confirm_cycle(preview: dict) -> bool:
    geometry = preview["geometry"]
    print(f"目标 {preview['target_id']}（本帧 {preview['frame_target_id']}）；面积 {preview['area_px']} px；质心 {preview['centroid_px']}")
    print(f"目标位移 {geometry['target_delta_steps']}；喷头有符号偏移 {geometry['offset_delta_steps']}（只加一次）")
    print(f"标定：{geometry['outbound_request']['calibration_ref']}；装配 {preview['offset']['setup_id']}；偏移版本 {preview['offset']['version']}，不确定性 {preview['offset']['uncertainty_steps']} 步")
    print(f"去程：{geometry['outbound']['lines']}；定点短喷 {preview['pump_request']['duration_ms']} ms")
    print(f"回原观察位：{geometry['returning']['lines']}；{geometry['execution_position']} → {geometry['observation_position']}")
    print(f"本轮含回程 |步| {geometry['cycle_abs_steps']}；此前累计 {preview['used_abs_steps_before']}；整任务每轴最多 1600")
    print(f"预览图：{preview['path_overlay']}；人应在设备旁，可立即停止。")
    return _yes("只批准本轮上述去程、短喷及回程，输入大写 YES：")


def confirm_pair(pair: dict) -> bool:
    print(f"前图：{pair['pre']['observation']['raw_image_ref']}")
    print(f"后图：{pair['post']['observation']['raw_image_ref']}")
    print("请核对同一视野、倍率、夹具与光照；当前没有自动配准验收。")
    return _yes("确认前后图可比较，输入 YES：")


def _initialize_position(path: Path, *, reset: bool) -> None:
    position = load_position(path)
    if reset or position.xy() is None:
        if not _yes("载物台已由人对到参考点，确认将当前位置记为人工零点（不是自动回零），输入 YES："):
            raise PermissionError("POSITION_REFERENCE_NOT_CONFIRMED")
        set_zero(path)
    elif not _yes(f"账本为 {position.xy()}；确认当前上电及实物位置仍对应该账本，输入 YES："):
        raise PermissionError("CURRENT_POSITION_NOT_CONFIRMED")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mock", action="store_true", help="软件闭环（默认），自动确认仅限 Mock")
    mode.add_argument("--real", action="store_true", help="显式选择实物；还需逐轮终端 YES")
    parser.add_argument("--mock-scenario", choices=SCENARIOS, default="success")
    parser.add_argument("--output-root", type=Path, default=Path("output/closed_loop"))
    parser.add_argument("--algorithm", choices=VISION_ALGORITHMS, default="local")
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--no-tuned-policy", action="store_true")
    parser.add_argument("--sequence-strategy", choices=STRATEGIES, default="nearest_neighbor")
    parser.add_argument("--max-cycles", type=int, default=3)
    parser.add_argument("--max-retries-per-target", type=int, default=0)
    parser.add_argument("--stage2-max-steps", type=int, default=1600)
    parser.add_argument("--pump-duration-ms", type=int, default=200)
    parser.add_argument("--path-placeholders", type=Path)
    parser.add_argument("--stage2-calibration", type=Path)
    parser.add_argument("--nozzle-offset", type=Path, help="版本化 scope_to_nozzle_delta_steps 配置")
    parser.add_argument("--position-path", type=Path, default=DEFAULT_POSITION_PATH)
    parser.add_argument("--stage2-set-zero", action="store_true", help="本进程内人工对位确认后归零")
    parser.add_argument("--serial-port")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--serial-timeout", type=float, default=2.0)
    parser.add_argument("--arm-stage2-xy", action="store_true")
    parser.add_argument("--arm-pump", action="store_true")
    parser.add_argument("--confirm-pump", action="store_true", help="启用逐轮 YES 流程；参数本身不批准动作")
    parser.add_argument("--camera-index", type=int)
    parser.add_argument("--camera-width", type=int)
    parser.add_argument("--camera-height", type=int)
    parser.add_argument("--camera-backend", type=int)
    parser.add_argument("--warmup-frames", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        config = LoopConfig(args.max_cycles, args.max_retries_per_target, args.sequence_strategy, args.stage2_max_steps, args.real)
        config.validate()
        if not 100 <= args.pump_duration_ms <= 300:
            raise ValueError("pump-duration-ms 必须在 100～300")
        if args.real and any(value is None for value in (args.serial_port, args.camera_index, args.stage2_calibration, args.nozzle_offset, args.path_placeholders)):
            raise ValueError("实物必须显式指定 COM、camera-index、motor calibration、nozzle-offset、path-placeholders，不扫口")
        if not args.real and (args.serial_port or args.stage2_set_zero or args.position_path != DEFAULT_POSITION_PATH):
            raise ValueError("Mock 禁止指定实物端口或位置账本；仅使用本运行目录的独立账本")
        segmenter = FrozenSegmenter(args.algorithm, policy_path=args.policy, use_tuned_policy=False if args.no_tuned_policy else None)
        placeholders = load_path_placeholders(args.path_placeholders)
        calibration = load_motor_calibration(args.stage2_calibration) if args.stage2_calibration else None
        offset = load_nozzle_offset(args.nozzle_offset) if args.nozzle_offset else mock_offset()
        offset.validate(calibration, real=args.real)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    folder = args.output_root / f"station_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    folder.mkdir(parents=True)
    source, executor = None, None
    try:
        if args.real:
            _initialize_position(args.position_path, reset=args.stage2_set_zero)
            source = CameraPreview(camera_index=args.camera_index, segmenter=segmenter, width=args.camera_width,
                height=args.camera_height, backend=args.camera_backend, warmup_frames=args.warmup_frames)
            position_path, factory, confirm, compare = args.position_path, None, confirm_cycle, confirm_pair
        else:
            source = MockFrames(args.mock_scenario)
            serial = MockF103Serial(source, args.mock_scenario)
            position_path, factory = folder / "mock_position.json", serial.factory
            set_zero(position_path)
            confirm, compare = lambda _: args.mock_scenario != "decline", lambda _: True
        session = F103SerialSession(port=args.serial_port if args.real else None, baudrate=args.baudrate,
            timeout=args.serial_timeout, serial_factory=factory)
        link = Stage2SerialLink(session=session, armed=args.arm_stage2_xy if args.real else True)
        controller = STM32SerialController(session=session, arm_pump=(args.arm_pump and args.confirm_pump) if args.real else True)
        executor = HardwareExecutor(session=session, link=link, controller=controller, position_path=position_path,
            confirm=confirm, pump_duration_ms=args.pump_duration_ms)
        result = CleaningLoop(output_dir=folder, source=source, segmenter=segmenter, executor=executor,
            placeholders=placeholders, calibration=calibration, offset=offset, config=config, compare=compare).run()
    except (Exception, KeyboardInterrupt) as exc:
        result = {"status": "ERROR", "reasons": ["INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else str(exc)], "mode": "real" if args.real else "mock"}
        write_json(folder / "summary.json", result)
    finally:
        cleanup_errors = []
        for resource, code in ((executor, "EXECUTOR_FINAL_CLOSE_FAILED"), (source, "FRAME_SOURCE_FINAL_CLOSE_FAILED")):
            if resource is not None:
                try:
                    resource.close()
                except (Exception, KeyboardInterrupt) as exc:
                    cleanup_errors.append({"reason": code, "type": type(exc).__name__, "message": str(exc)})
        if cleanup_errors:
            result["reasons"] = ([] if result["status"] == "SUCCESS" else list(result["reasons"])) + [error["reason"] for error in cleanup_errors]
            result["status"] = "ERROR"
            result.setdefault("cleanup_errors", []).extend(cleanup_errors)
            write_json(folder / "summary.json", result)
    print(f"闭环结果：{result['status']}；原因：{result['reasons']}；记录：{folder.resolve()}")
    return {"SUCCESS": 0, "HUMAN": 2, "ERROR": 3}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
