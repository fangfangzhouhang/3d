"""操作员实时窗口：B 分割辅助对焦，空格冻结后走 Demo。

实时叠加不是正式证据；空格抓到的那一帧才会写入 ``output/demo/<run_id>/``。
默认只分析、不发泵。显式 ``arm-pump`` 时，空格在识别到目标后发送限时 PUMP。
看见污渍不会在预览循环里自动喷水，必须按空格。

Q 只关闭预览并释放相机；暂停画面上按其他键重新打开，再按 Q 才结束整个程序。
``--wait-usb`` 会等到指定编号的相机可读再进入预览（适合先启动命令再插上显微镜）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from microcleaning.data_learning.usb_camera import CAMERA_OPEN_FAILED, FRAME_READ_FAILED, USBCameraError

from demo.camera_preview import (
    _wait_until_camera_readable,
    _idle_pause,
    _compose_preview,
    _put_hud,
    _apply_resolution,
    LIVE_WINDOW,
    LIVE_WINDOW_PUMP,
    RESULT_WINDOW,
    IDLE_WINDOW,
    QUIT_KEYS,
    HSV_KEYS,
    OTSU_KEYS,
    EXG_KEYS,
    EXR_KEYS,
    LOCAL_KEYS,
    SPACE_KEY,
    VISION_ALGORITHMS,
)

CaptureFactory = Callable[..., Any]
SerialFactory = Callable[[], Any]
ShowFrame = Callable[[str, Any], None]
WaitKey = Callable[[int], int]
DestroyWindows = Callable[[], None]
AnalyzeFrame = Callable[..., Path]
SleepFn = Callable[[float], None]



def run_live_session(
    *,
    camera_index: int = 0,
    algorithm: str = "local",
    output_root: str | Path = Path("output") / "demo",
    warmup_frames: int = 5,
    camera_width: int | None = None,
    camera_height: int | None = None,
    camera_backend: int | None = None,
    capture_factory: CaptureFactory | None = None,
    imshow: ShowFrame | None = None,
    wait_key: WaitKey | None = None,
    destroy_windows: DestroyWindows | None = None,
    analyze_frame: AnalyzeFrame | None = None,
    max_frames: int | None = None,
    wait_usb: bool = False,
    allow_reopen: bool = True,
    sleep: SleepFn = time.sleep,
    max_wait_attempts: int | None = None,
    max_idle_frames: int | None = None,
    pump_on_analyze: bool = False,
    confirm_pump: bool = False,
    arm_pump: bool = False,
    controller_kind: str = "fake",
    serial_port: str | None = None,
    baudrate: int = 115200,
    serial_timeout: float = 2.0,
    pump_duration_ms: int = 200,
    serial_factory: SerialFactory | None = None,
) -> list[Path]:
    """实时预览会话：Q 暂停，其他键重开；默认不发泵。"""

    from demo.image_ops import _load_dependencies

    if algorithm not in VISION_ALGORITHMS:
        raise ValueError(f"algorithm必须是{'/'.join(VISION_ALGORITHMS)}")

    cv2, np = _load_dependencies()
    factory = capture_factory if capture_factory is not None else cv2.VideoCapture
    show = imshow if imshow is not None else cv2.imshow
    key_fn = wait_key if wait_key is not None else cv2.waitKey
    close = destroy_windows if destroy_windows is not None else cv2.destroyAllWindows
    algorithm_state = [algorithm]
    run_dirs: list[Path] = []

    if pump_on_analyze:
        print("空格会分析当前帧；识别到目标后发送限时 PUMP。预览画面本身不会自动喷。人必须在场。")
    else:
        print("看见污渍不会自动喷水。空格只分析；喷泵必须另外走人工确认的 arm-pump。")
    if wait_usb:
        print(f"等待 USB 相机 device_index={camera_index} … 插上显微镜后会自动打开预览。")

    while True:
        existing_capture = None
        if wait_usb:
            existing_capture = _wait_until_camera_readable(
                camera_index=camera_index,
                camera_backend=camera_backend,
                factory=factory,
                sleep=sleep,
                max_attempts=max_wait_attempts,
            )
        try:
            run_dirs.extend(
                run_live_station(
                    camera_index=camera_index,
                    algorithm=algorithm_state[0],
                    output_root=output_root,
                    warmup_frames=warmup_frames,
                    camera_width=camera_width,
                    camera_height=camera_height,
                    camera_backend=camera_backend,
                    capture_factory=factory,
                    imshow=show,
                    wait_key=key_fn,
                    destroy_windows=lambda: None,
                    analyze_frame=analyze_frame,
                    max_frames=max_frames,
                    algorithm_state=algorithm_state,
                    existing_capture=existing_capture,
                    pump_on_analyze=pump_on_analyze,
                    confirm_pump=confirm_pump,
                    arm_pump=arm_pump,
                    controller_kind=controller_kind,
                    serial_port=serial_port,
                    baudrate=baudrate,
                    serial_timeout=serial_timeout,
                    pump_duration_ms=pump_duration_ms,
                    serial_factory=serial_factory,
                )
            )
        except USBCameraError as exc:
            print(f"相机不可用：{exc}")
            if not allow_reopen:
                raise
        if not allow_reopen:
            close()
            return run_dirs
        print("预览已关闭。点暂停窗口：任意键重新打开，Q 结束程序。")
        action = _idle_pause(
            cv2=cv2,
            np=np,
            show=show,
            key_fn=key_fn,
            max_idle_frames=max_idle_frames,
        )
        if action == "exit":
            close()
            print("已退出实时会话。")
            return run_dirs
        print("重新打开实时预览。")


def run_live_station(
    *,
    camera_index: int = 0,
    algorithm: str = "local",
    output_root: str | Path = Path("output") / "demo",
    warmup_frames: int = 5,
    camera_width: int | None = None,
    camera_height: int | None = None,
    camera_backend: int | None = None,
    capture_factory: CaptureFactory | None = None,
    imshow: ShowFrame | None = None,
    wait_key: WaitKey | None = None,
    destroy_windows: DestroyWindows | None = None,
    analyze_frame: AnalyzeFrame | None = None,
    max_frames: int | None = None,
    algorithm_state: list[str] | None = None,
    existing_capture: Any | None = None,
    pump_on_analyze: bool = False,
    confirm_pump: bool = False,
    arm_pump: bool = False,
    controller_kind: str = "fake",
    serial_port: str | None = None,
    baudrate: int = 115200,
    serial_timeout: float = 2.0,
    pump_duration_ms: int = 200,
    serial_factory: SerialFactory | None = None,
) -> list[Path]:
    """打开相机循环显示 B 叠加；空格把当前帧交给 ``run_demo``。"""

    from demo.image_ops import _draw_contamination, _load_dependencies, segment_demo_image
    from demo.single_frame import run_demo

    if algorithm not in VISION_ALGORITHMS:
        raise ValueError(f"algorithm必须是{'/'.join(VISION_ALGORITHMS)}")

    cv2, _np = _load_dependencies()
    factory = capture_factory if capture_factory is not None else cv2.VideoCapture
    show = imshow if imshow is not None else cv2.imshow
    key_fn = wait_key if wait_key is not None else cv2.waitKey
    close = destroy_windows if destroy_windows is not None else cv2.destroyAllWindows
    preview_window = LIVE_WINDOW_PUMP if pump_on_analyze else LIVE_WINDOW
    analyze = analyze_frame if analyze_frame is not None else (
        lambda image, current_algorithm: _analyze_frozen_frame(
            image,
            algorithm=current_algorithm,
            output_root=output_root,
            camera_index=camera_index,
            cv2=cv2,
            run_demo=run_demo,
            pump_on_analyze=pump_on_analyze,
            confirm_pump=confirm_pump,
            arm_pump=arm_pump,
            controller_kind=controller_kind,
            serial_port=serial_port,
            baudrate=baudrate,
            serial_timeout=serial_timeout,
            pump_duration_ms=pump_duration_ms,
            serial_factory=serial_factory,
        )
    )

    capture: Any = None
    run_dirs: list[Path] = []
    last_status = (
        "SPACE=analyze+pump if target  Q=pause"
        if pump_on_analyze
        else "SPACE=analyze  Q=pause  no auto pump"
    )
    current_algorithm = algorithm_state[0] if algorithm_state else algorithm
    frames_shown = 0
    try:
        if existing_capture is not None:
            capture = existing_capture
        else:
            capture = factory(camera_index) if camera_backend is None else factory(camera_index, camera_backend)
        if capture is None or not bool(capture.isOpened()):
            raise USBCameraError(CAMERA_OPEN_FAILED, f"无法打开 USB 相机 device_index={camera_index}")
        _apply_resolution(capture, cv2, camera_width, camera_height)
        for warmup_index in range(max(0, warmup_frames)):
            ok, _warmup = capture.read()
            if not ok:
                raise USBCameraError(FRAME_READ_FAILED, f"warm-up 第 {warmup_index + 1} 帧失败")

        print(f"实时窗口已打开：device_index={camera_index}。空格抓帧分析，H=HSV，O=Otsu，G=ExG，E=ExR，L=邻域差异，Q暂停预览。")
        if pump_on_analyze:
            print("已武装实喷：空格在识别到目标后发限时 PUMP；无目标不发。预览循环不会自动喷。")
        else:
            print("预览叠加只辅助对焦；正式结果在空格之后的 output/demo/<run_id>/。看见污渍不会发泵。")

        while max_frames is None or frames_shown < max_frames:
            ok, frame = capture.read()
            if not ok or frame is None or not hasattr(frame, "size") or int(frame.size) <= 0:
                raise USBCameraError(FRAME_READ_FAILED, f"无法读取画面 device_index={camera_index}")

            view, last_status = _compose_preview(
                frame,
                algorithm=current_algorithm,
                camera_index=camera_index,
                status=last_status,
                cv2=cv2,
                segment_demo_image=segment_demo_image,
                draw_contamination=_draw_contamination,
                pump_on_analyze=pump_on_analyze,
            )
            show(preview_window, view)
            frames_shown += 1
            key = int(key_fn(1))
            if key < 0:
                continue
            key = key & 0xFF
            if key in QUIT_KEYS:
                break
            if key in HSV_KEYS:
                current_algorithm = "hsv"
                last_status = "algorithm=hsv"
                continue
            if key in OTSU_KEYS:
                current_algorithm = "otsu"
                last_status = "algorithm=otsu"
                continue
            if key in EXG_KEYS:
                current_algorithm = "exg"
                last_status = "algorithm=exg"
                continue
            if key in EXR_KEYS:
                current_algorithm = "exr"
                last_status = "algorithm=exr"
                continue
            if key in LOCAL_KEYS:
                current_algorithm = "local"
                last_status = "algorithm=local"
                continue
            if key == SPACE_KEY:
                try:
                    run_dir = analyze(frame, current_algorithm)
                    run_dirs.append(run_dir)
                    last_status = _status_after_analyze(run_dir, pump_on_analyze=pump_on_analyze)
                    result_path = run_dir / "path_overlay.png"
                    if result_path.is_file():
                        result = cv2.imread(str(result_path), cv2.IMREAD_COLOR)
                        if result is not None:
                            show(RESULT_WINDOW, result)
                except Exception as exc:
                    print(f"分析出错但继续预览：{exc}")
                    last_status = f"analyze failed: {type(exc).__name__}"
                continue
    finally:
        if algorithm_state is not None:
            algorithm_state[0] = current_algorithm
        if capture is not None:
            try:
                capture.release()
            except Exception:
                pass
        try:
            close()
        except Exception:
            pass
    return run_dirs


def _analyze_frozen_frame(
    image,
    *,
    algorithm: str,
    output_root: str | Path,
    camera_index: int,
    cv2,
    run_demo,
    pump_on_analyze: bool = False,
    confirm_pump: bool = False,
    arm_pump: bool = False,
    controller_kind: str = "fake",
    serial_port: str | None = None,
    baudrate: int = 115200,
    serial_timeout: float = 2.0,
    pump_duration_ms: int = 200,
    serial_factory: SerialFactory | None = None,
) -> Path:
    staging_dir = Path(output_root)
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging = staging_dir / "_live_last_frame.png"
    if not cv2.imwrite(str(staging), image):
        raise OSError(f"无法写入冻结帧：{staging}")
    return run_demo(
        input_path=staging,
        mode="arm-pump" if pump_on_analyze else "analyze",
        output_root=output_root,
        algorithm=algorithm,
        camera_index=camera_index,
        source_kind_override="camera",
        input_source_override=f"usb-live:index={camera_index}",
        confirm_pump=confirm_pump if pump_on_analyze else False,
        arm_pump=arm_pump if pump_on_analyze else False,
        controller_kind=controller_kind if pump_on_analyze else "fake",
        serial_port=serial_port if pump_on_analyze else None,
        baudrate=baudrate,
        serial_timeout=serial_timeout,
        pump_duration_ms=pump_duration_ms,
        serial_factory=serial_factory if pump_on_analyze else None,
    )


def _status_after_analyze(run_dir: Path, *, pump_on_analyze: bool) -> str:
    summary_path = run_dir / "summary.json"
    if not summary_path.is_file():
        return f"saved {run_dir.name}"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return f"saved {run_dir.name}"
    receipt = summary.get("execution_receipt") or {}
    reasons = tuple((summary.get("verification") or {}).get("reason_codes") or ())
    if not pump_on_analyze:
        return f"saved {run_dir.name}  no pump"
    if receipt.get("success"):
        return f"PUMP sent {run_dir.name}"
    if "NO_TARGET" in reasons:
        return f"no target, no pump  {run_dir.name}"
    if "ESTOP_ACTIVE" in reasons or receipt.get("error_code") == "ESTOP":
        return f"ESTOP blocked pump  {run_dir.name}"
    outcome = ((summary.get("safety_decision") or {}).get("outcome")) or "none"
    return f"pump blocked ({outcome})  {run_dir.name}"
