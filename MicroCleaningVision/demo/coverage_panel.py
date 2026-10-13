"""P4 standalone Tk coverage preview; no workbench or hardware imports.

Run: python -m demo.coverage_panel --synthetic split --diameter-px 36
Every diameter is supplied by the caller or an explicit profile, never inferred.
"""

from __future__ import annotations

import argparse
import json
import queue
import threading
import time
import tkinter as tk
import uuid
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageTk

from demo.coverage_session import (
    SYNTHETIC_SHAPES, CoverageDataset, CoverageRequest, LatestCoverageWorker,
    load_image_mask, synthetic_dataset,
)
from microcleaning.control_system.planning.coverage_archive import (
    export_coverage_preview, load_coverage_preview, profile_from_dict, scale_from_dict,
)
from microcleaning.control_system.planning.coverage_model import Evidence, FootprintProfile, immutable_array
from microcleaning.control_system.planning.coverage_planner import CoverageConstraints, CoveragePolicy
from microcleaning.control_system.planning.coverage_visualization import (
    COLORS, RenderOptions, render_coverage,
)

BG, INK, MUTED, TEAL = "#edf3f1", "#173d3e", "#627c79", "#167f77"
LAYER_LABELS = {"original": "原始图像", "target": "完整目标 Mask", "covered": "预计已覆盖",
    "uncovered": "预计遗漏", "footprints": "覆盖核边界", "overlap": "重复覆盖",
    "conservative": "完整计划保守覆盖", "stations": "驻点编号", "route": "访问顺序虚线"}
# Fixed v1 badges can obscure an entire small footprint. Keep them opt-in;
# recorded archives still pass their original options explicitly.
PREVIEW_OPTIONS = RenderOptions(target=False, stations=False, alpha=0.25)
MASK_TITLES = {"segmented_stain": "污渍识别结果", "manual_region": "人工框选区域",
    "manual_roi_segmented": "框选内污渍分割", "reference_annotation": "参考标注",
    "synthetic": "合成 Mask"}


class StandaloneCoveragePanel:
    def __init__(self, dataset: CoverageDataset, profile: FootprintProfile, *, scale=None,
            policy: CoveragePolicy = CoveragePolicy(candidate_stride_px=2),
            constraints: CoverageConstraints | None = None, root=None, worker=None,
            options: RenderOptions | None = None, model=None, initial_step_count=None,
            input_locked=False, on_view=None, on_invalidate=None, on_close=None,
            exporter_factory=None, on_exported=None, initial_generation=0, owns_worker=True, layout_scale=1.0,
            source_description=""):
        self.input_locked = input_locked
        self.on_view, self.on_invalidate, self.on_close = on_view, on_invalidate, on_close
        self.exporter_factory, self.on_exported = exporter_factory, on_exported
        self.owns_worker = owns_worker
        self.layout_scale = float(layout_scale)
        self.source_description = source_description
        self.root = root if root is not None else tk.Tk()
        self.root.title("MicroCleaningVision · 智能覆盖预览（仅模拟）")
        width = min(round(1280 * self.layout_scale), self.root.winfo_screenwidth() - 70)
        height = min(round(820 * self.layout_scale), self.root.winfo_screenheight() - 90)
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(width, round(1080 * self.layout_scale)), min(height, round(730 * self.layout_scale)))
        self.root.configure(bg=BG)
        self.dataset, self.base_profile, self.profile, self.scale = dataset, profile, profile, scale
        self.policy, self.constraints = policy, constraints if constraints is not None else CoverageConstraints()
        self.model_override, self.initial_step_count = model, initial_step_count
        self.diameter_edited = False
        self.worker = worker if worker is not None else LatestCoverageWorker()
        self.current_plan = self.current_view = None
        self.generation, self.accepted_generation, self.heartbeat_count = initial_generation, 0, 0
        self.closed, self.playing, self._updating = False, False, False
        self._after_ids = set()
        self._debounce_id = self._resize_id = self._play_id = None
        self._photo = None
        self._photos = {}
        self._image_sizes = {}
        self._input_dataset = None
        self._export_results = queue.Queue(maxsize=1)
        self.export_busy = False
        self.export_thread = None
        self.diameter = tk.StringVar(value="" if profile.diameter is None else f"{profile.diameter:g}")
        self.slider_value = tk.DoubleVar(value=0 if profile.diameter is None else profile.diameter)
        self.goal = tk.StringVar(value=f"{policy.target_coverage * 100:g}")
        self.budget = tk.StringVar(value=str(policy.max_stations))
        self.stride = tk.StringVar(value=str(policy.candidate_stride_px))
        self.step = tk.IntVar(value=0)
        self.status = tk.StringVar(value="准备离线规划")
        self.provenance = tk.StringVar()
        self.export_status = tk.StringVar(value="导出同一份参数、原图、Mask 和计划")
        self.metrics_text = tk.StringVar(value="—")
        options = PREVIEW_OPTIONS if options is None else options
        self.layers = {key: tk.BooleanVar(value=getattr(options, key)) for key in LAYER_LABELS}
        self.alpha = options.alpha
        self.alpha_value = tk.DoubleVar(value=self.alpha * 100)
        self.alpha_text = tk.StringVar(value=f"{self.alpha:.0%}")
        self.original_only = tk.BooleanVar(value=False)
        self._build()
        self._update_provenance()
        self.diameter.trace_add("write", self._diameter_changed)
        for variable in (self.goal, self.budget, self.stride):
            variable.trace_add("write", self._parameter_changed)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._invalidate()
        self._submit()
        self._later(40, self._poll)

    def _later(self, delay, callback):
        holder = []
        def run():
            self._after_ids.discard(holder[0])
            if not self.closed:
                callback()
        identifier = self.root.after(delay, run)
        holder.append(identifier)
        self._after_ids.add(identifier)
        return identifier

    def _cancel(self, identifier):
        if identifier in self._after_ids:
            self.root.after_cancel(identifier)
            self._after_ids.discard(identifier)

    def _build(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Coverage.TFrame", background=BG)
        style.configure("Coverage.TLabel", background=BG, foreground=INK, font=("Microsoft YaHei UI", 10))
        style.configure("Coverage.TCheckbutton", background=BG, foreground=INK, font=("Microsoft YaHei UI", 10))
        style.configure("Coverage.TButton", font=("Microsoft YaHei UI", 10), padding=(10, 8))
        header = ttk.Frame(self.root, style="Coverage.TFrame", padding=(24, 16))
        header.pack(fill="x")
        ttk.Label(header, text="智能覆盖预览", style="Coverage.TLabel", font=("Microsoft YaHei UI", 23, "bold")).pack(side="left")
        ttk.Label(header, text="SIMULATION · 仅模拟 · executable=false", style="Coverage.TLabel", foreground=TEAL).pack(side="right")
        ttk.Label(self.root, text="预计几何覆盖 ≠ 实际清除率   |   访问虚线 ≠ 平台实际轨迹   |   全程不发送硬件动作", style="Coverage.TLabel").pack(fill="x", padx=26, pady=(0, 10))
        body = ttk.Frame(self.root, style="Coverage.TFrame", padding=(20, 0))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=5)
        body.rowconfigure(0, weight=1)
        left = ttk.Frame(body, style="Coverage.TFrame", width=round(260 * self.layout_scale), padding=(0, 4, 8, 0))
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        left.rowconfigure(3, weight=1)
        ttk.Label(left, text="01  原始图像", style="Coverage.TLabel", font=("Microsoft YaHei UI", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.original_canvas = tk.Canvas(left, bg="#e1e9e7", highlightthickness=0,
            width=round(260 * self.layout_scale), height=round(210 * self.layout_scale))
        self.original_canvas.grid(row=1, column=0, sticky="nsew")
        self.mask_title = tk.StringVar()
        mask_heading = ttk.Label(left, textvariable=self.mask_title, style="Coverage.TLabel", font=("Microsoft YaHei UI", 12, "bold"),
            wraplength=round(260 * self.layout_scale))
        mask_heading.grid(row=2, column=0, sticky="ew", pady=(14, 6))
        mask_heading.bind("<Configure>", lambda event: mask_heading.configure(wraplength=max(round(180 * self.layout_scale), event.width)))
        self.mask_canvas = tk.Canvas(left, bg="#e1e9e7", highlightthickness=0,
            width=round(260 * self.layout_scale), height=round(210 * self.layout_scale))
        self.mask_canvas.grid(row=3, column=0, sticky="nsew")
        ttk.Label(left, textvariable=self.provenance, style="Coverage.TLabel", wraplength=round(260 * self.layout_scale),
            foreground=MUTED, font=("Microsoft YaHei UI", 9)).grid(row=4, column=0, sticky="ew", pady=(10, 0))
        for canvas in (self.original_canvas, self.mask_canvas):
            canvas.bind("<Configure>", self._resize)
        center = ttk.Frame(body, style="Coverage.TFrame")
        center.grid(row=0, column=1, sticky="nsew", padx=10)
        ttk.Label(center, text="03  预计覆盖叠加 · 仅模拟", style="Coverage.TLabel", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w", pady=(4, 8))
        self.canvas = tk.Canvas(center, bg="#e1e9e7", highlightthickness=0, width=round(580 * self.layout_scale), height=round(480 * self.layout_scale))
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._resize)
        self.playback_label = ttk.Label(center, text="驻点进度 0 / 0", style="Coverage.TLabel")
        self.playback_label.pack(anchor="w", pady=(10, 3))
        self.step_slider = ttk.Scale(center, from_=0, to=1, command=self._step_changed)
        self.step_slider.pack(fill="x")
        controls = ttk.Frame(center, style="Coverage.TFrame")
        controls.pack(side="bottom", fill="x", pady=8, before=self.canvas)
        self.step_slider.pack(side="bottom", fill="x", before=self.canvas)
        self.playback_label.pack(side="bottom", anchor="w", pady=(10, 3), before=self.canvas)
        self.previous_button = ttk.Button(controls, text="◀ 上一点", command=lambda: self.show_step(self.step.get() - 1), style="Coverage.TButton")
        self.previous_button.pack(side="left")
        self.play_button = ttk.Button(controls, text="播放 / 暂停", command=self.toggle_play, style="Coverage.TButton")
        self.play_button.pack(side="left", padx=6)
        self.next_button = ttk.Button(controls, text="下一点 ▶", command=lambda: self.show_step(self.step.get() + 1), style="Coverage.TButton")
        self.next_button.pack(side="left")
        self.full_button = ttk.Button(controls, text="完整计划", command=lambda: self.show_step(len(self.current_plan.stations) if self.current_plan else 0), style="Coverage.TButton")
        self.full_button.pack(side="right")
        controls_frame = ttk.Frame(body, style="Coverage.TFrame", padding=(8, 4, 0, 0))
        controls_frame.grid(row=0, column=2, sticky="nsew")
        # Keep every control reachable at the existing minimum size/high DPI.
        self.controls_canvas = tk.Canvas(controls_frame, bg=BG, highlightthickness=0, width=round(235 * self.layout_scale))
        scrollbar = ttk.Scrollbar(controls_frame, orient="vertical", command=self.controls_canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.controls_canvas.pack(side="left", fill="both", expand=True)
        self.controls_canvas.configure(yscrollcommand=scrollbar.set)
        right = ttk.Frame(self.controls_canvas, style="Coverage.TFrame")
        control_window = self.controls_canvas.create_window(0, 0, window=right, anchor="nw")
        right.bind("<Configure>", lambda _event: self.controls_canvas.configure(scrollregion=self.controls_canvas.bbox("all")))
        self.controls_canvas.bind("<Configure>", lambda event: self.controls_canvas.itemconfigure(control_window, width=event.width))
        ttk.Label(right, text="覆盖模型参数", style="Coverage.TLabel", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(right, text=f"覆盖直径（{self.profile.unit}）· 显式参数", style="Coverage.TLabel").pack(anchor="w")
        self.diameter_entry = ttk.Entry(right, textvariable=self.diameter, width=18)
        self.diameter_entry.pack(fill="x", pady=5)
        maximum = max(80.0, (self.profile.diameter or 0) * 2)
        self.diameter_slider = ttk.Scale(right, from_=0.5, to=maximum, variable=self.slider_value, command=self._slider_changed)
        self.diameter_slider.pack(fill="x", pady=(0, 8))
        if self.profile.diameter is None:
            self.diameter_slider.state(["disabled"])
        self.advanced_button = ttk.Button(right, text="展开高级规划参数", command=self._toggle_advanced, style="Coverage.TButton")
        self.advanced_button.pack(fill="x", pady=(0, 8))
        self.advanced_frame = ttk.Frame(right, style="Coverage.TFrame")
        for title, variable in (("覆盖目标（%）", self.goal), ("驻点上限", self.budget), ("候选网格间隔（px）", self.stride)):
            ttk.Label(self.advanced_frame, text=title, style="Coverage.TLabel").pack(anchor="w", pady=(4, 2))
            ttk.Entry(self.advanced_frame, textvariable=variable, width=18).pack(fill="x")
        self.metrics_heading = ttk.Label(right, text="当前画面指标", style="Coverage.TLabel", font=("Microsoft YaHei UI", 13, "bold"))
        self.metrics_heading.pack(anchor="w", pady=(8, 10))
        ttk.Label(right, textvariable=self.metrics_text, style="Coverage.TLabel", justify="left", font=("Microsoft YaHei UI", 11), wraplength=round(215 * self.layout_scale)).pack(anchor="w", pady=(0, 18))
        alpha_row = ttk.Frame(right, style="Coverage.TFrame")
        alpha_row.pack(fill="x")
        ttk.Label(alpha_row, text="区域填色强度", style="Coverage.TLabel").pack(side="left")
        ttk.Label(alpha_row, textvariable=self.alpha_text, style="Coverage.TLabel").pack(side="right")
        self.alpha_slider = ttk.Scale(right, from_=0, to=100, variable=self.alpha_value, command=self._alpha_changed)
        self.alpha_slider.pack(fill="x", pady=(4, 8))
        self.original_button = ttk.Checkbutton(right, text="主图仅查看原图", variable=self.original_only,
            command=self._redraw, style="Coverage.TCheckbutton")
        self.original_button.pack(anchor="w", pady=(0, 8))
        ttk.Label(right, text="图层控制", style="Coverage.TLabel", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", pady=6)
        self.layer_buttons = {}
        for key, label in LAYER_LABELS.items():
            button = ttk.Checkbutton(right, text=label, variable=self.layers[key], command=self._redraw, style="Coverage.TCheckbutton")
            button.pack(anchor="w", pady=3)
            self.layer_buttons[key] = button
        for key, label in (("covered", "绿色：预计覆盖"), ("uncovered", "红色：预计遗漏"), ("overlap", "紫色：重复覆盖")):
            ttk.Label(right, text=label, style="Coverage.TLabel").pack(anchor="w", pady=2)
        ttk.Label(right, text="输入来源", style="Coverage.TLabel", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", pady=(14, 6))
        self.sample = tk.StringVar(value="split")
        self.sample_selector = ttk.Combobox(right, textvariable=self.sample, values=SYNTHETIC_SHAPES, state="readonly", width=16)
        self.sample_selector.bind("<<ComboboxSelected>>", self._sample_changed)
        self.mask_kind = tk.StringVar(value="segmented_stain")
        if not self.input_locked:
            self.sample_selector.pack(fill="x")
            ttk.Label(right, text="合成样例，非实拍", style="Coverage.TLabel", foreground=MUTED).pack(anchor="w", pady=4)
            ttk.Combobox(right, textvariable=self.mask_kind, values=("segmented_stain", "manual_region", "manual_roi_segmented", "reference_annotation"), state="readonly", width=16).pack(fill="x", pady=(4, 4))
            ttk.Button(right, text="载入原图 + 二值 Mask", command=self._choose_input, style="Coverage.TButton").pack(fill="x")
        else:
            self.sample_selector.state(["disabled"])
            ttk.Label(right, text="输入绑定工作台目标与帧", style="Coverage.TLabel", foreground=MUTED).pack(anchor="w")
        footer = ttk.Frame(self.root, style="Coverage.TFrame", padding=(26, 12))
        footer.pack(side="bottom", fill="x", before=body)
        self.export_button = ttk.Button(footer, text="保存本任务模拟预览" if self.exporter_factory else "导出 PNG + 可复现归档", command=self._choose_export, style="Coverage.TButton")
        self.export_button.pack(side="right")
        ttk.Label(footer, textvariable=self.status, style="Coverage.TLabel", wraplength=round(800 * self.layout_scale)).pack(anchor="w")
        ttk.Label(footer, textvariable=self.export_status, style="Coverage.TLabel", foreground=MUTED, wraplength=round(800 * self.layout_scale)).pack(anchor="w", pady=3)

    def _toggle_advanced(self):
        if self.advanced_frame.winfo_manager():
            self.advanced_frame.pack_forget()
            self.advanced_button.configure(text="展开高级规划参数")
        else:
            self.advanced_frame.pack(fill="x", before=self.metrics_heading, pady=(0, 6))
            self.advanced_button.configure(text="收起高级规划参数")

    def _alpha_changed(self, value):
        self.alpha = max(0.0, min(1.0, float(value) / 100))
        self.alpha_value.set(self.alpha * 100)
        self.alpha_text.set(f"{self.alpha:.0%}")
        self._redraw()

    def _update_provenance(self):
        self.provenance.set(f"{self.dataset.input.target_id} · {self.dataset.input.frame_id or '帧未提供'}\n{self.dataset.input.mask_kind} · {self.profile.evidence.status}\n完整 Mask {self.dataset.input.mask.shape[1]} × {self.dataset.input.mask.shape[0]}\n尺度：{'未知' if self.scale is None else self.scale.evidence.status}")
        if self.source_description:
            self.provenance.set(self.provenance.get() + "\n" + self.source_description)

    def _set_controls(self, ready):
        for widget in (self.step_slider, self.previous_button, self.next_button, self.full_button, self.play_button):
            widget.state(["!disabled"] if ready else ["disabled"])
        self.export_button.state(["!disabled"] if ready and not self.export_busy else ["disabled"])

    def _invalidate(self):
        self.generation += 1
        self.current_plan = self.current_view = None
        self.playing = False
        self._cancel(self._play_id)
        self.metrics_text.set("参数已变化\n等待最新结果…")
        self.status.set("正在等待最新参数规划；旧结果已停用")
        self.playback_label.configure(text="驻点进度 — / —")
        self._set_controls(False)
        self.canvas.delete("all")
        self._photo = None
        self._photos.pop("plan", None)
        self.canvas.create_text(20, 24, text="等待最新覆盖计划…", anchor="nw", fill=INK, font=("Microsoft YaHei UI", 13))
        self._redraw_inputs()
        if self.on_invalidate is not None:
            self.on_invalidate(self)

    def _parameter_changed(self, *_):
        if self._updating or self.closed:
            return
        self._invalidate()
        self._cancel(self._debounce_id)
        self._debounce_id = self._later(160, self._submit)

    def _diameter_changed(self, *_):
        if not self._updating:
            self.diameter_edited = True
            self._parameter_changed()

    def _slider_changed(self, value):
        if not self._updating:
            self.diameter.set(f"{float(value):.1f}")

    def parameter_snapshot(self):
        """Read UI settings without submitting; preserves pending valid edits."""
        text = self.diameter.get().strip()
        if not text:
            profile = replace(self.base_profile, diameter=None, evidence=Evidence("assumed", f"derived:{self.base_profile.sha256}"),
                semantics="geometric_proxy", parent_sha256=self.base_profile.sha256) if self.diameter_edited else self.base_profile
        elif not self.diameter_edited and float(text) == self.base_profile.diameter:
            profile = self.base_profile
        else:
            profile = self.base_profile.with_diameter(float(text))
        policy = replace(self.policy, target_coverage=float(self.goal.get()) / 100,
            max_stations=int(self.budget.get()), candidate_stride_px=int(self.stride.get()))
        return profile, policy

    def _submit(self):
        self._debounce_id = None
        try:
            self.profile, self.policy = self.parameter_snapshot()
            self._updating = True
            if self.profile.diameter is not None:
                self.diameter_slider.configure(to=max(float(self.diameter_slider.cget("to")), self.profile.diameter))
                self.slider_value.set(self.profile.diameter)
                self.diameter_slider.state(["!disabled"])
            else:
                self.diameter_slider.state(["disabled"])
            self._updating = False
            self._update_provenance()
            model = self.model_override if self.model_override is not None and self.model_override.profile == self.profile else None
            self.worker.submit(CoverageRequest(self.generation, self.dataset, self.profile, self.scale, self.policy, self.constraints, model))
            self.status.set(f"离线计算中 · 请求 {self.generation}")
        except (ValueError, TypeError) as exc:
            self._updating = False
            self.status.set(f"参数无效：{exc}")
            self.metrics_text.set("无法预测\n请修正参数")

    def _poll(self):
        self.heartbeat_count += 1
        result = self.worker.poll()
        if result is not None and result.generation == self.generation:
            if result.error:
                self.status.set("规划失败：" + result.error)
                self.metrics_text.set("无法预测\n结果不可用")
            else:
                self.current_plan = result.plan
                self.accepted_generation = result.generation
                self._updating = True
                self.step.set(min(self.initial_step_count, len(result.plan.stations)) if self.initial_step_count is not None else len(result.plan.stations))
                self.initial_step_count = None
                self.step_slider.configure(to=max(1, len(result.plan.stations)))
                self.step_slider.set(self.step.get())
                self._updating = False
                self._set_controls(True)
                reasons = ", ".join(result.plan.reason_codes)
                self.status.set(f"{result.plan.status} · {self.profile.evidence.status} · {reasons or '规划就绪，仅模拟'}")
                self._redraw()
        try:
            path, error = self._export_results.get_nowait()
            self.export_busy = False
            self.export_status.set(f"导出失败：{error}" if error else f"已保存归档：{path}")
            self._set_controls(self.current_plan is not None)
            if error is None and self.on_exported is not None:
                self.on_exported(path)
        except queue.Empty:
            pass
        self._later(40, self._poll)

    def _options(self):
        return RenderOptions(**{key: variable.get() for key, variable in self.layers.items()}, alpha=self.alpha)

    def _display_options(self):
        options = self._options()
        if self.original_only.get():
            return replace(options, **{key: key == "original" for key in self.layers})
        return options

    def _draw_image(self, canvas, pixels, key):
        size = (max(1, canvas.winfo_width()), max(1, canvas.winfo_height()))
        if key != "plan" and self._image_sizes.get(key) == (id(pixels), size):
            return
        image = Image.fromarray(pixels)
        factor = min(size[0] / image.width, size[1] / image.height)
        image = image.resize((max(1, round(image.width * factor)), max(1, round(image.height * factor))), Image.Resampling.NEAREST)
        canvas.delete("all")
        self._photos[key] = ImageTk.PhotoImage(image, master=self.root)
        self._image_sizes[key] = (id(pixels), size)
        canvas.create_image(canvas.winfo_width() / 2, canvas.winfo_height() / 2, image=self._photos[key], anchor="center")

    def _redraw_inputs(self):
        # Inputs remain visible while planning is pending/unavailable. Never
        # re-segment, crop, select a component or alter the source arrays here.
        if self._input_dataset is not self.dataset:
            self._input_dataset = self.dataset
            self.input_images = {"original": self.dataset.image_rgb}
            pixels = self.dataset.image_rgb.copy()
            mask = self.dataset.input.mask
            pixels[mask] = np.rint(0.75 * pixels[mask] + 0.25 * np.array(COLORS["target"])).astype(np.uint8)
            self.input_images["mask"] = immutable_array(pixels)
            title = MASK_TITLES.get(self.dataset.input.mask_kind, "输入 Mask")
            self.mask_title.set(f"02  {title} · 完整目标")
        self._draw_image(self.original_canvas, self.input_images["original"], "original")
        self._draw_image(self.mask_canvas, self.input_images["mask"], "mask")

    def _resize(self, _event=None):
        self._cancel(self._resize_id)
        self._resize_id = self._later(80, self._redraw)

    def _redraw(self):
        self._redraw_inputs()
        if self.current_plan is None:
            return
        self.current_view = render_coverage(self.dataset.image_rgb, self.current_plan,
            options=self._display_options(), step_count=self.step.get())
        self._draw_image(self.canvas, self.current_view.image_rgb, "plan")
        self._photo = self._photos["plan"]
        count, total = self.step.get(), len(self.current_plan.stations)
        self.playback_label.configure(text=f"驻点进度 {count} / {total} · 虚线仅表示访问顺序")
        metrics = self.current_view.metrics
        if metrics is None:
            self.metrics_text.set("预计覆盖：无法预测\n遗漏面积：未知\n驻点：0\n" + ", ".join(self.current_plan.reason_codes))
        else:
            rate = "无目标" if metrics.geometric_coverage_rate is None else f"{metrics.geometric_coverage_rate:.1%}"
            conservative = "未知 / 前缀未计算" if metrics.conservative_coverage_rate is None else f"{metrics.conservative_coverage_rate:.1%}"
            label = "人工区域面积" if self.dataset.input.mask_kind == "manual_region" else "目标 Mask 面积"
            self.metrics_text.set(f"{label}：{metrics.target_area_px:,} px\n预计几何覆盖：{rate}\n遗漏：{metrics.uncovered_area_px:,} px\n重复覆盖：{metrics.overlap_target_area_px:,} px\n当前驻点：{count} / {total}\n保守覆盖：{conservative}")
        if self.on_view is not None:
            self.on_view(self)

    def _step_changed(self, value):
        if not self._updating:
            self.show_step(round(float(value)))

    def show_step(self, count):
        if self.current_plan is None:
            return
        self._updating = True
        self.step.set(max(0, min(int(count), len(self.current_plan.stations))))
        self.step_slider.set(self.step.get())
        self._updating = False
        self._redraw()

    def toggle_play(self):
        if self.current_plan is None:
            return
        self.playing = not self.playing
        self._cancel(self._play_id)
        if self.playing:
            if self.step.get() >= len(self.current_plan.stations):
                self.show_step(0)
            self._advance()

    def _advance(self):
        if not self.playing or self.current_plan is None:
            return
        if self.step.get() >= len(self.current_plan.stations):
            self.playing = False
            return
        self.show_step(self.step.get() + 1)
        self._play_id = self._later(450, self._advance)

    def _sample_changed(self, _event=None):
        if self.input_locked:
            return
        if self.constraints.to_dict() != CoverageConstraints().to_dict() or self.scale is not None:
            # Constraints/scale refer to the existing frame. Do not silently
            # carry them to a different synthetic image.
            self.status.set("带尺度或区域约束的输入请重新启动面板选择新场景")
            return
        self.dataset = synthetic_dataset(self.sample.get())
        self._update_provenance()
        self._parameter_changed()

    def _choose_input(self):
        if self.input_locked:
            return
        if self.scale is not None or self.constraints.to_dict() != CoverageConstraints().to_dict():
            messagebox.showinfo("输入绑定", "当前尺度或约束绑定原图，请用启动参数载入新场景。", parent=self.root)
            return
        image_path = filedialog.askopenfilename(title="选择原图", parent=self.root)
        if not image_path:
            return
        mask_path = filedialog.askopenfilename(title="选择完整二值 Mask（0/1/255）", parent=self.root)
        if not mask_path:
            return
        try:
            self.dataset = load_image_mask(image_path, mask_path, mask_kind=self.mask_kind.get())
            self._update_provenance()
            self._parameter_changed()
        except (ValueError, OSError) as exc:
            messagebox.showerror("输入无效", str(exc), parent=self.root)

    def _choose_export(self):
        if self.exporter_factory is not None:
            self.export_to(None)
            return
        directory = filedialog.askdirectory(title="选择归档父目录（将创建新目录）", parent=self.root)
        if directory:
            child = Path(directory) / f"coverage_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
            self.export_to(child)

    def export_to(self, destination):
        if self.current_plan is None or self.export_busy:
            raise ValueError("CURRENT_PREVIEW_REQUIRED_OR_EXPORT_BUSY")
        snapshot = (self.dataset.image_rgb, self.current_plan, self.constraints, self._display_options(), self.step.get())
        exporter = self.exporter_factory(self) if self.exporter_factory is not None else export_coverage_preview
        self.export_busy = True
        self.export_button.state(["disabled"])
        self.export_status.set("正在导出当前画面与完整计划…")
        results = self._export_results
        def save():
            try:
                path = exporter(snapshot[0], snapshot[1], destination,
                    constraints=snapshot[2], options=snapshot[3], step_count=snapshot[4])
                outcome = (str(path), None)
            except Exception as exc:
                outcome = (None, f"{type(exc).__name__}: {exc}")
            results.put_nowait(outcome)
        self.export_thread = threading.Thread(target=save, name="coverage-export", daemon=True)
        self.export_thread.start()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.playing = False
        for identifier in tuple(self._after_ids):
            self._cancel(identifier)
        if self.owns_worker:
            self.worker.close()
        for canvas in (self.canvas, self.original_canvas, self.mask_canvas):
            canvas.delete("all")
        self._photo = None
        self._photos.clear()
        self._image_sizes.clear()
        if self.on_close is not None:
            self.on_close(self)
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Independent offline coverage preview; no hardware actions")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--synthetic", choices=SYNTHETIC_SHAPES)
    source.add_argument("--image", type=Path)
    source.add_argument("--archive", type=Path)
    parser.add_argument("--mask", type=Path)
    parser.add_argument("--mask-kind", choices=("segmented_stain", "manual_region", "manual_roi_segmented", "reference_annotation"))
    profile_args = parser.add_mutually_exclusive_group()
    profile_args.add_argument("--diameter-px", type=float)
    profile_args.add_argument("--profile", type=Path)
    parser.add_argument("--scale", type=Path)
    parser.add_argument("--export-only", type=Path, help="headless export into a new directory")
    args = parser.parse_args(argv)
    if args.archive:
        if any((args.diameter_px is not None, args.profile, args.scale, args.mask, args.mask_kind)):
            parser.error("archive already contains its input, profile, scale and constraints")
        archive = load_coverage_preview(args.archive)
        dataset = CoverageDataset(archive.image_rgb, archive.plan.input)
        profile, scale, policy, constraints, options = archive.plan.model.profile, (
            None if archive.plan.model.kernel is None else archive.plan.model.kernel.scale), archive.plan.policy, archive.constraints, archive.options
        plan, step_count = archive.plan, archive.step_count
    else:
        if args.diameter_px is None and args.profile is None:
            parser.error("supply --diameter-px or --profile; there is no implicit nozzle size")
        if args.image and (args.mask is None or args.mask_kind is None):
            parser.error("--image requires --mask and explicit --mask-kind")
        if args.synthetic and (args.mask is not None or args.mask_kind is not None):
            parser.error("synthetic inputs cannot reuse an external mask")
        dataset = synthetic_dataset(args.synthetic) if args.synthetic else load_image_mask(args.image, args.mask, mask_kind=args.mask_kind)
        profile = profile_from_dict(json.loads(args.profile.read_text(encoding="utf-8"))) if args.profile else FootprintProfile(
            "virtual-circle-v1", args.diameter_px, "px", Evidence("assumed", "explicit-cli-simulation"))
        scale = scale_from_dict(json.loads(args.scale.read_text(encoding="utf-8"))) if args.scale else None
        policy, constraints, options, step_count = CoveragePolicy(candidate_stride_px=2), CoverageConstraints(), (
            RenderOptions() if args.export_only else PREVIEW_OPTIONS), None
        from demo.coverage_session import compute_request
        plan = compute_request(CoverageRequest(1, dataset, profile, scale, policy, constraints)) if args.export_only else None
    if args.export_only:
        destination = export_coverage_preview(dataset.image_rgb, plan, args.export_only,
            constraints=constraints, options=options, step_count=step_count)
        print(destination)
        return
    panel = StandaloneCoveragePanel(dataset, profile, scale=scale, policy=policy, constraints=constraints, options=options,
        model=plan.model if args.archive else None, initial_step_count=step_count)
    panel.root.mainloop()


if __name__ == "__main__":
    main()
