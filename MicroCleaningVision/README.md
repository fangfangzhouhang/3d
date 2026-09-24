# MicroCleaningVision

MicroCleaningVision 是一个显微表面智能处理科研项目。当前三人团队先建设视觉和上位机软件：让真实图片变成污染测量，让测量变成目标与控制仿真，并为未来STM32保留清晰接口。

## 当前真实状态

- 软件组件：E1；A/B/C软件集成：E2。程序生成图和FakeSerial已回归，第一张来源待核实的真实像素图已完成A/B评价。
- Demo v0.2：文件/合成图/相机抓帧可进入同一 Demo；`analyze`/`camera-analyze` 默认不发泵；`ping-only` 只探测通信；`arm-pump` 需人工关卡，STM32 还需 `--arm-pump`。
- 当前回归：项目`.venv`中150项测试全部执行并通过，无跳过。
- 13 张图均已人工 Mask 且 `labeled`；开发 9 / 留出 4 已冻结。Demo 默认改为邻域差异 `local`；Otsu 可对照；HSV 只留作失败对照，不再当主算法。这不是识别过关。
- `public_001.jpg` 已完成人工Mask与HSV预测比较：IoU为0.0739，证明链路可运行，也证明当前HSV基线在此图上失败。
- U500 USB数码显微镜适配器：已实现并通过FakeVideoCapture测试；尚未运行实机probe，团队采集数据仍未进入证据链。
- NUCLEO接口：MCV1 电脑端协议、`STM32SerialController` 和只读 `PING/STATUS` 已通过无硬件测试；默认不发 PUMP。**固件源码已在** `firmware/nucleo_f401re/f401re-stage1/`；**无**可烧录 `.elf`、**无**烧录记录、**无**实机 PONG/喷洗回执。
- 近场执行准则：[说明文档/总流程说明/项目作战总表.md](说明文档/总流程说明/项目作战总表.md)。同学机已报 F103 短喷，**本仓无该次证据、不冻结本机 Demo**。下一阶段：[喷水后下一阶段讨论](说明文档/总流程说明/喷水后下一阶段讨论.md)（先 U500 识别，再路径预览，再采购/数据，最后才 XY）。
- 像素到毫米标定：尚未验证。
- STM32、运动和喷洗：整体硬件仍不能写成 E3；喷出水 ≠ 清洗有效。正式 F401 仍无本仓 PONG 入库。

目录和文档完整不等于系统已经实现。动态事实见 [project_state.yaml](project_state.yaml)。

## 三人怎样合作

```text
A 数据与模型              B 视觉识别与测量            C 目标规划与控制仿真
真实图片/清单/标注 ─────> mask/面积/中心/状态 ─────> 目标点/路线/动作/FakeSerial
       ↑                                                        │
       └──────────── 失败样本与Episode反馈 ─────────────────────┘
```

- A独占 `microcleaning/data_learning/`；
- B独占 `microcleaning/vision/`；
- C独占 `microcleaning/control_system/`；
- `contracts.py` 和 `ports.py` 是共享接口，不能单人随意改。

## 目录

```text
MicroCleaningVision/
├── microcleaning/
│   ├── contracts.py
│   ├── ports.py
│   ├── data_learning/
│   ├── vision/
│   └── control_system/
├── test/
│   ├── data_learning/
│   ├── vision/
│   └── control_system/
├── demo/                    # 明确的软件演示入口
├── data/                    # 数据集：raw_images暂存+六类分类+标注预留
├── 说明文档/                # 总导航、长期规划、个人手册、Git、术语
├── legacy/                  # 旧原型，只读参考
├── AGENTS.md
└── project_state.yaml
```

## 新手从这里开始

1. [共同上下文](AGENTS.md)
2. [当前事实](project_state.yaml)
3. [文档总导航](说明文档/README.md)
4. [团队入门指南](说明文档/总流程说明/团队入门指南.md)
5. [本轮任务看板](说明文档/总流程说明/团队任务看板.md)
6. [成员A工作流程与命令百科](说明文档/成员A/成员A_工作流程与命令百科.md)
7. [U500 USB数码显微镜接入指南](说明文档/成员A/U500_USB数码显微镜接入指南.md)
8. [硬件组说明与接口](说明文档/硬件组/README.md)

## 环境和测试

虚拟环境不进入Git；每台电脑按依赖清单重建。

```powershell
cd D:\大创\3d\MicroCleaningVision
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\check_environment.py --profile mock
.\.venv\Scripts\python.exe -m unittest discover -s test -p "test*.py" -v
.\.venv\Scripts\python.exe main.py
```

需要OpenCV真实图片任务时再安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements\perception-opencv.txt
```

测试通过表示软件接口和回归正常，不表示真实相机、标定、STM32或清洗有效。

## Demo v0.2：现在最明确的程序入口

输入必须三选一：`--input`、`--generate-sample` 或 `--from-camera`。模式决定证据边界，默认不发泵。

### 1. 不依赖外部图片的完整软件模拟

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline --generate-sample --mode simulate
```

明确输入：程序生成、可重复的红色模拟污染图。

明确输出：

```text
output/demo/<run_id>/
├── input.png
├── mask.png
├── contamination_overlay.png
├── path_overlay.png
├── post_mask.png
├── summary.json
├── episode_<id>.json
└── episode_<id>.sha256
```

这个模式会产生 `ActionRequest → SafetyDecision → FakeSerial → ExecutionReceipt → VerificationResult`，但使用的是虚拟归一化标定和模拟动作后mask，不能写成真实清洗。

### 2. 分析一张手机或USB显微镜图片

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --input data\raw_images\你的图片.png `
  --mode analyze
```

这个模式输出真实像素的mask、面积、中心和像素路线。因为目前没有真实像素到毫米标定，所以 `SPRAY_AT_POINT` 申请必须为空；这不是程序缺陷，而是在证据不足时拒绝伪造物理坐标。

### 3. 从 USB 相机抓一帧再分析（默认不发泵）

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --mode analyze
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --mode camera-analyze --camera-index 0 --warmup-frames 5
```

内部调用已有 `USBCamera.capture()`，再走现有 B 分割和 C `plan_cleaning` 可视化。输出仍在 `output/demo/<run_id>/`。没有相机时会以 `CAMERA_OPEN_FAILED` 明确失败。本路径默认不发送 PUMP。

显微镜已确认编号后，用实时窗口对焦：默认叠加邻域差异 `local`（`O` 切 Otsu，`H` 切 HSV 对照，`G`/`E` 切色指数），**空格**冻结当前帧。默认 `--live` **不会**发泵。

```powershell
# 只看画面、空格只分析
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --live --camera-index 1

# 链路实喷：空格分析，识别到目标后发限时 PUMP（人必须在场；把 COM5 换成设备管理器里的 ST-LINK 口）
.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --from-camera --live --camera-index 1 `
  --mode arm-pump --confirm-pump --arm-pump `
  --controller stm32 --serial-port COM5 --pump-duration-ms 200

.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --live --camera-index 1 --wait-usb
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --live --camera-index 1 --algorithm otsu
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --live --camera-index 1 --algorithm exg
```

`--wait-usb` 适合先运行命令再插显微镜：检测到 1 号设备可读后自动打开预览。Windows 不会在你没运行程序时因插上 USB 自己启动 Demo。

点开窗口后：默认 `L` 邻域差异；`O` = Otsu，`H` = HSV（对照，不再当主算法），`G` = ExG，`E` = ExR。空格分析（武装后有目标才喷），`Q` 暂停。空格后的结果在 `output/demo/<run_id>/`。这不是识别过关，也不能写成清洗有效。

实机（显微镜 + NUCLEO 那台 Windows）开跑前只确认三个接口，不要猜：

| 接口 | 在哪看 | 填到命令里 |
|------|--------|------------|
| USB 显微镜 | `.\.venv\Scripts\python.exe scripts\probe_usb_camera.py` 或先 `--live --camera-index 0` 再试 `1` | `--camera-index N` |
| STM32 串口 | 设备管理器 → 端口 → `STLink Virtual COM Port (COMx)` | `--serial-port COMx` |
| 串口参数 | 固件固定 | `115200 8N1`（默认，不用改） |

先 `ping-only` 看到 `PONG` 且 `ESTOP=0`，再武装 live。若 `ESTOP=1`，急停未接时把 **PB12 接到 GND**。时长 `--pump-duration-ms` 只允许 100～300。云端 Agent 没有相机和 STM32，测不了真喷。

### 4. 只探测 STM32 通信（不发泵）

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline --generate-sample --mode ping-only
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --mode ping-only --serial-port COM5
```

未给 `--serial-port` 时只记录将要发送的 `MCV1|PING` / `STATUS`，不打开 COM。给了端口也只允许 PING/STATUS。

### 5. 人工确认后的定点短喷申请

无 XY 标定时使用 `PUMP_IN_PLACE`：坐标系 `nozzle_fixed`，目标 `(0,0)` 表示喷头原地脉冲，不是伪造的 `work_mm`。治理器对第一次泵动作返回 HUMAN；没有 `--confirm-pump` 不会发泵。STM32 路径还要显式 `--arm-pump` 才会翻译 PUMP。

```powershell
# 软件一拍：人工确认 + FakeSerial，不打开 COM
.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --generate-sample --mode arm-pump --confirm-pump --controller fake

# 看申请但不发泵（缺人工关卡）
.\.venv\Scripts\python.exe -m demo.demo_pipeline --generate-sample --mode arm-pump

# STM32 武装路径（仍须人在场；未接 12V 不能写成实喷有效）
.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --from-camera --mode arm-pump --confirm-pump --arm-pump `
  --controller stm32 --serial-port COM5 --pump-duration-ms 200
```

`simulate` 不能与 `--from-camera` 混用。不要把这条链写成清洗有效或识别已过关。
## 数据集入口

Dataset v0.2 由 `data/` 目录管理，规范见 [data/dataset_management.md](data/dataset_management.md)：新图先进入 `data/raw_images/`，经过质量检查、metadata登记和必要的人工Mask后再交给视觉算法。

```powershell
# 对某个类别目录做批量质量检查
.\.venv\Scripts\python.exe -m microcleaning.data_learning.inspect_images data\dataset\particle

# 用已分类图片跑视觉分析Demo
.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --input data\dataset\particle\MC_particle_001.jpg `
  --mode analyze
```

`data/raw_images/` 现有 11 张公共样例图（`public_001.jpg` ~ `public_011.jpg`），等待人工分类。

第一条真实数据关卡不是“先凑够50张才运行”，而是：先分类1张跑通Demo，再扩展到两个采集批次、50张图片和至少10张人工标注。

## 长期路线

```text
P0目录/接口统一
→ P1真实USB数据和HSV
→ P2真实图片软件回放
→ P3定位与控制仿真
→ P4真实硬件最小闭环
→ P5证据驱动模型升级
→ P6结果预测与适应
→ P7平台化
```

详细入口、出口、三人任务和停止条件见 [长期分阶段研发规划](说明文档/未来计划/长期分阶段研发规划.md)。
