# MicroCleaningVision

MicroCleaningVision 是一个显微表面智能处理科研项目。当前三人团队先建设视觉和上位机软件：让真实图片变成污染测量，让测量变成目标与控制仿真，并为未来STM32保留清晰接口。

## 当前真实状态

- 软件组件：E1；当前回归测试可重复。
- 真实相机/USB显微镜数据：尚未进入仓库证据链。
- HSV真实污染识别：尚未验证。
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
├── docs/                    # 总导航、长期规划、个人手册、Git、术语
├── legacy/                  # 旧原型，只读参考
├── AGENTS.md
└── project_state.yaml
```

## 新手从这里开始

1. [共同上下文](AGENTS.md)
2. [当前事实](project_state.yaml)
3. [文档总导航](docs/README.md)
4. [团队入门指南](docs/team/团队入门指南.md)
5. [本轮任务看板](docs/team/团队任务看板.md)

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
