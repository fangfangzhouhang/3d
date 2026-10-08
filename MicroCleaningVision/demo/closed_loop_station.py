"""单进程入口：同一个窗口里看显微镜、看第一次标注，并在这个窗口确认。

没有同时武装喷水时只走去程和回程。旧的 demo.demo_pipeline --live 不发送步进。
短喷默认 500 ms。第一次画面标注了几块污渍，就处理几块。
"""

from __future__ import annotations

import argparse
import queue
import threading
import time
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
from microcleaning.control_system.planning.stage2_axes import parse_movexy_line
from microcleaning.control_system.safety.fixed_rule import DEFAULT_IN_PLACE_DURATION_MS, MAX_IN_PLACE_DURATION_MS
from microcleaning.control_system.safety.motion_gate import STAGE2_RUN_STEP_CAP
from microcleaning.control_system.serial.f103_session import F103SerialSession
from microcleaning.control_system.serial.stage2_link import Stage2SerialLink
from microcleaning.control_system.serial.stm32_serial import STM32SerialController


_LIVE_PREVIEW = None
_PANEL = None
_LAST_MOTION_PRINT = 0.0


def bind_panel(panel) -> None:
    global _PANEL
    _PANEL = panel


def _tell(message: str, flush: bool = True) -> None:
    print(message, flush=True)
    panel = _PANEL
    if panel is not None:
        panel.write(message, echo=False)


def _refresh_during_wait() -> None:
    preview = _LIVE_PREVIEW
    if preview is None:
        return
    status = "AT NOZZLE  confirm in this window" if _OVERLAY.at_nozzle else "confirm in this window"
    preview.refresh(status)


class StageOverlay:
    """用台面计数把显微镜中心、针头目标和当前点交给实时窗口。"""

    def __init__(self) -> None:
        self.scope_xy = (0, 0)
        self.nozzle_xy = (0, 0)
        self.current_xy = (0, 0)
        self.stain_px = None
        self.line_origin = (0, 0)
        self.line_delta = (0, 0)

    def plan(self, scope_xy, nozzle_xy, stain_px) -> None:
        self.scope_xy = (int(scope_xy[0]), int(scope_xy[1]))
        self.nozzle_xy = (int(nozzle_xy[0]), int(nozzle_xy[1]))
        self.current_xy = self.scope_xy
        self.line_origin = self.scope_xy
        self.line_delta = (0, 0)
        self.stain_px = None if stain_px is None else (float(stain_px[0]), float(stain_px[1]))

    def feed(self, message: str) -> None:
        text = message.strip()
        if text.startswith("MOVEXY "):
            self.line_delta = parse_movexy_line(text)
            self.line_origin = self.current_xy
            return
        if not text.startswith("STEP2 X="):
            return
        fields = {}
        for part in text.split()[1:]:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            try:
                fields[key] = int(value)
            except ValueError:
                return
        dx, dy = self.line_delta
        ox, oy = self.line_origin
        sx, sy = fields.get("X", 0), fields.get("Y", 0)
        cx = ox if dx == 0 else ox + (1 if dx > 0 else -1) * min(abs(dx), max(0, sx))
        cy = oy if dy == 0 else oy + (1 if dy > 0 else -1) * min(abs(dy), max(0, sy))
        idle = fields.get("BX", 1) == 0 and fields.get("BY", 1) == 0
        if idle and (dx == 0 or sx >= abs(dx)) and (dy == 0 or sy >= abs(dy)):
            cx, cy = ox + dx, oy + dy
        self.current_xy = (cx, cy)

    @property
    def at_nozzle(self) -> bool:
        return self.current_xy == self.nozzle_xy

    def as_marks(self) -> dict:
        return {"scope_xy": self.scope_xy, "nozzle_xy": self.nozzle_xy, "current_xy": self.current_xy, "stain_px": self.stain_px}


_OVERLAY = StageOverlay()


def bind_live_preview(preview) -> None:
    global _LIVE_PREVIEW
    _LIVE_PREVIEW = preview


def _push_marks() -> None:
    preview = _LIVE_PREVIEW
    if preview is not None and hasattr(preview, "set_marks"):
        preview.set_marks(_OVERLAY.as_marks())


def _yes(prompt: str) -> bool:
    if _PANEL is not None:
        return _PANEL.ask(prompt, refresh=_refresh_during_wait)
    _tell(prompt)
    _tell("在这个终端输入 yes 后回车，大小写都可以。只有 yes 才批准这一步，其它内容都表示不批准。")
    if _LIVE_PREVIEW is None:
        try:
            return input().strip().upper() == "YES"
        except EOFError:
            return False
    answers: queue.Queue[str] = queue.Queue()

    def read() -> None:
        try:
            answers.put(input())
        except EOFError:
            answers.put("")

    threading.Thread(target=read, daemon=True).start()
    while True:
        try:
            text = answers.get(timeout=0.03)
        except queue.Empty:
            waiting = "AT NOZZLE  type yes in terminal" if _OVERLAY.at_nozzle else "type yes in terminal"
            _LIVE_PREVIEW.refresh(waiting)
            continue
        return text.strip().upper() == "YES"


def report_motion(event: dict) -> None:
    """步进发送过程中刷新显微镜中心和针头目标，并在终端打印板子回复。READXY 轮询做节流。"""

    global _LAST_MOTION_PRINT
    message = str(event.get("message", ""))
    _OVERLAY.feed(message)
    _push_marks()
    noisy = message == "READXY" or message.startswith("STEP2 X=")
    now = time.monotonic()
    if not (noisy and now - _LAST_MOTION_PRINT < 0.45):
        _LAST_MOTION_PRINT = now
        label = "发送" if event.get("phase") == "tx" else "收到"
        _tell(f"{label}：{message}", flush=True)
    if _LIVE_PREVIEW is not None:
        _LIVE_PREVIEW.refresh(message)


def _print_path(preview: dict) -> None:
    geometry = preview["geometry"]
    index = preview.get("roster_index")
    total = preview.get("roster_total")
    place = f"第 {index}/{total} 块 " if index else ""
    _tell(
        f"{place}{preview['target_id']}，面积 {float(preview['area_px']):.0f} px。"
        f"去程 {geometry['outbound']['lines']}。回程 {geometry['returning']['lines']}。"
    )


def _arm_overlay(preview: dict, current=None) -> None:
    geometry = preview.get("geometry") or {}
    if "observation_position" not in geometry or "execution_position" not in geometry:
        return
    _OVERLAY.plan(geometry["observation_position"], geometry["execution_position"], preview.get("centroid_px"))
    if current is not None:
        _OVERLAY.current_xy = (int(current[0]), int(current[1]))
    _push_marks()


def confirm_cycle(preview: dict) -> bool:
    phase = preview.get("phase", "move")
    geometry = preview.get("geometry") or {}
    if phase == "move":
        _arm_overlay(preview, geometry.get("observation_position"))
        _print_path(preview)
        if preview.get("include_pump", True):
            _tell(f"预定短喷 {preview['pump_request']['duration_ms']} ms。这一次 yes 不喷水。")
            return _yes("输入 yes 只开始把这一块送向针头。输入 no 取消，电机不动，也不喷水。")
        _tell("这一步不喷水。")
        return _yes("输入 yes 批准去程和回程。输入 no 取消，电机不动。")
    if phase == "align":
        _arm_overlay(preview, geometry.get("execution_position"))
        _tell(f"{preview.get('target_id')} 的计数已到针头目标。小图圆点落到 N 上只说明计数到了。")
        return _yes("输入 yes 才喷水。输入 no 不喷水。")
    if phase == "return":
        _arm_overlay(preview, geometry.get("execution_position"))
        _tell("喷水输出已结束。还没有复检。")
        return _yes("输入 yes 才回到显微镜。输入 no 停在原地，不复检。")
    if phase == "return_without_spray":
        _arm_overlay(preview, geometry.get("execution_position"))
        _tell("没有喷水。")
        return _yes(f"输入 yes 才回到原观察位 {geometry.get('observation_position')}。输入 no 停在原地。")
    if phase == "recheck":
        _arm_overlay(preview, geometry.get("observation_position"))
        _tell("回程计数已结束。请看画面是否回到原位置。")
        return _yes("输入 yes 才抓后图并复检。输入 no 不复检，也不开始下一块。")
    if phase == "next":
        nxt = preview.get("next_target_id")
        if preview.get("cycles_left") == 0 or not nxt:
            return _yes("输入 yes 结束这一块。没有下一块。输入 no 也停止。")
        return _yes(f"输入 yes 结束这一块，并按编号继续 {nxt}。输入 no 停止。")
    if phase == "next_incomplete":
        nxt = preview.get("next_target_id")
        _tell(f"{preview.get('target_id')} 没有记成洗净，原因 {preview.get('stop_reasons', ())}。")
        if preview.get("cycles_left") == 0 or not nxt:
            return _yes("输入 yes 结束这一块。没有下一块。输入 no 也停止。")
        return _yes(f"输入 yes 结束这一块，并按编号继续 {nxt}。输入 no 停止。")
    if phase == "retry":
        _tell(f"{preview.get('target_id')} 还有残留。这是再试同一块，不是下一块。")
        return _yes("输入 yes 再试这一块。输入 no 停止。")
    _tell(f"{preview.get('target_id')} 不能再自动重试，原因 {preview.get('stop_reasons', ())}。")
    return _yes("输入 yes 结束。输入 no 也结束。")


def confirm_pair(pair: dict) -> bool:
    _tell(f"前图：{pair['pre']['observation']['raw_image_ref']}")
    _tell(f"后图：{pair['post']['observation']['raw_image_ref']}")
    _tell("核对是否同一视野。这一次 yes 只表示可以比较，不表示已经洗净。")
    return _yes("输入 yes 继续算出复检数字。输入 no 不比较。")


def _initialize_position(path: Path, *, reset: bool) -> None:
    position = load_position(path)
    if reset or position.xy() is None:
        if not _yes("载物台已由人对到参考点，确认将当前位置记为人工零点（不是自动回零），输入 yes："):
            raise PermissionError("POSITION_REFERENCE_NOT_CONFIRMED")
        set_zero(path)
    elif not _yes(f"账本为 {position.xy()}；确认当前上电及实物位置仍对应该账本，输入 yes："):
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
    parser.add_argument("--stage2-max-steps", type=int, default=STAGE2_RUN_STEP_CAP)
    parser.add_argument("--pump-duration-ms", type=int, default=DEFAULT_IN_PLACE_DURATION_MS)
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
        if not 100 <= args.pump_duration_ms <= MAX_IN_PLACE_DURATION_MS:
            raise ValueError(f"pump-duration-ms 必须在 100～{MAX_IN_PLACE_DURATION_MS}")
        if args.real and any(value is None for value in (args.serial_port, args.camera_index, args.stage2_calibration, args.nozzle_offset, args.path_placeholders)):
            raise ValueError("实物必须显式指定 COM、camera-index、motor calibration、nozzle-offset、path-placeholders，不扫口")
        if args.real and not args.arm_stage2_xy:
            raise ValueError("实物移动必须加 --arm-stage2-xy。旧入口 --live 不发送步进。")
        if args.real and args.arm_pump != args.confirm_pump:
            raise ValueError("要短喷必须同时加 --arm-pump 和 --confirm-pump。只移动步进时两个都不要加。")
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
    source, executor, panel = None, None, None
    try:
        if args.real:
            from demo.station_window import StationPanel
            panel = StationPanel()
            bind_panel(panel)
            _tell("同一个窗口：左边实时显微镜，右边第一次画面的编号。空格抓图。确认和不确认都在这个窗口，不用回到 Cursor 终端。")
            _tell(f"短喷 {args.pump_duration_ms} ms。按第一次编号从 S0001 开始逐块清洗，不漏也不多洗。轮数 {args.max_cycles} 只是上限。")
            if args.arm_pump:
                _tell("喷水已武装。去程、喷水、回位、复检、下一块，各自再确认。")
            else:
                _tell("没有武装喷水。确认只批准去程和回程。")
            source = CameraPreview(camera_index=args.camera_index, segmenter=segmenter, width=args.camera_width,
                height=args.camera_height, backend=args.camera_backend, warmup_frames=args.warmup_frames, panel=panel)
            source.open()
            bind_live_preview(source)
            _initialize_position(args.position_path, reset=args.stage2_set_zero)
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
        if args.real:
            link.on_progress = report_motion
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
    _tell(f"闭环结果：{result['status']}；原因：{result['reasons']}；记录：{folder.resolve()}")
    if panel is not None:
        panel.wait_dismiss(8)
    return {"SUCCESS": 0, "HUMAN": 2, "ERROR": 3}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
