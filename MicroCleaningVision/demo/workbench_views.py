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


def motion_chinese(line):
    """面向操作者解释串口句子；原文仍由 serial.json 保存。"""
    from microcleaning.control_system.planning.stage2_axes import parse_movexy_line
    text = str(line).strip()
    if text.startswith("MOVEXY "):
        try:
            dx, dy = parse_movexy_line(text)
            axis = lambda value: "不移动" if value == 0 else f"{'正向' if value > 0 else '反向'} {abs(value):,} 步"
            return f"规划移动：X 轴{axis(dx)}；Y 轴{axis(dy)}"
        except ValueError:
            return "运动报文格式异常，不能作为有效移动"
    if text.startswith("STEP2 X="):
        from microcleaning.control_system.serial.stage2_protocol import parse_stage2_reply
        try:
            reply = parse_stage2_reply(text.encode("ascii"))
            return f"本段已发脉冲：X {reply.x_sent:,} 步（{'移动中' if reply.x_busy else '已停'}）；Y {reply.y_sent:,} 步（{'移动中' if reply.y_busy else '已停'}）"
        except (ValueError, UnicodeError):
            return "脉冲回执解析失败"
    if text.startswith("STEP2_START"):
        return "控制板接受本段双轴运动，等待脉冲完成回执"
    if text.startswith("MCV1|PUMP|"):
        return "请求定点短喷 " + text.rsplit("|", 1)[-1] + " 毫秒，等待控制板确认"
    if text.startswith("MCV1|STATUS|"):
        return "设备状态：" + ("急停已激活" if "ESTOP=1" in text else "急停未激活") + "；" + ("泵输出中" if "PUMP=1" in text else "泵已关闭")
    if text.startswith("MCV1|ACK|"):
        return "控制板已接受停止请求" if text.endswith("|STOP") else "控制板已接受短喷请求"
    if text.startswith("MCV1|DONE|"):
        return "控制板确认停止流程结束" if text.endswith("|STOP") else "控制板确认短喷输出流程结束；清洗效果由复检判断"
    if text.startswith("MCV1|ERR|") or text.startswith("ERR:"):
        return "控制板报告错误：" + explain(text.rsplit("|", 1)[-1].removeprefix("ERR:").strip())
    return {"HELLO": "查询步进协议版本", "STEP_OK v0.3": "步进协议握手通过", "READXY": "查询本段双轴已发脉冲",
            "STOP": "请求立即停止双轴与泵", "STEP_STOPPED": "控制板确认双轴停止",
            "MCV1|STOP": "请求停止双轴与泵", "MCV1|PING": "查询喷洗控制器在线状态",
            "MCV1|PONG": "喷洗控制器在线", "MCV1|STATUS": "查询急停与泵状态"}.get(text, "诊断回执已保存到原始串口记录")


def roi_explanation(analysis):
    if not analysis:
        return "等待本轮复检。仅比较同一参考坐标区域；前后图偏移未通过核验时不报告有效清洗率。"
    value = analysis.get("removal_rate")
    rate = "无法计算" if value is None else f"{value:.2%}"
    iou = analysis.get("iou")
    overlap = "未记录" if iou is None else f"{iou:.2%}"
    shift = analysis.get("alignment_shift_px")
    displacement = "无法核验" if shift is None else f"ΔX={shift[0]:.3f}、ΔY={shift[1]:.3f} 像素"
    reasons = "；".join(analysis.get("messages_zh") or [explain(code) for code in analysis.get("reason_codes", ())])
    return (f"{'位置核验通过' if analysis.get('valid') else '本次计算不具备有效证据'} · 前后位移：{displacement}\n"
            f"前面积 {analysis.get('pre_area_px', '未知')} 像素；后面积 {analysis.get('post_area_px', '未知')} 像素；重合度 IoU {overlap}\n"
            f"重合度＝交集 / 并集；清洗率＝（前面积－后面积）/ 前面积＝{rate}。"
            + ("未检测到有效污渍，显示 0%，不能据此判为洗净。" if value == 0 and not analysis.get("valid") else "")
            + ("\n" + reasons if reasons else ""))


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
            f"{action}；请求短喷 {plan.get('pump_duration_ms', '未记录')} 毫秒\n"
            "去程：" + "；".join(motion_chinese(line).removeprefix("规划移动：") for line in geometry.get('outbound', {}).get('lines', []))
            + "\n回程：" + "；".join(motion_chinese(line).removeprefix("规划移动：") for line in geometry.get('returning', {}).get('lines', []))
            + f"\n{reason}")
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
        # 正常画面和复检证据在同一页；缩小窗口后可以滚动，确认按钮始终在页外。
        scroll = tk.Frame(self, bg=CARD)
        scroll.pack(fill="both", expand=True)
        self.scroller = tk.Canvas(scroll, bg=CARD, highlightthickness=0, height=390)
        self.scroller.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(scroll, orient="vertical", command=self.scroller.yview)
        scrollbar.pack(side="right", fill="y")
        self.scroller.configure(yscrollcommand=scrollbar.set)
        content = tk.Frame(self.scroller, bg=CARD)
        item = self.scroller.create_window(0, 0, window=content, anchor="nw")
        content.bind("<Configure>", lambda _: self.scroller.configure(scrollregion=self.scroller.bbox("all")))
        self.scroller.bind("<Configure>", lambda event: self.scroller.itemconfigure(item, width=event.width))
        images = tk.Frame(content, bg=CARD)
        images.pack(fill="x")
        images.columnconfigure(0, weight=1)
        images.columnconfigure(1, weight=1)
        images.rowconfigure(1, weight=1)
        for column, caption in ((0, "首次检测 · 冻结参考"), (1, "实时显微图 · 设备当前画面")):
            tk.Label(images, text=caption, bg=CARD, fg=MUTED, anchor="w", font=(FONT, 10)).grid(row=0, column=column, sticky="ew", pady=5)
        self.reference = ImageCanvas(images, height=250)
        self.live = ImageCanvas(images, height=250)
        self.reference.grid(row=1, column=0, sticky="nsew", padx=(0, 7))
        self.live.grid(row=1, column=1, sticky="nsew", padx=(7, 0))
        self.nozzle = tk.Canvas(self.live, bg="#132b30", highlightbackground="#6a8982", highlightthickness=1,
                                width=220, height=155)
        self.nozzle.place(relx=1, rely=1, anchor="se", x=-7, y=-7)
        self.nozzle.bind("<Configure>", lambda _: self._draw_nozzle())
        self.position_data, self.geometry_data = {}, {}
        self.position = tk.Label(content, text="XY 位置参考尚未确认；机械边界未知。", bg=PALE, fg=INK,
                                 anchor="w", justify="left", padx=10, pady=8, font=(FONT, 10))
        self.position.pack(fill="x", pady=(9, 0))
        self.position.bind("<Configure>", lambda event: self.position.config(wraplength=max(180, event.width-20)))
        self.calibration = tk.Label(content, text="喷头中心：没有有效标定文件；不显示对齐估算。", bg=CARD, fg=MUTED,
                                    anchor="w", justify="left", font=(FONT, 9))
        self.calibration.pack(fill="x", pady=(6, 0))
        self.calibration.bind("<Configure>", lambda event: self.calibration.config(wraplength=max(180, event.width-12)))
        self.feedback = tk.Label(content, text="计划位置与已发脉冲会在这里显示；物理位移需现场验证。", bg=CARD,
            fg=MUTED, anchor="w", justify="left", font=(FONT, 10))
        self.feedback.pack(fill="x", pady=8)
        self.feedback.bind("<Configure>", lambda event: self.feedback.config(wraplength=max(180, event.width-12)))
        comparison = tk.LabelFrame(content, text="本轮复检 · 同一参考位置的放大对比", bg=CARD, fg=INK, font=(FONT, 10, "bold"), padx=8, pady=6)
        comparison.pack(fill="x", pady=(0, 8))
        for column, caption in enumerate(("清洗前污渍", "复检后同位置", "绿：减少 · 黄：重合 · 红：新增")):
            comparison.columnconfigure(column, weight=1)
            tk.Label(comparison, text=caption, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w").grid(row=0, column=column, sticky="ew")
        self.pre_crop, self.post_crop, self.overlay = [ImageCanvas(comparison, height=150) for _ in range(3)]
        for column, canvas in enumerate((self.pre_crop, self.post_crop, self.overlay)):
            canvas.configure(width=220)
            canvas.grid(row=1, column=column, sticky="nsew", padx=3, pady=4)
        self.calculation = tk.Label(comparison, text=roi_explanation(None), bg=CARD, fg=MUTED, font=(FONT, 10),
                                    justify="left", anchor="w")
        self.calculation.grid(row=2, column=0, columnspan=3, sticky="ew", pady=5)
        self.calculation.bind("<Configure>", lambda event: self.calculation.config(wraplength=max(180, event.width-16)))
        actions = tk.Frame(self, bg=CARD)
        actions.pack(fill="x")
        button(actions, "← 查看锁定名单", lambda: owner.navigate("review")).pack(side="left")
        self.capture = button(actions, "采集当前前 / 后图  [空格]", owner.capture, primary=True)
        self.capture.pack(side="right")
        self.capture.state(["disabled"])
        def scroll_wheel(event):
            self.scroller.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"
        def bind_scroll(widget):
            widget.bind("<MouseWheel>", scroll_wheel)
            for child in widget.winfo_children():
                bind_scroll(child)
        bind_scroll(content)
        self._draw_nozzle()

    def reset(self):
        self.position_data, self.geometry_data = {}, {}
        for canvas in (self.reference, self.live, self.pre_crop, self.post_crop, self.overlay):
            canvas.set_image(None)
        self.calculation.config(text=roi_explanation(None), fg=MUTED)
        self.position.config(text="XY 位置参考尚未确认；机械边界未知。")
        self.calibration.config(text="喷头中心：没有有效标定文件；不显示对齐估算。")
        self.scroller.yview_moveto(0)
        self._draw_nozzle()

    def update_verification(self, payload):
        analysis = payload.get("roi_analysis")
        self.calculation.config(text=roi_explanation(analysis), fg=TEAL if analysis and analysis.get("valid") else AMBER)
        for key, canvas in (("pre_crop", self.pre_crop), ("post_crop", self.post_crop), ("overlay", self.overlay)):
            canvas.set_image(payload.get(key), placeholder="本次证据图未生成")
        self.scroller.update_idletasks()
        self.scroller.yview_moveto(1)

    def update_position(self, payload):
        self.position_data = dict(payload)
        status = {"READY": "位置账本可用", "MOVING": "移动中，显示本段脉冲估算", "UNHOMED": "尚未建立参考", "POSITION_UNCERTAIN": "位置不可信，停止普通任务"}.get(payload.get("status"), "位置状态未知")
        confirmed = payload.get("confirmed_xy_steps")
        estimated = payload.get("estimated_xy_steps")
        coord = lambda point: "未知" if point is None else f"X {point[0]:+,} 步 / Y {point[1]:+,} 步"
        self.position.config(text=f"{status}　|　已提交坐标：{coord(confirmed)}　|　移动估算：{coord(estimated)}\n"
                             f"预计终点：{coord(payload.get('expected_xy_steps'))}；四向机械余量：未知（未配置实测边界）\n"
                             f"参考建立：{payload.get('zero_set_at') or '未确认'}；这些数字来自指令与脉冲回执。",
                             fg=AMBER if payload.get("status") in {"POSITION_UNCERTAIN", "UNHOMED"} else INK)
        self._draw_nozzle()

    def update_geometry(self, payload):
        self.geometry_data = dict(payload)
        offset = payload.get("offset") or {}
        motor = payload.get("motor_calibration") or {}
        self.calibration.config(text=f"喷头标定来源：{offset.get('calibration_source') or '未知'}；装配：{offset.get('setup_id') or '未知'}；"
            f"偏移 {offset.get('scope_to_nozzle_delta_steps')} 步；标定误差 {offset.get('uncertainty_steps')} 步。\n"
            f"电机标定文件：{motor.get('ref') or '未加载'}；标定文件有效性：{'已核对' if payload.get('calibration_valid') else '未通过'}；"
            "实际定位精度仍需现场测量。")
        self._draw_nozzle()

    def _draw_nozzle(self):
        canvas = self.nozzle
        canvas.delete("all")
        w, h = max(220, canvas.winfo_width()), max(155, canvas.winfo_height())
        geometry = self.geometry_data.get("geometry") or {}
        offset = self.geometry_data.get("offset") or {}
        motor = self.geometry_data.get("motor_calibration") or {}
        calibrated = (self.geometry_data.get("calibration_valid") is True and offset.get("axes_confirmed") is True
            and offset.get("scope_to_nozzle_delta_steps") is not None and offset.get("calibration_source")
            and offset.get("uncertainty_steps") is not None
            and (not self.geometry_data.get("real") or (not offset.get("mock_only")
                and motor.get("sha256") == offset.get("motor_calibration_sha256"))))
        status = self.position_data.get("status")
        current = (self.position_data.get("estimated_xy_steps") if status == "MOVING"
                   else self.position_data.get("confirmed_xy_steps") if status == "READY" else None)
        expected = geometry.get("execution_position")
        self.nozzle_remaining = None
        title = "标定喷头中心 · 步数投影" if calibrated else "喷头中心尚未有效标定"
        canvas.create_text(9, 12, text=title, fill="#d8e8e3" if calibrated else "#aabeb8", anchor="w", font=(FONT, 9, "bold"))
        if not calibrated or current is None or expected is None:
            canvas.create_text(w/2, h/2, text="未标定 / 位置不可信\n不显示假对齐点", justify="center", fill="#aabeb8", font=(FONT, 9))
            return
        remaining = tuple(expected[index] - current[index] for index in (0, 1))
        self.nozzle_remaining = remaining
        cx, cy = w/2, 66
        canvas.create_line(cx-40, cy, cx+40, cy, fill="#dcb062")
        canvas.create_line(cx, cy-29, cx, cy+29, fill="#dcb062")
        canvas.create_oval(cx-4, cy-4, cx+4, cy+4, fill="#dcb062", outline="")
        # 投影按当前计划缩放；数字与标定 uncertainty 才是可核对的量。
        before = geometry.get("observation_position") or current
        extent = max(1, *(abs(expected[i]-before[i]) for i in (0, 1)), *(abs(v) for v in remaining))
        px, py = cx + remaining[0]/extent*48, cy - remaining[1]/extent*29
        canvas.create_oval(px-4, py-4, px+4, py+4, outline="#7ad6d1", width=2)
        canvas.create_text(9, 105, text=f"距喷头：ΔX {remaining[0]:+} / ΔY {remaining[1]:+} 步", fill="#c6e0da", anchor="w", font=(FONT, 8))
        uncertainty = offset["uncertainty_steps"]
        canvas.create_text(9, 124, text=f"标定误差 ±({uncertainty[0]}, {uncertainty[1]}) 步", fill="#c6e0da", anchor="w", font=(FONT, 8))
        canvas.create_text(9, 142, text="脉冲估算，不等于台面实测到位", fill="#9db3ae", anchor="w", font=(FONT, 8))


class ResultsPage(Page):
    def __init__(self, owner):
        super().__init__(owner)
        self.attempt_index, self.recheck_index = None, None
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
        self.detail = tk.Text(left, height=9, bg=BG, fg=INK, wrap="word", relief="flat",
                              padx=10, pady=8, font=(FONT, 9), state="disabled")
        self.detail.pack(fill="both", expand=True, pady=7)
        self.review_options = {"待质量复核": "UNCERTAIN", "人工确认已洗净": "CLEANED", "人工确认有残留": "NOT_CLEANED", "撤销人工结论": "REVOKED"}
        self.review_value = tk.StringVar(value="待质量复核")
        ttk.Combobox(left, textvariable=self.review_value, values=tuple(self.review_options), state="readonly").pack(fill="x")
        self.review_note = tk.StringVar()
        ttk.Entry(left, textvariable=self.review_note).pack(fill="x", pady=5)
        tk.Label(left, text="复核依据必填；原算法结论会保留", bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")
        button(left, "保存人工复核 / 撤销记录", owner.quality_review).pack(anchor="w", pady=6)
        right.columnconfigure(0, weight=1)
        right.columnconfigure(1, weight=1)
        right.rowconfigure(2, weight=1)
        rounds = tk.Frame(right, bg=CARD)
        rounds.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 7))
        rounds.columnconfigure(1, weight=1)
        rounds.columnconfigure(3, weight=1)
        tk.Label(rounds, text="清洗轮次", bg=CARD, fg=MUTED, font=(FONT, 9)).grid(row=0, column=0, padx=(0, 6))
        self.attempt_value, self.recheck_value = tk.StringVar(), tk.StringVar()
        self.attempt_select = ttk.Combobox(rounds, textvariable=self.attempt_value, state="readonly", width=17)
        self.attempt_select.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        self.attempt_select.bind("<<ComboboxSelected>>", self._attempt_changed)
        tk.Label(rounds, text="复检轮次", bg=CARD, fg=MUTED, font=(FONT, 9)).grid(row=0, column=2, padx=(0, 6))
        self.recheck_select = ttk.Combobox(rounds, textvariable=self.recheck_value, state="readonly", width=16)
        self.recheck_select.grid(row=0, column=3, sticky="ew")
        self.recheck_select.bind("<<ComboboxSelected>>", self._recheck_changed)
        for column, caption in ((0, "处理前"), (1, "处理后")):
            tk.Label(right, text=caption, bg=CARD, fg=MUTED, anchor="w", font=(FONT, 10)).grid(row=1, column=column, sticky="ew")
        self.pre, self.post = ImageCanvas(right, height=270), ImageCanvas(right, height=270)
        self.pre.grid(row=2, column=0, sticky="nsew", padx=(0, 5), pady=5)
        self.post.grid(row=2, column=1, sticky="nsew", padx=(5, 0), pady=5)
        image_actions = tk.Frame(right, bg=CARD)
        image_actions.grid(row=3, column=0, columnspan=2, sticky="ew")
        button(image_actions, "全图", lambda: owner.result_images(False)).pack(side="left", padx=(0, 7))
        button(image_actions, "分割 Mask", lambda: owner.result_images(True)).pack(side="left", padx=(0, 7))
        button(image_actions, "本轮同位置放大图", lambda: owner.result_images(crop=True)).pack(side="left")
        self.export_state = tk.Label(self, text="尚未导出。可任选格式，不会自动打印。", bg=CARD, fg=MUTED, anchor="w", font=(FONT, 9))
        self.export_state.pack(fill="x", pady=8)
        actions = tk.Frame(self, bg=CARD)
        actions.pack(fill="x")
        self.export_buttons = []
        for text, formats, printing in (("HTML 报告", ("html",), False), ("CSV 数据", ("csv",), False), ("HTML + CSV", ("html", "csv"), False), ("打印 / 另存 PDF", ("html",), True)):
            control = button(actions, text, lambda formats=formats, printing=printing: owner.export(formats, printing), primary=printing)
            control.pack(side="left", padx=(0, 7))
            self.export_buttons.append(control)
        self.print_button = self.export_buttons[-1]
        button(actions, "新任务  →", lambda: owner.navigate("cover")).pack(side="right")
        footer = tk.Frame(self, bg=CARD)
        footer.pack(fill="x", pady=(8, 0))
        button(footer, "← 查看执行记录", lambda: owner.navigate("monitor")).pack(side="left", padx=(0, 8))
        button(footer, "打开历史任务", owner.choose_history).pack(side="left", padx=(0, 8))
        button(footer, "原始证据目录", owner.open_evidence).pack(side="left")
        self.selected_id = None

    def update_snapshot(self, snapshot):
        stats = {key: value if value is not None else "未记录" for key, value in snapshot["statistics"].items()}
        self.summary.config(text=f"{label(snapshot['quality_status'])}    |    {label(snapshot['workflow_status'])}    |    确认 {stats['approved']} · 输出完成 {stats['executed_targets']} · 通过 {stats['cleaned']} · 残留 {stats['not_cleaned']} · 待复核 {stats['uncertain']}")
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
        self.refresh_exports()

    def print_ready(self):
        snapshot = self.owner.snapshot or {}
        return bool(snapshot.get("report_ready", (snapshot.get("summary") or {}).get("report_ready", snapshot.get("quality_status") == "PASS")))

    def refresh_exports(self, *, busy=None):
        busy = self.owner.runtime.active or (self.owner.runtime.report_busy if busy is None else busy)
        for control in self.export_buttons:
            enabled = not busy and (control is not self.print_button or self.print_ready())
            control.state(["!disabled"] if enabled else ["disabled"])
        if self.owner.snapshot is not None and not self.print_ready() and not busy:
            self.export_state.config(text="末目标尚未复检通过：可导出完整档案，正式打印仍未开放。所有清洗与复检轮次保留。")

    def selected(self, event=None):
        selected = self.tree.selection()
        if not selected:
            return
        changed = self.selected_id != selected[0]
        self.selected_id = selected[0]
        target = self.owner.result_target()
        if target is None:
            return
        attempts = target.get("attempts") or []
        self.attempt_select.config(values=[f"第 {i+1} 次清洗 · 任务轮 {attempt.get('cycle', i+1)}" for i, attempt in enumerate(attempts)])
        if attempts:
            index = len(attempts)-1 if changed or self.attempt_index is None else min(self.attempt_index, len(attempts)-1)
            self.attempt_select.current(index)
            self._attempt_changed()
        else:
            self.attempt_index, self.recheck_index = None, None
            self.attempt_value.set("没有清洗动作")
            self.recheck_value.set("没有复检")
            self._details()
            self.owner.result_images(False)

    def _attempt_changed(self, event=None):
        target = self.owner.result_target()
        if target is None or not target.get("attempts"):
            return
        self.attempt_index = max(0, self.attempt_select.current())
        attempt = target["attempts"][self.attempt_index]
        checks = attempt.get("rechecks") or []
        self.recheck_select.config(values=[f"第 {check.get('index', i+1)} 次复检" for i, check in enumerate(checks)])
        if checks:
            self.recheck_select.current(len(checks)-1)
            self.recheck_index = len(checks)-1
        else:
            self.recheck_value.set("旧档案单次复检")
            self.recheck_index = None
        self._details()
        self.owner.result_images(False)

    def _recheck_changed(self, event=None):
        self.recheck_index = max(0, self.recheck_select.current())
        self._details()
        self.owner.result_images(False)

    def _details(self):
        target = self.owner.result_target()
        if target is None:
            return
        evidence = target.get("quality_evidence") or {}
        attempts = target.get("attempts") or []
        text = (f"{target['target_id']} · {label(target.get('source'))}\n自动：{label(evidence.get('automatic_quality'))}；有效：{label(target.get('quality'))}\n"
                f"共 {len(attempts)} 次清洗；短喷输出结束 {sum(a.get('pump_done', False) for a in attempts)} 次\n")
        if attempts and self.attempt_index is not None:
            attempt = attempts[self.attempt_index]
            checks = attempt.get("rechecks") or []
            check = checks[self.recheck_index] if checks and self.recheck_index is not None else attempt
            text += (f"当前：第 {self.attempt_index+1} 次清洗 / 第 {self.recheck_index+1 if self.recheck_index is not None else 1} 次复检\n"
                     f"请求短喷 {attempt.get('requested_duration_ms', '未知')} 毫秒；实测时长：未配置测量\n"
                     f"动作编号：{attempt.get('action_id') or '未记录'}\n")
            geometry = attempt.get("geometry") or {}
            for title, key in (("去程", "outbound"), ("回程", "returning")):
                lines = (geometry.get(key) or {}).get("lines") or []
                text += title + "：" + "；".join(motion_chinese(line).removeprefix("规划移动：") for line in lines) + "\n"
            text += roi_explanation(check.get("roi_analysis") or attempt.get("roi_analysis")) + "\n"
            decision = check.get("decision") or attempt.get("recheck_decision")
            if decision:
                choice = decision.get("choice") or decision.get("answer") or decision.get("decision") if isinstance(decision, dict) else str(decision)
                text += "人工决定：" + {"rewash": "再次清洗", "next": "不复洗，继续或最终确认", "retake": "仅重拍复检", "pause": "暂停并保存"}.get(choice, str(choice)) + "\n"
                if isinstance(decision, dict):
                    text += "人工依据：" + str(decision.get("reason") or "未填写") + "\n"
            text += "；".join(explain(code) for code in (check.get("issues") or ()))
        text += "\n" + "；".join(explain(code) for code in evidence.get("reasons", ()))
        self.detail.config(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.config(state="disabled")
