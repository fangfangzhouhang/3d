# MicroCleaningVision 共同上下文

> 人和 AI 开始任务时依次阅读：本文件 → `project_state.yaml` → [当前该做什么与项目日志](说明文档/总流程说明/当前该做什么与项目日志.md) → `说明文档/README.md`。作战总表和百科当准则/字典，不要当本周待办。

## 这个项目现在到底在做什么

长期目标是研究机器怎样感知、处理并复检微观表面。当前软件主线仍是视觉和上位机软件。NUCLEO-F401RE 的 MCV1 **源码已在本仓**；同学机已报 **STM32F103** 受限短喷，**证据未入库**，不得把本机代码冻结为官方实喷。真机未验收 ≠ 软件未实现。正式板仍是 F401。

当前软件主线：

```text
A真实图片/数据
→ B污染mask、面积和中心
→ C目标点、路线、动作申请
→ Safety Governor → Human Gate
→ FakeSerial 或 STM32SerialController（默认不发泵）
→ 动作后视觉复检（真实后图仍缺）
→ Episode
```

FakeSerial只模拟确认、超时和错误，不打开COM口。软件回放不是硬件闭环，更不证明真实清洗有效。`analyze` / 默认 `--live` 不发送 PUMP。`--live --mode arm-pump --confirm-pump --arm-pump` 时，空格在识别到目标后才发限时 PUMP。

## 三个人的责任和目录

- A 数据与模型：`microcleaning/data_learning/`、`test/data_learning/`。
- B 视觉识别与测量：`microcleaning/vision/`、`test/vision/`。
- C 目标规划与控制仿真：`microcleaning/control_system/`、`test/control_system/`。
- 硬件组：`firmware/nucleo_f401re/`（正式目标）与 `firmware/stm32f103/`（走通实验）。源码在仓；可烧录工程与实机记录由硬件组留下，不提交 `.elf`。不要把 F103 走通写成 F401 已验收。

所有人理解整条链，但只直接修改自己的业务目录。上游未到位时使用合成fixture继续，不把fixture写成真实证据。

## 共享接口

`microcleaning/contracts.py` 是数据合同，`microcleaning/ports.py` 是未来设备端口合同。

| 对象 | 通俗解释 |
|---|---|
| `Observation` | 一张图从哪里来、质量怎样 |
| `StateEstimate` | 系统认为污染在哪里、有多大、有多不确定 |
| `ActionRequest` | 软件建议做什么；它不是硬件命令 |
| `SafetyDecision` | ALLOW / DENY / HUMAN |
| `ExecutionReceipt` | 控制端实际返回了什么 |
| `VerificationResult` | 前后图是否可比、污染是否减少 |
| `Episode` | 一次任务从输入到结果的完整记录 |
| `FailureRecord` | 失败发生在哪一层、怎样复现和恢复 |

共享文件不能夹在个人功能PR中随意修改。确需变更时，先写消费者、迁移方法、单位、失败测试和接口版本，由至少两人评审。

## 任务和AI开工规则

1. 每个代码修改先解释方案和文件清单，经用户/负责人批准后实施。
2. 一次只做一张任务卡；默认只修改负责人目录和测试。
3. 每人都有核心、扩展、备用和集成任务，不因上游未到位停工。
4. 写代码同时写测试；完成后运行个人测试和全部回归。
5. 复杂词首次出现时使用“中文（英文词，一句话解释）”。
6. AI可以生成代码、测试、样例和文档；不能制造人工真值、批准硬件或把Mock当实验。
7. 交付必须区分：代码框架、合成测试、真实像素、真实硬件和研究结论。

成员日常命令、任务手册、硬件组接口和 Git 教程统一从 `说明文档/README.md` 进入；当前不维护单独的项目 Skill 文件。

## 证据和复杂度规则

- E0：设计或框架；E1：单组件可重复；E2：多个真实模块交接；E3：真实闭环；E4：重复对照与误差/局限分析。
- HSV 已退出 Demo 默认。主入口用邻域差异 `local`（看一块和旁边差多少）；Otsu 可对照。只有固定条件下出现稳定失败，并有人工标注与独立测试集，才讨论学习模型。
- 第二视角、3D、Transformer、PINN、强化学习或World Model必须对应已记录失败和最小A/B。
- 模型、Agent或LLM不能通过自然语言直接控制泵、电机、阀、平台或喷头。

## 当前最低硬件边界

1. 默认不打开真实串口；`ping-only` / probe 必须使用人确认的 ST-LINK COM，禁止扫口。
2. 没有有效标定不产生毫米动作（`work_mm` / `SPRAY_AT_POINT`）。`PUMP_IN_PLACE` 的 `nozzle_fixed (0,0)` 不是伪造工作台坐标。
3. 自然语言、模型或 LLM 不能成为硬件命令。
4. 第一次真实泵动作必须经过 Safety Governor（HUMAN）→ `--confirm-pump` → `--arm-pump`，并有人在场。缺一不可。
5. 视觉模块不得 `serial.write`。未接 12V 的 PUMP 回执只能写逻辑脚/协议，不能写清洗有效。
6. 固件源码在仓不等于已烧录、不等于已联调。

完整任务、术语、Git、作战总表和长期阶段见 `说明文档/README.md`。
