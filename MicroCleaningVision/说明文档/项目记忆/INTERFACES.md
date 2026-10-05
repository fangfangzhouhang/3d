# INTERFACES｜模块之间怎样传数据

核对日期：2026-10-05，HEAD f31e445 + 当前未提交固件改动；Python共享接口未改变。共享 Python 合同版本 `mcl-v0.1`；泵线上协议 `MCV1`；步进协议 `v0.3`。三个版本各管不同层，不能相互替代。

## 1. 合同的真实来源

- [contracts.py](../../microcleaning/contracts.py)：八个共享数据对象，字段以代码为准。
- [ports.py](../../microcleaning/ports.py)：CameraPort 和 ControllerPort 方法签名。
- [串口协议与参数](../硬件组/串口协议与参数.md)：当前双方共同使用的线上格式、引脚和参数唯一正文。
- 本文件保留交接语义和债务；上述实现/协议变化时同步更新。本文件不创建新的字段或固件命令。

## 2. 八个共享对象

| 对象 | 实际关键字段 | 产生者 → 消费者 | 必须保留的意义 |
|---|---|---|---|
| Observation | observation_id、task_id、timestamp、frame_id、raw_image_ref、focus_quality、illumination_quality、confidence、quality_flags、software_version | A/成像 → B、编排器 | 图像身份、质量和来源引用；对象本身没有 calibration_version 字段 |
| StateEstimate | state_id、task_id、observation_id、target_area_px、target_centroid_px/mm、uncertainty_px/mm、coordinate_frame、device_state、calibration_version/valid、prior_actions | B 状态估计 → C/安全 | 像素测量和设备状态不能被默认升级为已标定毫米 |
| ActionRequest | action_id、task_id、state_id、target_centroid_mm、coordinate_frame、primitive、duration_ms、pressure、constraints、expected_effect、rule_version | C 决策 → 泵治理器/ControllerPort | 动作申请；当前 primitive 是 SPRAY_AT_POINT 或 PUMP_IN_PLACE，不是 MOVEXY |
| SafetyDecision | action_id、outcome、reason_codes、approval_token、state_id、request_digest、policy_version、issued_at/expires_at、interface_version | 治理器 → 执行器 | ALLOW/DENY/HUMAN；审批绑定内容、时限和身份 |
| ExecutionReceipt | action_id、mode、started_at/ended_at、actual_target_mm、actual_duration_ms、actual_pressure、controller_state、interlock_state、success、error_code | 控制器 → 复检/存档 | 区分请求值、固件报告值和传感器实测；名字叫 actual 也不能自动认定有物理传感器 |
| VerificationResult | task_id、pre/post_observation_id、residual_area_px、removal_rate、damage_flag、next_route、reason_codes | B 复检 → 路由/存档 | 缺后图时结果为空并人工处理；RETRY 不等于已经重试 |
| Episode | episode_id、task_id、mode、protocol_version、observation_pre/post、state、action_request、safety_decision、execution_receipt、verification、failures | 编排器 → A/实验资产 | 同一任务的整条证据链；字段为空要解释，不能补造 |
| FailureRecord | failure_id、task_id、stage、severity、reason_codes、reproducible、recovery | 出错模块/编排器 → 存档 | 失败位置、原因、复现和恢复 |

`Observation.confidence` 是现有质量流程的评分，`ContaminationMeasurement.confidence` 是算法启发式分数；都没有经过概率校准。`uncertainty_px` 目前也不是统计置信区间，不能据此宣传“95% 定位准确”。

## 3. A → B：数据交接

当前入口是原图路径或 CameraPort 返回的 Observation。metadata.csv 实际列为：

```text
image_name, category, source, capture_date, device, resolution,
magnification, annotation_status, remark
```

尚无专用 sample_id/capture_session 字段。新采集时先在批次记录和 remark 中关联样本、条件；要升级 schema 时另提案，不能宣称旧 CSV 已经有这些字段。未知设备、倍率、来源写 unknown，不由 AI 看图猜。

人工标注用 Labelme `contamination`，JSON 的 imagePath 定位原图，Polygon/Circle 转同尺寸 Mask。Mask 是二维 uint8，背景 0、目标 255；原图、人工 Mask、算法 Mask 的宽高和图片身份必须一致。不缩放人工答案来偷偷匹配错误预测。

A 的人工 Mask 用于开发和评价，B 的运行时输入仍是原图。冻结名单当前是 `public_002`、`public_011`、`M9`、`M12`，代码在 [eval_split.py](../../microcleaning/data_learning/eval_split.py)。

CameraPort 保持：

```python
capture(task_id: str, phase: str) -> Observation
```

它只负责成像。能抓帧不意味着允许运动。

## 4. B → C：测量与 Mask

当前 `ContaminationMeasurement` 字段：area_px、centroid_px、uncertainty_px、confidence、mask_ref、component_count、algorithm_version。分割函数返回 `SegmentationResult(measurement, mask)`，其定义复用在 hsv_baseline.py；local/Otsu 等遵守相同输出。

- 面积单位是像素计数，不是 mm²。无目标时面积 0、中心 None；不能用 `(0,0)` 冒充一个检测目标。
- C 规划读取算法 Mask，保留坐标系 `image_px`、原图尺寸和测量版本。
- 图上总体中心可能落在多个区域之间；规划按连通块处理，不能只相信总体质心就是可喷的位置。
- `target_instance.py` 的 `TargetInstance` 是视觉内部对象，不在 `contracts.py` 里。字段：`target_id`、`centroid_px`、`area_px`、`bbox`、`component_label`、`confidence`，可选 `mask_ref`。`extract_target_instances` 用 8 连通拆 Mask，编号从 T1 起。面积属于这一块，不是整幅前景。
- `verify_single_target` 用搜索框（默认外扩 8 像素）、重叠比（默认 0.20）和质心距离（默认 16 像素）把后图的一块配回指定目标。唯一配上才把这一对面积交给 `verify_area_change`。配不上或配到多块时 `match_status` 为 `unmatched` 或 `ambiguous`，路由是 HUMAN。别的污渍变小不能把仍在的 T1 判成已洗净。
- `mask_ref` 为文件引用时，读者必须知道它相对哪个运行目录。图片 ID、路径和版本在 summary 中对齐。

## 5. C → MCV1：固定喷头短喷

ControllerPort 保持：

```python
execute(request: ActionRequest, decision: SafetyDecision) -> ExecutionReceipt
```

`PUMP_IN_PLACE` 使用 `coordinate_frame=nozzle_fixed`、目标 `(0,0)` 和禁止 XY 的约束；这里的 `(0,0)` 表示喷头不移动，不是伪造工作台定位。`SPRAY_AT_POINT` 才需要有效 `work_mm` 标定。

线上是 `MCV1|PING`、`STATUS`、`PUMP|action_id|duration_ms`、`STOP`；回 `PONG`、`STATUS`、`ACK`、`DONE`、`ERR`。ACK 表示接受，DONE 表示固件动作流程结束，均不能独自证明液体到达目标或污染减少。电脑当前泵时长 100–500 ms；MCU沿用旧范围100–2000ms，未扩大电脑权限。当前common/main.c已接入共享MCV1，旧Stage1兼容构建继续使用同一协议。

当前F103引脚和完整协议只查共同协议文档。PB2在联合入口假设常闭接地，断开高急停；实际接线未确认。不要恢复 F401 PB5/PB12 作为当前板子配置。

## 6. C → Stage 2：受限双轴运动

像素路线仍是：`CleaningPlan → PathPreview → Stage2Dispatch → MotionRequest → SafetyDecision → Stage2SerialLink`。

「下一块洗谁」是另一条入口：`plan_sequence(SequenceTarget 列表)`。`SequenceTarget` 在 `planning/sequence_planner.py`，字段是 `target_id`、`centroid_px`、`area_px`、`retry_count`、`difficulty`、`risk`，单位是像素。策略为 `nearest_neighbor`、`area_desc`、`weighted_score`。加权分是 `w_area*面积 - w_distance*到起点的距离 + w_retry*重试 + w_difficulty*难度 - w_risk*风险`，默认权重都是 1。分数越高越靠前；平局按 `target_id` 字符串较小者优先。它不导入视觉的 `TargetInstance`，也不生成 `MOVEXY`。集成测试自己抄字段。

同一条 COM 上的顺序由 `F103Session.run_step_then_pump` 管理：步进句子仍只来自 `stage2_protocol`，喷水句子仍只来自 `stm32_protocol`。

MotionRequest 是 C 的局部数据类，包含 request_id、task_id、lines、position_before_steps、start_reference、calibration_ref/sha256/warnings、dispatch_truncated、rule_version。审批摘要绑定这些内容，发送器核对同一 tuple of lines。

线上是 `HELLO → STEP_OK v0.3`、`MOVEXY`、`READXY` 和 `STOP`。`Stage2SerialLink` 仍拒绝把 `PUMP` / `MCV1` 写进自己的载荷。固件可以听两类句子。电脑端从 2026-10-05 起用 `F103Session` 让它们顺序共用一条已打开的连接，而不是把两个解析器合成一个。Demo 还没有调用这个会话。READXY 给的是 X/Y 已发脉冲与忙闲标记，没有绝对位置、清洗效果或喷液数据。

| 文件 | 写入时机 | 保存什么 |
|---|---|---|
| stage2_xy_pulses.txt | 规划后；分析模式也会有 | 计划指令，不证明已经发过 |
| stage2_intent.json | 打开串口之前 | 请求、标定引用/哈希、审批、位置和计划 |
| stage2_receipt.json | 尝试发送后，成功/失败都写 | 已发送/在途段、回复、STOP 结果、位置后状态 |
| summary.json | 本次编排结果 | hardware_actions、模式、证据边界及文件引用 |
| output/stage2/position.json | 人工归零或运动结果后 | 相对零点计数，失败后 unknown；不是编码器反馈 |

## 7. 已识别接口债务与提案边界

1. **Stage 2 合同债务**：局部 MotionRequest/回执没有完整进入八对象合同；Episode 的 action_request/execution_receipt 为 None。未来要单独提出运动请求/回执版本或引用式方案，列出 Demo、回放、存档消费者，不把 MOVEXY 硬填进 SPRAY_AT_POINT。
2. **执行字段的证据债务**：实际时长、压力和目标位置要注明获得方式。没有流量/压力/位置传感器时，只能记固件报告或估计，不把字段名当实测。
3. **相机来源债务**：Probe PNG 和 device_index 缺少稳定设备身份/采集条件的共享记录；设备 index 换电脑可能变化。
4. **参数版本债务**：本机存在 `data/models/local_contrast_policy.json` 时 local 可自动加载。复现实验需冻结实际参数及哈希；`--no-tuned-policy` 明确使用源码默认。本次核对该生效文件不存在。
5. **数据划分债务**：旧 9/4 按文件名划分不等于新同场景数据已按样本/批次隔离；以后要防相邻帧泄漏。

接口提案应包含问题、现有消费者、字段/单位/版本、旧数据迁移、最小失败测试。未批准前只写提案，不修改共享接口。

## 8. 固件迁移与本次新增连接

源文件归 H1 pump / H2 motion / common 共同入口；Keil和旧Stage1移到common，安全核心与MCV1分别在safety/serial，只有一份共享实现。源代码迁移路径见验收记录。

TO_NEEDLE/TO_SCOPE 由同一XY驱动执行，相对Y名义7680步；正式主机还不自动发送，现有预算不足。它们不能直接升级为work_mm标定，也不是“自动返回保存位置”。

旧PUMP ON回复保留但限时300ms；CLEAR只解除已恢复的急停，不续跑；ARM是否需要按钮取决于配置（默认不强制）。PB2上拉/常闭的现场条件改变需要人确认。默认物理按钮握手不是已经启用的能力。

MCV1动作只缓存最近8个已完成编号，电脑必须保持唯一编号。STOP中止时会有被中止动作的ERR和STOP自身ACK/DONE；既有主机stop可能先返回该ERR，不能把它当作清洗完成。未来会话管理需要关联动作并清理剩余回复。

本轮改变固件入口连接与故障处理，不改变contracts.py/ports.py签名或ActionRequest业务含义；共享Python运动合同债务仍需另行提案。
