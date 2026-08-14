# 成员 C：软件闭环与 STM32 接口预留

你的目标是让 A/B 的结果安全地流完整条软件链，并给未来 STM32 留下稳定接口。

## 负责文件

- `decision/fixed_rule.py`
- `safety/governor.py`
- `execution/fake_serial.py`
- `data/episode_store.py`
- `app/replay_mcl.py`
- `test/visual_loop/test_member_c_workflow.py`

## 要学习什么

1. dataclass（数据类，用固定字段表达合同）和 JSON。
2. 状态机：一次任务只能按规定阶段前进或停止。
3. ACK（确认帧）：确认收到/处理命令，不代表任务效果。
4. Timeout（超时）：规定时间没有回执时进入失败状态。
5. Idempotency（幂等性，同一动作重复提交不能重复执行）和审批令牌。
6. 依赖倒置：上层依赖 `ControllerPort`，不依赖具体 STM32 型号。

## 第一张任务卡 C-01

- 从回放数据构造软件任务。
- 只允许通过安全审批的 ActionRequest 进入 FakeSerial。
- 覆盖成功 ACK、超时、断连、审批过期和参数篡改。
- 保存 Episode JSON 与哈希。

验收：不打开 COM 口；失败路径没有成功回执；Episode 不覆盖；能清楚解释未来 STM32 需要实现什么。

## 如何让 AI 帮你

把 `contracts.py` 设为只读约束，要求 AI 只改 C 的文件；让另一个 AI 专门攻击审批重放、参数篡改、错误 ACK 和把 FakeSerial 误写成真实证据的问题。

