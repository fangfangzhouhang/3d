"""电机已知位移标定真实 mm/px（离线，不喷水）。

原理：让 X/Y 各走一段精确脉冲数（固件计数可靠，5mm/圈、320步/mm），
对比画面里同一个固定特征点移动了多少像素，反推：

    mm_per_px = (steps / 320.0) / abs(pixel_shift)

步骤：抓帧f0 → 只走X → 抓帧f1 → 只走Y → 抓帧f2 → 模板匹配跟踪特征。
结果写入 JSON 证据文件；feeds_action_request 始终为 false。
这一步只标定尺度，不修改规划规则，也不写工作台绝对坐标。

标定期间保持预览窗口打开，可以看到电机转动过程。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

STEPS_PER_MM = 320.0  # 1600步/圈 ÷ 5mm/圈


def _load_deps():
    try:
        import cv2
        import numpy as np
        import serial
    except ImportError as exc:  # pragma: no cover - 环境依赖提示
        raise RuntimeError(
            "标定需要 NumPy/OpenCV/pyserial；请安装 requirements/perception-opencv.txt 和 control-serial.txt"
        ) from exc
    return cv2, np, serial


class Motor:
    def __init__(self, serial_mod, port: str, baudrate: int, timeout: float):
        self.ser = serial_mod.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=timeout,
            write_timeout=timeout,
        )
        self.speed_hz = 500
        time.sleep(0.3)
        self.ser.reset_input_buffer()

    def set_speed(self, hz: int) -> None:
        self._cmd(f"SPEED {hz}")
        self.speed_hz = hz

    def _cmd(self, line: str, wait: float = 0.3) -> list[str]:
        self.ser.write((line + "\r\n").encode("ascii"))
        time.sleep(wait)
        out: list[str] = []
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if self.ser.in_waiting:
                out.append(self.ser.readline().decode("ascii", "replace").strip())
            else:
                time.sleep(0.05)
        return out

    def hello(self) -> None:
        replies = self._cmd("HELLO")
        if "STEP_OK v0.3" not in replies:
            raise RuntimeError(f"握手失败，期望 STEP_OK v0.3，实际 {replies}")

    def move_async(self, nx: int, dx: str, ny: int, dy: str) -> threading.Thread:
        """异步移动，返回线程对象"""
        def _move():
            replies = self._cmd(f"MOVEXY {nx} {dx} {ny} {dy}")
            if not any(r.startswith("STEP2_START") for r in replies):
                raise RuntimeError(f"未收到 STEP2_START：{replies}")
            steps = max(abs(nx), abs(ny))
            deadline = time.monotonic() + (max(steps, 1) / max(self.speed_hz, 1)) + 2.0
            last = ""
            while time.monotonic() < deadline:
                replies = self._cmd("READXY", wait=0.12)
                last = replies[0] if replies else ""
                parts = dict(tok.split("=") for tok in last.split()[1:] if "=" in tok)
                if parts.get("BX") == "0" and parts.get("BY") == "0":
                    return
                time.sleep(0.05)
            raise TimeoutError(f"电机在时限内未空闲：{last}")

        thread = threading.Thread(target=_move, daemon=True)
        thread.start()
        return thread

    def close(self) -> None:
        self.ser.close()


def wait_live(cap, cv2, np, timeout_s: float = 8.0) -> bool:
    """确认相机流是活的：连续两帧之间应有传感器噪声差异。"""
    t0 = time.time()
    prev = None
    while time.time() - t0 < timeout_s:
        ok, f = cap.read()
        if ok:
            if prev is not None:
                if float(np.mean(cv2.absdiff(f, prev))) > 0.05:
                    return True
            prev = f
        time.sleep(0.05)
    return False


def track(cv2, np, frame_from, frame_to, half: int):
    """在两帧间用归一化互相关跟踪中央小邻域，返回 (dx_px, dy_px, score)。"""
    h, w = frame_from.shape[:2]
    cx, cy = w // 2, h // 2
    x0 = max(half, cx - half)
    y0 = max(half, cy - half)
    x1 = min(w - half, cx + half)
    y1 = min(h - half, cy + half)
    templ = frame_from[y0:y1, x0:x1]
    if templ.size == 0:
        raise RuntimeError("模板为空")
    res = cv2.matchTemplate(frame_to, templ, cv2.TM_CCOEFF_NORMED)
    _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(res)
    bx = max_loc[0] + (x1 - x0) // 2
    by = max_loc[1] + (y1 - y0) // 2
    return (bx - cx, by - cy, float(max_val))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="电机已知位移标定真实 mm/px（保持预览窗口打开）")
    parser.add_argument("--serial-port", required=True, help="Stage2 COM 口，如 COM5")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--serial-timeout", type=float, default=2.0)
    parser.add_argument("--camera-index", type=int, default=1)
    parser.add_argument("--camera-backend", type=int, default=1400, help="1400=MSMF，700=DSHOW")
    parser.add_argument("--steps", type=int, default=800, help="每轴标定脉冲数，默认800=2.5mm")
    parser.add_argument("--speed", type=int, default=500, help="标定期间脉冲频率 Hz；接触不良时建议 50")
    parser.add_argument("--template-half", type=int, default=40, help="跟踪模板半边长 px")
    parser.add_argument("--min-score", type=float, default=0.6, help="模板匹配最低可信度")
    parser.add_argument("--output-dir", type=Path, default=Path("output") / "calibration")
    args = parser.parse_args(argv)

    cv2, np, serial_mod = _load_deps()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.output_dir / f"cal_{run_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(args.camera_index, args.camera_backend)
    if not cap.isOpened():
        raise RuntimeError(f"无法打开相机 index={args.camera_index} backend={args.camera_backend}")
    if not wait_live(cap, cv2, np):
        cap.release()
        raise RuntimeError(
            "相机流冻结（连续多帧完全相同）。"
            "常见原因：上一个占用相机的进程刚被关闭、相机未释放干净。"
            "请等几秒后重试，或重新插拔摄像头 USB 线。"
        )
    print("相机流活性检测通过")

    # 状态机：0=对位, 1=标定X中, 2=标定Y中, 3=完成, 4=预览
    state = 0
    half = args.template_half + 8
    f0 = f1 = f2 = None
    motor_thread = None
    motor = None
    status_text = "SPACE=start  move sharp mark into box"

    print("对位：把一个清晰、高对比的小特征（如深色小点/细划痕交点）移到中央框内并对焦，按空格开始。")
    print("标定期间窗口保持打开，可以看到电机转动。")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.02)
                continue

            h, w = frame.shape[:2]
            cx, cy = w // 2, h // 2
            view = frame.copy()

            # 画中央框
            cv2.rectangle(view, (cx - half, cy - half), (cx + half, cy + half), (0, 255, 0), 2)

            # 根据状态显示不同文字
            if state == 0:
                cv2.putText(view, "SPACE=start  move sharp mark into box", (8, 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3, cv2.LINE_AA)
                cv2.putText(view, "SPACE=start  move sharp mark into box", (8, 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1, cv2.LINE_AA)
            elif state == 1:
                cv2.putText(view, "CALIBRATING X...  motor moving", (8, 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv2.LINE_AA)
            elif state == 2:
                cv2.putText(view, "CALIBRATING Y...  motor moving", (8, 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv2.LINE_AA)
            elif state == 3:
                cv2.putText(view, "CALIBRATION DONE  entering preview...", (8, 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2, cv2.LINE_AA)
            else:
                cv2.putText(view, status_text, (8, 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1, cv2.LINE_AA)

            cv2.imshow("calibration", view)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break

            if state == 0 and key == ord(" "):
                # 开始对位确认，抓 f0
                f0 = frame.copy()
                cv2.imwrite(str(out_dir / "f0.png"), f0)
                print(f"f0 已抓：{out_dir/'f0.png'}")

                # 启动电机 X 移动
                motor = Motor(serial_mod, args.serial_port, args.baudrate, args.serial_timeout)
                motor.hello()
                print("握手成功 STEP_OK v0.3")
                motor.set_speed(args.speed)
                print(f"标定频率 {args.speed} Hz")

                motor_thread = motor.move_async(args.steps, "FWD", 0, "FWD")
                state = 1
                status_text = "CALIBRATING X..."

            elif state == 1:
                # 等待 X 移动完成
                if motor_thread and not motor_thread.is_alive():
                    time.sleep(0.3)  # 稳定
                    ok2, f1 = cap.read()
                    if ok2:
                        f1 = f1.copy()
                        cv2.imwrite(str(out_dir / "f1_after_x.png"), f1)
                        print(f"f1 已抓：{out_dir/'f1_after_x.png'}")

                        # 启动 Y 移动
                        motor_thread = motor.move_async(0, "FWD", args.steps, "REV")
                        state = 2
                        status_text = "CALIBRATING Y..."
                    else:
                        print("f1 抓帧失败，重试")

            elif state == 2:
                # 等待 Y 移动完成
                if motor_thread and not motor_thread.is_alive():
                    time.sleep(0.3)  # 稳定
                    ok2, f2 = cap.read()
                    if ok2:
                        f2 = f2.copy()
                        cv2.imwrite(str(out_dir / "f2_after_y.png"), f2)
                        print(f"f2 已抓：{out_dir/'f2_after_y.png'}")

                        # 计算结果
                        motor.close()
                        state = 3
                        status_text = "CALIBRATION DONE"
                    else:
                        print("f2 抓帧失败，重试")

            elif state == 3:
                # 显示结果，进入预览
                time.sleep(1)
                state = 4
                status_text = "calibration done  SPACE=analyze  Q=quit"

            elif state == 4:
                # 预览模式
                if key == ord(" "):
                    # 简单分析
                    from demo.demo_pipeline import _draw_contamination, segment_demo_image
                    seg = segment_demo_image(frame, "local")
                    overlay = _draw_contamination(frame, seg.mask, seg.measurement.centroid_px, cv2)
                    cv2.imshow("analysis result", overlay)
                    status_text = f"analyzed area={seg.measurement.area_px:.0f}"
                elif key == ord("h"):
                    status_text = "algorithm=hsv"
                elif key == ord("o"):
                    status_text = "algorithm=otsu"
                elif key == ord("g"):
                    status_text = "algorithm=exg"
                elif key == ord("e"):
                    status_text = "algorithm=exr"
                elif key == ord("l"):
                    status_text = "algorithm=local"

    finally:
        if motor:
            try:
                motor.set_speed(500)
            except Exception:
                pass
            motor.close()
        cap.release()
        cv2.destroyAllWindows()

    # 计算并输出结果
    if f0 is not None and f1 is not None and f2 is not None:
        dxx, dxy, sx = track(cv2, np, f0, f1, args.template_half)
        dyx, dyy, sy = track(cv2, np, f1, f2, args.template_half)
        print(f"X移动后特征位移 dx={dxx:.1f}, dy={dxy:.1f}, score={sx:.3f}")
        print(f"Y移动后特征位移 dx={dyx:.1f}, dy={dyy:.1f}, score={sy:.3f}")

        known_mm = args.steps / STEPS_PER_MM
        result: dict[str, object] = {
            "calibration_id": f"motor_mm_per_px_{run_id}",
            "method": "motor_known_displacement",
            "steps_per_axis": args.steps,
            "known_displacement_mm": known_mm,
            "steps_per_mm_assumed": STEPS_PER_MM,
            "x_axis": {"pixel_shift_along": dxx, "pixel_shift_cross": dxy, "match_score": sx},
            "y_axis": {"pixel_shift_along": dyy, "pixel_shift_cross": dyx, "match_score": sy},
            "feeds_action_request": False,
            "evidence_boundary": "电机位移反推 mm/px；仅尺度，未标定原点/旋转/喷头偏移，不得直接当作验收坐标",
        }

        warnings: list[str] = []
        if sx < args.min_score or sy < args.min_score:
            warnings.append(f"MATCH_SCORE_LOW (x={sx:.3f}, y={sy:.3f})")
        if abs(dxx) < 10 or abs(dyy) < 10:
            warnings.append("SHIFT_TOO_SMALL")
        if abs(dxy) > 0.25 * max(abs(dxx), 1) or abs(dyx) > 0.25 * max(abs(dyy), 1):
            warnings.append("AXIS_CROSSTALK_HIGH：电机轴与画面轴可能未对齐（旋转/装配）")

        mm_per_px_x = known_mm / abs(dxx) if dxx else None
        mm_per_px_y = known_mm / abs(dyy) if dyy else None
        result["mm_per_px_x"] = mm_per_px_x
        result["mm_per_px_y"] = mm_per_px_y
        result["warnings"] = warnings

        out_file = out_dir / "mm_per_px.json"
        out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        print("---------- 标定结果 ----------")
        print(f"X mm/px = {mm_per_px_x}")
        print(f"Y mm/px = {mm_per_px_y}")
        if warnings:
            print("警告：" + "; ".join(warnings))
        print(f"证据文件：{out_file}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
