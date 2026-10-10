#!/usr/bin/env python3
"""按空格将画面中的物体自动移到画面正中央。

原理：
  1. 空格时对当前帧做污渍分割，得到质心 (cx, cy)
  2. 计算质心到画面中心 (w/2, h/2) 的像素偏移 (dx, dy)
  3. 用标定好的 mm/px 把像素偏移换算成毫米，再乘 320 步/mm 得到脉冲数
  4. 发 MOVEXY 命令，让载物台移动把物体拉回中央

改进：电机移动在后台线程执行，预览窗口在移动过程中持续刷新。
只用于 --maintenance-mode --no-spray 维护；每次短距运动确认后使正式账本失效。
居中完成不会喷水，正式清洗使用工作台并重新建立参考位置。
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 标定参数（来自最新一次成功标定 cal_20260929T124723Z）
MM_PER_PX_X = 0.0187
MM_PER_PX_Y = 0.0181
STEPS_PER_MM = 320.0
MAX_STEPS_PER_MOVE = 20000  # 固件一条报文的上限；闭环是否越界看位置账本
ITERATIONS = 3  # 迭代居中次数，越多次越准
CENTER_TOLERANCE_PX = 3.0  # 距中心小于此像素视为已居中
PUMP_ON_MS = 300  # 本脚本固定 300ms。主机定点短喷许可已是 500ms，这里不跟着改。


def main() -> int:
    parser = argparse.ArgumentParser(description="按空格将物体移到画面中央")
    parser.add_argument("--serial-port", default="COM5", help="Stage2 COM 口")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--camera-index", type=int, default=1)
    parser.add_argument("--camera-backend", type=int, default=1400, help="1400=MSMF, 700=DSHOW")
    parser.add_argument("--algorithm", default="local", choices=["hsv", "otsu", "exg", "exr", "local"])
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS_PER_MOVE)
    parser.add_argument("--no-spray", action="store_true", help="居中后不自动喷水（用于 Gate5 等不含泵的阶段）")
    parser.add_argument("--maintenance-mode", action="store_true", help="显式维护模式；移动前逐次输入 YES，位置账本失效")
    parser.add_argument("--position-path", type=Path, default=ROOT / "output/stage2/position.json")
    args = parser.parse_args()
    if not args.maintenance_mode or not args.no_spray:
        parser.error("旧居中工具仅允许 --maintenance-mode --no-spray；正式喷洗请使用五页工作台")
    from microcleaning.control_system.safety.maintenance import authorize_maintenance_move, MAINTENANCE_STEP_CAP
    from microcleaning.control_system.serial.resource_lease import ResourceLease, device_resources
    args.max_steps = min(args.max_steps, MAINTENANCE_STEP_CAP)
    lease = ResourceLease(device_resources(args.serial_port, args.position_path)).acquire()

    import cv2
    import numpy as np
    import serial

    from demo.demo_pipeline import _draw_contamination, segment_demo_image

    # 打开串口
    try:
        ser = serial.Serial(
        port=args.serial_port,
        baudrate=args.baudrate,
        bytesize=8,
        parity="N",
        stopbits=1,
        timeout=2.0,
        write_timeout=2.0,
        )
    except BaseException:
        lease.close()
        raise
    time.sleep(0.3)
    ser.reset_input_buffer()

    # 用锁保护串口，避免主线程和移动线程同时读写
    ser_lock = threading.Lock()
    closing = threading.Event()

    def cmd(line: str, wait: float = 0.3) -> list[str]:
        if line.startswith("MOVEXY "):
            authorize_maintenance_move(line, args.position_path, enabled=args.maintenance_mode, cancelled=closing.is_set)
        with ser_lock:
            if closing.is_set() and line != "STOP":
                raise RuntimeError("维护已关闭，禁止发新命令")
            ser.write((line + "\r\n").encode("ascii"))
            time.sleep(wait)
            out: list[str] = []
            deadline = time.time() + 1.0
            while time.time() < deadline:
                if ser.in_waiting:
                    out.append(ser.readline().decode("ascii", "replace").strip())
                else:
                    time.sleep(0.05)
            return out

    # 握手
    replies = cmd("HELLO")
    if "STEP_OK v0.3" not in replies:
        print(f"握手失败：{replies}")
        ser.close()
        lease.close()
        return 1
    print("握手成功 STEP_OK v0.3")

    # 后台移动状态
    move_done = threading.Event()
    move_done.set()  # 初始为空闲
    move_result = {"success": False, "steps_x": 0, "steps_y": 0, "message": ""}

    def move_xy(nx: int, dx: str, ny: int, dy: str) -> bool:
        """同步发送 MOVEXY 并等待完成（在迭代线程内调用）"""
        replies = cmd(f"MOVEXY {nx} {dx} {ny} {dy}")
        if not any(r.startswith("STEP2_START") for r in replies):
            print(f"启动失败：{replies}")
            return False
        steps = max(abs(nx), abs(ny))
        deadline = time.monotonic() + (max(steps, 1) / 500.0) + 2.0
        while time.monotonic() < deadline:
            replies = cmd("READXY", wait=0.1)
            last = replies[0] if replies else ""
            try:
                parts = dict(tok.split("=") for tok in last.split()[1:] if "=" in tok)
                if parts.get("BX") == "0" and parts.get("BY") == "0" and (not nx or parts.get("X") == str(nx)) and (not ny or parts.get("Y") == str(ny)):
                    return True
            except Exception:
                pass
            time.sleep(0.05)
        print("电机超时未完成")
        return False

    def pump_spray(ms: int) -> bool:
        """喷水一次：PUMP ON → 延时 → PUMP OFF（finally 保证异常时也一定关泵）"""
        replies = cmd("PUMP ON")
        if not any("PUMP_ON" in r for r in replies):
            print(f"开水泵失败：{replies}")
            return False
        try:
            time.sleep(ms / 1000.0)
        finally:
            replies = cmd("PUMP OFF")
        if not any("PUMP_OFF" in r for r in replies):
            print(f"关水泵失败：{replies}")
            return False
        return True

    def center_iteration() -> None:
        """后台线程：迭代居中，居中成功后自动喷水一次"""
        try:
            detected = False
            spray = False
            for i in range(ITERATIONS):
                # 抓一帧做分割
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.1)
                    continue
                seg = segment_demo_image(frame, current_algorithm)
                centroid = seg.measurement.centroid_px
                if centroid is None:
                    move_result["message"] = "no target"
                    print("未检测到物体")
                    return
                detected = True

                h, w = frame.shape[:2]
                cx_img, cy_img = w // 2, h // 2
                obj_x, obj_y = centroid
                dx = obj_x - cx_img
                dy = obj_y - cy_img

                dist = (dx * dx + dy * dy) ** 0.5
                if dist < CENTER_TOLERANCE_PX:
                    print(f"已居中：第{i + 1}次迭代，距中心{dist:.1f}px")
                    move_result["success"] = True
                    spray = True
                    break

                mm_x = abs(dx) * MM_PER_PX_X
                mm_y = abs(dy) * MM_PER_PX_Y
                steps_x = min(int(round(mm_x * STEPS_PER_MM)), args.max_steps)
                steps_y = min(int(round(mm_y * STEPS_PER_MM)), args.max_steps)
                dir_x = "FWD" if dx > 0 else "REV"
                dir_y = "REV" if dy > 0 else "FWD"

                if steps_x == 0 and steps_y == 0:
                    print("物体已在中心")
                    move_result["success"] = True
                    spray = True
                    break

                print(f"迭代{i + 1}: 质心=({obj_x:.0f},{obj_y:.0f}) "
                      f"偏移=({dx:+.0f},{dy:+.0f})px → X={steps_x}{dir_x} Y={steps_y}{dir_y}")
                move_result["message"] = f"iter {i + 1}/{ITERATIONS}  X={steps_x}{dir_x} Y={steps_y}{dir_y}"

                if not move_xy(steps_x, dir_x, steps_y, dir_y):
                    move_result["message"] = "move failed"
                    return
            else:
                move_result["success"] = False
                move_result["message"] = "未达到居中容差；停止维护，不喷水"
                spray = False

            if spray and not args.no_spray:
                move_result["message"] = "spraying..."
                print("居中完成，喷水 300ms")
                if pump_spray(PUMP_ON_MS):
                    move_result["message"] = "done + sprayed"
                    print("喷水完成")
                else:
                    move_result["message"] = "spray failed"
                    print("喷水失败")
        finally:
            move_done.set()

    # 打开相机
    cap = cv2.VideoCapture(args.camera_index, args.camera_backend)
    if not cap.isOpened():
        print(f"无法打开相机 index={args.camera_index}")
        ser.close()
        lease.close()
        return 1

    current_algorithm = args.algorithm
    last_status = "SPACE=center+spray  H/O/G/E/L=algo  Q=quit"
    move_thread: threading.Thread | None = None
    print("预览已打开。按空格将物体移到中央并喷水一次，H/O/G/E/L 切换算法，Q 退出。")

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.02)
                continue

            h, w = frame.shape[:2]
            cx_img, cy_img = w // 2, h // 2

            # 实时分割叠加
            seg = segment_demo_image(frame, current_algorithm)
            view = _draw_contamination(frame, seg.mask, seg.measurement.centroid_px, cv2)

            # 画中心十字
            cv2.drawMarker(view, (cx_img, cy_img), (0, 255, 255), cv2.MARKER_CROSS, 30, 2)

            # 显示移动中状态
            if move_thread is not None and move_thread.is_alive():
                msg = move_result.get("message", "MOVING...")
                cv2.putText(view, f"MOVING: {msg}", (8, 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2, cv2.LINE_AA)
            else:
                cv2.putText(view, last_status, (8, 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
                cv2.putText(view, last_status, (8, 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1, cv2.LINE_AA)

            cv2.imshow("center object", view)
            key = cv2.waitKey(1) & 0xFF

            # 处理上一次移动完成
            if move_thread is not None and not move_thread.is_alive():
                last_status = move_result.get("message", "done")
                move_thread = None

            if key == ord("q"):
                break
            if key == ord("h"):
                current_algorithm = "hsv"
                last_status = "algorithm=hsv"
            elif key == ord("o"):
                current_algorithm = "otsu"
                last_status = "algorithm=otsu"
            elif key == ord("g"):
                current_algorithm = "exg"
                last_status = "algorithm=exg"
            elif key == ord("e"):
                current_algorithm = "exr"
                last_status = "algorithm=exr"
            elif key == ord("l"):
                current_algorithm = "local"
                last_status = "algorithm=local"
            elif key == ord(" "):
                # 只在空闲时响应
                if move_thread is not None and move_thread.is_alive():
                    last_status = "busy, wait..."
                    continue

                centroid = seg.measurement.centroid_px
                if centroid is None:
                    last_status = "no target detected"
                    print("未检测到物体")
                    continue

                move_done.clear()
                move_result["success"] = False
                move_result["message"] = "starting..."
                move_thread = threading.Thread(
                    target=center_iteration,
                    daemon=True,
                )
                move_thread.start()
                last_status = "centering..."

    finally:
        closing.set()
        try:
            cmd("STOP", wait=0.1)
        except Exception:
            pass
        # 等待移动线程结束
        if move_thread is not None and move_thread.is_alive():
            move_thread.join(timeout=5.0)
        cap.release()
        cv2.destroyAllWindows()
        ser.close()
        lease.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
