# MicroCleaningVision 共同上下文

> 人和 AI 每次开始任务时，依次阅读：本文件 → `project_state.yaml` → `docs/README.md` → 自己的任务手册。

## 这个项目现在到底在做什么

MicroCleaningVision 的长期目标是研究机器如何感知、处理并复检微观表面。当前三人团队只负责**视觉成像与软件闭环**，不负责机械结构、STM32 固件或真实喷洗控制。

当前要做的是软件回放闭环（`Software Replay Loop`，用动作前后图像和假串口完整演练数据流）：

```text
前图 → 图像质量 → 污染测量 → 状态判断 → 动作申请
    → 安全审批 → FakeSerial 回执 → 后图复检 → Episode
```

这里的 FakeSerial（假串口）只模拟 STM32 的确认、超时和错误，不打开 COM 口。软件回放闭环不是硬件闭环，更不证明真实清洗有效。

## 三个人的责任

- 成员 A：图像输入和质量，输出 `Observation`（观测记录）。
- 成员 B：污染测量、状态估计和前后复检，输出 `StateEstimate` 与 `VerificationResult`。
- 成员 C：动作申请、安全规则、FakeSerial、流程编排与 Episode。

成员需要理解整条链，但只修改自己拥有的业务文件。文件所有权见 `docs/team/三人分工与文件所有权.md`。

## 共享接口

`microcleaning/contracts.py` 是三人共同的数据合同；`microcleaning/adapters/ports.py` 是未来硬件必须遵守的端口合同：

| 对象 | 通俗解释 |
|---|---|
| `Observation` | 一张图从哪里来、质量如何 |
| `StateEstimate` | 系统根据图像认为污染在哪里、有多大、有多不确定 |
| `ActionRequest` | 软件建议做什么；它不是硬件命令 |
| `SafetyDecision` | 独立输出 ALLOW / DENY / HUMAN |
| `ExecutionReceipt` | 控制端实际返回了什么；当前只能是假串口回执 |
| `VerificationResult` | 前后图是否可比、污染是否减少 |
| `Episode` | 一次任务从输入到结果的完整记录 |
| `FailureRecord` | 失败发生在哪一层、如何复现和恢复 |

任何人不得单独修改 `contracts.py` 或 `adapters/ports.py`。确需修改时，先在 PR 中写清消费者、迁移方法和测试，由另外两人评审。

## AI 开工规则

1. 只解决当前任务卡，不顺手加入 YOLO、3D、Agent 或真实串口。
2. 先检查文件所有权；默认不得修改他人的业务文件。
3. 写代码必须同时写负责人自己的测试。
4. 复杂词首次出现时使用“中文（英文词，一句话解释）”。
5. AI 可以生成代码、测试、失败假设和文档；AI 不得把 Mock 当实验结果，不得直接控制硬件。
6. 完成后报告：改了什么、测试证据、仍未验证什么、下一位成员需要什么输入。

项目 Skill：`.codex/skills/visual-closed-loop-task/SKILL.md`。

## 技术升级门槛

传统方法先行。只有固定条件下出现稳定失败，并完成最小 A/B 对照后，才讨论 CNN、YOLO、双视角或 3D。Agent 当前服务于研发协作、审查和文档，不进入物理控制链。
