# 文档导航

目标是让新成员在 60 分钟内回答：项目做什么、软件怎样流动、我负责哪些文件、做完拿什么验收。

## 必读四份

1. `../AGENTS.md`：共同上下文。
2. `../project_state.yaml`：当前事实。
3. `team/三人分工与文件所有权.md`：谁改什么。
4. 自己的手册：`team/成员A_成像任务手册.md`、`team/成员B_测量复检任务手册.md` 或 `team/成员C_闭环接口任务手册.md`。

## 按职责分类

```text
docs/
├── architecture/      # 当前软件架构、接口和四周路线
├── team/              # 入门、分工、任务、AI/Git 协作
├── research/          # Episode 数据和失败复盘
├── future_hardware/   # 未来真实硬件阶段，当前不执行
└── archive/           # 旧版详细文档与历史审查
```

| 想解决的问题 | 阅读文件 |
|---|---|
| 软件链怎样连接 | `architecture/系统架构说明.md` |
| 模块交接什么 | `architecture/接口契约说明.md` |
| 四周做什么 | `architecture/软件回放闭环路线图.md` |
| 我负责什么 | `team/三人分工与文件所有权.md`、`team/团队任务看板.md` |
| 如何使用 AI | `team/研发协作与AI工作流.md` |
| 数据怎么保存 | `research/实验数据与回合规范.md` |
| 失败怎么复盘 | `research/失败分类与复盘.md` |
| 环境怎么重建 | `12_开发环境与依赖说明.md` |

`future_hardware/` 和 `archive/` 不属于新手第一周任务。旧文档的双视角、YOLO、3D、机械/STM32 分工和直接串口流程均不代表当前能力。
