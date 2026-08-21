# MicroCleaningVision 项目介绍 — 面向 AI 协作者

> 本文件写给任何第一次参与本项目的 AI。目标：让你在没有看过任何历史对话的情况下，
> 能立即理解项目是什么、当前状态如何、成员 A 是谁、以及你该如何帮助 A 推进工作。

---

## 0. 阅读顺序（强制）

1. 本文件（项目介绍）
2. `project_state.yaml`（项目唯一事实源）
3. `AGENTS.md`（团队协作规则）
4. `docs/team/成员A_数据与模型任务手册.md`（A 的长期手册）
5. `docs/team/团队任务看板.md`（当前迭代任务卡）

---

## 1. 项目是什么

### 一句话描述

**显微智能自清洗科研平台**——研究机器人怎样感知、处理并复检微观表面的 3D 打印后清洗过程。

### 技术本质

这是一个**最小闭环（Minimum Closed Loop, MCL）**框架，核心链路是：

```
Observation（观测） → StateEstimate（状态估计） → ActionRequest（动作申请）
→ SafetyDecision（安全审批） → ExecutionReceipt（执行回执）
→ VerificationResult（复检结果） → Episode（回合记录）
```

### 项目不做什么

- ❌ 不负责机械结构设计与加工
- ❌ 不负责 STM32 固件实现
- ❌ 不打开真实 COM 口控制泵、电机、喷头
- ❌ 不宣称真实清洗有效
- ❌ 不训练深度模型（除非 HSV 基线已出现稳定失败）

### 当前证据等级

| 维度 | 等级 | 含义 |
|---|---|---|
| software_components | E1 | 单组件可重复，项目`.venv`中54项测试全部执行并通过 |
| software_integration | E1 | 软件回放闭环可运行，但只用合成/程序生成数据 |
| overall_hardware | E0 | 无真实硬件、无真实图像、无标定 |

---

## 2. 三人团队结构

### 成员 A：数据与模型（你正在协助的人）

- **目录**：`microcleaning/data_learning/` + `test/data_learning/`
- **职责**：把真实世界变成算法可以学习、团队可以复查的证据
- **不是**：负责拍照的人 / 负责硬件的人 / 负责决策的人
- **输出给 B**：原始图片路径、采集条件、人工标注、质量报告

### 成员 B：视觉识别与测量

- **目录**：`microcleaning/vision/` + `test/vision/`
- **职责**：从图像得到污染 mask、面积、中心

### 成员 C：目标规划与控制仿真

- **目录**：`microcleaning/control_system/` + `test/control_system/`
- **职责**：从 mask 生成目标点、路线，控制仿真

### 共享文件（三人共同维护）

- `microcleaning/contracts.py` — 8 个契约对象（Observation, StateEstimate 等）
- `microcleaning/ports.py` — CameraPort / ControllerPort 抽象接口
- `project_state.yaml` — 唯一事实源
- `AGENTS.md` — 协作规则

---

## 3. 项目文件结构详解

```
MicroCleaningVision/
├── AGENTS.md                          # 协作规则（必读）
├── main.py                            # 入口：默认跑 Mock MCL
├── project_state.yaml                 # 唯一事实源（必读）
├── README.md                          # 项目概览
├── requirements.txt                   # 基础依赖（空，用标准库）
├── requirements/
│   ├── perception-opencv.txt          # numpy + opencv（A/B 真实图像时安装）
│   └── control-serial.txt             # pyserial（C 获得协议后安装）
├── scripts/
│   └── check_environment.py           # 环境检查脚本
│
├── microcleaning/                     # ★ 核心代码 ★
│   ├── contracts.py                   # 8 个契约对象（全项目共享）
│   ├── ports.py                       # CameraPort / ControllerPort 接口
│   ├── __init__.py
│   │
│   ├── data_learning/                 # ★ A 的目录 ★
│   │   ├── image_quality.py           # 核心算法：像素→质量指标→质量分数
│   │   ├── replay_camera.py           # 适配器：图片文件→Observation
│   │   ├── inspect_images.py          # 命令行工具：批量质量检查
│   │   └── __init__.py
│   │
│   ├── vision/                        # B 的目录
│   │   ├── contamination.py           # 污染测量数据格式
│   │   ├── state_estimator.py         # 状态估计器
│   │   ├── verification.py            # 前后图复检
│   │   └── __init__.py
│   │
│   └── control_system/                 # C 的目录
│       ├── governor.py                # 安全治理器
│       ├── fixed_rule.py              # 固定规则动作申请
│       ├── fake_serial.py             # 伪串口控制器
│       ├── mock_mcl.py                # 纯合成 Mock 闭环
│       ├── replay_mcl.py              # 软件回放闭环
│       ├── episode_store.py           # Episode JSON 持久化
│       └── __init__.py
│
├── test/                              # ★ 测试 ★
│   ├── data_learning/                 # ★ A 的测试（20项）★
│   │   ├── test_data_learning.py      # 图像质量与合成像素边界
│   │   └── test_dataset_manifest.py   # 数据集导入、去重和哈希检查
│   ├── vision/                        # B 的测试（3 项）
│   │   └── test_vision.py
│   └── control_system/                # C 的测试（21 项）
│       ├── test_mock_mcl.py
│       └── test_control_system.py
│
├── docs/                              # 文档体系
│   ├── README.md
│   ├── architecture/                  # 架构、接口、路线图
│   ├── team/                          # 团队手册、任务看板
│   ├── research/                      # 研究方法、失败分类
│   ├── future_hardware/               # 未来硬件相关
│   └── archive/                       # 归档旧文档
│
├── legacy/                            # 旧代码归档（不参与主线）
│   ├── camera/ detection/ models/ planning/ communication/ ...
│   └── project_docs/
│
└── .codex/skills/                     # Skill 定义
    └── visual-closed-loop-task/
```

---

## 4. 成员 A 的文件详解

### 4.1 库文件（被其他代码调用，不直接运行）

#### `microcleaning/data_learning/image_quality.py`

**这是 A 最核心的文件。** 实现了从真实像素到质量分数的完整算法链。

| 类/函数 | 作用 | 输入 | 输出 |
|---|---|---|---|
| `ImageMetrics` | 原始像素指标（不可变） | — | width_px, height_px, channels, laplacian_variance, mean_intensity, dark_fraction, bright_fraction |
| `ImageQuality` | 质量分数（0~1，不可变） | — | focus, illumination, confidence, flags() |
| `ImageQualityPolicy` | 转换策略（不可变） | — | focus_reference, acceptable_mean_low/high, dark_pixel_max, bright_pixel_min, max_clipped_fraction, minimum_quality |
| `ImageInspection` | 一次完整检查结果 | — | path, sha256, algorithm_version, policy, metrics, quality |
| `measure_image_quality()` | **核心算法** | numpy 图像数组 | (ImageMetrics, ImageQuality) |
| `inspect_image_file()` | 从文件检查 | 文件路径 | ImageInspection |
| `build_observation()` | 封装 Observation | task_id, frame_id, raw_image_ref, quality | Observation |

**算法逻辑**（`measure_image_quality`）：
```
1. 验证输入：numpy 数组、非空、2D 或 3D(3/4通道)、uint8
2. 转灰度（如果是 BGR/BGRA）
3. 计算原始指标：
   - laplacian_variance = cv2.Laplacian(gray, CV_64F).var()  → 清晰度
   - mean_intensity = gray.mean()                              → 平均亮度
   - dark_fraction = 暗像素数/总像素数                          → 过暗比例
   - bright_fraction = 亮像素数/总像素数                        → 过亮比例
4. 计算质量分数：
   - focus = clamp01(laplacian_variance / focus_reference)
   - illumination = min(brightness_score, clipped_score)
   - confidence = min(focus, illumination)
5. 返回 (ImageMetrics, ImageQuality)
```

**依赖**：`cv2` (opencv-python), `numpy`。通过 `_load_perception_dependencies()` 延迟加载，不在文件顶部 import。

#### `microcleaning/data_learning/replay_camera.py`

**适配器**：把图片文件包装成 `Observation`，实现 `CameraPort` 接口。

| 类/函数 | 作用 |
|---|---|
| `ReplayFrame` | 登记一个回放帧：phase（pre/post）、frame_id、raw_image_ref、可选 quality |
| `ReplayCamera` | CameraPort 实现 |
| `ReplayCamera.capture(task_id, phase)` | 返回 Observation；如果 ReplayFrame 没提供 quality，则从图片像素实时计算 |
| `ReplayCamera.inspection(phase)` | 返回某次真实像素检查的 ImageInspection |

**两种模式**：
- Mock 模式：ReplayFrame 提供 `quality=ImageQuality(...)` ，直接用，不解码像素
- Gate 1 模式：ReplayFrame `quality=None`，从图片文件真实解码像素并计算质量

#### `microcleaning/data_learning/inspect_images.py`

**命令行工具**：批量检查一个目录下的所有图片。

```powershell
# 用法
python -m microcleaning.data_learning.inspect_images <图片目录>
python -m microcleaning.data_learning.inspect_images <图片目录> --output output/quality_report.json
```

输出 JSON 数组，每张图包含：status, file, sha256, algorithm_version, policy, metrics, quality, quality_flags。

### 4.2 测试文件

#### `test/data_learning/test_data_learning.py`

`test_data_learning.py`有17项图像测试，另有`test_dataset_manifest.py`的3项数据集测试；A模块当前共20项。

**ImagingQualityTests（5项）**— 基础接口测试：
| # | 测试 | 验证 |
|---|---|---|
| 1 | `test_low_focus_becomes_machine_readable_flag` | 低 focus 产生 FOCUS_LOW 标志 |
| 2 | `test_invalid_quality_score_is_rejected` | 非法分数 (>1.0) 被拒绝 |
| 3 | `test_replay_camera_converts_registered_frame_to_observation` | Mock 模式：ReplayCamera 正确转换 |
| 4 | `test_replay_camera_decodes_real_pixels_and_keeps_hash` | Gate 1 模式：真实解码像素、计算 sha256、生成质量指标 |
| 5 | `test_real_pixel_mode_rejects_missing_and_undecodable_files` | 不存在的文件和不可解码的文件被拒绝 |

**ImageAlgorithmTests（12 项）**— 合成像素边界测试：
| # | 测试 | 验证 |
|---|---|---|
| 1 | 全黑图 | dark_fraction≈1.0, FOCUS_LOW + ILLUMINATION_LOW |
| 2 | 全白图 | bright_fraction≈1.0, ILLUMINATION_LOW |
| 3 | 噪声 vs 模糊 | 锐利图 laplacian_variance > 模糊图 |
| 4 | 均匀灰 128 | illumination 通过但 FOCUS_LOW |
| 5 | 灰度图 channels=1 | 通道数正确 |
| 6 | BGR 三通道 channels=3 | 通道数正确 |
| 7 | BGRA 四通道 channels=4 | 通道数正确 |
| 8 | 非 numpy 输入 | ValueError |
| 9 | 空数组 | ValueError |
| 10 | 1D/2通道数组 | ValueError |
| 11 | float32/uint16 | ValueError |
| 12 | 自定义 policy | focus 计算公式验证 |

所有测试通过 `@unittest.skipUnless(HAS_PERCEPTION_DEPS)` 保护——如果 opencv/numpy 没装，算法测试自动跳过。

---

## 5. 如何运行 A 的代码

### 5.1 前置条件

```powershell
# 1. 确认虚拟环境
# 按 Ctrl+Shift+P → Python: Select Interpreter → 选 .\.venv\Scripts\python.exe

# 2. 安装依赖（已安装则跳过）
.\.venv\Scripts\python.exe -m pip install -r requirements/perception-opencv.txt
# 当前版本：numpy 2.5.2, opencv-python 4.14.0.94
```

### 5.2 跑测试

```powershell
# 只跑A的20项测试
python -m unittest test.data_learning.test_data_learning -v

# 跑全部54项测试（当前基线；新增有效能力后数量可增加）
python -m unittest discover -s test -v
```

### 5.3 跑质量检查工具

```powershell
# 检查一个目录下的所有图片
python -m microcleaning.data_learning.inspect_images <图片目录>

# 输出到 JSON 文件
python -m microcleaning.data_learning.inspect_images <图片目录> --output output/quality_report.json
```

### 5.4 合成像素快速演示（不需要真实照片）

如果想立刻看到 `inspect_images` 输出效果，可以生成几张合成图：

```powershell
python -c "
import numpy as np, cv2, os
os.makedirs('temp_demo', exist_ok=True)
cv2.imwrite('temp_demo/black.png', np.zeros((64,64), dtype=np.uint8))
cv2.imwrite('temp_demo/white.png', np.full((64,64), 255, dtype=np.uint8))
cv2.imwrite('temp_demo/noisy.png', np.random.randint(0,256,(64,64), dtype=np.uint8))
cv2.imwrite('temp_demo/gray128.png', np.full((64,64), 128, dtype=np.uint8))
print('4 张合成图已生成')
"
python -m microcleaning.data_learning.inspect_images temp_demo
```

---

## 6. A 当前状态总结

### 已完成 ✅

| 项 | 状态 | 证据 |
|---|---|---|
| 图像质量算法（拉普拉斯方差+亮度+暗亮比例） | 已实现 | `image_quality.py` + 12 项合成像素测试 |
| 质量策略与阈值 | 已实现 | `ImageQualityPolicy`，Gate 1 冒烟阈值 |
| ReplayCamera 适配器 | 已实现 | `replay_camera.py`，支持 Mock 和 Gate 1 两种模式 |
| 批量质量检查工具 | 已实现 | `inspect_images.py`，命令行可用 |
| SHA-256 可追溯 | 已实现 | 每次检查记录文件哈希 |
| 异常输入拒绝 | 已实现 | 不存在/不可解码/非法 dtype/非法维度全部被拒 |
| 测试覆盖 | 20/20 通过 | 图像质量17项 + 数据集清单3项 |

### 未完成 ❌

| 项 | 阻塞原因 | 优先级 |
|---|---|---|
| 真实 USB 显微镜图像 | A 无法拍照 | **最高** |
| 数据清单（image_id, sample_id, capture_session 等） | 没有真实图片 | 高 |
| 人工标注（contamination/ignore 多边形） | 没有真实图片 | 高 |
| 前后图成对采集 | 没有真实图片 | 中 |
| 像素→毫米标定 | 没有标定板 | 低（P3） |
| 真实模型训练 | HSV尚未完成真实图片误差分析 | 低（P5） |

### 当前最大瓶颈

**`current_dataset: structure_initialized_empty`** — 数据集目录、清单工具和检查程序已经建立，但真实记录数仍为0。现在的阻塞不是缺程序，而是没有一张团队真实显微镜照片流过 A→B→C 链路。

---

## 7. 作为 AI，你该如何帮助 A

### 7.1 当 A 能拍照时

按以下优先级协助：

1. **生成数据清单模板**（CSV/JSON），字段参考 `docs/team/成员A_数据与模型任务手册.md` 第 4.5 节
2. **自动命名程序**：按 `pla_001_<日期>_<批次>_<序号>` 格式生成文件名
3. **清单检查器**：检查重复 ID、缺失字段、路径有效性
4. **运行 `inspect_images` 并分析报告**：解释 quality_flags、对比不同采集条件的质量差异
5. **标注辅助**：把标注 JSON 转换为 mask 的程序草案
6. **数据划分**：按样本/批次隔离开发集和保留集，防止相邻帧泄漏

### 7.2 当 A 还不能拍照时（当前情况）

按以下优先级协助：

1. **补充合成像素测试**：如果有新的边界情况（如 16 位图像、非标准分辨率），增加测试
2. **生成合成图片演示**：用 numpy+cv2 生成演示图，跑通 inspect_images 输出
3. **设计数据清单字段**：定义每张图的元数据格式
4. **编写清单检查器**：独立于真实数据的 Python 脚本
5. **准备标注工具环境**：介绍 labelme/VIA 等工具的使用
6. **学习辅导**：解释 HSV 分割、图像配准、数据版本管理等 A 需掌握的知识

### 7.3 永远不要做的事

- ❌ 不要制造人工真值（假的标注、假的质量分数）
- ❌ 不要把合成测试结果写成"系统已验证"
- ❌ 不要调低质量阈值让失败图通过
- ❌ 不要在 HSV 基线尚未完成真实图片误差分析时建议训练深度模型
- ❌ 不要修改 `contracts.py` 或 `ports.py` 而不经三人评审
- ❌ 不要导入 `legacy/` 下的任何代码

---

## 8. 对抗性审查（本文件的已知局限）

### 可能让 AI 困惑的地方

| # | 局限 | 影响 | 应对 |
|---|---|---|---|
| 1 | **目录刚重组过**：旧文档（`说明/`、`docs/00_项目研发宪章.md`）可能仍在磁盘上，但已归档到 `docs/archive/` | AI 可能读到旧路径并尝试引用 | 始终以本文件和 `project_state.yaml` 为准；遇到引用 `说明/` 或 `00_项目研发宪章.md` 的内容，视为历史归档 |
| 2 | **`legacy/` 代码引用了不存在的依赖**（loguru, PyYAML, ultralytics, torch） | AI 可能尝试安装这些包来跑 legacy | legacy 是归档代码，不参与主线。禁止在主线代码中 import legacy |
| 3 | **像素与毫米不能混用** | `mcl-v0.1` 已把B的误差改为 `uncertainty_px`，但真实标定仍不存在 | 检查无标定状态的毫米字段必须为空；`simulation-normalized-v0`只能用于FakeSerial |
| 4 | **`project_state.yaml` 版本可能滞后于实际对话** | 对话中可能已完成某些任务但 yaml 未更新 | 读取 yaml 后，对比当前代码和测试状态。如有差异，以实际代码为准并提醒 A 更新 yaml |
| 5 | **54项测试依赖正确的任务环境** | 没有安装OpenCV/NumPy时感知和Demo测试会被skip | 使用项目`.venv`安装`requirements/perception-opencv.txt`，并确认最终结果没有skip |
| 6 | **`inspect_images` 的默认阈值是 Gate 1 冒烟阈值** | AI 可能把这些阈值当成显微成像标准 | 明确告知 A：这些阈值只证明"链路通了"，不是科学结论。正式阈值需要固定设备+光照+倍率的数据 |
| 7 | **项目 Skill（`.codex/skills/`）可能与实际代码不同步** | Skill 是通用模板，可能引用旧接口 | 以实际代码和本文件为准。Skill 只作为辅助参考 |
| 8 | **三人时间投入不稳定** | 任务看板上的任务可能没有按预期完成 | AI 不应假设某个任务已完成。每次协作前先读 `project_state.yaml` 和实际代码确认状态 |

### 自我检查清单

作为 AI 协作者，在每次回复 A 之前，确认：

- [ ] 我读了 `project_state.yaml` 吗？
- [ ] 我确认了 A 的当前代码状态（而不是只看文档）吗？
- [ ] 我建议的下一步能缩短到下一 Evidence Level 的距离吗？
- [ ] 我有没有引入 A 不需要的复杂度？
- [ ] 我区分了"代码框架"、"合成测试"、"真实像素"和"真实硬件"吗？
- [ ] 我有没有在 A 不能拍照时建议拍照相关的任务？
- [ ] 我的建议是否符合 AGENTS.md 的"Simple First, Complexity by Evidence"？

---

## 9. 快速参考卡片

### A 的关键文件
```
代码:  microcleaning/data_learning/image_quality.py
       microcleaning/data_learning/replay_camera.py
       microcleaning/data_learning/inspect_images.py
测试:  test/data_learning/test_data_learning.py
手册:  docs/team/成员A_数据与模型任务手册.md
看板:  docs/team/团队任务看板.md
```

### A 的核心命令
```powershell
# 跑 A 的测试
python -m unittest test.data_learning.test_data_learning -v

# 跑全部测试
python -m unittest discover -s test -v

# 质量检查
python -m microcleaning.data_learning.inspect_images <目录> --output output/report.json

# 合成图演示
python -c "import numpy as np,cv2,os;os.makedirs('tmp',exist_ok=True);[cv2.imwrite(f'tmp/{n}.png',np.full((64,64),v,dtype=np.uint8)) for n,v in [('black',0),('white',255),('gray',128)]];print('done')"
python -m microcleaning.data_learning.inspect_images tmp
```

### A 的当前任务
```
A-P1-01：采集至少12张、两个批次的USB显微镜图片，建立清单并标注5张
前置条件：能使用USB显微镜
当前状态：BLOCKED（无法拍照）
备用任务：A-BACKUP-01 — 读懂代码、用普通图片走通检查命令、设计清单字段
```

### 8 个契约对象一句话解释
| 对象 | 一句话 |
|---|---|
| Observation | 一张图从哪里来、质量怎样 |
| StateEstimate | 系统认为污染在哪里、有多大 |
| ActionRequest | 软件建议做什么（不是硬件命令） |
| SafetyDecision | ALLOW / DENY / HUMAN |
| ExecutionReceipt | 控制端实际返回了什么 |
| VerificationResult | 前后图是否可比、污染是否减少 |
| Episode | 一次任务从输入到结果的完整记录 |
| FailureRecord | 失败发生在哪一层、怎样恢复 |
