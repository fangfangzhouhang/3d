# MicroCleaningVision

MicroCleaningVision 是一个显微表面智能处理科研项目。当前三人团队专注视觉成像软件，先让“前图—测量—模拟动作—后图复检—记录”形成完整、可测试的软件闭环；机械和 STM32 固件由未来接口接入。

## 新手先看这里

按顺序阅读：

1. [共同上下文](AGENTS.md)：项目边界和 AI 工作规则。
2. [当前事实](project_state.yaml)：哪些通过测试，哪些仍是假设。
3. [文档导航](docs/README.md)：架构、接口、分工和每周任务。

## 当前软件链

```text
成员 A                     成员 B                     成员 C
图像 + 质量 ──Observation──> 污染测量 + State ──────> 动作申请
    ^                         │                         │
    │                         └── 前后复检 <── 后图      v
ReplayCamera                                      Safety + FakeSerial
    │                                                   │
    └──────────────────── Episode <─────────────────────┘
```

- ReplayCamera（回放相机）：读取预先登记的图片，不打开真实相机。
- FakeSerial（假串口）：模拟 STM32 确认、超时和错误，不打开 COM 口。
- Episode（回合记录）：保存一次任务的输入、状态、申请、审批、回执和复检。

## 当前目录

```text
MicroCleaningVision/
├── AGENTS.md / project_state.yaml       # 共享上下文与当前事实
├── microcleaning/
│   ├── contracts.py                     # 共享接口，三人评审后才改
│   ├── perception/                      # A：质量；B：污染测量
│   ├── state/                           # B：状态估计
│   ├── verification/                    # B：前后复检
│   ├── decision/                        # C：动作申请
│   ├── safety/                          # C：独立安全规则
│   ├── execution/                       # C：FakeSerial
│   ├── data/                            # C：Episode 保存
│   ├── adapters/                        # 软硬件接口与回放适配器
│   └── app/                             # Mock 和软件回放编排
├── test/visual_loop/                    # A/B/C 各自拥有的测试文件
├── docs/                                # architecture/team/research/future_hardware/archive
├── .codex/skills/                       # 项目 AI Skill
├── legacy/                              # 旧代码与旧测试，仅作迁移参考
└── docs/archive/legacy_docs/            # 旧版详细说明，仅作历史参考
```

`legacy/` 中的相机、检测、规划、通信等是早期原型，含大量空实现；当前主线不导入它们。可借鉴其中思路，但迁移前必须有新接口和测试。

## 运行与测试

在本目录打开 PowerShell：

```powershell
python main.py
python -m unittest discover -s test\visual_loop -v
python -m unittest discover -s test\mcl -v
```

当前通过测试表示软件框架连通，不表示真实相机、STM32 或清洗已经完成。虚拟环境不进 Git；每台电脑按照 `docs/12_开发环境与依赖说明.md` 重建。
