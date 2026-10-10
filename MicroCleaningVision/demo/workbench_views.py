"""独立页面和原像素坐标画布；本模块只在 Tk 主线程使用。"""

from __future__ import annotations

import base64
import tkinter as tk
from tkinter import ttk

from microcleaning.control_system.reporting.quality_report import explain, label


BG = "#edf3f1"
CARD = "#ffffff"
INK = "#173d3e"
MUTED = "#627c79"
TEAL = "#167f77"
PALE = "#deeeea"
AMBER = "#d48a24"
FONT = "Microsoft YaHei UI"


def heading(parent, title, subtitle=""):
    tk.Label(parent, text=title, bg=CARD, fg=INK, font=(FONT, 21, "bold"), anchor="w").pack(fill="x", pady=(0, 5))
    if subtitle:
        tk.Label(parent, text=subtitle, bg=CARD, fg=MUTED, font=(FONT, 10), anchor="w").pack(fill="x", pady=(0, 15))


def button(parent, text, command, *, primary=False):
    return ttk.Button(parent, text=text, command=command, style="Primary.TButton" if primary else "TButton")


class PixelTransform:
    def __init__(self, image_width, image_height, canvas_width, canvas_height):
        self.w, self.h = image_width, image_height
        self.scale = min(canvas_width / image_width, canvas_height / image_height)
        self.x = (canvas_width - image_width * self.scale) / 2
        self.y = (canvas_height - image_height * self.scale) / 2

    def to_pixel(self, x, y):
        px, py = (x - self.x) / self.scale, (y - self.y) / self.scale
        if not 0 <= px < self.w or not 0 <= py < self.h:
            return None
        return px, py

    def to_canvas(self, x, y):
        return self.x + x * self.scale, self.y + y * self.scale


class ImageCanvas(tk.Canvas):
    def __init__(self, parent, *, select=None, manual=None, height=370):
        super().__init__(parent, bg="#112b2d", highlightthickness=0, height=height, width=430)
        self.image, self.targets, self.selected, self.active = None, {}, None, None
        self.photo, self.transform = None, None
        self.on_select, self.on_manual = select, manual
        self.manual_enabled, self._start, self._drag = False, None, None
        self.placeholder = "等待显微图像"
        self.bind("<Configure>", lambda _: self.redraw())
        self.bind("<Map>", lambda _: self.redraw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)

    def set_image(self, image, placeholder="图像未记录"):
        self.image = image
        self.placeholder = placeholder
        self.redraw()

    def set_targets(self, targets, selected=None, active=None):
        self.targets, self.selected, self.active = targets, selected, active
        self.redraw()

    def redraw(self):
        if not self.winfo_ismapped():
            return
        self.delete("all")
        w, h = max(1, self.winfo_width()), max(1, self.winfo_height())
        if self.image is None:
            self.create_text(w / 2, h / 2, text=self.placeholder, fill="#a8c9c3", font=(FONT, 12))
            self.transform = None
            return
        import cv2
        ih, iw = self.image.shape[:2]
        self.transform = PixelTransform(iw, ih, w, h)
        fitted = cv2.resize(self.image, (max(1, round(iw * self.transform.scale)), max(1, round(ih * self.transform.scale))), interpolation=cv2.INTER_AREA)
        okay, encoded = cv2.imencode(".png", fitted, [cv2.IMWRITE_PNG_COMPRESSION, 1])
        if not okay:
            return
        payload = base64.b64encode(encoded.tobytes()).decode("ascii")
        try:
            self.photo = tk.PhotoImage(master=self, data=payload)
        except tk.TclError:
            rgb = cv2.cvtColor(fitted, cv2.COLOR_BGR2RGB)
            height, width = rgb.shape[:2]
            self.photo = tk.PhotoImage(master=self, width=width, height=height)
            self.photo.put(" ".join(
                "{" + " ".join(f"#{red:02x}{green:02x}{blue:02x}" for red, green, blue in row) + "}"
                for row in rgb
            ))
        self.create_image(w / 2, h / 2, image=self.photo)
        for stable, target in self.targets.items():
            instance = target.get("instance") or {}
            bbox = instance.get("bbox")
            if not bbox:
                continue
            x, y, width, height = bbox
            a, b = self.transform.to_canvas(x - 3, y - 3)
            c, d = self.transform.to_canvas(x + width + 3, y + height + 3)
            current = stable == self.active
            color = "#ffc15a" if current else "#6ef6d2" if stable == self.selected else "#8dbfb6"
            if target.get("decision") == "EXCLUDED":
                color = "#a6aeb0"
            self.create_rectangle(a, b, c, d, outline=color, width=3 if current or stable == self.selected else 1,
                                  dash=(5, 3) if target.get("decision") == "EXCLUDED" else ())
            self.create_text(a, max(12, b - 11), text=stable + ("  ◉ 当前清洗" if current else ""), fill=color,
                             anchor="w", font=(FONT, 10, "bold" if current else "normal"))

    def _press(self, event):
        if self.transform is None:
            return
        point = self.transform.to_pixel(event.x, event.y)
        if point is None:
            return
        if self.manual_enabled:
            self._start = point
            self._drag = self.create_rectangle(event.x, event.y, event.x, event.y, outline="#ffc15a", width=2)
            return
        hits = []
        for stable, target in self.targets.items():
            bbox = (target.get("instance") or {}).get("bbox")
            if bbox:
                x, y, w, h = bbox
                if x - 4 <= point[0] <= x + w + 4 and y - 4 <= point[1] <= y + h + 4:
                    hits.append((w * h, stable))
        if hits and self.on_select:
            self.on_select(min(hits)[1])

    def _motion(self, event):
        if self._start is not None and self._drag is not None:
            x, y = self.transform.to_canvas(*self._start)
            self.coords(self._drag, x, y, event.x, event.y)

    def _release(self, event):
        if self._start is None:
            return
        end = self.transform.to_pixel(event.x, event.y)
        start, self._start = self._start, None
        if self._drag:
            self.delete(self._drag)
            self._drag = None
        if end is None or not self.on_manual:
            return
        x, y = int(min(start[0], end[0])), int(min(start[1], end[1]))
        right, bottom = int(max(start[0], end[0])) + 1, int(max(start[1], end[1])) + 1
        if right - x >= 2 and bottom - y >= 2:
            self.on_manual((x, y, right - x, bottom - y))


class Page(tk.Frame):
    def __init__(self, owner):
        super().__init__(owner.pages, bg=CARD, padx=24, pady=20)
        self.owner = owner


class CoverPage(Page):
    def __init__(self, owner):
        super().__init__(owner)
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(0, weight=1)
        left = tk.Frame(self, bg=CARD, padx=14)
        left.grid(row=0, column=0, sticky="nsew")
        tk.Label(left, text="MICRO / CLEANING / VISION", fg=TEAL, bg=CARD, font=("Segoe UI", 12, "bold"), anchor="w").pack(fill="x", pady=(42, 22))
        tk.Label(left, text="从一帧显微图，\n到一份清洗证据。", fg=INK, bg=CARD, font=(FONT, 31, "bold"), anchor="w", justify="left").pack(fill="x")
        tk.Label(left, text="观察 → 审核 → 逐目标清洗 → 复检\n每一步看得清楚，每个决定都有记录。", fg=MUTED, bg=CARD,
            font=(FONT, 12), anchor="w", justify="left").pack(fill="x", pady=22)
        form = tk.Frame(left, bg=CARD)
        form.pack(fill="x", pady=10)
        for row, (caption, variable) in enumerate((("样品编号", owner.sample), ("操作员", owner.operator))):
            tk.Label(form, text=caption, bg=CARD, fg=MUTED, font=(FONT, 10)).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=9)
            ttk.Entry(form, textvariable=variable, width=32).grid(row=row, column=1, sticky="w", pady=9)
        actions = tk.Frame(left, bg=CARD)
        actions.pack(fill="x", pady=20)
        self.start = button(actions, "开始检测  →", owner.start_task, primary=True)
        self.start.pack(side="left", padx=(0, 12))
        button(actions, "打开历史任务", owner.choose_history).pack(side="left")
        self.note = tk.Label(left, text="Mock 模式只使用合成图像和串口替身。", bg=CARD, fg=MUTED, wraplength=530, justify="left", anchor="w", font=(FONT, 10))
        self.note.pack(fill="x", pady=15)
        if owner.runtime.args.real:
            self.note.config(text="Real 模式：相机和 COM 必须人工指定；运动、短喷、回程仍分别确认。设备状态以实际探测为准。")
        art = tk.Canvas(self, bg="#e4efeb", highlightthickness=0)
        art.grid(row=0, column=1, sticky="nsew", padx=(15, 0), pady=12)
        art.bind("<Configure>", lambda event: self._art(art, event.width, event.height))

    def _art(self, canvas, w, h):
        canvas.delete("all")
        for x in range(25, w, 25):
            for y in range(25, h, 25):
                canvas.create_oval(x, y, x + 2, y + 2, fill="#bfd5cc", outline="")
        cx, cy = w / 2, h * .43
        radius = min(w * .33, h * .28)
        canvas.create_oval(cx-radius, cy-radius, cx+radius, cy+radius, outline="#6bafa0", width=2)
        canvas.create_oval(cx-radius*.72, cy-radius*.72, cx+radius*.72, cy+radius*.72, fill="#d2e7de", outline="")
        for dx, dy, r in ((-.25, -.15, 12), (.3, .05, 8), (.02, .32, 6)):
            canvas.create_oval(cx+radius*dx-r, cy+radius*dy-r, cx+radius*dx+r, cy+radius*dy+r, fill="#d9a94f", outline="")
        canvas.create_line(cx-radius*1.15, cy, cx+radius*1.15, cy, fill="#4d9d8e", dash=(5, 5))
        canvas.create_line(cx, cy-radius*1.15, cx, cy+radius*1.15, fill="#4d9d8e", dash=(5, 5))
        canvas.create_text(cx, h*.78, text="看见 · 决定 · 执行 · 验证", fill=TEAL, font=(FONT, 14, "bold"))
        canvas.create_text(cx, h*.84, text="MICROSCOPE  /  MOTION  /  EVIDENCE", fill=MUTED, font=("Segoe UI", 9))


class DetectionPage(Page):
    def __init__(self, owner):
        super().__init__(owner)
        heading(self, "01  实时检测", "先观察显微画面，确认视野与焦点，再采集首次图像。")
        self.canvas = ImageCanvas(self, height=460)
        self.canvas.pack(fill="both", expand=True)
        self.message = tk.Label(self, text="相机准备中…", bg=CARD, fg=MUTED, font=(FONT, 11), anchor="w")
        self.message.pack(fill="x", pady=12)
        actions = tk.Frame(self, bg=CARD)
        actions.pack(fill="x")
        button(actions, "← 返回封面", lambda: owner.navigate("cover")).pack(side="left")
        self.capture = button(actions, "采集首次图像并识别  [空格]", owner.capture, primary=True)
        self.capture.pack(side="right")
        self.capture.state(["disabled"])


class ReviewPage(Page):
    def __init__(self, owner):
        super().__init__(owner)
        heading(self, "02  目标审核", "图像与列表双向选择。只把已确认且定位有效的目标锁定到执行名单。")
        columns = tk.Frame(self, bg=CARD)
        columns.pack(fill="both", expand=True)
        columns.columnconfigure(0, weight=5)
        columns.columnconfigure(1, weight=6)
        columns.rowconfigure(0, weight=1)
        left, right = tk.Frame(columns, bg=CARD), tk.Frame(columns, bg=CARD)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 15))
        right.grid(row=0, column=1, sticky="nsew")
        self.canvas = ImageCanvas(left, select=self.select, manual=owner.manual_add, height=330)
        self.canvas.pack(fill="both", expand=True)
        self.manual = button(left, "人工补框：关闭", self.toggle_manual)
        self.manual.pack(anchor="w", pady=9)
        self.tree = ttk.Treeview(right, columns=("source", "area", "decision"), show="tree headings", selectmode="browse", height=7)
        for key, title, width in (("#0", "编号", 85), ("source", "来源", 90), ("area", "像素面积", 90), ("decision", "操作决定", 110)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=round(width * owner.scale), minwidth=round(width * owner.scale), stretch=key != "#0")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._selected)
        edits = tk.Frame(right, bg=CARD)
        edits.pack(fill="x", pady=8)
        self.edit_buttons = []
        for text, value in (("确认处理", "APPROVED"), ("排除", "EXCLUDED"), ("待复核 / 撤销", "PENDING_REVIEW")):
            control = button(edits, text, lambda value=value: owner.decide(value))
            control.pack(side="left", padx=(0, 7))
            self.edit_buttons.append(control)
        self.all = button(right, "确认全部有效目标", lambda: owner.runtime.submit("approve_all"))
        self.all.pack(anchor="w", pady=(0, 8))
        self.reason = ttk.Entry(right, textvariable=owner.note)
        self.reason.pack(fill="x")
        tk.Label(right, text="备注 / 排除或复核原因（可填）", bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x", pady=4)
        self.detail = tk.Label(right, text="选择目标查看坐标与动作预览。", bg=BG, fg=INK, font=(FONT, 10),
            anchor="nw", justify="left", wraplength=460, padx=12, pady=9)
        self.detail.pack(fill="x", pady=(5, 0))
        self.detail.bind("<Configure>", lambda event: self.detail.config(wraplength=max(200, event.width - 24)))
        self.counts = tk.Label(self, text="", bg=CARD, fg=TEAL, anchor="w", font=(FONT, 10, "bold"))
        self.counts.pack(side="bottom", fill="x", pady=10, before=columns)
        actions = tk.Frame(self, bg=CARD)
        actions.pack(side="bottom", fill="x", before=self.counts)
        button(actions, "← 返回检测", lambda: owner.navigate("detection")).pack(side="left", padx=(0, 10))
        self.recapture = button(actions, "重新采集识别", owner.recapture)
        self.recapture.pack(side="left")
        self.begin = button(actions, "开始执行  →", owner.begin, primary=True)
        self.begin.pack(side="right", padx=(10, 0))
        self.lock = button(actions, "确认并锁定名单", lambda: owner.runtime.submit("lock"))
        self.lock.pack(side="right")
        self.selected = None

    def toggle_manual(self):
        self.canvas.manual_enabled = not self.canvas.manual_enabled
        self.manual.config(text="人工补框：拖拽首次图像" if self.canvas.manual_enabled else "人工补框：关闭")

    def select(self, stable):
        if self.tree.exists(stable):
            self.tree.selection_set(stable)
            self.tree.see(stable)

    def _selected(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        self.selected = selection[0]
        task = self.owner.task or {}
        targets = task.get("targets") or {}
        item = targets.get(self.selected, {})
        self.canvas.set_targets(targets, selected=self.selected)
        instance, plan = item.get("instance") or {}, item.get("plan") or {}
        geometry = plan.get("geometry") or {}
        reason = "；".join(explain(code) for code in (item.get("eligibility_reasons") or ())) or "定位预览有效；实际动作仍经过原安全关卡"
        centroid = instance.get("centroid_px")
        point = "未记录" if not centroid else f"({centroid[0]:.2f}, {centroid[1]:.2f})"
        action = "定点短喷" if plan.get("action") == "CENTER_POINT" else "扫描动作（本版不执行）" if plan.get("action") == "RASTER" else "未记录"
        self.detail.config(text=f"{self.selected} · {label(item.get('source'))}\n原图处理点 {point} px；框 {instance.get('bbox')}\n"
            f"{action}；请求短喷 {plan.get('pump_duration_ms', '未记录')} ms\n去程 {geometry.get('outbound', {}).get('lines', [])}\n回程 {geometry.get('returning', {}).get('lines', [])}\n{reason}")
        self.owner.note.set(item.get("note", ""))

    def update_task(self, task):
        targets = task.get("targets") or {}
        for item in self.tree.get_children():
            if item not in targets:
                self.tree.delete(item)
        for stable, item in targets.items():
            values = (label(item["source"]), f"{item['instance']['area_px']:.0f}" + (" 区域" if item["source"] == "manual" else ""), label(item["decision"]))
            if self.tree.exists(stable):
                self.tree.item(stable, values=values)
            else:
                self.tree.insert("", "end", iid=stable, text=stable, values=values)
        editable = task["state"] == "DRAFT"
        approved = sum(item["decision"] == "APPROVED" for item in targets.values())
        pending = sum(item["decision"] == "PENDING_REVIEW" for item in targets.values())
        excluded = sum(item["decision"] == "EXCLUDED" for item in targets.values())
        self.counts.config(text=f"共 {len(targets)} 个候选 · 确认 {approved} · 排除 {excluded} · 待复核 {pending}    |    状态：{label(task['state'])}")
        for control in self.edit_buttons + [self.manual, self.all, self.recapture, self.reason]:
            control.state(["!disabled"] if editable else ["disabled"])
        self.lock.state(["!disabled"] if editable and approved else ["disabled"])
        self.begin.state(["!disabled"] if task["state"] == "LOCKED" else ["disabled"])
        if not editable:
            self.canvas.manual_enabled = False
            self.manual.config(text="人工补框：名单已锁定")
        self.canvas.set_targets(targets, self.selected)
        if not self.selected and targets:
            self.select(next(iter(targets)))


class MonitorPage(Page):
    def __init__(self, owner):
        super().__init__(owner)
        heading(self, "03  清洗监控", "左侧保留首次检测参考，右侧持续显示显微画面。当前 S 编号以金色高亮。")
        self.current = tk.Label(self, text="等待执行", bg=PALE, fg=INK, anchor="w", padx=12, pady=10, font=(FONT, 13, "bold"))
        self.current.pack(fill="x", pady=(0, 10))
        images = tk.Frame(self, bg=CARD)
        images.pack(fill="both", expand=True)
        images.columnconfigure(0, weight=1)
        images.columnconfigure(1, weight=1)
        images.rowconfigure(1, weight=1)
        for column, caption in ((0, "首次检测 · 冻结参考"), (1, "实时显微图 · 设备当前画面")):
            tk.Label(images, text=caption, bg=CARD, fg=MUTED, anchor="w", font=(FONT, 10)).grid(row=0, column=column, sticky="ew", pady=5)
        self.reference = ImageCanvas(images, height=310)
        self.live = ImageCanvas(images, height=310)
        self.reference.grid(row=1, column=0, sticky="nsew", padx=(0, 7))
        self.live.grid(row=1, column=1, sticky="nsew", padx=(7, 0))
        self.feedback = tk.Label(self, text="计划位置与 READXY 脉冲计数会在这里显示；物理位移需现场验证。", bg=CARD,
            fg=MUTED, anchor="w", font=(FONT, 10))
        self.feedback.pack(fill="x", pady=8)
        actions = tk.Frame(self, bg=CARD)
        actions.pack(fill="x")
        button(actions, "← 查看锁定名单", lambda: owner.navigate("review")).pack(side="left")
        self.capture = button(actions, "采集当前前 / 后图  [空格]", owner.capture, primary=True)
        self.capture.pack(side="right")
        self.capture.state(["disabled"])


class ResultsPage(Page):
    def __init__(self, owner):
        super().__init__(owner)
        heading(self, "04  结果与报告", "原始记录自动保存。报告由你选择导出或打印；流程结束和清洗质量分别显示。")
        self.summary = tk.Label(self, text="尚无任务结果", bg=PALE, fg=INK, anchor="w", padx=12, pady=9, font=(FONT, 12, "bold"))
        self.summary.pack(fill="x", pady=(0, 8))
        body = tk.Frame(self, bg=CARD)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=7)
        body.rowconfigure(0, weight=1)
        left, right = tk.Frame(body, bg=CARD), tk.Frame(body, bg=CARD)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        right.grid(row=0, column=1, sticky="nsew")
        self.tree = ttk.Treeview(left, columns=("quality",), show="tree headings", height=6, selectmode="browse")
        self.tree.heading("#0", text="目标 / 操作决定")
        self.tree.column("#0", width=165)
        self.tree.heading("quality", text="质量")
        self.tree.column("quality", width=110)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.selected)
        self.detail = tk.Label(left, text="选择目标查看证据", bg=BG, fg=INK, anchor="nw", justify="left", wraplength=330,
            padx=10, pady=8, font=(FONT, 9))
        self.detail.pack(fill="x", pady=7)
        self.detail.bind("<Configure>", lambda event: self.detail.config(wraplength=max(200, event.width - 20)))
        self.review_options = {"待质量复核": "UNCERTAIN", "人工确认已洗净": "CLEANED", "人工确认有残留": "NOT_CLEANED", "撤销人工结论": "REVOKED"}
        self.review_value = tk.StringVar(value="待质量复核")
        ttk.Combobox(left, textvariable=self.review_value, values=tuple(self.review_options), state="readonly").pack(fill="x")
        self.review_note = tk.StringVar()
        ttk.Entry(left, textvariable=self.review_note).pack(fill="x", pady=5)
        tk.Label(left, text="复核依据必填；原算法结论会保留", bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")
        button(left, "保存人工复核 / 撤销记录", owner.quality_review).pack(anchor="w", pady=6)
        right.columnconfigure(0, weight=1)
        right.columnconfigure(1, weight=1)
        right.rowconfigure(1, weight=1)
        for column, caption in ((0, "处理前"), (1, "处理后")):
            tk.Label(right, text=caption, bg=CARD, fg=MUTED, anchor="w", font=(FONT, 10)).grid(row=0, column=column, sticky="ew")
        self.pre, self.post = ImageCanvas(right, height=270), ImageCanvas(right, height=270)
        self.pre.grid(row=1, column=0, sticky="nsew", padx=(0, 5), pady=5)
        self.post.grid(row=1, column=1, sticky="nsew", padx=(5, 0), pady=5)
        image_actions = tk.Frame(right, bg=CARD)
        image_actions.grid(row=2, column=0, columnspan=2, sticky="ew")
        button(image_actions, "全图", lambda: owner.result_images(False)).pack(side="left", padx=(0, 7))
        button(image_actions, "分割 Mask", lambda: owner.result_images(True)).pack(side="left", padx=(0, 7))
        button(image_actions, "放大当前区域", owner.zoom_result).pack(side="left")
        self.export_state = tk.Label(self, text="尚未导出。可任选格式，不会自动打印。", bg=CARD, fg=MUTED, anchor="w", font=(FONT, 9))
        self.export_state.pack(fill="x", pady=8)
        actions = tk.Frame(self, bg=CARD)
        actions.pack(fill="x")
        self.export_buttons = []
        for text, formats, printing in (("HTML 报告", ("html",), False), ("CSV 数据", ("csv",), False), ("HTML + CSV", ("html", "csv"), False), ("打印 / 另存 PDF", ("html",), True)):
            control = button(actions, text, lambda formats=formats, printing=printing: owner.export(formats, printing), primary=printing)
            control.pack(side="left", padx=(0, 7))
            self.export_buttons.append(control)
        button(actions, "新任务  →", lambda: owner.navigate("cover")).pack(side="right")
        footer = tk.Frame(self, bg=CARD)
        footer.pack(fill="x", pady=(8, 0))
        button(footer, "← 查看执行记录", lambda: owner.navigate("monitor")).pack(side="left", padx=(0, 8))
        button(footer, "打开历史任务", owner.choose_history).pack(side="left", padx=(0, 8))
        button(footer, "原始证据目录", owner.open_evidence).pack(side="left")
        self.selected_id = None

    def update_snapshot(self, snapshot):
        stats = {key: value if value is not None else "未记录" for key, value in snapshot["statistics"].items()}
        self.summary.config(text=f"{snapshot['quality_status']} · {label(snapshot['quality_status'])}    |    {label(snapshot['workflow_status'])}    |    确认 {stats['approved']} · 输出完成 {stats['executed_targets']} · 通过 {stats['cleaned']} · 残留 {stats['not_cleaned']} · 待复核 {stats['uncertain']}")
        previous = self.selected_id
        for item in self.tree.get_children():
            self.tree.delete(item)
        for target in snapshot["targets"]:
            stable = target["target_id"]
            self.tree.insert("", "end", iid=stable, text=f"{stable} · {label(target['decision'])}", values=(label(target["quality"]),))
        if snapshot["targets"]:
            self.tree.selection_set(previous if previous and self.tree.exists(previous) else snapshot["targets"][0]["target_id"])
        else:
            self.pre.set_image(None)
            self.post.set_image(None)
        for control in self.export_buttons:
            control.state(["!disabled"] if not self.owner.runtime.active else ["disabled"])

    def selected(self, event=None):
        selected = self.tree.selection()
        if not selected:
            return
        self.selected_id = selected[0]
        target = self.owner.result_target()
        if target is None:
            return
        evidence = target["quality_evidence"]
        self.detail.config(text=f"{target['target_id']} · {label(target['source'])}\n自动：{label(evidence['automatic_quality'])}\n有效：{label(target['quality'])}（{label(evidence['effective_source'])}）\n"
            f"尝试 {len(target['attempts'])} 次；输出 DONE {sum(a['pump_done'] for a in target['attempts'])} 次\n" + "；".join(explain(code) for code in evidence["reasons"]))
        self.owner.result_images(False)
