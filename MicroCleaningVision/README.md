# MicroCleaningVision

MicroCleaningVision 是一个显微表面智能处理科研项目。当前三人团队先建设视觉和上位机软件：让真实图片变成污染测量，让测量变成目标与控制仿真，并为未来STM32保留清晰接口。

## 当前真实状态

- 软件组件：E1；A/B/C软件集成：E2。程序生成图和FakeSerial已回归，第一张来源待核实的真实像素图已完成A/B评价。
- Demo v0.1：已能输出图片、mask、面积、中心、路线、模拟动作与Episode。
- 当前回归：项目`.venv`中74项测试全部执行并通过，无跳过。
- `public_001.jpg` 已完成人工Mask与HSV预测比较：IoU为0.0739，证明链路可运行，也证明当前HSV基线在此图上失败。
- USB显微镜团队采集数据：尚未进入证据链；现有图片来源仍需metadata核实。
- 像素到毫米标定：尚未验证。
- STM32、运动和喷洗：E0，无真实执行证据。

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
├── docs/                    # 总导航、长期规划、个人手册、Git、术语
├── legacy/                  # 旧原型，只读参考
├── AGENTS.md
└── project_state.yaml
```

## 新手从这里开始

1. [共同上下文](AGENTS.md)
2. [当前事实](project_state.yaml)
3. [文档总导航](docs/README.md)
4. [团队文档分类导航](docs/team/README.md)
5. [团队入门指南](docs/team/团队公共/团队入门指南.md)
6. [本轮任务看板](docs/team/团队公共/团队任务看板.md)
7. [成员A工作流程与命令百科](docs/team/成员A/成员A_工作流程与命令百科.md)

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

## Demo v0.1：现在最明确的程序入口

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

这个模式输出真实像素的mask、面积、中心和像素路线。因为目前没有真实像素到毫米标定，所以 `ActionRequest` 必须为空；这不是程序缺陷，而是在证据不足时拒绝伪造物理坐标。

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

详细入口、出口、三人任务和停止条件见 [长期分阶段研发规划](docs/architecture/长期分阶段研发规划.md)。
