---
name: visual-closed-loop-task
description: 在 MicroCleaningVision 中实现或评审视觉软件闭环的小任务。用于修改成像质量、污染测量、状态估计、复检、软件回放、FakeSerial、Episode 或对应测试时，强制先同步共享上下文、遵守单人文件所有权，并以测试和证据结束任务。
---

# 视觉软件闭环任务

## 开工前

1. 依次阅读项目根目录的 `AGENTS.md`、`project_state.yaml`、`docs/team/团队入门指南.md`。
2. 阅读 `microcleaning/contracts.py`，确认本任务消费和产出的契约对象。
3. 阅读 `docs/team/团队任务看板.md`，只修改任务负责人拥有的文件。
4. 若必须改变共享契约，先写接口变更提案；没有三人评审记录不得修改 `contracts.py`。

## 实施

- 一次只解决一张任务卡，不顺手增加 YOLO、3D、Agent 或硬件控制。
- 成员 A 的代码只产生 `Observation` 和图像质量证据。
- 成员 B 的代码只产生污染测量、`StateEstimate` 或 `VerificationResult`。
- 成员 C 的代码只编排结构化动作、安全规则、FakeSerial 和 Episode。
- 所有真实硬件默认不可用；不得打开 COM 口，不得把模拟 ACK 写成真实执行。
- 复杂术语首次出现时，写成“中文（`EnglishTerm`，一句话解释）”。

## 验证

1. 运行本人的测试文件，再运行全部 `test/visual_loop` 测试。
2. 运行 `test/mcl` 回归测试，确认没有破坏已有 Mock 基线。
3. 检查输出能否追溯到输入图像、接口版本和失败原因。
4. 在交付中区分：框架、模拟验证、真实数据验证、真实硬件验证。

## AI 交付格式

报告解决的问题、负责人文件、未改的共享接口、测试结果、新增证据、未验证假设，以及下一位成员需要的输入。
