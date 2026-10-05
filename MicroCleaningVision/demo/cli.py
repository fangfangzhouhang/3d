"""从原 Demo 抽取的 demo.cli；保持既有单帧行为。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from microcleaning.control_system.planning.stage2_position import DEFAULT_POSITION_PATH
from microcleaning.control_system.planning.stage2_position import set_zero
from microcleaning.control_system.safety.fixed_rule import DEFAULT_IN_PLACE_DURATION_MS
from microcleaning.control_system.safety.fixed_rule import MAX_IN_PLACE_DURATION_MS
from microcleaning.control_system.safety.motion_gate import STAGE2_RUN_STEP_CAP
from demo import DEMO_MODES, VISION_ALGORITHMS
from demo.motion_mode import Stage2MotionFailed, _cli_motion_confirm
from demo.single_frame import run_demo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MicroCleaningVision Demo v0.2")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", help="jpg/png真实图片路径")
    source.add_argument("--generate-sample", action="store_true", help="生成可复现的红色模拟污染图")
    source.add_argument("--from-camera", action="store_true", help="用 USBCamera 抓一帧；默认不发泵")
    parser.add_argument(
        "--mode",
        choices=DEMO_MODES,
        default="analyze",
        help=(
            "analyze/camera-analyze 只分析；ping-only 只探测通信；arm-pump 需人工确认才可能发泵；"
            "stage2-move 是唯一可能发步进的模式（关卡+YES+武装）；前三种可与 --live 组合"
        ),
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
        "--policy",
        type=Path,
        help="local 策略 JSON；省略时若存在 data/models/local_contrast_policy.json 则自动使用",
    )
    parser.add_argument(
        "--no-tuned-policy",
        action="store_true",
        help="忽略已调参 JSON，使用代码内 local-contrast-v0.1",
    )
    parser.add_argument(
        "--path-placeholders",
        type=Path,
        help="C 路径占位 JSON：mm/px、喷头偏移、步进参数；禁止 feeds_action_request 或发 MOVE",
    )
    parser.add_argument(
        "--stage2-xy",
        action="store_true",
        help="用 1600 步/转、5 mm/转规划 X/Y，只写 stage2_xy_pulses.txt，不打开串口",
    )
    parser.add_argument(
        "--arm-stage2-xy",
        action="store_true",
        help="仅限 --mode stage2-move：运动关卡通过且人输入 YES 后，打开指定 COM 发送 MOVEXY",
    )
    parser.add_argument(
        "--stage2-calibration",
        type=Path,
        help="电机位移标定 JSON（scripts/calibrate_motor_mm_per_px.py 输出）；stage2-move 武装时必填",
    )
    parser.add_argument(
        "--stage2-set-zero",
        action="store_true",
        help="人已把台面对到参考位置：把 output/stage2/position.json 记为 (0,0)。单独运行，不发脉冲",
    )
    parser.add_argument(
        "--stage2-x",
        action="store_true",
        help="兼容旧参数，等价 --stage2-xy（当前固件双轴都可发送）",
    )
    parser.add_argument(
        "--arm-stage2-x",
        action="store_true",
        help="兼容旧参数，等价 --arm-stage2-xy",
    )
    parser.add_argument(
        "--stage2-max-steps",
        type=int,
        default=STAGE2_RUN_STEP_CAP,
        help=f"一次运行每轴累计脉冲上限，只能调小；最大 {STAGE2_RUN_STEP_CAP}（1 圈，约 5 mm）",
    )
    parser.add_argument(
        "--wait-usb",
        action="store_true",
        help="实时会话：先等待指定 camera-index 可读取再打开预览；Q暂停后按其他键重开",
    )
    args = parser.parse_args(argv)
    has_source = bool(args.input or args.generate_sample or args.from_camera)
    if args.stage2_set_zero:
        if has_source or args.live:
            parser.error("--stage2-set-zero 单独运行，不和抓图或分析放在一起")
        position = set_zero(DEFAULT_POSITION_PATH)
        print(f"位置账本已归零：{DEFAULT_POSITION_PATH}")
        print(f"{position.to_dict()}")
        print("这只是计数起点，不是回零；每次上电或发送失败后都要人重新对位再归零。")
        return 0
    if not has_source:
        parser.error("必须选择 --input、--generate-sample 或 --from-camera 之一")
    stage2_armed = args.arm_stage2_x or args.arm_stage2_xy
    stage2_any = stage2_armed or args.stage2_x or args.stage2_xy or args.mode == "stage2-move"
    if stage2_armed and args.mode != "stage2-move":
        parser.error(
            "analyze 不再发步进。发送请用 --mode stage2-move --stage2-calibration <json> "
            "--arm-stage2-xy --serial-port COMx"
        )
    if stage2_armed and not args.serial_port:
        parser.error("发送步进必须指定 --serial-port，不扫描 COM")
    if stage2_armed and args.stage2_calibration is None:
        parser.error("发送步进必须指定 --stage2-calibration（电机位移标定 JSON）")
    if stage2_armed and (args.arm_pump or args.mode == "arm-pump"):
        parser.error("步进发送不能和喷水武装放在同一次运行")
    if not 0 <= args.stage2_max_steps <= STAGE2_RUN_STEP_CAP:
        parser.error(f"--stage2-max-steps 必须在 0 到 {STAGE2_RUN_STEP_CAP} 之间（只能调小）")
    if args.live and stage2_any:
        parser.error("实时窗口不发送步进；请去掉 --live，用 --from-camera --stage2-xy")
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
            policy_path=args.policy,
            use_tuned_policy=False if args.no_tuned_policy else None,
            path_placeholders=args.path_placeholders,
            stage2_xy=stage2_any,
            arm_stage2_xy=stage2_armed,
            stage2_max_steps=args.stage2_max_steps,
            stage2_calibration=args.stage2_calibration,
            motion_confirm=_cli_motion_confirm if stage2_armed else None,
        )
    except Stage2MotionFailed as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except Exception as exc:
        from microcleaning.data_learning.usb_camera import USBCameraError

        if isinstance(exc, USBCameraError):
            print(f"相机采集失败：{exc}", file=sys.stderr)
            return 2
        raise
    return 0
