# MicroCleaningVision 交接文档对照审计

对照对象：`/cursor/stores/bc-3ca064e4-6927-4391-a491-574b362f73a5/docs/MicroCleaningVision_Project_Handover.md`（交接文档写的路径 `/cursor/stores/self/docs/...` 在本环境中不存在；实际文件在父项目 store）。

仓库：`https://github.com/fangfangzhouhang/3d`  
审计提交：`4364d70`（`origin/main`，2026-09-05）  
说明：只读核对，未改应用代码，未开 PR。交接原文未改写；事实偏差记在本报告。

---

## 1. 当前仓库结构

仓库根目录几乎只有 `MicroCleaningVision/`。早期说明在 `MicroCleaningVision/legacy/`，当前主线禁止导入。

```text
3d/
├── README.md                          # 指向 MicroCleaningVision/
└── MicroCleaningVision/
    ├── AGENTS.md
    ├── README.md
    ├── project_state.yaml             # 标注 2026-08-31，已落后于 main
    ├── main.py                        # 只跑 Mock，不碰相机/串口
    ├── p1_baseline.py                 # PR #2 新文件，未接入主线
    ├── requirements.txt
    ├── requirements/                  # perception-opencv、control-serial
    ├── microcleaning/
    │   ├── contracts.py               # mcl-v0.1
    │   ├── ports.py                   # CameraPort / ControllerPort
    │   ├── data_learning/             # A
    │   ├── vision/                    # B：官方仍是 HSV
    │   └── control_system/            # C：规划 + FakeSerial + MCV1 编解码
    ├── test/{data_learning,vision,control_system,test_demo_pipeline.py}
    ├── demo/demo_pipeline.py          # 文件/合成图软件链，不是实机 Runtime
    ├── scripts/                       # probe_usb_camera.py、probe_stm32_link.py
    ├── data/                          # 13 张 JPG + 13 JSON + 13 Mask + metadata.csv
    ├── firmware/nucleo_f401re/f401re-stage1/   # PR #1 已合并
    ├── 说明文档/                      # 真实文档树；仓库里没有 docs/
    └── legacy/                        # 旧原型，只读
```

没有名为 `STM32SerialController` 的类。`docs/` 目录不存在；导航实际在 `说明文档/`。`AGENTS.md` / `README.md` / `project_state.yaml` 仍写 `docs/README.md`。

---

## 2. 逐项核对（相对交接文档）

### 仍成立

| 交接主张 | 代码依据 |
|---|---|
| 目标是显微视觉自动清洗闭环；当前阶段是仿真 Demo → 真机联调，Integration First | `AGENTS.md`、`README.md` 与代码分层一致 |
| A/B/C 目录与测试路径 | `microcleaning/{data_learning,vision,control_system}` + `test/` 对应目录 |
| 分层：感知 → 规划 → Safety → Controller；契约在 `contracts.py` / `ports.py` | 八个数据对象均在；端口只有相机/控制器两个 ABC |
| `ReplayCamera`、`USBCamera`、`FakeSerial` 存在；FakeSerial 不是真 STM32 | `replay_camera.py`、`usb_camera.py`、`fake_serial.py`；`FakeSerialController` 实现 `ControllerPort`，不开 COM |
| USBCamera：打开 → 预热 → 抓帧 → PNG → 质量检查 → Observation；U500 实机未验证 | `usb_camera.py` + `scripts/probe_usb_camera.py`；测试用 FakeVideoCapture；`physical_capture_verified: false` |
| 质量指标与 Gate 1 冒烟阈值，不能当科研标准 | `image_quality.py`：`quality-gate1-v0` |
| 官方 B 基线仍是 HSV：阈值 → mask → 连通域 → 面积/质心/confidence | `hsv_baseline.py`（`hsv-red-baseline-v0.1`）；Demo 调的是它 |
| HSV 在 `public_001` 上失败（亮线误检） | `project_state.yaml`：IoU=0.0739，面积 +911 px，中心误差 86.87 px |
| IoU / 面积误差 / 中心误差（像素）评价工具已有 | `mask_evaluation.py` |
| C 在 `image_px` 规划：小目标中心、大目标往复扫描、多连通域分段 | `cleaning_plan.py` |
| 无标定不得把 pixel 当 mm；analyze 模式不伪造物理动作 | `state_estimator.py`、`fixed_rule.py`、Demo `analyze` 的 `action_request=None` |
| MCV1：PING / STATUS / PUMP / STOP；ACK / DONE / ERR；action_id；E-stop ≠ 软件 STOP；MOSFET 方案 | 固件 `mcv1_protocol.c` + 上位机 `stm32_protocol.py` |
| 固件 Stage 1 已合并；真机编译/烧录/USB 联调未完成 | PR #1；`verification/report.txt`：host-only，无 `.elf`、未烧录 |
| A→B→C 软件 Demo 链可跑 | `python -m demo.demo_pipeline` |
| 缺口：U500 实机、自动 Runtime、真实 Controller、pixel→mm、步进电机、清洗后视觉验证 | 见第 5 节 |
| 现在不要上 YOLO / 工业相机 / 完整 3D 打印 / 假标定 / 12V PUMP / MOVE | 与 `AGENTS.md` 证据规则一致 |

### 已过时或不准确

| 交接主张 | 当前代码事实 |
|---|---|
| 「3 幅图完成人工标注」 | **13** 张 JPG、**13** 份 Labelme JSON、**13** 张二值 Mask。CSV 里只有 `public_001` 为 `labeled`；其余 12 张转换成功但未人工验收 |
| 「CSV 还没完全填写」偏轻 | `data/metadata.csv` 13 行几乎全是 `unknown`。且存在**两套字段**：实际 CSV 用 `image_name/category/annotation_status`（`metadata_builder.py`）；`dataset_manifest.py` 另一套 `image_id/sha256/sample_id/label_status`。现网 CSV 不能直接给 manifest `check` |
| 「自动 Orchestrator 还不存在」 | 文件级编排已存在：`demo/demo_pipeline.py`（质量 → HSV → 规划 → 可选 FakeSerial）。缺的是**相机入口**和**真串口 Controller**，不是从零写编排器 |
| C 验收含「Mask → … → ActionRequest」 | 仅 `simulate`（虚拟 100×100 mm 标定）会生成 `ActionRequest`。真实图片 `analyze` 明确不生成 |
| B 输出含 regions 列表 | 官方 HSV 输出整图 mask + `component_count`，没有逐区域 bbox 列表 |
| 文档入口是 `docs/` | 实际是 `说明文档/`。`README`/`AGENTS.md`/`project_state.yaml` 的 `docs/...` 链接会断 |
| 未提及 PR #2 | `p1_baseline.py` 已在 main：Otsu/自适应阈值 + 中文 dict API，**不在** `microcleaning/vision/`，Demo/测试都不引用 |
| `project_state.yaml`：固件 `protocol_not_frozen`、串口 `unavailable`、三人组「不负责 STM32 固件」、81 项测试 | 固件与 MCV1 编解码已在仓库；`test_stm32_protocol.py` 使测试方法数约为 **87**（文档仍写 81=A47+B6+C26+Demo2） |
| `说明文档/总流程说明/系统架构说明.md` 仍写「HSV 尚未接入」「标注转换尚未加入」 | `hsv_baseline.py`、`annotation_tools.py`、`usb_camera.py` 均已存在 |
| 固件 README 仍混写旧命令 `PING`/`ARM`/`PUMP n`/`CLEAR`，路径还指向 `阶段一_基础喷液控制\` | `main.c` 走 MCV1；旧解析器仍在固件内部。上位机只说 MCV1 |
| 上位机 PUMP 默认上限 500 ms vs 固件 2000 ms | `DEFAULT_MAX_PUMP_DURATION_MS = 500`；测试拒绝 501。min=100 已对齐，**max 未对齐** |

### 尚未存在（交接当成下一步是对的）

| 交接缺口 | 现状 |
|---|---|
| `STM32SerialController` | **无此类**。只有 `stm32_protocol.py`（纯编解码，不开串口）和 `probe_stm32_link.py`（可开指定 COM，只发 PING/STATUS） |
| 一条命令：插 U500 → 拍照 → 质量 → HSV → 路径 | Demo 必须 `--input` 文件或 `--generate-sample`，不调用 `USBCamera` |
| 真机 PING/PONG、STATUS | 探测脚本在；无烧录证据、无联调记录 |
| pixel → mm 标定 | `calibration_valid` 默认 False；无标定模块 |
| HOME / MOVE / 步进电机 | 协议文档明确留到 MCV2；固件遇到未知命令拒绝 |
| 真实前后图 `VerificationResult` | `verification.py` 与 Demo `simulate` 用模拟 post mask，不是二次实拍 |
| YOLO、工业相机、完整 3D 结构、12V 泵闭环 | 均未进入主线（`legacy/models/yolo_model.py` 属旧代码） |

---

## 3. 官方软件链实际走到哪

```text
已通（软件）：
  文件/合成图
    → inspect_image_file / ReplayCamera
    → hsv_baseline.segment_contamination
    → plan_cleaning（image_px）
    → 可视化 + Episode
  simulate 额外：虚拟标定 → Safety Governor → FakeSerial → 模拟复查

未通：
  U500 VideoCapture 实机
    → 同一条 Demo
    → ControllerPort 真实现
    → USB COM
    → 已烧录的 NUCLEO
```

`main.py` 只跑 `MockMCLRunner`，与视觉链无关。

---

## 4. 现在最优先的 3 个开发任务

按 **main 上真实缺口** 排序，不是按交接文档的愿望清单。原则仍是：链路完整性 > 算法准确率；不接 12V、不做假 mm。

### 1. 把 U500 接到现有 Demo，而不是再写一套流水线

已有：`USBCamera`、`probe_usb_camera.py`、质量检查、HSV、`plan_cleaning`。  
没有：Demo/`main` 调用相机。

建议验收：插入 U500 → 一条命令完成 probe + 抓 PNG + Gate 1 质量 + HSV mask + 像素路径，输出进 `output/demo/`。先保存分辨率/FPS/SHA256/质量 flag。不把 FakeVideoCapture 测试写成实机完成。

### 2. 处理 main 上两套 B 检测器，并用已有 13 张 Mask 做定量对比

官方入口仍是 `microcleaning/vision/hsv_baseline.py`。  
PR #2 的 `p1_baseline.py` 在包根目录：import 即扫 `./data/raw_images/public/`、硬编码 Windows 路径、给 C 的 `detect_stain_from_path(v, W, H)` **宽高对调**、返回中文 bbox dict 而不是 mask/`ContaminationMeasurement`、无测试。

C 的 `plan_cleaning` 吃的是 uint8 mask，吃不了这套 API。

建议：用 `mask_evaluation.py` 在至少 3 张开发图 + 2 张留出图上对比 HSV v0.1 与 Otsu 候选；人眼复核 Mask 后才把 `annotation_status` 改成 `labeled`。若 Otsu 更好，应迁入 `vision/`、对齐契约、补测试，再让 Demo 切换。在此之前不要让 C 去迁就根目录脚本。

### 3. 不接 12V 的 PC ↔ NUCLEO：PING/STATUS，并补 `ControllerPort` 真适配器

已有：MCV1 编解码、固件 host 测试、`probe_stm32_link.py`。  
没有：烧录证据、`STM32SerialController`。

建议顺序：CubeIDE 出 `.elf` 并烧录 → `probe_stm32_link.py --port … --command ping/status` 留文本证据 → 实现 `STM32SerialController(ControllerPort)`：只翻译已批准的 `ActionRequest`，默认只允许 PING/STATUS。  
**现在不要**经主流程发 PUMP，不要加 MOVE。先记下上位机默认 PUMP 上限 500 ms 与固件 2000 ms 的差异，真上泵再统一。

---

## 5. 以后再做 vs 现在不要做

**可以等（P2 及以后）**

- YOLO / Transformer / 学习模型：HSV 连留出集对比都还没做完
- 工业相机：U500 尚未证明是瓶颈
- 完整 3D 打印结构：先固定相机和样品
- pixel → mm：要等 U500 位姿、托盘、XY 平台事实
- PUMP（12V / MOSFET / 真水）：要有 Human Gate、E-stop 记录、时长上限对齐
- MOVE / HOME / 步进电机：协议故意留到 MCV2
- 清洗后二次拍摄闭环：要先有真执行和可配准后图

**现在不要做**

- 把 `p1_baseline.py` 或 HSV 输出直接当毫米坐标 / 硬件命令
- 用 FakeSerial 或 Demo `simulate` 当「已联调 STM32」
- Agent/LLM 直写串口
- 在无标定、无 Human Gate 时接 12V 泵或电机
- 为了「高级」拆掉现有 A→B→C Demo
- 把 13 张自动 Mask 全部写成已验收 Ground Truth（只有 1 张 labeled）
- 混用两套 metadata 字段却不声明以哪份为准

---

## 6. 建议同步的仓库内文档（非本审计范围）

这些不是交接文档独有问题，下一轮改文档时应一起修：

1. `project_state.yaml`：固件已合并、测试约 87 项、`p1_baseline.py`、文档目录实为 `说明文档/`
2. `AGENTS.md`：三人组「不负责 STM32 固件」已不成立（固件在本仓 `firmware/`）
3. `README.md`：样例数写成 11 张，实际 13；`docs/` 链接全部失效
4. 固件 README：删除或标明旧 `ARM/CLEAR` 不是上位机接口；修正 `阶段一_基础喷液控制` 路径

---

## 7. 一句话

截至 `4364d70`：软件架构、HSV Demo 链、13 张候选标注、USB 相机适配器、MCV1 固件源码都在；**真机相机、真串口 Controller、官方视觉入口统一**这三件事还没做完。交接里「3 张标注、Orchestrator 完全没有、文档在 docs/」已经落后。下一步不要上 YOLO，先把 U500 接入现有 Demo，再收拾 B 的双基线，再做不接 12V 的 PING/STATUS。
