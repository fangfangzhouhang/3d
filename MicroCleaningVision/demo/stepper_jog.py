"""Bounded keyboard maintenance jog; no pump, calibration or automatic reference.

The UI admits one immutable key request at a time. One worker owns the serial
connection; STOP uses an event, never queues behind movement requests.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
import json
from pathlib import Path
import queue
import threading
import time

from microcleaning.control_system.planning.stage2_position import mark_unknown
from microcleaning.control_system.safety.maintenance import MAINTENANCE_STEP_CAP
from microcleaning.control_system.serial.resource_lease import ResourceLease, device_resources
from microcleaning.control_system.serial.stage2_protocol import (
    encode_hello, encode_move_xy, encode_read_xy, encode_stop, parse_stage2_reply,
)

ROOT = Path(__file__).resolve().parents[1]
KEYS = {"w": (1, 0), "s": (-1, 0), "a": (0, -1), "d": (0, 1)}


@dataclass(frozen=True)
class JogConfig:
    real: bool = False
    port: str | None = None
    steps: int = 20
    timeout: float = 2.0
    enable: bool = False
    position_path: Path = ROOT / "output/stage2/position.json"

    def __post_init__(self):
        if type(self.real) is not bool:
            raise ValueError("real must be bool")
        if type(self.enable) is not bool:
            raise ValueError("enable must be bool")
        if type(self.steps) is not int or not 1 <= self.steps <= MAINTENANCE_STEP_CAP:
            raise ValueError("每次点动步数必须为1–1000整数")
        if isinstance(self.timeout, bool) or not math.isfinite(self.timeout) or not 0.2 <= self.timeout <= 10:
            raise ValueError("timeout必须为0.2–10秒有限数")
        if self.real and (not isinstance(self.port, str) or not self.port.strip()
                          or any(ord(c) < 33 for c in self.port)):
            raise ValueError("真实模式必须显式指定已人工确认的端口")
        if not self.real and self.port is not None:
            raise ValueError("模拟模式不接受端口")


@dataclass(frozen=True)
class JogRequest:
    key: str
    x: int
    y: int

    def wire(self):
        return encode_move_xy(abs(self.x), "FWD" if self.x >= 0 else "REV",
                              abs(self.y), "FWD" if self.y >= 0 else "REV")


class JogDevice:
    """Only called by its owning worker. Injected serial factories are for tests."""
    def __init__(self, config, *, serial_factory=None, lock_directory=None, log=lambda message: None):
        self.config = config
        self.serial_factory = serial_factory
        self.lock_directory = lock_directory
        self.log = log
        self.serial = None
        self.lease = None

    def _write(self, data, cancel=None):
        self.log("TX_PENDING " + data.decode("ascii").strip())
        if cancel is not None and cancel.is_set():
            raise InterruptedError("发送前取消，未写入命令")
        if self.serial.write(data) != len(data):
            raise OSError("串口短写，动作状态未知")
        self.log("TX " + data.decode("ascii").strip())

    def _reply(self, deadline, cancel=None):
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("点动已取消")
            raw = self.serial.readline(513)
            if not raw:
                continue
            if len(raw) > 512 or not raw.endswith(b"\n"):
                raise OSError("不完整或过长回复")
            self.log("RX " + raw.decode("ascii", "strict").strip())
            reply = parse_stage2_reply(raw)
            if reply.kind == "ERR":
                raise OSError(reply.raw)
            return reply
        raise TimeoutError("等待固件回复超时")

    def open(self, cancel):
        if not self.config.real:
            self.log("模拟连接：未打开COM")
            return
        try:
            self.lease = ResourceLease(device_resources(self.config.port, self.config.position_path),
                                       directory=self.lock_directory).acquire()
            factory = self.serial_factory
            if factory is None:
                import serial
                factory = serial.Serial
            self.serial = factory(port=self.config.port, baudrate=115200, bytesize=8,
                                  parity="N", stopbits=1, timeout=0.05, write_timeout=0.2)
            self.serial.reset_input_buffer()
            if cancel.is_set():
                raise InterruptedError("连接取消")
            self._write(encode_hello(), cancel)
            if self._reply(time.monotonic() + self.config.timeout, cancel).kind != "STEP_OK":
                raise OSError("握手版本不符")
            self._write(encode_read_xy(), cancel)
            state = self._reply(time.monotonic() + self.config.timeout, cancel)
            if state.kind != "STEP2" or state.x_busy or state.y_busy:
                raise OSError("设备状态异常或忙")
        except BaseException:
            self.close()
            raise

    def move(self, request, cancel):
        # Validate again at the final boundary; never accept arbitrary strings.
        direction = KEYS.get(request.key)
        if direction is None or (request.x, request.y) != tuple(v * self.config.steps for v in direction):
            raise PermissionError("按键请求与固定步数不符")
        if cancel.is_set():
            raise InterruptedError("点动取消，未发送")
        if not self.config.real:
            self.log("SIM " + request.wire().decode().strip())
            if cancel.wait(0.15):
                raise InterruptedError("模拟停止")
            return
        if self.serial is None:
            raise OSError("未连接")
        mark_unknown(self.config.position_path, run_id=None,
                     reason="WASD维护点动即将发送；必须人工重新建立正式参考")
        if cancel.is_set():
            raise InterruptedError("点动取消，位置保持未知")
        deadline = time.monotonic() + self.config.timeout
        self._write(request.wire(), cancel)
        reply = self._reply(deadline, cancel)
        expected = (abs(request.x), "FWD" if request.x >= 0 else "REV",
                    abs(request.y), "FWD" if request.y >= 0 else "REV")
        if (reply.kind != "STEP2_START" or
                (reply.x_steps, reply.x_direction, reply.y_steps, reply.y_direction) != expected):
            raise OSError("运动起始回执不匹配")
        while time.monotonic() < deadline:
            if cancel.wait(0.05):
                raise InterruptedError("点动取消")
            self._write(encode_read_xy(), cancel)
            state = self._reply(deadline, cancel)
            if state.kind != "STEP2":
                raise OSError("READXY回复不符")
            if not state.x_busy and not state.y_busy:
                if ((request.x and state.x_sent != abs(request.x)) or
                        (request.y and state.y_sent != abs(request.y))):
                    raise OSError("脉冲计数不足；位置未知")
                return
        raise TimeoutError("点动完成超时")

    def stop(self):
        if not self.config.real:
            self.log("SIM STOP（模拟）")
            return
        if self.serial is None:
            raise OSError("连接已关闭，STOP未确认")
        # A pending move reply can precede STOP. Preserve and drain boundedly.
        self._write(encode_stop())
        deadline = time.monotonic() + self.config.timeout
        for _ in range(40):
            reply = self._reply(deadline)
            if reply.kind == "STEP_STOPPED":
                self.log("STOP收到协议回执；不代表物理断能")
                return
        raise TimeoutError("STOP未确认")

    def close(self):
        try:
            if self.serial is not None:
                self.serial.close()
        finally:
            self.serial = None
            if self.lease is not None:
                self.lease.close()
                self.lease = None


class JogController:
    """No deferred move queue: busy/held keys are consumed and discarded."""
    def __init__(self, config, device=None):
        self.config = config
        self.events = queue.SimpleQueue()
        self.device = device or JogDevice(config, log=self._emit)
        self.state = "LOCKED"
        self.held = set()
        self.cancel = threading.Event()
        self.cancel.set()
        self.condition = threading.Condition()
        self.pending = None
        self.operation = None
        self.quit_requested = False
        self.stdout_error = None
        self.thread = threading.Thread(target=self._worker, name="stepper-jog-owner", daemon=False)
        self.thread.start()

    def _emit(self, message):
        self.events.put(message)
        if self.stdout_error is not None:
            return
        try:
            print(json.dumps({"mode": "real" if self.config.real else "simulation",
                              "port": self.config.port, "message": str(message)}, ensure_ascii=False), flush=True)
        except (OSError, UnicodeError, ValueError) as exc:
            # Logging must never escape the worker's error handling and skip
            # STOP/close. The UI remains available even if stdout has broken.
            self.stdout_error = str(exc)
            self.events.put("日志输出失败，已请求停止；请重启程序恢复记录")
            self.stop()

    def arm(self, *, operator_authorized=False):
        with self.condition:
            if self.quit_requested or self.state != "LOCKED" or self.operation is not None or self.held:
                return False
            if self.stdout_error is not None:
                self.events.put("日志通道失效，拒绝重新启用；请重启程序")
                return False
            if self.config.real and operator_authorized is not True:
                self._emit("需要操作者明确启用；不自动认定急停或行程已验收")
                return False
            self.cancel.clear()
            self.state = "CONNECTING"
            self.operation = "open"
            self.condition.notify()
            return True

    def press(self, key):
        key = key.lower()
        if key in {"space", " "}:
            self.stop()
            return False
        if key == "q":
            self.close()
            return False
        if key not in KEYS:
            return False
        with self.condition:
            if key in self.held:
                return False
            self.held.add(key)
            if self.state != "READY" or self.operation is not None or self.cancel.is_set():
                self._emit("忽略按键：未启用或设备忙，不排队")
                return False
            x, y = KEYS[key]
            self.pending = JogRequest(key, x * self.config.steps, y * self.config.steps)
            self._emit(f"请求 {key.upper()} X={self.pending.x} Y={self.pending.y} 脉冲")
            if self.cancel.is_set():
                return False
            self.state = "BUSY"
            self.operation = "move"
            self.condition.notify()
            return True

    def release(self, key):
        with self.condition:
            self.held.discard(key.lower())

    def stop(self):
        with self.condition:
            self.cancel.set()
            self.pending = None
            if not self.quit_requested:
                self.state = "STOPPING"
            self.condition.notify()

    def close(self):
        with self.condition:
            self.quit_requested = True
        self.stop()

    def _worker(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.operation is not None or
                                        self.state == "STOPPING" or self.quit_requested)
                operation, request = self.operation, self.pending
                self.pending = None
            try:
                if not self.cancel.is_set():
                    if operation == "open":
                        self.device.open(self.cancel)
                    elif operation == "move" and request is not None:
                        self.device.move(request, self.cancel)
                with self.condition:
                    if not self.cancel.is_set():
                        self.operation = None
                        self.state = "READY"
                        self._emit("READY：每次独立按键一个短步；松开后才能再次触发")
                        continue
            except Exception as exc:
                self._emit(f"错误/取消：{exc}")
                self.cancel.set()
            try:
                self.device.stop()
            except Exception as exc:
                self._emit(f"STOP未确认：{exc}")
            finally:
                try:
                    self.device.close()
                except Exception as exc:
                    self._emit(f"关闭失败：{exc}")
            with self.condition:
                self.operation = None
                self.state = "CLOSED" if self.quit_requested else "LOCKED"
                self._emit(self.state + "：需人工重新启用")
                if self.quit_requested:
                    return


class JogWindow:
    def __init__(self, root, controller):
        import tkinter as tk
        from tkinter import ttk
        self.root, self.controller = root, controller
        self.releases = {}
        self.closing = False
        root.title("WASD 步进点动 · " + ("真实设备" if controller.config.real else "模拟（电机不会动）"))
        root.geometry("720x520")
        ttk.Label(root, text="W/S：X正/反向    A/D：Y反/正向", font=("Microsoft YaHei UI", 17)).pack(pady=12)
        ttk.Label(root, text=f"每次 {controller.config.steps} 脉冲 · 按住不连发 · 空格停止并锁定 · Q退出").pack()
        ttk.Label(root, text="点击本窗口再用英文输入法按键；相机预览窗口不接收点动。\n方向是命令轴正负，尚未验证对应画面方向；STOP不能代替实物急停。",
                  justify="center").pack(pady=8)
        if controller.config.real:
            ttk.Label(root, text="实物急停/行程尚未验收。仅由现场操作者按键短步测试。\n点动后正式位置参考失效；此窗口不控制水泵。",
                      foreground="#a02516", justify="center").pack(pady=8)
        self.button = ttk.Button(root, text="启用点动" if controller.config.real else "启用模拟按键", command=self._arm)
        self.button.pack(pady=8)
        self.status = tk.StringVar(value="LOCKED：先点击启用")
        ttk.Label(root, textvariable=self.status).pack()
        self.log = tk.Text(root, height=10, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=15, pady=8)
        root.bind("<KeyPress>", self._press)
        root.bind("<KeyRelease>", self._release)
        root.bind("<FocusOut>", self._focus_out)
        root.protocol("WM_DELETE_WINDOW", self._close)
        self.poll_id = root.after(40, self._poll)
        if controller.config.enable:
            # Explicit startup authorization requests connection/readiness only.
            # It never synthesizes keys, a move request, or hardware acceptance.
            root.after_idle(self._arm)

    def _arm(self):
        self.controller.arm(operator_authorized=True)

    def _press(self, event):
        key = event.keysym.lower()
        # Ctrl+A in the log, Alt shortcuts, and system chords are not jogs.
        # Shift/CapsLock remain usable for uppercase WASD. STOP stays available.
        if key in KEYS and event.state & (0x4 | 0x8 | 0x40 | 0x80 | 0x20000):
            return
        pending = self.releases.pop(key, None)
        if pending is not None:
            self.root.after_cancel(pending)
            # X11 auto-repeat synthesizes release/press; retain held status.
        if key == "q":
            self._close()
        else:
            self.controller.press(key)
        if key in KEYS or key in {"space", "q"}:
            return "break"

    def _release(self, event):
        key = event.keysym.lower()
        if key not in self.releases:
            self.releases[key] = self.root.after(40, lambda: self._finish_release(key))

    def _finish_release(self, key):
        self.releases.pop(key, None)
        self.controller.release(key)

    def _focus_out(self, event):
        self.root.after_idle(self._check_focus)

    def _check_focus(self):
        if self.closing:
            return
        focused = self.root.focus_displayof()
        if focused is None or focused.winfo_toplevel() != self.root:
            self.controller.stop()
            # Keep held keys across focus loss. A held key must never become a
            # fresh authorization just because the operator clicks Enable again.
            for timer in self.releases.values():
                self.root.after_cancel(timer)
            self.releases.clear()
            self.controller._emit("若启用无响应，请在本窗口松开WASD；失焦不清除按住状态")

    def _close(self):
        self.closing = True
        self.button.configure(state="disabled")
        self.controller.close()

    def _poll(self):
        self.status.set(self.controller.state + (" · 正在退出" if self.closing else ""))
        while True:
            try:
                message = self.controller.events.get_nowait()
            except queue.Empty:
                break
            self.log.configure(state="normal")
            self.log.insert("end", str(message) + "\n")
            if int(self.log.index("end-1c").split(".")[0]) > 300:
                self.log.delete("1.0", "100.0")
            self.log.see("end")
            self.log.configure(state="disabled")
        if self.closing and not self.controller.thread.is_alive():
            for timer in self.releases.values():
                self.root.after_cancel(timer)
            self.root.destroy()
            return
        self.poll_id = self.root.after(40, self._poll)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="WASD独立维护点动；默认模拟，无COM。真实模式由现场操作者按键。")
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--port", help="人工明确指定端口；不扫描")
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--enable", action="store_true", help="操作者明确请求启动时启用；仅连接和握手，不自动移动，失焦/STOP后仍须手动重启")
    try:
        return JogConfig(**vars(parser.parse_args(argv)))
    except ValueError as exc:
        parser.error(str(exc))


def main(argv=None):
    config = parse_args(argv)
    import tkinter as tk
    root = tk.Tk()
    controller = JogController(config)
    try:
        JogWindow(root, controller)
        root.mainloop()
    finally:
        controller.close()
        controller.thread.join(timeout=config.timeout * 3 + 1)
    return 0
