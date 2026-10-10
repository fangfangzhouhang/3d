"""实物闭环的同一个窗口：实时显微镜、第一次画面标注、中文说明和确认。

OpenCV 的文字不能写中文，所以说明放在这个窗口的文字区。确认、不确认和空格抓图
都在这里完成，不用回到 Cursor 终端。
"""

from __future__ import annotations

import base64
import time


class StationPanel:
    """主线程上的一个窗口。按钮只记下选择，真正的动作仍由原来的确认点决定。"""

    def __init__(self) -> None:
        import tkinter as tk

        self.tk = tk
        self.root = tk.Tk()
        self.root.title("显微清洗")
        self.font = ("Microsoft YaHei UI", 12)
        self.small = ("Microsoft YaHei UI", 10)
        self._closed = False
        self._waiting = "keys"
        self._answer: bool | None = None
        self._dismissed = False
        self._keys: list[int] = []
        self._last_live = 0.0
        self.roster_locked = False
        self._right_source = ""
        self._live_photo = None
        self._roster_photo = None

        images = tk.Frame(self.root)
        images.pack(fill="both", expand=True, padx=8, pady=8)
        left = tk.Frame(images)
        right = tk.Frame(images)
        left.pack(side="left", padx=4)
        right.pack(side="left", padx=4)
        tk.Label(left, text="实时显微镜", font=self.font).pack()
        tk.Label(right, text="第一次画面的标注", font=self.font).pack()
        self.live_label = tk.Label(left, text="正在打开相机", font=self.small, width=42, height=12)
        self.roster_label = tk.Label(right, text="抓到第一帧之后，标注会留在这里", font=self.small, width=42, height=12)
        self.live_label.pack()
        self.roster_label.pack()

        self.status = tk.Label(self.root, text="正在准备", font=self.font, anchor="w")
        self.status.pack(fill="x", padx=8)
        self.log = tk.Text(self.root, height=9, wrap="word", font=self.font, takefocus=0)
        self.log.pack(fill="both", expand=True, padx=8, pady=4)
        self.log.configure(state="disabled")

        bar = tk.Frame(self.root)
        bar.pack(fill="x", padx=8, pady=8)
        self.yes_button = tk.Button(bar, text="确认（Y）", font=self.font, width=14, command=lambda: self._decide(True))
        self.no_button = tk.Button(bar, text="不确认（N）", font=self.font, width=14, command=lambda: self._decide(False))
        self.yes_button.pack(side="left", padx=4)
        self.no_button.pack(side="left", padx=4)
        tk.Label(bar, text="抓图按空格，结束按 Q。不用回到 Cursor 终端。", font=self.small).pack(side="left", padx=8)
        self._set_buttons()
        self.root.bind_all("<Key>", self._on_key)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._focus()
        self._pump()

    def write(self, message: str, *, echo: bool = True) -> None:
        text = str(message).rstrip()
        if echo:
            print(text, flush=True)
        if self._closed or self.root is None:
            return
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self._pump()

    def set_status(self, text: str) -> None:
        if self._closed or self.root is None:
            return
        self.status.configure(text=text)
        self._set_buttons()
        self._pump()

    def show_frame(self, name: str, image) -> None:
        if self._closed or image is None:
            return
        title = str(name).lower()
        if "path" in title:
            if self.roster_locked:
                self._pump()
                return
            self._set_photo(self.roster_label, image, "_roster_photo")
            self._right_source = "path"
        else:
            now = time.monotonic()
            if now - self._last_live < 0.08:
                self._pump()
                return
            self._last_live = now
            self._set_photo(self.live_label, image, "_live_photo")
        self._pump()

    def show_roster(self, image) -> None:
        self.roster_locked = True
        self._right_source = "roster"
        self._set_photo(self.roster_label, image, "_roster_photo")
        self.set_status("右边是第一次画面的标注。这次只处理这些污渍。")
        self._pump()

    def ask(self, prompt: str, refresh=None) -> bool:
        self.write(prompt)
        self.write("确认或 Y 批准。不确认或 N 不批准。不用回到 Cursor 终端。")
        self._waiting = "yesno"
        self._answer = None
        self.set_status("现在在这个窗口确认。确认或 Y 才批准。不确认或 N 不批准。")
        self._focus()
        while self._answer is None and not self._closed:
            if refresh is not None:
                refresh()
            else:
                self._pump()
            if self._answer is None and not self._closed:
                time.sleep(0.02)
        self._waiting = "keys"
        self.set_status("显微镜画面")
        return self._answer is True

    def wait_key(self, delay_ms: int) -> int:
        if self._keys:
            return self._keys.pop(0)
        if self._closed:
            return ord("q")
        deadline = time.monotonic() + max(int(delay_ms), 1) / 1000.0
        while time.monotonic() < deadline:
            self._pump()
            if self._keys:
                return self._keys.pop(0)
            time.sleep(0.01)
        return -1

    def wait_dismiss(self, seconds: float = 8) -> None:
        self._waiting = "dismiss"
        self._dismissed = False
        self.set_status("这一次结束。点窗口关闭或按 Q。")
        self._focus()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self._closed and not self._dismissed:
            self._pump()
            if self._keys:
                key = self._keys.pop(0)
                if key in {ord("q"), ord("Q"), 27}:
                    break
            time.sleep(0.02)
        self.close()

    def close(self) -> None:
        self._live_photo = None
        self._roster_photo = None
        root, self.root = self.root, None
        self._closed = True
        if root is not None:
            try:
                root.destroy()
            except self.tk.TclError:
                pass

    def _decide(self, accepted: bool) -> None:
        if self._waiting != "yesno" or self._answer is not None:
            return
        self._answer = bool(accepted)

    def _on_key(self, event):
        keysym = (event.keysym or "").lower()
        if self._waiting == "yesno":
            if keysym == "y":
                self._decide(True)
            elif keysym == "n":
                self._decide(False)
            return "break"
        if keysym == "space":
            self._keys.append(32)
        elif keysym == "q":
            self._keys.append(ord("q"))
        elif keysym == "escape":
            self._keys.append(27)
            if self._waiting == "dismiss":
                self._dismissed = True
        return "break"

    def _on_close(self) -> None:
        self._closed = True
        self._dismissed = True
        if self._answer is None:
            self._answer = False
        self._keys.append(ord("q"))
        if self.root is not None:
            try:
                self.root.withdraw()
            except self.tk.TclError:
                pass

    def _set_buttons(self) -> None:
        state = "normal" if self._waiting == "yesno" else "disabled"
        self.yes_button.configure(state=state)
        self.no_button.configure(state=state)

    def _focus(self) -> None:
        if self._closed or self.root is None:
            return
        try:
            self.root.lift()
            self.root.focus_force()
        except self.tk.TclError:
            self._closed = True

    def _pump(self) -> None:
        if self._closed or self.root is None:
            return
        try:
            self.root.update_idletasks()
            self.root.update()
        except self.tk.TclError:
            self._closed = True

    def _set_photo(self, label, image, attr: str) -> None:
        import cv2

        fitted = _fit(image)
        okay, encoded = cv2.imencode(".png", fitted)
        if not okay:
            return
        payload = base64.b64encode(encoded.tobytes()).decode("ascii")
        try:
            photo = self.tk.PhotoImage(data=payload)
        except self.tk.TclError:
            rgb = cv2.cvtColor(fitted, cv2.COLOR_BGR2RGB)
            height, width = rgb.shape[:2]
            photo = self.tk.PhotoImage(width=width, height=height)
            photo.put(" ".join(
                "{" + " ".join(f"#{red:02x}{green:02x}{blue:02x}" for red, green, blue in row) + "}"
                for row in rgb
            ))
        setattr(self, attr, photo)
        label.configure(image=photo, text="", width=0, height=0)


def _fit(image, max_w: int = 520, max_h: int = 390):
    import cv2

    height, width = image.shape[:2]
    scale = min(max_w / max(width, 1), max_h / max(height, 1), 1.0)
    if scale == 1:
        return image
    return cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))))


class WorkbenchWindow:
    """新的 mainloop 工作台；旧 StationPanel 仍服务旧入口和兼容测试。"""

    PAGE_ORDER = ("cover", "detection", "review", "monitor", "results")

    def __init__(self, runtime):
        import gc
        gc.collect()  # 先在主线程回收先前已关闭的 Tk 测试/窗口。
        import tkinter as tk
        from tkinter import ttk
        from demo.workbench_views import BG, CARD, INK, MUTED, TEAL, PALE, FONT
        from demo.workbench_views import CoverPage, DetectionPage, ReviewPage, MonitorPage, ResultsPage, button
        self.tk, self.runtime = tk, runtime
        import sys
        if sys.platform == "win32":
            import ctypes
            try:
                ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            except (AttributeError, OSError):
                pass
        self.root = tk.Tk()
        scale = min(self.root.winfo_fpixels("1i") / 96, max(1.0, self.root.winfo_screenheight() / 900))
        self.scale = scale
        self.root.tk.call("tk", "scaling", 4 / 3 * scale)
        self.root.title("MicroCleaningVision · 显微清洗工作台")
        width, height = int(min(1420*scale, self.root.winfo_screenwidth()-70)), int(min(960*scale, self.root.winfo_screenheight()-170))
        self.root.geometry(f"{width}x{height}+30+20")
        self.root.minsize(int(min(1100*scale, width)), int(min(760*scale, height)))
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TButton", font=(FONT, 10), padding=(10, 7), background=BG, foreground=INK, borderwidth=0)
        style.map("TButton", background=[("active", PALE)], foreground=[("disabled", "#9aa9a7")])
        style.configure("Primary.TButton", background=TEAL, foreground="white", padding=(14, 8))
        style.map("Primary.TButton", background=[("disabled", "#c2d0cc"), ("active", "#11645f")], foreground=[("disabled", "#6c8580")])
        style.configure("Treeview", font=(FONT, 10), rowheight=int(29*scale), background=CARD, fieldbackground=CARD, foreground=INK)
        style.configure("Treeview.Heading", font=(FONT, 10, "bold"), background=BG, foreground=INK)
        style.map("Treeview", background=[("selected", TEAL)], foreground=[("selected", "white")])
        style.configure("TEntry", padding=6, font=(FONT, 10), fieldbackground=BG, bordercolor="#b8cec8", lightcolor="#b8cec8", darkcolor="#b8cec8", borderwidth=1)
        style.configure("TCombobox", padding=5, font=(FONT, 10))
        style.configure("Horizontal.TProgressbar", background=TEAL, troughcolor=PALE, borderwidth=0)
        self.sample, self.operator, self.note = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.task, self.snapshot, self.reference = None, None, None
        self.current_page, self.current_phase, self.current_target = "cover", "WELCOME", None
        self.request_id, self.capture_ready = None, False
        self.camera_state, self.serial_state, self.pump_state = "尚未连接", "尚未探测", "尚未探测"
        self.closing, self.restart_requested = False, False
        self.pair_window, self.last_report, self.result_image_id = None, None, None
        self.heartbeat_count = 0
        self.destroyed = False
        outer = tk.Frame(self.root, bg=BG, padx=20, pady=12)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(3, weight=1)
        outer.columnconfigure(0, weight=1)
        header = tk.Frame(outer, bg=BG)
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(header, text="MicroCleaningVision", bg=BG, fg=INK, font=("Segoe UI", 19, "bold")).pack(side="left")
        mode = "REAL · 实物模式" if runtime.args.real else "MOCK · 软件模拟"
        tk.Label(header, text=f"  WORKBENCH v2   /   {mode}", bg=BG, fg=TEAL, font=(FONT, 10)).pack(side="left", padx=12)
        self.stop = button(header, "停止 / 取消任务  [Q]", lambda: runtime.submit("cancel"))
        self.stop.pack(side="right")
        self.stop.state(["disabled"])
        self.device = tk.Label(outer, text="相机：尚未连接　|　COM：尚未探测　|　XY：人工参考待确认　|　泵：尚未探测", bg=BG, fg=MUTED,
            font=(FONT, 9), anchor="w")
        self.device.grid(row=1, column=0, sticky="ew", pady=(7, 10))
        progress = tk.Frame(outer, bg=BG)
        progress.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        navigation = tk.Frame(progress, bg=BG)
        navigation.pack(fill="x")
        self.nav = {}
        for page, caption in zip(self.PAGE_ORDER, ("封面", "01 实时检测", "02 目标审核", "03 清洗监控", "04 结果报告")):
            control = button(navigation, caption, lambda page=page: self.navigate(page))
            control.pack(side="left", padx=(0, 7))
            self.nav[page] = control
        self.progress_text = tk.Label(navigation, text="准备新任务", bg=BG, fg=TEAL, font=(FONT, 10, "bold"), anchor="e")
        self.progress_text.pack(side="right", fill="x", expand=True)
        self.progress = ttk.Progressbar(progress, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(7, 0))
        self.phase_animation = ttk.Progressbar(progress, mode="indeterminate", maximum=100, length=120)
        self.phase_animation.pack(fill="x", pady=(3, 0))
        self.pages = tk.Frame(outer, bg=CARD)
        self.pages.grid(row=3, column=0, sticky="nsew")
        self.pages.rowconfigure(0, weight=1)
        self.pages.columnconfigure(0, weight=1)
        self.views = {name: view(self) for name, view in zip(self.PAGE_ORDER, (CoverPage, DetectionPage, ReviewPage, MonitorPage, ResultsPage))}
        for view in self.views.values():
            view.grid(row=0, column=0, sticky="nsew")
        self.confirm_card = tk.Frame(outer, bg="#fff2d9", padx=12, pady=9)
        self.confirm_text = tk.Label(self.confirm_card, text="", bg="#fff2d9", fg=INK, anchor="w", justify="left", font=(FONT, 11), wraplength=1020)
        self.confirm_text.pack(side="left", fill="x", expand=True)
        self.confirm_yes = button(self.confirm_card, "确认这一步", lambda: self.answer(True), primary=True)
        self.confirm_yes.pack(side="right", padx=(7, 0))
        self.confirm_no = button(self.confirm_card, "拒绝这一步", lambda: self.answer(False))
        self.confirm_no.pack(side="right")
        log_area = tk.Frame(outer, bg=BG)
        log_area.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        log_bar = tk.Frame(log_area, bg=BG)
        log_bar.pack(fill="x")
        tk.Label(log_bar, text="中文解释日志", bg=BG, fg=MUTED, font=(FONT, 9, "bold")).pack(side="left")
        self.log_toggle = button(log_bar, "收起日志", self.toggle_log)
        self.log_toggle.pack(side="right")
        self.log = tk.Text(log_area, height=4, bg="#e4ece8", fg=INK, font=(FONT, 9), relief="flat", padx=10, pady=6, state="disabled", wrap="word")
        self.log.pack(fill="x", pady=5)
        self.log_visible = True
        self.root.bind("<space>", lambda event: self._shortcut(event, "capture"))
        self.root.bind("<Key-q>", lambda event: self._shortcut(event, "cancel"))
        self.root.bind("<Escape>", lambda event: runtime.submit("cancel") if runtime.active else None)
        self.navigate("cover")
        self._pump()

    def _shortcut(self, event, action):
        if self.root.focus_get() is not None and self.root.focus_get().winfo_class() in {"TEntry", "Entry", "Text", "TCombobox"}:
            return
        if action == "capture":
            self.capture()
        elif self.runtime.active:
            self.runtime.submit(action)

    def navigate(self, page):
        if page == "review" and self.task is None:
            return
        if page == "results" and self.snapshot is None:
            return
        if page == "monitor" and self.task is None:
            return
        if page == "detection" and self.reference is None and not self.runtime.active:
            return
        self.current_page = page
        for name, view in self.views.items():
            if name != page:
                view.grid_remove()
        self.views[page].grid()
        self.views[page].tkraise()
        for key, control in self.nav.items():
            control.configure(style="Primary.TButton" if key == page else "TButton")
        self._refresh_nav()

    def _refresh_nav(self):
        allowed = {"cover"}
        if self.runtime.active or self.reference is not None:
            allowed.add("detection")
        if self.task is not None:
            allowed.update(("review", "monitor"))
        if self.snapshot is not None:
            allowed.add("results")
        for name, control in self.nav.items():
            control.state(["!disabled"] if name in allowed else ["disabled"])

    def start_task(self):
        if self.runtime.active:
            self.navigate("review" if self.task is not None and self.task.get("state") in {"DRAFT", "LOCKED"} else "monitor" if self.task is not None else "detection")
            return
        self.task, self.snapshot, self.reference, self.current_target = None, None, None, None
        self.views["review"].selected = None
        self.views["review"].canvas.set_image(None)
        self.views["review"].canvas.manual_enabled = False
        self.views["review"].manual.config(text="人工补框：关闭")
        self.views["monitor"].reference.set_image(None)
        self.views["monitor"].live.set_image(None)
        self.views["detection"].canvas.set_image(None)
        self.views["detection"].message.config(text="相机准备中…")
        self.progress.stop()
        self.progress.configure(mode="indeterminate")
        self.progress.start(18)
        self.progress_text.config(text="正在准备显微视野")
        self.stop.state(["!disabled"])
        if self.runtime.start({"sample_id": self.sample.get().strip(), "operator": self.operator.get().strip()}):
            self.views["cover"].start.config(text="返回当前任务  →")
            self.navigate("detection")
            self.write("开始新任务。请采集首次图像；此时不会发送运动或喷洗指令。")

    def capture(self):
        if self.capture_ready:
            self.capture_ready = False
            self.runtime.submit("capture")
            self._capture_buttons(False)

    def _capture_buttons(self, available):
        for key in ("detection", "monitor"):
            self.views[key].capture.state(["!disabled"] if available else ["disabled"])

    def decide(self, decision):
        stable = self.views["review"].selected
        if stable:
            self.runtime.submit("decision", target_id=stable, decision=decision, reason=self.note.get().strip())

    def manual_add(self, bbox):
        if self.task is not None and self.task.get("state") == "DRAFT":
            self.runtime.submit("manual_add", bbox=bbox, reason=self.note.get().strip())

    def begin(self):
        self.views["review"].begin.state(["disabled"])
        self.runtime.submit("begin")
        self.write("提交执行请求。锁定名单不替代每次运动和短喷的人工确认。")

    def recapture(self):
        if self.task and self.task.get("state") == "DRAFT":
            self.restart_requested = True
            self.runtime.submit("cancel", reason="SUPERSEDED_BY_RECAPTURE")
            self.write("结束当前草稿并留档；设备释放后创建新的采集轮次。")

    def answer(self, value):
        identity, self.request_id = self.request_id, None
        if identity:
            self.runtime.submit("confirm", request_id=identity, answer=value)
            self.confirm_yes.state(["disabled"])
            self.confirm_no.state(["disabled"])

    def write(self, message):
        from datetime import datetime
        self.log.configure(state="normal")
        self.log.insert("end", datetime.now().strftime("%H:%M:%S") + "  " + str(message) + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 600:
            self.log.delete("1.0", "100.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log.pack(fill="x", pady=5)
        else:
            self.log.pack_forget()
        self.log_toggle.config(text="收起日志" if self.log_visible else "展开日志")

    def _task_update(self, task):
        if task is None:
            return
        self.task = task
        self._refresh_nav()
        self.views["review"].update_task(task)
        targets = task.get("targets") or {}
        self.views["monitor"].reference.set_targets(targets, active=self.current_target)
        approved = task.get("execution_ids") or [key for key, item in targets.items() if item.get("decision") == "APPROVED"]
        finished = sum(targets[key].get("finished", False) for key in approved)
        self.progress_text.config(text=f"{finished}/{len(approved)} 已处理" + (f" · 当前 {self.current_target}" if self.current_target else ""))
        if task.get("state") != "DRAFT":
            self.progress.stop()
            self.progress.configure(mode="determinate", value=100 * finished / len(approved) if approved else 0)

    def _event(self, event):
        payload, kind = event.payload, event.kind
        if "task" in payload:
            self._task_update(payload["task"])
        if kind == "log":
            self.write(payload["message"])
        elif kind == "error":
            from microcleaning.control_system.reporting.quality_report import explain
            self.write("操作未完成：" + explain(payload["message"]))
            self.progress_text.config(text="操作未完成，请查看中文日志")
        elif kind == "device":
            self.camera_state, self.serial_state = payload["camera"], payload["serial"]
            self._device_update()
            if "失败" in str(payload["camera"]) or "读不到" in str(payload["camera"]):
                self.views["detection"].message.config(text="显微镜画面没有读到。看下方日志。关闭本窗口后重新运行。")
        elif kind == "capture_ready":
            self.capture_ready = True
            self._capture_buttons(True)
            self.views["detection"].message.config(text="实时画面已准备。按空格或点击采集，冻结用于本轮审核的图像。")
            self.views["monitor"].current.config(text=f"{self.current_target or '首次检测'} · 等待采集{'后' if payload['phase']=='post' else '前'}图。按空格或点击采集。")
        elif kind == "capture_closed":
            self.capture_ready = False
            self._capture_buttons(False)
        elif kind == "capture_instruction":
            self.write(payload["text"])
        elif kind == "reference":
            if self.reference is None:
                self.reference = payload["image"]
                self.views["monitor"].reference.set_image(self.reference)
        elif kind == "candidates":
            self.reference = payload["image"]
            self.views["review"].canvas.set_image(self.reference)
            self.views["monitor"].reference.set_image(self.reference)
            self.navigate("review")
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self.write("首次检测完成。全部候选默认为待审核，未确认者不会执行。")
        elif kind == "stage":
            self.current_phase = payload["phase"]
            self.current_target = payload.get("current_target_id")
            self.views["monitor"].current.config(text=f"{self.current_target or '任务'} · {payload['explanation']}")
            if self.task:
                self.views["monitor"].reference.set_targets(self.task["targets"], active=self.current_target)
            if self.current_phase == "MOVE":
                self.navigate("monitor")
            self.phase_animation.stop()
            if self.current_phase in {"MOVE", "PUMP", "RETURN", "SEGMENT_PRE", "SEGMENT_POST", "VERIFY_TARGET", "PROBE_DEVICE"}:
                self.phase_animation.start(22)
            if self.current_phase in {"CAPTURE_PRE", "SEGMENT_PRE", "CAPTURE_POST", "SEGMENT_POST", "VERIFY_TARGET"}:
                self.views["monitor"].current.config(fg="#167f77")
            else:
                self.views["monitor"].current.config(fg="#a46a19")
        elif kind == "confirmation":
            self.phase_animation.stop()
            self.request_id = payload["request_id"]
            self.confirm_text.config(text=payload["prompt"])
            self.confirm_card.grid(row=4, column=0, sticky="ew", pady=(9, 0))
            self.confirm_yes.state(["!disabled"])
            self.confirm_no.state(["!disabled"])
            self.write("等待人工确认：" + payload["prompt"])
        elif kind == "confirmation_closed":
            if self.request_id in {None, payload["request_id"]}:
                self.request_id = None
                self.confirm_card.grid_remove()
            if self.pair_window is not None:
                self._release_photos(self.pair_window)
                self.pair_window.destroy()
                self.pair_window = None
        elif kind == "motion":
            message = payload["message"]
            self.views["monitor"].feedback.config(text=("发送请求：" if payload["phase"] == "tx" else "板子回复：") + message + "   （脉冲计数，非编码器）")
            if payload["phase"] == "rx":
                self.serial_state = "已收到 STEP 协议回复"
                self._device_update()
            if message.startswith("MOVEXY") or message in {"STEP_STOPPED", "STOP"}:
                self.write("运动：" + message)
        elif kind == "serial":
            line = payload["line"]
            if payload["direction"] == "rx_partial":
                self.write("收到不完整串口字节（未作为成功回执）：" + line)
                return
            if line.startswith("MCV1|PUMP|"):
                self.pump_state = "请求短喷，等待回执"
            elif line.startswith("MCV1|DONE|"):
                self.pump_state = "输出结束回执" if line != "MCV1|DONE|STOP" else "STOP 已确认"
            elif line.startswith("MCV1|STATUS|"):
                self.pump_state = "急停生效" if "ESTOP=1" in line else "STATUS 急停未激活"
                self.serial_state = "已收到 MCV1 STATUS"
            self._device_update()
            self.write(("发送请求：" if payload["direction"] == "tx" else "收到回执：") + line)
        elif kind == "verification":
            from microcleaning.control_system.reporting.quality_report import label
            self.write(f"{payload['target_id']} 复检：{label(payload['quality_evidence']['quality'])}；原算法去除率 {payload['verification'].get('removal_rate')}。")
        elif kind == "pair":
            self.runtime.pair_images(payload)
        elif kind == "pair_images":
            self._comparison_popup(payload["pre"], payload["post"], "前后视野确认 · 确认按钮仍在主窗口")
        elif kind == "stopping":
            self.progress_text.config(text="STOPPING · 正在收尾，停止回执待确认")
            self.stop.state(["disabled"])
            self.write(payload["explanation"])
        elif kind == "completed":
            self.write(f"任务结束：{payload['summary']['status']}；原始记录：{payload['folder']}。报告等待你选择导出。")
        elif kind == "idle":
            self.stop.state(["disabled"])
            self.views["cover"].start.config(text="开始新任务  →")
            if self.restart_requested:
                self.restart_requested = False
                self.root.after(50, self.start_task)
            elif self.closing and not self.runtime.report_busy:
                self._destroy()
                return
        elif kind == "results":
            if self.restart_requested or self.runtime.active:
                return
            self.snapshot = payload["snapshot"]
            self.views["results"].update_snapshot(self.snapshot)
            self.navigate("results")
        elif kind == "result_images":
            if self.snapshot and payload["folder"] == self.snapshot["folder"] and payload["target_id"] == self.views["results"].selected_id:
                self.views["results"].pre.set_image(payload["pre"])
                self.views["results"].post.set_image(payload["post"])
        elif kind == "report_busy":
            self.views["results"].export_state.config(text="正在后台生成报告…" if payload["busy"] else "已导出：" + self.last_report if self.last_report else "报告操作已结束。原始记录保留。")
            for control in self.views["results"].export_buttons:
                control.state(["disabled"] if payload["busy"] else ["!disabled"])
            if self.closing and not payload["busy"] and not self.runtime.active:
                self._destroy()
        elif kind == "report_ready":
            self.last_report = payload["folder"]
            self.views["results"].export_state.config(text="已导出：" + self.last_report)
            self.write("报告已导出：" + self.last_report)

    def _device_update(self):
        self.device.config(text=f"相机：{self.camera_state}　|　COM：{self.serial_state}　|　XY：READXY 脉冲计数　|　泵：{self.pump_state}")

    def _pump(self):
        if self.destroyed:
            return
        self.heartbeat_count += 1
        for event in self.runtime.bus.drain():
            self._event(event)
            if self.destroyed:
                return
        image = self.runtime.bus.latest_frame()
        if image is not None:
            try:
                if self.current_page == "detection":
                    self.views["detection"].canvas.set_image(image)
                    self.views["detection"].message.config(text="显微画面已打开。确认视野和焦点后按空格采集。")
                    if self.progress_text.cget("text") == "正在准备显微视野":
                        self.progress.stop()
                        self.progress_text.config(text="显微视野已打开")
                elif self.current_page == "monitor":
                    self.views["monitor"].live.set_image(image)
            except Exception:
                self.write("这一帧没有画上窗口，继续读下一帧。")
        self.root.after(40, self._pump)

    def result_target(self):
        if self.snapshot is None:
            return None
        identity = self.views["results"].selected_id
        return next((row for row in self.snapshot["targets"] if row["target_id"] == identity), None)

    def result_images(self, masks=False):
        target = self.result_target()
        if target:
            self.runtime.comparison_images(self.snapshot["folder"], target, masks=masks)

    def quality_review(self):
        target = self.result_target()
        if target is None:
            return
        last = target["attempts"][-1] if target["attempts"] else {}
        references = [last[key] for key in ("pre_image", "post_image") if last.get(key)]
        self.runtime.quality_review(self.snapshot["folder"], target_id=target["target_id"], operator=self.operator.get().strip(),
            conclusion=self.views["results"].review_options[self.views["results"].review_value.get()],
            reason=self.views["results"].review_note.get().strip(), evidence_refs=references)

    def export(self, formats, printing=False):
        if self.snapshot is not None and not self.runtime.active:
            self.runtime.report(self.snapshot["folder"], formats, open_after="html" in formats, print_after=printing)

    def choose_history(self):
        from tkinter import filedialog
        if self.runtime.active:
            self.write("设备任务运行中；任务结束后可打开历史结果。返回查看当前锁定名单不会重新发令。")
            return
        folder = filedialog.askdirectory(parent=self.root, title="选择包含 summary.json 的任务目录", initialdir=str(self.runtime.args.output_root.resolve()))
        if folder:
            self.runtime.load_results(folder)

    def open_evidence(self):
        import os
        if self.snapshot is not None:
            os.startfile(self.snapshot["folder"])

    def _comparison_popup(self, pre, post, title):
        from demo.workbench_views import ImageCanvas, BG, INK, FONT
        if self.pair_window is not None:
            self._release_photos(self.pair_window)
            self.pair_window.destroy()
        popup = self.tk.Toplevel(self.root)
        popup.title(title)
        popup.geometry("1000x520")
        popup.configure(bg=BG)
        popup.columnconfigure(0, weight=1)
        popup.columnconfigure(1, weight=1)
        popup.rowconfigure(1, weight=1)
        for column, caption, image in ((0, "处理前", pre), (1, "处理后", post)):
            self.tk.Label(popup, text=caption, bg=BG, fg=INK, font=(FONT, 12, "bold")).grid(row=0, column=column, pady=10)
            canvas = ImageCanvas(popup, height=400)
            canvas.grid(row=1, column=column, sticky="nsew", padx=12, pady=(0, 12))
            canvas.set_image(image)
        self.pair_window = popup
        popup.protocol("WM_DELETE_WINDOW", lambda: (self._release_photos(popup), popup.destroy(), setattr(self, "pair_window", None)))

    def zoom_result(self):
        target = self.result_target()
        if target is None:
            return
        bbox = (target.get("instance") or {}).get("bbox")
        images = []
        for canvas in (self.views["results"].pre, self.views["results"].post):
            image = canvas.image
            if image is not None and bbox:
                x, y, w, h = map(int, bbox)
                image = image[max(0, y-15):y+h+15, max(0, x-15):x+w+15]
            images.append(image)
        self._comparison_popup(*images, title=f"{target['target_id']} · 区域放大")

    def close(self):
        if self.runtime.active:
            self.closing = True
            self.runtime.submit("cancel", reason="WINDOW_CLOSE_REQUESTED")
            self.write("关闭请求已收到。等待设备线程收尾和证据保存后关闭窗口。")
        elif self.runtime.report_busy:
            self.closing = True
            self.write("等待正在导出的报告收尾后关闭窗口。")
        else:
            self._destroy()

    def _destroy(self):
        import gc
        self.destroyed = True
        self.phase_animation.stop()
        self._release_photos(self.root)
        self.root.destroy()
        # Tk 图片/变量的析构必须留在主线程，不能等下一次设备线程触发循环 GC。
        for view in self.views.values():
            view.owner = None
        self.views.clear()
        gc.collect()

    def _release_photos(self, widget):
        for child in widget.winfo_children():
            self._release_photos(child)
        if hasattr(widget, "photo"):
            widget.photo = None
        if hasattr(widget, "on_select"):
            widget.on_select = None
            widget.on_manual = None
