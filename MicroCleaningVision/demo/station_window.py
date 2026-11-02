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

    PAGE_ORDER = ("cover", "detection", "review", "monitor", "results", "jog")

    def __init__(self, runtime):
        import gc
        gc.collect()  # 先在主线程回收先前已关闭的 Tk 测试/窗口。
        import tkinter as tk
        from tkinter import ttk
        from demo.workbench_views import BG, CARD, INK, MUTED, TEAL, PALE, FONT
        from demo.workbench_views import CoverPage, DetectionPage, ReviewPage, MonitorPage, ResultsPage, JogPage, button
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
        width, height = int(min(1420*scale, self.root.winfo_screenwidth()-70)), int(min(980*scale, self.root.winfo_screenheight()-70))
        self.root.geometry(f"{width}x{height}+30+20")
        self.root.minsize(int(min(960*scale, width)), int(min(680*scale, height)))
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
        self.result_folder, self.archive_states = None, {}
        self.data_save_window = None
        from demo.workbench_coverage import WorkbenchCoverageHub
        self.coverage = WorkbenchCoverageHub(self)
        self.current_page, self.current_phase, self.current_target = "cover", "WELCOME", None
        self.request_id, self.capture_ready = None, False
        self.answer_mode, self.choice_values, self.choice_facts = "confirmation", (), {}
        self.choice_reason = tk.StringVar()
        self.result_image_nonce = 0
        self.camera_state, self.serial_state, self.pump_state = "尚未连接", "尚未探测", "尚未探测"
        self.closing, self.restart_requested = False, False
        self._close_started, self._close_after = None, None
        self.pair_window, self.last_report, self.result_image_id = None, None, None
        self.heartbeat_count = 0
        self.destroyed = False
        self._pump_after = None
        outer = tk.Frame(self.root, bg=BG, padx=20, pady=12)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(3, weight=1)
        outer.columnconfigure(0, weight=1)
        header = tk.Frame(outer, bg=BG)
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(header, text="MicroCleaningVision", bg=BG, fg=INK, font=("Segoe UI", 19, "bold")).pack(side="left")
        mode = ("REAL · 真实设备·仅连接" if getattr(runtime, "connection_only", False)
            else "REAL · 实物模式" if runtime.args.real else "MOCK · 软件模拟")
        tk.Label(header, text=f"  WORKBENCH v3   /   {mode}", bg=BG, fg=TEAL, font=(FONT, 10)).pack(side="left", padx=12)
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
        for page, caption in zip(self.PAGE_ORDER, ("封面", "01 实时检测", "02 目标审核", "03 清洗监控", "04 结果报告", "05 手动点动")):
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
        self.views = {name: view(self) for name, view in zip(self.PAGE_ORDER, (CoverPage, DetectionPage, ReviewPage, MonitorPage, ResultsPage, JogPage))}
        for view in self.views.values():
            view.grid(row=0, column=0, sticky="nsew")
        self.confirm_card = tk.Frame(outer, bg="#fff2d9", padx=12, pady=9)
        self.confirm_text = tk.Label(self.confirm_card, text="", bg="#fff2d9", fg=INK, anchor="w", justify="left", font=(FONT, 11), wraplength=1020)
        self.confirm_text.pack(fill="x", expand=True)
        self.confirm_text.bind("<Configure>", lambda event: self.confirm_text.config(wraplength=max(200, event.width-8)))
        self.normal_confirmation = tk.Frame(self.confirm_card, bg="#fff2d9")
        self.normal_confirmation.pack(fill="x", pady=(5, 0))
        self.confirm_yes = button(self.normal_confirmation, "确认这一步", lambda: self.answer(True), primary=True)
        self.confirm_yes.pack(side="right", padx=(7, 0))
        self.confirm_no = button(self.normal_confirmation, "拒绝这一步", lambda: self.answer(False))
        self.confirm_no.pack(side="right")
        self.choice_frame = tk.Frame(self.confirm_card, bg="#fff2d9")
        self.choice_buttons = {}
        for column, (value, caption) in enumerate((("rewash", "再洗一次"), ("next", "不复洗，继续下一污渍"),
                                                  ("retake", "仅重拍复检"), ("pause", "暂停并保存"))):
            self.choice_frame.columnconfigure(column, weight=1)
            control = button(self.choice_frame, caption, lambda value=value: self.answer(value), primary=value == "rewash")
            control.grid(row=0, column=column, sticky="ew", padx=3, pady=4)
            self.choice_buttons[value] = control
        self.choice_reason_label = tk.Label(self.choice_frame, text="人工复核原因（最终人工通过时必填）", bg="#fff2d9", fg=INK, font=(FONT, 9))
        self.choice_reason_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=3)
        self.choice_reason_entry = ttk.Entry(self.choice_frame, textvariable=self.choice_reason)
        self.choice_reason_entry.grid(row=1, column=2, columnspan=2, sticky="ew", padx=3, pady=4)
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
        if getattr(runtime, "connection_only", False):
            self.root.title("MicroCleaningVision · 真实设备·仅连接")
            self.pump_state = "未武装；仅连接模式"
            for control in (self.views["review"].begin, self.views["review"].lock,
                    self.views["review"].all, *self.views["review"].edit_buttons,
                    self.confirm_yes, self.confirm_no, *self.choice_buttons.values()):
                control.state(["disabled"])
            self._device_update()
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
        if getattr(self.runtime, "connection_only", False) and page not in {"cover", "detection", "jog"}:
            return
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
        allowed = {"cover", "jog"}
        if self.runtime.active or self.reference is not None:
            allowed.add("detection")
        if self.task is not None:
            allowed.update(("review", "monitor"))
        if self.snapshot is not None:
            allowed.add("results")
        if getattr(self.runtime, "connection_only", False):
            allowed.intersection_update({"cover", "detection", "jog"})
        for name, control in self.nav.items():
            control.state(["!disabled"] if name in allowed else ["disabled"])

    def start_task(self):
        if self.runtime.active:
            self.navigate("review" if self.task is not None and self.task.get("state") in {"DRAFT", "LOCKED"} else "monitor" if self.task is not None else "detection")
            return
        self.task, self.snapshot, self.reference, self.current_target = None, None, None, None
        self.coverage.reset()
        self.views["review"].selected = None
        self.views["review"].canvas.set_image(None)
        self.views["review"].canvas.manual_enabled = False
        self.views["review"].manual.config(text="人工补框：关闭")
        self.views["monitor"].reset()
        self.views["detection"].canvas.set_image(None)
        self.views["detection"].message.config(text="相机准备中…")
        self.progress.stop()
        self.progress.configure(mode="indeterminate")
        self.progress.start(18)
        self.progress_text.config(text="正在准备显微视野")
        self.stop.state(["!disabled"])
        if self.runtime.start({"sample_id": self.sample.get().strip(), "operator": self.operator.get().strip()}):
            self.result_folder = getattr(self.runtime, "folder", None)
            self.last_report = None
            self.views["results"].update_archive({"status": "pending", "local_saved": False})
            self.views["cover"].start.config(text="返回当前任务  →")
            self.navigate("detection")
            self.write("开始新任务。请采集首次图像；此时不会发送运动或喷洗指令。")

    def coverage_select(self, target):
        self.coverage.select(target)

    def coverage_open(self, *, latest=False):
        target = self.current_target if latest else self.views["review"].selected
        if target and self.coverage.open(target, latest=latest):
            self.write(f"{target}：打开模拟覆盖预览，参数与驻点均不会送到执行器。")

    def coverage_changed(self):
        if not hasattr(self, "views") or self.destroyed:
            return
        review, monitor = self.views["review"], self.views["monitor"]
        if self.reference is not None:
            review.canvas.set_image(self.reference)
        for view, target, latest in ((review, review.selected, False), (monitor, self.current_target, True)):
            source = self.coverage.get(target, latest=latest)
            view.coverage_button.state(["!disabled"] if source is not None else ["disabled"])
            view.coverage_text.config(text="模拟覆盖：选择目标后打开，直径需显式输入" if source is None else
                f"{target} · {source.binding['stage']} · {source.dataset.input.mask_kind} · 仅模拟，尚未预测")
        preview, source = self.coverage.preview, self.coverage.source
        if preview is not None and source is not None and preview[0] == source.identity:
            _, plan, rendered = preview
            metrics = rendered.metrics
            rate = "无法预测" if metrics is None or metrics.geometric_coverage_rate is None else f"{metrics.geometric_coverage_rate:.1%}"
            note = "；输入位置/可比性未通过" if not source.binding["input_valid"] else ""
            text = (f"{source.binding['stage']} / {source.binding['frame_id']} · 模拟 {plan.model.profile.evidence.status} · 预计几何 {rate} · "
                f"驻点 {rendered.step_count}/{len(plan.stations)} · 遗漏 {metrics.uncovered_area_px if metrics else '未知'} px{note}")
            if review.selected == source.binding["target_id"] and source.binding["stage"] == "initial":
                review.coverage_text.config(text=text)
                review.canvas.set_image(rendered.image_rgb[..., ::-1])
            latest = self.coverage.get(self.current_target, latest=True)
            if latest is not None and latest.identity == source.identity:
                monitor.coverage_text.config(text=text)

    def capture(self):
        if self.capture_ready:
            self.capture_ready = False
            self.runtime.submit("capture")
            self._capture_buttons(False)

    def _capture_buttons(self, available):
        for key in ("detection", "monitor"):
            if key == "monitor" and getattr(self.runtime, "connection_only", False):
                available_for_page = False
            else:
                available_for_page = available
            self.views[key].capture.state(["!disabled"] if available_for_page else ["disabled"])

    def decide(self, decision):
        stable = self.views["review"].selected
        if stable:
            self.runtime.submit("decision", target_id=stable, decision=decision, reason=self.note.get().strip())

    def manual_add(self, bbox):
        if self.task is not None and self.task.get("state") == "DRAFT":
            self.runtime.submit("manual_add", bbox=bbox, reason=self.note.get().strip())

    def begin(self):
        if getattr(self.runtime, "connection_only", False):
            self.write("仅连接模式不提供清洗执行。")
            return
        self.views["review"].begin.state(["disabled"])
        self.runtime.submit("begin")
        self.write("提交执行请求。锁定名单不替代每次运动和短喷的人工确认。")

    def recapture(self):
        if self.task and self.task.get("state") == "DRAFT":
            self.restart_requested = True
            self.runtime.submit("cancel", reason="SUPERSEDED_BY_RECAPTURE")
            self.write("结束当前草稿并留档；设备释放后创建新的采集轮次。")

    def answer(self, value):
        if self.answer_mode == "recheck_choice":
            if value not in self.choice_values or self.choice_buttons[value].instate(["disabled"]):
                return
            if value == "next" and self.choice_facts.get("require_reason") and not self.choice_reason.get().strip():
                self.write("最终人工通过需要填写中文复核依据；未提交决定，也不会发送动作。")
                self.choice_reason_entry.focus_set()
                return
        elif not isinstance(value, bool):
            return
        identity, self.request_id = self.request_id, None
        if identity:
            arguments = {"request_id": identity, "answer": value}
            if self.answer_mode == "recheck_choice":
                arguments["reason"] = self.choice_reason.get().strip()
            self.runtime.submit("confirm", **arguments)
            self.confirm_yes.state(["disabled"])
            self.confirm_no.state(["disabled"])
            for control in self.choice_buttons.values():
                control.state(["disabled"])

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

    def _matches_task(self, payload):
        from demo.workbench_runtime import task_key
        key = payload.get("task_key")
        if key is None:
            return True  # legacy synchronous/device events
        expected = self.result_folder
        if expected is None and self.snapshot is not None:
            expected = self.snapshot["folder"]
        return expected is not None and key == task_key(expected)

    def _close_when_ready(self):
        if not self.closing or self.destroyed or self.runtime.active:
            return  # physical safety cleanup is never cut short by archive timeouts
        from demo.workbench_runtime import ARCHIVE_CLOSE_GRACE_SECONDS
        import time
        if self._close_started is None:
            self._close_started = time.monotonic()
        elapsed = time.monotonic() - self._close_started
        busy = getattr(self.runtime, "archive_busy", False)
        if not busy and not self.runtime.report_busy:
            self._destroy()
            return
        if elapsed >= ARCHIVE_CLOSE_GRACE_SECONDS:
            stop = getattr(self.runtime, "request_archive_stop", None)
            if stop is not None:
                stop()
            self.progress_text.config(text="归档等待超时，正在终止后台归档；本地原始档案保留")
        if elapsed >= ARCHIVE_CLOSE_GRACE_SECONDS + 2.0:
            self.write("共享归档未确认完成，稍后须独立校验/重新归档。原始任务状态不变。")
            self._destroy()
            return
        if self._close_after is None:
            def poll():
                self._close_after = None
                self._close_when_ready()
            self._close_after = self.root.after(50, poll)

    def _event(self, event):
        from demo.workbench_runtime import task_key
        payload, kind = event.payload, event.kind
        if getattr(self.runtime, "connection_only", False):
            if kind in {"candidates", "task", "confirmation", "recheck_choice", "stage",
                    "motion", "position", "geometry", "verification"} or "task" in payload:
                self.write("仅连接模式忽略执行流程事件：" + kind)
                return
            if kind == "connection_device":
                self.camera_state = payload.get("camera", self.camera_state)
                self.serial_state = payload.get("serial", self.serial_state)
                self.pump_state = payload.get("pump", self.pump_state)
                self._device_update()
                return
            if kind == "connection_capture":
                result = payload["result"]
                self.write("已保存采图/识别：" + result["files"]["raw"]["path"]
                    + "；面积 " + str(result["measurement"]["area_px"]) + " px；不会执行清洗。")
                return
        if kind == "archive_status":
            self.archive_states[payload["task_key"]] = payload
            if self._matches_task(payload):
                self.views["results"].update_archive(payload)
            dialog = getattr(self, "data_save_window", None)
            if dialog is not None and not dialog.closed and payload["task_key"] == task_key(dialog.folder):
                dialog.update_archive(payload)
            self._close_when_ready()
            return
        if payload.get("task_key") is not None and not self._matches_task(payload):
            if kind == "report_busy":
                self.views["results"].refresh_exports()
            self._close_when_ready()
            return
        if "task" in payload:
            self._task_update(payload["task"])
        if kind == "log":
            self.write(payload["message"])
        elif kind == "coverage_source":
            self.coverage.ingest(payload["source"])
        elif kind == "coverage_issue":
            self.write(payload["message"] + "；旧执行流程继续由原关卡决定。")
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
            if getattr(self.runtime, "connection_only", False):
                self.views["detection"].message.config(text="真实设备·仅连接：空格只采图和识别；移动、喷水禁用。")
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
            self.coverage_changed()
        elif kind == "stage":
            self.current_phase = payload["phase"]
            self.current_target = payload.get("current_target_id")
            self.coverage_changed()
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
            self.answer_mode = "confirmation"
            self.log.config(height=4)
            self.choice_frame.pack_forget()
            self.normal_confirmation.pack(fill="x", pady=(5, 0))
            self.confirm_text.config(text=payload["prompt"])
            self.confirm_card.grid(row=4, column=0, sticky="ew", pady=(9, 0))
            self.confirm_yes.state(["!disabled"])
            self.confirm_no.state(["!disabled"])
            self.write("等待人工确认：" + payload["prompt"])
        elif kind == "recheck_choice":
            self.phase_animation.stop()
            self.request_id, self.answer_mode = payload["request_id"], "recheck_choice"
            self.choice_values = tuple(payload.get("choices") or ())
            self.choice_facts = dict(payload.get("facts") or {})
            self.choice_reason.set("")
            self.log.config(height=2)
            self.normal_confirmation.pack_forget()
            self.confirm_yes.state(["disabled"])
            self.confirm_no.state(["disabled"])
            self.choice_frame.pack(fill="x", pady=(5, 0))
            last = self.choice_facts.get("last_target")
            self.choice_buttons["next"].config(text="不复洗，人工确认通过" if last else "不复洗，继续下一污渍")
            for key, control in self.choice_buttons.items():
                allowed = key in self.choice_values
                if key == "next" and last and not self.choice_facts.get("passed") and not self.choice_facts.get("manual_accept_allowed"):
                    allowed = False
                control.state(["!disabled"] if allowed else ["disabled"])
            self.confirm_text.config(text=payload["prompt"])
            self.confirm_card.grid(row=4, column=0, sticky="ew", pady=(9, 0))
            self.navigate("monitor")
            self.write("复检结果已出，请人工选择复洗、继续、重拍或暂停。每次复洗仍重新授权移动与短喷。")
        elif kind == "confirmation_closed":
            if self.request_id in {None, payload["request_id"]}:
                self.request_id = None
                self.confirm_card.grid_remove()
            if self.pair_window is not None:
                self._release_photos(self.pair_window)
                self.pair_window.destroy()
                self.pair_window = None
        elif kind == "motion":
            from demo.workbench_views import motion_chinese
            message = payload["message"]
            translated = motion_chinese(message)
            self.views["monitor"].feedback.config(text=translated + "   （由脉冲估算，物理位移需验证）")
            if payload["phase"] == "rx":
                self.serial_state = "已收到 STEP 协议回复"
                self._device_update()
            if message.startswith("MOVEXY") or message in {"STEP_STOPPED", "STOP"}:
                self.write(translated)
        elif kind == "position":
            self.views["monitor"].update_position(payload)
            self._device_update()
        elif kind == "geometry":
            self.views["monitor"].update_geometry(payload)
        elif kind == "serial":
            from demo.workbench_views import motion_chinese
            line = payload["line"]
            if payload["direction"] == "rx_partial":
                self.write("收到不完整串口字节，不能作为成功回执；原字节保留在诊断记录。")
                return
            if line.startswith("MCV1|PUMP|"):
                self.pump_state = "请求短喷，等待回执"
            elif line.startswith("MCV1|DONE|"):
                self.pump_state = "输出结束回执" if line != "MCV1|DONE|STOP" else "STOP 已确认"
            elif line.startswith("MCV1|STATUS|"):
                self.pump_state = "急停生效" if "ESTOP=1" in line else "STATUS 急停未激活"
                self.serial_state = "已收到 MCV1 STATUS"
            self._device_update()
            self.write(motion_chinese(line))
        elif kind == "verification":
            from microcleaning.control_system.reporting.quality_report import label
            self.views["monitor"].update_verification(payload)
            rate = (payload.get("roi_analysis") or {}).get("removal_rate", payload["verification"].get("removal_rate"))
            self.write(f"{payload['target_id']} 复检：{label(payload['quality_evidence']['quality'])}；同位置区域清洗率 {'无法计算' if rate is None else f'{rate:.2%}'}。")
        elif kind == "pair":
            self.runtime.pair_images(payload)
        elif kind == "pair_images":
            self._comparison_popup(payload["pre"], payload["post"], "前后视野确认 · 确认按钮仍在主窗口")
        elif kind == "stopping":
            self.progress_text.config(text="正在收尾，停止回执待确认")
            self.stop.state(["disabled"])
            self.write(payload["explanation"])
        elif kind == "completed":
            from microcleaning.control_system.reporting.quality_report import label
            self.write(f"任务结束：{label(payload['summary']['status'])}；原始记录：{payload['folder']}。报告等待你选择导出。")
        elif kind == "idle":
            self.stop.state(["disabled"])
            self.views["cover"].start.config(text="开始新任务  →")
            if getattr(self.runtime, "connection_only", False):
                self.views["cover"].start.config(text="重新连接设备  →")
                self.progress.stop()
                self.progress_text.config(text="设备连接已结束")
            if self.restart_requested:
                self.restart_requested = False
                self.root.after(50, self.start_task)
            elif self.closing:
                self._close_when_ready()
                return
        elif kind == "results":
            if self.restart_requested:
                return
            self.snapshot = payload["snapshot"]
            self.result_folder = self.snapshot["folder"]
            self.views["results"].update_snapshot(self.snapshot)
            from demo.workbench_runtime import task_key
            getter = getattr(self.runtime, "archive_status", lambda folder: {})
            state = getter(self.result_folder) or self.archive_states.get(task_key(self.result_folder)) or {
                "status": "not_configured" if getattr(self.runtime, "dataset_root", None) is None else "unknown"}
            self.views["results"].update_archive(state)
            if payload.get("auto_navigate", True) and not self.runtime.active:
                self.navigate("results")
            else:
                self._refresh_nav()
                self.write("末目标尚未复检通过或任务暂停；保留监控页。可显式打开结果页查看完整档案。")
        elif kind == "result_images":
            if (self.snapshot and payload["folder"] == self.snapshot["folder"]
                and payload["target_id"] == self.views["results"].selected_id
                and payload.get("nonce") == self.result_image_nonce):
                self.views["results"].pre.set_image(payload["pre"])
                self.views["results"].post.set_image(payload["post"])
        elif kind == "report_busy":
            self.views["results"].export_state.config(text="正在后台生成报告…" if payload["busy"] else "已导出：" + self.last_report if self.last_report else "报告操作已结束。原始记录保留。")
            self.views["results"].refresh_exports(busy=payload["busy"])
            self._close_when_ready()
        elif kind == "report_ready":
            self.last_report = payload["folder"]
            self.views["results"].export_state.config(text="已导出：" + self.last_report)
            self.write("报告已导出：" + self.last_report)

    def _device_update(self):
        status = self.views["monitor"].position_data.get("status")
        xy = {"READY": "累计步数账本可用", "MOVING": "移动估算中", "POSITION_UNCERTAIN": "位置不可信", "UNHOMED": "参考待建立"}.get(status, "人工参考待确认")
        if getattr(self.runtime, "connection_only", False):
            xy = "禁用（不建立零点）"
        self.device.config(text=f"相机：{self.camera_state}　|　COM：{self.serial_state}　|　XY：{xy}　|　泵：{self.pump_state}")

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
                    self.views["detection"].message.config(text="真实设备·仅连接：空格只采图和识别；移动、喷水禁用。"
                        if getattr(self.runtime, "connection_only", False)
                        else "显微画面已打开。确认视野和焦点后按空格采集。")
                    if self.progress_text.cget("text") == "正在准备显微视野":
                        self.progress.stop()
                        self.progress_text.config(text="显微视野已打开")
                elif self.current_page == "monitor":
                    self.views["monitor"].live.set_image(image)
            except Exception:
                self.write("这一帧没有画上窗口，继续读下一帧。")
        self._pump_after = self.root.after(40, self._pump)

    def result_target(self):
        if self.snapshot is None:
            return None
        identity = self.views["results"].selected_id
        return next((row for row in self.snapshot["targets"] if row["target_id"] == identity), None)

    def result_images(self, masks=False, *, crop=False):
        target = self.result_target()
        if target:
            self.result_image_nonce += 1
            self.views["results"].pre.set_image(None, placeholder="正在读取本轮前图")
            self.views["results"].post.set_image(None, placeholder="正在读取本轮后图")
            self.runtime.comparison_images(self.snapshot["folder"], target, masks=masks, crop=crop,
                attempt_index=self.views["results"].attempt_index, recheck_index=self.views["results"].recheck_index,
                nonce=self.result_image_nonce)

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
            if printing and not self.views["results"].print_ready():
                self.write("末目标尚未复检通过，不能打印完成报告；可以导出全部原始档案。")
                return
            self.runtime.report(self.snapshot["folder"], formats, open_after="html" in formats, print_after=printing)

    def choose_history(self):
        from tkinter import filedialog
        if self.runtime.active:
            self.write("设备任务运行中；任务结束后可打开历史结果。返回查看当前锁定名单不会重新发令。")
            return
        folder = filedialog.askdirectory(parent=self.root, title="选择包含 summary.json 的任务目录", initialdir=str(self.runtime.args.output_root.resolve()))
        if folder:
            self.result_folder = folder
            self.runtime.load_results(folder)

    def show_data_save(self):
        from demo.workbench_runtime import task_key
        if self.snapshot is None or self.runtime.active:
            self.write("任务结束且原始档案保存后，才可以选择正式保留。")
            return
        dialog = self.data_save_window
        if dialog is not None and not dialog.closed:
            if task_key(dialog.folder) == task_key(self.snapshot["folder"]):
                dialog.window.lift()
                return
            dialog.close()
        from demo.workbench_views import DataSaveDialog
        self.data_save_window = DataSaveDialog(self)

    def open_data_location(self, folder, location):
        # A cloud-backed explorer path may stall; never wait on the Tk thread.
        import os
        import threading
        def open_location():
            try:
                os.startfile(location)
            except Exception as exc:
                self.runtime._task_event("error", folder, message=f"打开目录失败：{exc}")
        threading.Thread(target=open_location, name="mcv-open-data", daemon=True).start()

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
        elif self.runtime.report_busy or getattr(self.runtime, "archive_busy", False):
            self.closing = True
            self.write("设备已收尾。短暂等待后台归档，超时将终止归档并关闭；本地原始档案保留。")
            self._close_when_ready()
        else:
            self._destroy()

    def _destroy(self):
        import gc
        self.destroyed = True
        stop = getattr(self.runtime, "request_archive_stop", None)
        if stop is not None:
            stop()
        if self._close_after is not None:
            self.root.after_cancel(self._close_after)
            self._close_after = None
        dialog = getattr(self, "data_save_window", None)
        if dialog is not None:
            dialog.close()
        self.coverage.close()
        if self._pump_after is not None:
            self.root.after_cancel(self._pump_after)
            self._pump_after = None
        self.progress.stop()
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
