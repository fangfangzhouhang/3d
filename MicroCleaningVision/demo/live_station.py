"""操作员实时窗口：B 分割辅助对焦，空格冻结后走 Demo 分析。

实时叠加不是正式证据；空格抓到的那一帧才会写入 ``output/demo/<run_id>/``。
本窗口不发送 PUMP。看见污渍不会自动喷水。

Q 只关闭预览并释放相机；暂停画面上按其他键重新打开，再按 Q 才结束整个程序。
``--wait-usb`` 会等到指定编号的相机可读再进入预览（适合先启动命令再插上显微镜）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from microcleaning.data_learning.usb_camera import CAMERA_OPEN_FAILED, FRAME_READ_FAILED, USBCameraError

CaptureFactory = Callable[..., Any]
ShowFrame = Callable[[str, Any], None]
WaitKey = Callable[[int], int]
DestroyWindows = Callable[[], None]
AnalyzeFrame = Callable[..., Path]
SleepFn = Callable[[float], None]

LIVE_WINDOW = "MicroCleaningVision live  SPACE=analyze  H/O/G/E/L=algo  Q=pause"
RESULT_WINDOW = "Last Demo analysis (path overlay)"
IDLE_WINDOW = "Paused  any key=reopen  Q=exit program"
QUIT_KEYS = {ord("q"), ord("Q"), 27}
HSV_KEYS = {ord("h"), ord("H")}
OTSU_KEYS = {ord("o"), ord("O")}
EXG_KEYS = {ord("g"), ord("G")}
EXR_KEYS = {ord("e"), ord("E")}
LOCAL_KEYS = {ord("l"), ord("L")}
SPACE_KEY = 32
VISION_ALGORITHMS = ("hsv", "otsu", "exg", "exr", "local")


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
) -> list[Path]:
    """实时预览会话：Q 暂停，其他键重开；默认不发泵。"""

    from demo.demo_pipeline import _load_dependencies

    if algorithm not in VISION_ALGORITHMS:
        raise ValueError(f"algorithm必须是{'/'.join(VISION_ALGORITHMS)}")

    cv2, np = _load_dependencies()
    factory = capture_factory if capture_factory is not None else cv2.VideoCapture
    show = imshow if imshow is not None else cv2.imshow
    key_fn = wait_key if wait_key is not None else cv2.waitKey
    close = destroy_windows if destroy_windows is not None else cv2.destroyAllWindows
    algorithm_state = [algorithm]
    run_dirs: list[Path] = []

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
) -> list[Path]:
    """打开相机循环显示 B 叠加；空格把当前帧交给 ``run_demo(mode=analyze)``。"""

    from demo.demo_pipeline import _draw_contamination, _load_dependencies, run_demo, segment_demo_image

    if algorithm not in VISION_ALGORITHMS:
        raise ValueError(f"algorithm必须是{'/'.join(VISION_ALGORITHMS)}")

    cv2, _np = _load_dependencies()
    factory = capture_factory if capture_factory is not None else cv2.VideoCapture
    show = imshow if imshow is not None else cv2.imshow
    key_fn = wait_key if wait_key is not None else cv2.waitKey
    close = destroy_windows if destroy_windows is not None else cv2.destroyAllWindows
    analyze = analyze_frame if analyze_frame is not None else (
        lambda image, current_algorithm: _analyze_frozen_frame(
            image,
            algorithm=current_algorithm,
            output_root=output_root,
            camera_index=camera_index,
            cv2=cv2,
            run_demo=run_demo,
        )
    )

    capture: Any = None
    run_dirs: list[Path] = []
    last_status = "SPACE=analyze  Q=pause  no auto pump"
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
            )
            show(LIVE_WINDOW, view)
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
                run_dir = analyze(frame, current_algorithm)
                run_dirs.append(run_dir)
                last_status = f"saved {run_dir.name}  no pump"
                result_path = run_dir / "path_overlay.png"
                if result_path.is_file():
                    result = cv2.imread(str(result_path), cv2.IMREAD_COLOR)
                    if result is not None:
                        show(RESULT_WINDOW, result)
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
) -> Path:
    staging_dir = Path(output_root)
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging = staging_dir / "_live_last_frame.png"
    if not cv2.imwrite(str(staging), image):
        raise OSError(f"无法写入冻结帧：{staging}")
    return run_demo(
        input_path=staging,
        mode="analyze",
        output_root=output_root,
        algorithm=algorithm,
        camera_index=camera_index,
        source_kind_override="camera",
        input_source_override=f"usb-live:index={camera_index}",
    )


def _wait_until_camera_readable(
    *,
    camera_index: int,
    camera_backend: int | None,
    factory: CaptureFactory,
    sleep: SleepFn,
    max_attempts: int | None,
) -> Any:
    attempts = 0
    while max_attempts is None or attempts < max_attempts:
        attempts += 1
        capture = None
        keep_open = False
        try:
            capture = factory(camera_index) if camera_backend is None else factory(camera_index, camera_backend)
            if capture is not None and bool(capture.isOpened()):
                ok, frame = capture.read()
                if ok and frame is not None and hasattr(frame, "size") and int(frame.size) > 0:
                    print(f"已检测到 USB 相机 device_index={camera_index}，打开实时窗口。")
                    keep_open = True
                    return capture
        except Exception:
            pass
        finally:
            if capture is not None and not keep_open:
                try:
                    capture.release()
                except Exception:
                    pass
        sleep(1.0)
    raise USBCameraError(CAMERA_OPEN_FAILED, f"等待 USB 相机超时 device_index={camera_index}")


def _idle_pause(*, cv2, np, show: ShowFrame, key_fn: WaitKey, max_idle_frames: int | None) -> str:
    paused = np.zeros((220, 720, 3), dtype=np.uint8)
    _put_hud(paused, cv2, "Preview paused", y=70)
    _put_hud(paused, cv2, "Any other key = reopen camera", y=110)
    _put_hud(paused, cv2, "Q / Esc = exit program   no pump", y=150)
    frames = 0
    while max_idle_frames is None or frames < max_idle_frames:
        show(IDLE_WINDOW, paused)
        key = int(key_fn(100))
        frames += 1
        if key < 0:
            continue
        key = key & 0xFF
        if key in QUIT_KEYS:
            return "exit"
        return "reopen"
    return "exit"


def _compose_preview(
    frame,
    *,
    algorithm: str,
    camera_index: int,
    status: str,
    cv2,
    segment_demo_image,
    draw_contamination,
):
    try:
        segmentation = segment_demo_image(frame, algorithm)
        view = draw_contamination(frame, segmentation.mask, segmentation.measurement.centroid_px, cv2)
        area = segmentation.measurement.area_px
        centroid = segmentation.measurement.centroid_px
        hint = f"area={area:.0f}px  center={centroid}"
    except Exception as exc:
        view = frame.copy()
        hint = f"segment failed: {type(exc).__name__}"
        status = hint
    _put_hud(view, cv2, f"index={camera_index}  {algorithm}  SPACE=analyze  H/O/G/E/L=algo  Q=pause")
    _put_hud(view, cv2, hint, y=56)
    _put_hud(view, cv2, status, y=80)
    return view, status


def _put_hud(image, cv2, text: str, *, y: int = 32) -> None:
    cv2.putText(image, text[:88], (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(image, text[:88], (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1, cv2.LINE_AA)


def _apply_resolution(capture: Any, cv2, width: int | None, height: int | None) -> None:
    if width is not None:
        try:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
        except Exception:
            pass
    if height is not None:
        try:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
        except Exception:
            pass
