# ARCHITECTURE｜整个系统怎么工作

## 2026-11-02：无头点动适配层

`JogController.jog → 四档/单轴1600上限/一次性武装/位置预检 → single_move_dispatch → demo.motion_mode._stage2_move → 原 motion_gate/确认/Stage2SerialLink/回执/账本`。start_reference 固定 image_center，控制器不构造审批或直接写串口。
同步调用、同一控制器忙时拒绝重入/重武装/归零；点动与显式 set_zero 均持有原 COM/位置 ResourceLease。标定复用 load_motor_calibration，串口默认115200/2秒，与 closed_loop_station 一致；无 nozzle 偏移、相机或 Tk 依赖。调用方法见 [README](../../README.md)。

## 2026-10-30：真实设备只连接支路

用户批准新增 --connect-only，closed_loop_station 在原完整 Real/Mock 分支之前校验独立能力：指定 COM/相机，拒绝运动/泵武装、归零、位置账本覆盖及运动几何配置。WorkbenchRuntime 冻结 connection_only，并将任务分流到 demo/device_connection.run_connection_task；不构造 CleaningLoop、HardwareExecutor、Stage2SerialLink 或 STM32SerialController。

连接线程复用 COM ResourceLease 与 F103SerialSession，QueryOnlyConnection 只允许四项完整单行查询；相机仍由既有 CameraBroker 独占。Tk 主线程通过 connection_device / connection_capture 更新画面、状态和日志；后台和界面同时拒绝执行。串口保持到取消或关闭，关闭只释放传输、不发送 STOP/CLEAR/ARM，不建立位置零点。

保存独立 connection_* 目录及原图/Mask/识别叠加，不伪装为清洗 summary/Episode 或正式位置账本。原完整 Real 仍有运动/标定与人工授权要求，旧 Mock 路径保持。操作见 [工作台手册](../软件组/可视化工作台操作手册.md)。

## 当前交接：2026-10-13，三图显示支路

StandaloneCoveragePanel从同一CoverageDataset读取原RGB/完整Mask，独立绘制并按数据身份/画布尺寸缓存两张输入图；初始化、重算、无模型与缩放均不依赖current_plan。中央继续用原render_coverage。参数移至可滚动右侧，高级项可展开，底部播放/导出优先保留空间。

图层/强度只重绘，临时原图查看用于显示和导出快照，不覆盖普通偏好。Hub关闭重开保存普通Options，原worker/代次/源身份及五页事件路由保持。三图位于既有预览弹窗，未重排五页。[实现与验证](../软件组/覆盖预览三图同屏与导航验收_2026-10-13.md)。

## 当前交接：2026-10-13，P6 模拟预览接入工作台

新增demo/workbench_coverage：审核首次原图/该S完整Mask、人工矩形，以及CleaningLoop最大残片筛选前/复检的mask & measurement_domain→不可变Source/绑定→既有P1–P4→固定输入Toplevel→审核叠加/监控入口。设备线程只复制发布观察，参数和模拟Plan不进入执行器；on_coverage_input默认关闭且异常隔离。

Tk主线程持每目标initial/latest两份输入，整个工作台共用一个有界LatestCoverageWorker，跨弹窗generation单调且检查源/窗口身份。图层/播放只渲染；改变输入/直径/策略重算。关闭重开保留有效待提交参数，高DPI面板适配工作台尺度；无第二个mainloop或grab。

用户点击→后台完整P3导出/重放→独立binding最后发布。reporting/coverage_evidence有限资源哈希读档，不在动作/Tk/报告读档线程重做规划；可选问题与原质量证据隔离，旧报告兼容。清洗Loop仍选择最大残片作旧单点执行，预览保留全残留。[文件/线程与入口](../软件组/智能覆盖规划_P6.md)。

## 当前交接：2026-10-12，覆盖规划 P5

离线分析支路：`P3 CoverageArchive（完整原图/Mask/核/约束） → AnalysisSpec → coverage_analysis.analyze_coverage → CoverageAnalysis/Runs → coverage_analysis_report → input归档 + CSV/NPZ/科学PNG + 最后发布的JSON → 校验并完整重算`。纯分析无文件/UI/硬件依赖；报告使用Matplotlib Agg，不导入pyplot/Tk/串口/工作台。两个模块及测试归C，demo消费者与事件通信未改。

P2公共 `prepare_coverage_domain` 提供完整几何可行域、候选网格交集和可选保守核；P2与P5基线共用误差外扩/禁喷规则，避免复制两套私有安全定义。仅做该入口提取，15份冻结旧Plan、旧P3归档及212份受保护文件保持一致。

三个方法每run共享完整输入/核/约束/目标/起点/预算；中心按连通块，扫描复用旧点列但仅离线驻点模拟。提案保留审计、非法点不挪位；fixed严格保留原点（不可算也保留坐标），违规不排名；replan在新条件下调用原算法。基于nominal/conservative目标的差异是条件几何优化，非清除率或物理可靠性。耗时不进入稳定ID；文件资源限制和NPY预检先于解压分配。P6仍需工作台完整Mask适配与身份绑定。[字段/限制](../软件组/智能覆盖规划_P5.md)。

## 当前交接：2026-10-12，覆盖规划 P3/P4

离线支路增加：`CoverageDataset/Request → LatestCoverageWorker → 原P1/P2 CoveragePlan → coverage_visualization → StandaloneCoveragePanel`；导出支路 `coverage_archive → PNG + 原图 + 完整Mask/核/约束NPZ + JSON → 校验后原算法重算`。两份新 planning 模块归 C，两份独立 demo 模块归集成人；本轮没有改既有工作台或它的事件/执行器。

主线程独占Tk/PhotoImage/after，后台一份运行、一份可替换待处理、一份最新响应；调参立即清空旧结果并以generation双重检查，160ms防抖。运行中算法完成后丢弃过期结果，不宣称强制中断。导出抓取同一份不可变快照，异步编码不会拿后来参数替换先前图片。关闭取消全部Tk回调，后台不操作已销毁窗口。

图层/窗口变化只重绘；播放用精确核计算驻点前缀并集，完整Plan不变。部分播放保守率未知，完整计划复用P2结果。归档保存完整数据而非仅哈希，恢复显式非圆核也不替换成圆。所有输出不可执行且无硬件依赖。[接口与图示](../软件组/智能覆盖规划_P3_P4.md)。P6工作台完整目标适配尚未实施。

## 当前交接：2026-10-11，覆盖规划 P1/P2

新增 C 所有的离线支路：`coverage_model.FootprintProfile + PixelScale → build_kernel → FootprintKernel`；调用者冻结完整二值目标为 `coverage_planner.CoverageInput`，结合 `CoveragePolicy/Constraints → plan_coverage → CoveragePlan`；`coverage_metrics.evaluate_coverage` 输出只读预计覆盖、遗漏、重叠与可选保守覆盖 Mask。

此支路不导入 orchestration/serial/demo，不生成 ActionRequest，不修改旧规划类型或执行链；不改变 planning 包的旧导出。所有 CoveragePlan 固定 `executable=False`。圆盘直径改变只重建核/候选/规划/指标，输入与消费者结构不变。图像范围只是默认模拟域，未宣称物理边界。JSON 只提供结构和 Mask 哈希，完整归档/渲染在 P3。

本次未修改 `demo/`、Tk 事件协议及工作台 Mask 适配；后续由集成人在筛选前冻结完整原尺寸目标，避免只收到最大残片。当前旧执行选择和现有五页流程保留。接口和坐标约定见 [P1/P2 交接](../软件组/智能覆盖规划_P1_P2.md)。基线为 `fzh-branch@769abab6` 加本轮未提交模块。

## 当前交接：2026-10-10

新增主机链：`WorkbenchWindow`的复检四选项→`ConfirmationBroker.ask_choice`→`CleaningLoop._interactive_recheck`；rewash回新前帧/位置验算/新规划，retake仅新后帧，next保留结果并切目标，pause停止留档。物理动作继续使用布尔授权，不接受复洗字符串当True。

视觉层新增`roi_verification`固定ROI、背景位置/质量门、面积与重合图；`FrozenRoiSegmenter`冻结首次参数和排邻居测量域。位置层`begin_motion`→串口分段脉冲估计→`record_completed`；未完成重启未知。正式工作台/维护`ResourceLease`按COM与账本互斥，维护先作废参考。

记录`cycle_NNN.rechecks[]`保存每次后图/决定/验算，`stage_events.jsonl`保存全部阶段；UI只取轻量快照和最新实时帧。报告按数字读取所有轮次、验证全部ROI/图片哈希，末块认可+无档案问题才提供打印。长期无限复洗仍受磁盘/内存等有限资源约束，没有虚构无限存储能力。[本轮详细验收](../软件组/工作台复洗与位置管理验收_2026-10-10.md)。

> 以下保留此前有日期的说明和实验快照。与本节冲突的工作台、复洗、复检、位置和报告规则已被本节替代；历史数字不代表当前验收。

2026-10-09 当前新增链：`closed_loop_station` → 五页 `WorkbenchWindow/workbench_views` → `workbench_runtime`（主线程只收事件，相机线程独占VideoCapture，设备线程独占串口）→ `TaskModel` 全名单/审核/不可变锁定 → 原 `CleaningLoop/HardwareExecutor/serial` → 原 Episode + 补充 task_manifest/review_log → `result_evidence` → reporting。锁定只选已确认目标；不修改共享八对象，UI答复不是硬件命令。实际 MOVE 跳监控、结束跳结果；原记录自动保存、导出由用户选择。旧 StationPanel 和不加 workbench 的 Mock 保留，以下旧快照只代表当时版本。

核对日期：2026-10-05；基线 main `341224f`（PR #24 后），`feat/single-entry-cleaning-v1` worktree 的主机代码/说明改动尚未提交。固件未在此轮修改或重建，真实设备未操作。 目标见 [PROJECT](PROJECT.md)，接口见 [INTERFACES](INTERFACES.md)。

## 1. 目标系统与当前实现

目标数据流：

```text
真实前图 → Observation（图片与质量记录）
→ 污染 Mask/面积/中心 → StateEstimate（对世界的判断）
→ ActionRequest（动作申请）→ SafetyDecision（允许/拒绝/请人）
→ 确定性控制器 → ExecutionReceipt（设备回执）
→ 真实后图 → VerificationResult（效果判断）
→ STOP / RETRY / HUMAN → Episode（全过程档案）
```

旧单帧 Demo 保留以下三条运行链；新闭环入口另列在本节后半部分：

```text
图像分析链：文件 / USB → 质量 → local 等分割 → 测量 → 像素路径 → 保存
喷水主机链 MCV1：目标 + 固定喷头 → PUMP_IN_PLACE → 人工关卡 → MCV1 → 泵回执
运动链 Stage 2：像素路径 + 电机尺度 JSON + 人工零点
             → MotionRequest → 运动关卡 → 人确认/武装
             → HELLO/MOVEXY/READXY → 计数回执 + 位置账本
```

旧 stage2-move 使用 C 模块内的 MotionRequest，不走共享 ActionRequest/ExecutionReceipt。运动证据在 `stage2_intent.json`、`stage2_receipt.json`，Episode 中 `execution_receipt` 仍为空。完整合同化尚未实现，见 GAP-CTRL-02。

PR #24 最初的合成干跑入口是 `test/integration/test_closed_loop_dry_run.py`，不是 Demo：

```text
合成 Mask → TargetInstance 列表 → 测试里抄成 SequenceTarget
→ plan_sequence → F103SerialSession（先 MOVEXY，成功才 MCV1|PUMP）
→ verify_single_target → verify_area_change
```

`sequence_planner` 不导入 `TargetInstance`。Demo 的 `analyze` / `stage2-move` / `arm-pump` 仍各自独立。

### 新单入口闭环（本轮主机软件已接入）

```text
closed_loop_station CLI
→ CameraPreview/MockFrames：实物同一个窗口显示实时画面和第一次标注；空格只冻结新帧
→ FrozenSegmenter：锁定已有算法/参数/哈希 → B 提取和匹配工具
→ TargetLedger：第一次画面的 S 编号是本轮名单，按编号顺序逐块清洗；复检对不上也不漏下一块，之后不新开污渍
→ 同原图尺寸单目标 Mask → 原 plan_cleaning/path_preview/Stage2Dispatch
→ CycleGeometry：q_obs + d_target + 一次有符号偏移、整任务含回程预算
→ HardwareExecutor：去程、针头重合、短喷、回程各自在同一个窗口确认；重合确认前不发 PUMP
→ 同一 F103SerialSession：只读探测、足量 MOVEXY、确认后的 MCV1 默认 500 ms 短喷、再确认的 RETURN
→ 回原位后再确认 → 原观察位新后图 → 相同分割策略 → 原 verify_single_target
→ 同一个窗口显示复检数字后再确认，才结束这一块并抓下一块 / 有限新帧 RETRY 也要确认 / HUMAN / ERROR
```

V1 只执行 CENTER_POINT，RASTER_SCAN 交 HUMAN。新入口不更改共享 contracts/ports、固件或算法；运动仍在局部请求/回执侧文件中，Episode 存真实泵回执及前后观测。STATUS/READXY/DONE 都不能替代电气、绝对位置或清洗效果验收。

## 2. 实际目录及责任

| 所有者 | 目录 / 主要文件 | 实际职责 |
|---|---|---|
| A | `microcleaning/data_learning/usb_camera.py`、`replay_camera.py` | 真实抓帧或读文件，生成 Observation |
| A | `image_quality.py`、`inspect_images.py`、`metadata_builder.py`、`dataset_manifest.py`、`data_audit.py` | 质量、来源登记、哈希、重复/损坏检查 |
| A | `annotation_tools.py`、`mask_evaluation.py`、`eval_split.py` | 人工 JSON 转 Mask、对照评价、冻结划分 |
| A | `train_entry.py`、`review_round.py` | 当前 OpenCV 调参工作流；不是网络训练 |
| B | `microcleaning/vision/local_contrast_baseline.py`、`hsv_baseline.py`、`otsu_baseline.py`、`exg_baseline.py` | 基线分割；local 为 Demo 默认，ExG/ExR 为颜色对照 |
| B | `contamination.py`、`state_estimator.py`、`verification.py`、`target_instance.py`、`scale_measure.py` | 测量、状态、整图面积复检、单目标复检、离线尺度 |
| C | `control_system/planning/` | 小区域中心点、大区域往复扫描、分块访问；`sequence_planner.py` 只排下一块洗谁；坐标与脉冲预览 |
| C | `control_system/safety/` | 固定规则动作申请、泵治理器、运动关卡 |
| C | `control_system/serial/` | FakeSerial、MCV1 泵适配器、Stage 2 双轴协议与发送器；`f103_session.py` 让两者顺序共用一条串口 |
| C | `control_system/replay/` | Mock、软件回放、Episode 持久化 |
| 集成 | `demo/demo_pipeline.py` 兼容导出；cli/single_frame/image_ops/motion_mode/pump_mode/reporting/camera_preview | 原大文件机械拆分，旧单帧 API/命令保留 |
| 集成 / C | `demo/closed_loop_station.py`、`control_system/orchestration/`、`planning/stage2_geometry.py` | 薄入口 + CleaningLoop + HardwareExecutor + TargetLedger；接入已有视觉、规划、安全和串口 |
| H1 | `firmware/pump/` | 泵输出及测试 |
| H2 | `firmware/motion/` | XY脉冲、名义针头偏移及测试 |
| 公共 | `firmware/common/main.c`、`board/` | 唯一联合入口、读按钮、时钟/中断、输出 |
| 公共 | `firmware/common/safety/`、`serial/` | 复用原控制核心与MCV1，当前main实际调用 |
| 公共 | `firmware/common/keil/`、`compat/stage1/` | 两份可构建工程，旧入口兼容保留；不等于烧录 |

镜像测试在 `test/data_learning/`、`test/vision/`、`test/control_system/{planning,safety,serial,replay}/` 及 Demo 测试。不要恢复旧 control_system 扁平路径或已删除的 `legacy/`。

## 3. 入口决定权限

| 入口 / 模式 | 输入 | 输出 | 真实动作 |
|---|---|---|---|
| `main.py` | 内置合成数据 | Mock Episode | 无 |
| `demo.closed_loop_station --mock`（默认） | 合成原图/串口替身 | 多轮复检、执行回执、完整运行目录 | 无；Mock 独立位置账本 |
| `demo.closed_loop_station --real` | 人指定相机/COM/电机标定/工作坐标/偏移 | 同一个窗口抓图、确认、去程/重合后 500 ms 短喷/回程/后图 | 喷水要两个武装参数，并且重合确认之后才发 PUMP；第一次画面的标注是污渍名单 |
| Demo `analyze` / `camera-analyze` | 文件或 USB 图 | Mask、测量、路径、summary/Episode | 无；加 `--stage2-xy` 也只保存命令预览 |
| Demo `simulate` | 图像 + 模拟条件 | FakeSerial 回执与模拟后状态 | 无；后图变化由模拟产生 |
| Demo `ping-only` | 图像来源 + 人指定的泵协议端口 | PING/STATUS | 不发 PUMP/MOVEXY；探测须与匹配固件配套 |
| Demo `arm-pump` | 图像、固定喷头申请、人工确认 | ActionRequest、审批、泵回执 | controller=stm32 且确认/武装条件满足时可发 PUMP |
| Demo `stage2-move` | 图像、尺度 JSON、已知位置、人指定端口 | 运动申请、审批、意图和发送回执 | 人确认 + `--arm-stage2-xy` 后可发 MOVEXY |
| 旧 Demo `--live` 预览 | 指定 USB 视频设备 | 冻结帧分析、可视化 | 默认无；显式武装泵模式有受限短喷；不发 Stage 2 |

正常分析程序可立即跑，不需要硬件组先把泵装好：

```powershell
cd "D:\大创\3d\MicroCleaningVision"
.\.venv\Scripts\python.exe -m demo.demo_pipeline --input data\raw_images\public\public_001.jpg --mode analyze --stage2-xy --no-tuned-policy
```

这是离线命令，不代表现场实验已做。其它实际操作和硬件授权命令集中在[当前总流程](../总流程说明/团队总流程与输入输出.md)。

## 4. 坐标怎样流动

1. B 输出 `(x_px,y_px)`：x 是列、y 是行，原点通常在图像左上，y 向下。
2. 清洗几何规则输出 `image_px` 路线。点落在 Mask 内，不等于连接点的整段实际运动都覆盖污染。
3. 一般路径预览用 `assumed_work_mm`；默认 `0.01 mm/px` 只是占位。
4. Stage 2 加载电机位移 JSON，用画面中心或 `nozzle_px` 作起点；尺度由步数/图像位移推算，真实毫米仍依赖假定导程。
5. Stage 2 配置按 1600 步/圈和 5 mm/圈得到 320 步/mm；一般预览还有自己的通用默认值，不能混用它的 400 步/mm 文本去验收 Stage 2。
6. 位置账本记相对人工零点的脉冲。断电、丢步、滑移会使它失真；`--stage2-set-zero` 只重记数字，设备不会自动回零。

尺度、轴方向、原点、旋转、喷头偏移是不同问题。一个 mm/px 数字不能同时证明它们。

## 5. 运动执行与错误传播

Demo 先规划，缺标定、标定带警告或位置未知时拒发。主机不再按整份名单的步数合计拦住，也不再按人工零点两侧的步数边界拦住。去程和回程不相加。一条 MOVEXY 仍不能超过固件报文的 20000 步。全部满足仍先给 HUMAN；人在场输入 YES 后才获得短时一次性 ALLOW，发送器核对内容摘要与武装状态再打开 COM。

协议先确认 `STEP_OK v0.3`，按段发送 MOVEXY，轮询 READXY，两轴都停且每个非零轴已发计数等于申请才发下一段。两轴频率相同、步数不同，较短轴先停；**没有按比例插补斜线**。对角路线的图上预览和真实轨迹可能不同，应实测。

发送中途异常会尝试 STOP，并记录已完成、在途指令和回复；位置账本变为未知。STOP 回复也只是协议证据，不代表独立物理断电或位置反馈。

## 6. 明确的架构缺口

| 缺口 | 具体事实 | 下一步位置 |
|---|---|---|
| GAP-CTRL-01 | `scripts/center_object.py` 默认 COM5，直接 serial 写 MOVEXY；标定脚本也直接发运动。二者不经过 Demo motion_gate | ROADMAP：先说明调试入口，再由 C/硬件组提统一关卡方案 |
| GAP-CTRL-02 | Stage 2 运动是局部合同和侧文件；共享 Episode 不能独立容纳完整运动回执 | INTERFACES：合同债务；变更需提案，不能塞成喷射 ActionRequest |
| GAP-VERIFY-01 | 面积复检依赖调用者给 `images_comparable`；没有真实配准验收和自动损伤检测 | B 用真实前后图验证可比性 |
| GAP-INTEGRATION-01 | 新入口已接持续会话、单一偏移、回原观察位、后图与多目标；实物装配/烧录/坐标/效果未验收 | H1/H2 实测接线/偏移，A/B 可比图，C 一轮现场证据；不宣称 E3 |
| GAP-DATA-01 | 旧样本来源未知，正式同条件 U500 数据及现场证据未完整共享 | A 建采集批次与证据包 |

本系统当前是确定性软件流程，不是多 Agent 自动控制机器人。研发 Agent 可审计、实现和评审；本次没有增加 Agent Graph 运行代码，也没有授予物理执行权。

## 7. 此前固件连接及其边界（历史离线阶段）

~~~text
common/main.c
├─ service_safety → 读 PB2/PB3/毫秒 → MCV1 + 原 safety/control_core
│                  → apply_safe_outputs → pump_on/off
│                  → E_STOP / FAULT 时 sm_stop
├─ MOVEXY → motion/sm_start_xy → TIM2/TIM3 实际中断转发
├─ TO_NEEDLE / TO_SCOPE → sm_start_scope_offset → 同一 sm_start_xy
└─ STOP / MCV1 STOP → 两轴停止 + 关泵 + 对应回执
~~~

PUMP ON 固定 300 ms，重复不延期；MCV1有限时ACK/DONE。PB2 改为常闭接地/上拉假设，断线高锁存停止；实物未核对，未插触点会阻止动作。PB3 释放 JTAG 保留 SWD，默认 ARM 按钮非强制。

名义TO偏移继承24mm/7680Y脉冲，不是标定或回零；PC正式Stage2发送器仍不调用它，且现有1600步预算不能直接容纳。新入口已采用单一有符号标定接口；实测值仍缺，不让主机与固件各加一次。

五组当前C、七组旧兼容、工程路径和两套Keil编译证明代码能连接。主机旧独立脚本授权/尝试耗尽/补关等债务仍保留，A/B/C 源码未在该固件整理阶段修改；本次主机集成的新增/修改见上文。详见 [本次验收](../硬件组/结构整理与验收记录_2026-10-05.md)。
