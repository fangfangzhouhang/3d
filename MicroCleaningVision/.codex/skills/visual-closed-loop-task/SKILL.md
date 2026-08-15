---
name: visual-closed-loop-task
description: 在MicroCleaningVision中实现或评审A数据模型、B视觉测量、C目标控制的软件任务；强制先同步共同上下文、遵守顶层目录所有权，并以测试和证据结束。
---

# 视觉软件闭环任务

## 开工前

1. 依次阅读 `AGENTS.md`、`project_state.yaml`、`docs/README.md`。
2. 阅读 `docs/team/团队任务看板.md` 和负责人的长期任务手册。
3. 阅读 `microcleaning/contracts.py` 与 `microcleaning/ports.py`，确认输入、输出、单位和失败语义。
4. 代码变更前先解释方案、文件清单和测试，经用户/负责人批准后实施。

## 目录所有权

- A只直接修改 `microcleaning/data_learning/`、`test/data_learning/`。
- B只直接修改 `microcleaning/vision/`、`test/vision/`。
- C只直接修改 `microcleaning/control_system/`、`test/control_system/`。
- 共享契约变化先写提案；没有评审不得夹在个人功能任务中。

## 实施规则

- 一次只解决一张任务卡，不顺手加入YOLO、3D、Agent或真实串口。
- 上游未到位时使用明确标记的合成fixture，不停工，也不把fixture当真实证据。
- A生产数据、标注和未来模型；B生产视觉测量与复检；C生产目标、路线和控制仿真。
- 默认不打开真实硬件；无有效标定不产生毫米动作；自然语言不成为硬件命令。
- 复杂术语首次出现时写成“中文（EnglishTerm，一句话解释）”。

## 验证

1. 运行负责人测试目录。
2. 运行 `python -m unittest discover -s test -p "test*.py" -v`。
3. 检查输出能否追溯到输入、接口版本和失败原因。
4. 区分合成测试、真实像素、真实硬件和研究结论。
5. 更新 `project_state.yaml` 时只写实际发生的事实。

## AI交付格式

报告任务编号、解决的问题、修改文件、未改的共享接口、测试结果、新增证据、未验证假设、下一位成员需要的输入和实际专注时段。
