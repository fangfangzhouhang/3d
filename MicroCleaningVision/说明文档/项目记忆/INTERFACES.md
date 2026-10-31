# INTERFACES｜模块之间怎样传数据

## 2026-10-30：工作台仅连接接口（主机局部）

新增 CLI 模式 --connect-only，与 --mock / --real 互斥；必须显式 --serial-port / --camera-index，可指定现有相机参数和 --output-root。运动/泵武装、归零、非默认位置路径、尺度/偏移/工作坐标配置一律拒绝。没有新增串口命令、共享合同或位置字段。

demo.device_connection.QueryOnlyConnection.query(command) 接受精确 HELLO / READXY / MCV1|PING / MCV1|STATUS，按对应完整换行回复核验；异常关闭，未经白名单的字符串或非字符串在开口前拒绝。close() 只关闭传输。READXY/STATUS 是固件计数和逻辑状态，不是台面反馈或安全批准。

新增 workbench-connection-v1 的 connection.json：mode=connection_only、status=CONNECTING/CONNECTED/DISCONNECTED/ERROR、设备请求参数、冻结分割参数/哈希、查询回执、采图索引、原始 serial_events、错误与时间，executable/motion_armed/pump_armed/position_reference_established=false。不是清洗报告，也不写 summary.json。

connection-capture-v1 保存同帧身份/来源/实际设置、原始 PNG/Mask/叠加各文件 SHA、像素测量、原有质量评分/flags。connection_device 允许单独更新相机或串口状态，避免相机事件覆盖真实串口结果；connection_capture 只显示采图结果，不生成动作。文件仅本机，详见 EXPERIMENTS。

## 当前交接：2026-10-13，显示选项兼容

StandaloneCoveragePanel.options可省略/为None：新UI采用RenderOptions(target=False, stations=False, alpha=0.25)。显式Options原样使用；旧归档CLI传archive.options，headless export-only保留原默认。Profile/Kernel/Plan/Renderer/归档schema、共享合同与串口均未改。

三图共用image_rgb/input.mask和原frame_id/mask_kind；标题区分分割污渍、人工框、框内分割、参考标注和合成Mask。_options读取普通偏好，_display_options在临时原图查看时返回纯原图Options；导出冻结后者。Hub.view_options仅用于显示，不进入Profile/Policy/Plan ID。[字段和边界](../软件组/覆盖预览三图同屏与导航验收_2026-10-13.md)。

## 当前交接：2026-10-13，P6 可选观察与证据绑定

CleaningLoop新增可选on_coverage_input=None；WorkbenchRuntime明确接入。payload有task/target、folder、stage(pre/post)、cycle/recheck、frame、原尺寸BGR、完整同域Mask、mask_kind、metadata、input_valid/reasons；图/Mask/metadata是独立副本，回调异常不影响执行。首次/补框由runtime直接冻结，同样不修改task_model或共享contracts/ports。

WorkbenchCoverageSource持CoverageDataset(RGB+既有CoverageInput)、folder及不可变binding_json；identity内容摘要。binding schema=workbench-coverage-v1，含任务/目标/阶段/轮次/独立复检、observation/frame、原图文件SHA/RGB像素SHA、Mask/分割/ROI策略SHA、Plan ID、generation、输入状态、validation、comparison_status。所有executable/feeds_action_request/send_to_controller=false。初始manual_region与后续manual_roi_segmented区别保留。

station_*/coverage_previews/<uuid>/archive仍为原固定四文件；上层binding.json最后发布。read_run的quality-report-v1增加可选coverage_previews/coverage_preview_issues及目标对应列表，旧缺失为[]，不变原质量/打印许可。HTML独立模拟区；CSV有完整预览才新增coverage_previews.csv，整计划与播放前缀分列，清除预测准确率null。P4可选通用hooks/worker代次/布局不破坏独立默认接口。[完整字段与输入输出](../软件组/智能覆盖规划_P6.md)。

## 当前交接：2026-10-12，覆盖规划 P5

共享contracts/ports、串口、旧Plan及P3/P4接口保持。新增仅C离线 `coverage-analysis-v1`、`coverage-analysis-report-v1`：

- `prepare_coverage_domain(input,model,policy,constraints) -> CoverageDomain`：READY的只读feasible/candidate Mask、conservative_kernel_mask|None、candidate_count；无模型/缺条件/资源超限时Mask为None并返回原因，不是硬件授权。
- `AnalysisSpec`：升序预算、目标/网格/起点/basis、独立raster_step_px、显式直径情景（Profile单位，默认空）、误差情景/单位（默认空）。默认网格1px，粗网格不匹配独立记原因；无隐式喷头尺寸。
- `analyze_coverage(CoverageInput,KernelResult|FootprintKernel,spec,constraints) -> CoverageAnalysis`：原完整Mask与模型/约束、spec、runs、稳定analysis_id和另列耗时。run记录method/phase/参数/source_run_id、实际/提案点、拒绝/违规、所有Metrics/数组、原PlanID、路线/状态/来源与单次耗时。非法基线raw前缀只审计，不排名；fixed失去新模型时保留点但指标None。
- `analysis_csv(analysis) -> UTF-8 BOM bytes`；`export_analysis_report(verified_archive,analysis,new_directory,include_figures=True) -> Path`：输入须同完整Mask/模型/约束，新目录，input四件归档+CSV/NPZ+可选PNG+最后发布JSON。
- `load_analysis_report(directory) -> AnalysisReport`：固定文件/false标志/hash/大小/NPY声明校验，原P3输入重放，再逐run重算比对，恢复记录耗时并核对CSV/实际数组。输出archive/analysis/environment_json/include_figures。

所有分析结果的三项硬件标志固定False；坐标原尺寸image_px，点/锚为(x,y)，shape为(h,w)。固定/重规划、未知/空目标、manual_region面积、measured父Profile与显式非圆核语义保留；不能将非圆核换成圆做直径扫描。哈希不是来源认证或签名；耗时是一次本地调用，几何访问连线非实际XY轨迹。完整字段、CLI和资源限制见 [P5交接](../软件组/智能覆盖规划_P5.md)。

## 当前交接：2026-10-12，覆盖规划 P3/P4

共享contracts/ports与串口不变。新增仅离线的 `coverage-visualization-v1`、`coverage-preview-v1`；原P1/P2字段未改。

- `render_coverage(image_rgb, plan, options, step_count) -> CoverageVisualization`：同尺寸RGBuint8，输出只读RGB/当前前缀Metrics/plan_id/图层/像素哈希/坐标语义；step是0到N，未知模型metrics=None，前缀保守率None。
- `render_report(plan, view) -> PIL.Image`：模拟/尺寸来源/几何指标/图例报告；显示缩放不改原算法坐标。
- `export_coverage_preview(image_rgb,plan,new_directory,constraints,options,step_count) -> Path`：同一快照四件归档，JSON最后写，存在目录拒绝。约束非默认时要求原始Mask以便重放。
- `load_coverage_preview(directory) -> CoverageArchive`：校验固定文件/false发送标志/大小/hash，恢复完整目标、精确核、尺度/约束/Policy，原算法与图像重算比对；输出RGB、Plan、Constraints、Options和step。
- `CoverageDataset/Request/Response` 与 `LatestCoverageWorker.submit/poll/close`：本地输入和递增generation；没有新增工作台事件类型，也没有ActionRequest。
- `StandaloneCoveragePanel`：调用上述离线接口。文件Mask须明确来源，`manual_region`只显示人工区域面积；改measured直径变assumed，单改Policy保留原测量与显式核。

尺寸/驻点继续为 `(h,w)` / `(x,y)`、frame=image_px；虚线是访问顺序。PNG报告像素与原图像素分开，完整Plan与前缀视图分别存储；参数/原始文件/归档像素哈希各自保留。资源边界、字段和CLI见 [P3/P4交接](../软件组/智能覆盖规划_P3_P4.md)。

## 当前交接：2026-10-11，覆盖规划 P1/P2

新增仅 C 模块内部的 `footprint-profile-v1` / `footprint-kernel-v1` / `coverage-plan-v1`，共享 `mcl-v0.1`、contracts/ports 和串口协议均未改。

- `build_kernel(FootprintProfile, PixelScale|None) -> KernelResult`：直径无默认值，单位 px/mm、来源 assumed/measured。未知直径/mm缺尺度等有 reason_codes；错误参数抛 ValueError。实测湿痕必须带完整喷洗条件，不等于去除率。
- `CoverageInput(mask,target_id,mask_kind,source_ref,frame_id/image_ref/图像及分割sha可选)` 保存只读原尺寸二维 bool/uint8二值 Mask，完整残片均参与；manual_region 保留区域面积语义。
- `plan_coverage(input, model, policy=..., constraints=...) -> CoveragePlan`：完整足迹/误差外包盒检查喷湿域与禁喷域，收缩核作条件下保守覆盖；指标空值与失败原因显式。
- 输出 `points_px/stations`、覆盖/遗漏/重叠/次数/可选保守 Mask、覆盖率/面积/几何距离；`to_dict()` 产生可序列化结构、plan_id和来源/Mask哈希，不写 PNG/JSON 文件。数据格式只读，executable 和两项硬件发送标志固定 False。

尺寸 `(h,w)`，锚点/驻点 `(x,y)`，frame=image_px；不存在隐式 ROI 平移、毫米/脉冲转换。几何比例非实际清除率；空目标比例 None，模型不可用 metrics/Mask None。默认图像域和未知定位误差保留 warning，不能当实测安全。全部字段及失败状态见 [P1/P2 交接](../软件组/智能覆盖规划_P1_P2.md)。

## 当前交接：2026-10-10

线上MCV1、STEP v0.3与共享contracts/ports未改变。主机局部新增：

- `ConfirmationBroker.ask_choice(prompt, choices, facts) -> {choice, reason}`，允许rewash/next/retake/pause；reply绑定request_id且严格类型，旧ask仍只接bool。
- `CleaningLoop(recheck_decision=...)`开启人工工作台路由；未提供callback的旧CLI仍有限轮数。summary增加report_ready/recheck_mode/stage_events_ref，原键保留。
- `cycle.rechecks[]`包含pre/post、verification/comparability、roi_analysis及segmentation、roi_evidence与sha256、quality_evidence、decision；cycle旧post/verification镜像最新一项。
- 位置JSON兼容旧known/x/y，新增status/pending_motion/reference_epoch；回执不能把故障软件升级为物理定位已验证。
- on_position快照包括confirmed/estimated/before/expected XY、motion_id/当前段、reference时间，evidence明确command_pulses_not_physical_feedback。
- 结果图请求携attempt_index/recheck_index/nonce；旧图答复不能覆盖当前轮；read_run顶层report_ready是打印/自动导航依据。

坐标均保留原图ROI，喷头有符号偏移只应用一次。当前F103共同入口急停实际源码读PB12，旧PB2仅历史兼容；烧录身份未知。[本轮详细验收](../软件组/工作台复洗与位置管理验收_2026-10-10.md)。

> 以下保留此前有日期的说明和实验快照。与本节冲突的工作台、复洗、复检、位置和报告规则已被本节替代；历史数字不代表当前验收。

2026-10-09 工作台局部接口补充：`workbench-task-v1` task_manifest 保存全候选/决定/冻结名单/原像素几何/哈希；`review_log.jsonl` 追加编辑及质量复核；`quality-report-v1` 规范化原档案供 UI/HTML/CSV 使用；`workbench-evidence-v1` 解释证据，不改 B 原 verification。事件队列和 request_id 仅用于主机线程通信，串口句子和 mcl-v0.1 contracts/ports 没有改变。serial 会话新增可选 cancellation/on_serial_event，旧调用默认不启用；消费者仅工作台和其测试。summary 保留旧键并增加 workflow/quality、manifest引用、执行子集。版本和路径见手册；以下基线为历史。

核对日期：2026-10-05；基线 main `341224f`（PR #24 后），`feat/single-entry-cleaning-v1` worktree 的主机代码/说明改动尚未提交。固件未在此轮修改或重建，真实设备未操作。 共享 Python 合同 mcl-v0.1、泵 MCV1、步进 v0.3 均未更改。

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

线上是 `MCV1|PING`、`STATUS`、`PUMP|action_id|duration_ms`、`STOP`；回 `PONG`、`STATUS`、`ACK`、`DONE`、`ERR`。ACK 表示接受，DONE 表示固件动作流程结束，均不能独自证明液体到达目标或污染减少。MCV1 编码器上限 500 ms；主机控制器及新闭环 PUMP_IN_PLACE 许可为 100–500 ms，闭环默认 500 ms。固件 PUMP ON 仍是固定 300 ms。MCU 沿用旧范围 100–2000 ms，不把固件上限当作用户可以直接申请的上限。当前 common/main.c 已接入共享 MCV1，旧 Stage1 兼容构建继续使用同一协议。

当前F103引脚和完整协议只查共同协议文档。PB12在当前联合入口假设常闭接地，断开高急停；实际接线未确认。不要把旧F401引脚配置套用到当前F103；当前PB12来源于F103共同入口的实际源码。

## 6. C → Stage 2：受限双轴运动

像素路线仍是：`CleaningPlan → PathPreview → Stage2Dispatch → MotionRequest → SafetyDecision → Stage2SerialLink`。

「下一块洗谁」是另一条入口：`plan_sequence(SequenceTarget 列表)`。`SequenceTarget` 在 `planning/sequence_planner.py`，字段是 `target_id`、`centroid_px`、`area_px`、`retry_count`、`difficulty`、`risk`，单位是像素。策略为 `nearest_neighbor`、`area_desc`、`weighted_score`。加权分是 `w_area*面积 - w_distance*到起点的距离 + w_retry*重试 + w_difficulty*难度 - w_risk*风险`，默认权重都是 1。分数越高越靠前；平局按 `target_id` 字符串较小者优先。它不导入视觉的 `TargetInstance`，也不生成 `MOVEXY`。新 TargetLedger 适配器负责复制字段，原集成测试保留；难度/风险未增加学习算法。

同一条 COM 上的顺序由 `F103SerialSession.run_step_then_pump` 管理：步进句子仍只来自 `stage2_protocol`，喷水句子仍只来自 `stm32_protocol`。

MotionRequest 是 C 的局部数据类，包含 request_id、task_id、lines、position_before_steps、start_reference、calibration_ref/sha256/warnings、dispatch_truncated、rule_version。审批摘要绑定这些内容，发送器核对同一 tuple of lines。

线上是 `HELLO → STEP_OK v0.3`、`MOVEXY`、`READXY` 和 `STOP`。`Stage2SerialLink` 仍拒绝把 `PUMP` / `MCV1` 写进自己的载荷。固件可以听两类句子。电脑端从 2026-10-05 起用 `F103SerialSession` 让它们顺序共用一条已打开的连接，而不是把两个解析器合成一个。新 demo.closed_loop_station 已调用持续会话；旧单帧模式保持原流程。主机现在核对 READXY 非零轴已发计数等于本段申请；零轴可保留上段计数。READXY 给的是 X/Y 已发脉冲与忙闲标记，没有绝对位置、清洗效果或喷液数据。

| 文件 | 写入时机 | 保存什么 |
|---|---|---|
| stage2_xy_pulses.txt | 规划后；分析模式也会有 | 计划指令，不证明已经发过 |
| stage2_intent.json | 打开串口之前 | 请求、标定引用/哈希、审批、位置和计划 |
| stage2_receipt.json | 尝试发送后，成功/失败都写 | 已发送/在途段、回复、STOP 结果、位置后状态 |
| summary.json | 本次编排结果 | hardware_actions、模式、证据边界及文件引用 |
| output/stage2/position.json | 人工归零或运动结果后 | 相对零点计数，失败后 unknown；不是编码器反馈 |

## 7. 已识别接口债务与提案边界

1. **Stage 2 合同债务**：局部 MotionRequest/回执没有完整进入八对象合同；旧 stage2-move 的 Episode action_request/execution_receipt 为 None；新闭环 Episode 记录泵请求/回执，运动仍完整放在 geometry/execution 等侧文件。未来要单独提出运动请求/回执版本或引用式方案，列出 Demo、回放、存档消费者，不把 MOVEXY 硬填进 SPRAY_AT_POINT。
2. **执行字段的证据债务**：实际时长、压力和目标位置要注明获得方式。没有流量/压力/位置传感器时，只能记固件报告或估计，不把字段名当实测。
3. **相机来源债务**：Probe PNG 和 device_index 缺少稳定设备身份/采集条件的共享记录；设备 index 换电脑可能变化。
4. **参数版本债务**：本机存在 `data/models/local_contrast_policy.json` 时 local 可自动加载。复现实验需冻结实际参数及哈希；`--no-tuned-policy` 明确使用源码默认。本次核对该生效文件不存在。
5. **数据划分债务**：旧 9/4 按文件名划分不等于新同场景数据已按样本/批次隔离；以后要防相邻帧泄漏。

接口提案应包含问题、现有消费者、字段/单位/版本、旧数据迁移、最小失败测试。未批准前只写提案，不修改共享接口。

## 8. 固件迁移与本次新增连接

源文件归 H1 pump / H2 motion / common 共同入口；Keil和旧Stage1移到common，安全核心与MCV1分别在safety/serial，只有一份共享实现。源代码迁移路径见验收记录。

TO_NEEDLE/TO_SCOPE 由同一XY驱动执行，相对Y名义7680步；正式主机还不自动发送，现有预算不足。它们不能直接升级为work_mm标定，也不是“自动返回保存位置”。

旧PUMP ON回复保留但限时300ms；CLEAR只解除已恢复的急停，不续跑；ARM是否需要按钮取决于配置（默认不强制）。PB2上拉/常闭的现场条件改变需要人确认。默认物理按钮握手不是已经启用的能力。

MCV1动作只缓存最近8个已完成编号，电脑必须保持唯一编号。STOP中止时会有被中止动作的ERR和STOP自身ACK/DONE；本次主机 STOP 有界读取被中止动作 ERR 后，要求 STOP 自身 ACK/DONE，并保留原文；错误或错误 DONE 不算停止成功。持续会话保留协议归属，失败关闭且锁定。

该固件整理阶段改变了入口连接；本次只改主机编排与错误处理，不改变 contracts.py/ports.py 或 ActionRequest 含义；共享运动合同债务另行提案。

## 9. 新闭环的局部接口（不扩展共享合同）

- `CapturedFrame`：image、frame_id、timestamp、source_id、settings；同一相机前后帧、新 frame_id 且后帧时间晚于回程完成。A 的质量检查仍复用；新元数据不伪装成 Observation 已有字段。
- `FrozenSegmenter`：启动时解析一次已有 policy，后续所有帧调用同一策略并记录 SHA256；改外部参数文件不会在任务中途生效。
- B 公共工具 `extract_target_mask(mask,target)` 保留原尺寸/坐标并校验所选连通块；`match_target_instance` 提取原匹配规则，`verify_single_target` 仍用原去除率规则。
- `TargetLedger`：S0001 为任务内稳定 ID；当帧 T1/component_label 可重编号，retry_count 不随之归零。合并、分裂、多对一、一对多或已完成块可疑重现均 HUMAN。
- `NozzleOffset`：version/setup_id/coordinate_frame/scope_to_nozzle_delta_steps/axes_confirmed/calibration_source/uncertainty_steps/motor_calibration_sha256/mock_only。实物 null、Mock 配置、哈希不匹配、旧重复补偿均拒绝；JSON 模板在现场入口文档。
- `CycleGeometry`：保存观察位、目标位移、唯一偏移、执行位、去程与回程 dispatch/MotionRequest；calibration 摘要也绑定这套几何。整任务每轴 <=1600，包括预留回程；q_exec=q_obs+d_target+d_offset，RETURN=-(d_target+d_offset)。
- `F103SerialSession.run_step_then_pump` 保留默认单次结束即关闭；新闭环显式 close_after=False，完整成功才复用。结果新增 motion_result/motion_error/pump_reason；before_pump 回调先登记完成运动并检查回程许可。泵请求预检在第一步前，发送前再次检验/消费令牌。
- 真实可比性包含相同尺寸/来源/设置/固定策略、返回原观察位、新后图、质量与人工 YES。没有自动配准/损伤传感器；damage_flag 不能被当作“已证明无损伤”。

运行目录保存 run_config/progress/summary/serial/failure（失败时），每轮保存前后图/质量、单目标 Mask、sequence、geometry、execution、verification、episodes。泵失败或抓后图中断仍保留已得到的回执；缺后图不造效果。详细文件用途见 [现场可测入口](../硬件组/现场可测入口.md)。
