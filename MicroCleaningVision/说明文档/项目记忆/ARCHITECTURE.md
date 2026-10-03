# ARCHITECTURE｜整个系统怎么工作

核对日期：2026-10-03；源码基线 `17bf617`。目标和真实进度见 [PROJECT](PROJECT.md)，交接字段见 [INTERFACES](INTERFACES.md)。

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

当前有三条不同的运行链，不能因为共享一个入口就把它们当成同一个真实闭环：

```text
图像分析链：文件 / USB → 质量 → local 等分割 → 测量 → 像素路径 → 保存
喷水链 Stage 1：目标 + 固定喷头 → PUMP_IN_PLACE → 人工关卡 → MCV1 → 泵回执
运动链 Stage 2：像素路径 + 电机尺度 JSON + 人工零点
             → MotionRequest → 运动关卡 → 人确认/武装
             → HELLO/MOVEXY/READXY → 计数回执 + 位置账本
```

Stage 2 目前使用 C 模块内的 MotionRequest，不走共享 ActionRequest/ExecutionReceipt。运动证据在 `stage2_intent.json`、`stage2_receipt.json`，Episode 中 `execution_receipt` 仍为空。完整合同化尚未实现，见 GAP-CTRL-02。

## 2. 实际目录及责任

| 所有者 | 目录 / 主要文件 | 实际职责 |
|---|---|---|
| A | `microcleaning/data_learning/usb_camera.py`、`replay_camera.py` | 真实抓帧或读文件，生成 Observation |
| A | `image_quality.py`、`inspect_images.py`、`metadata_builder.py`、`dataset_manifest.py`、`data_audit.py` | 质量、来源登记、哈希、重复/损坏检查 |
| A | `annotation_tools.py`、`mask_evaluation.py`、`eval_split.py` | 人工 JSON 转 Mask、对照评价、冻结划分 |
| A | `train_entry.py`、`review_round.py` | 当前 OpenCV 调参工作流；不是网络训练 |
| B | `microcleaning/vision/local_contrast_baseline.py`、`hsv_baseline.py`、`otsu_baseline.py`、`exg_baseline.py` | 基线分割；local 为 Demo 默认，ExG/ExR 为颜色对照 |
| B | `contamination.py`、`state_estimator.py`、`verification.py`、`scale_measure.py` | 测量、状态、面积复检、离线尺度 |
| C | `control_system/planning/` | 小区域中心点、大区域往复扫描、分块访问；坐标与脉冲预览 |
| C | `control_system/safety/` | 固定规则动作申请、泵治理器、运动关卡 |
| C | `control_system/serial/` | FakeSerial、MCV1 泵适配器、Stage 2 双轴协议与发送器 |
| C | `control_system/replay/` | Mock、软件回放、Episode 持久化 |
| 集成 | `demo/demo_pipeline.py`、`demo/live_station.py` | 编排各模块、模式选择、预览、保存结果；变更前协调消费者 |
| 硬件组 | `firmware/stm32f103/f103-stage1/` | Stage 1 泵与联锁固件 |
| 硬件组 | `firmware/stage2-stepmotor/` | Stage 2 双轴步进测试固件 |
| 硬件组 | `firmware/keil/` | 两份 Keil 工程；构建输出不是烧录证据 |

镜像测试在 `test/data_learning/`、`test/vision/`、`test/control_system/{planning,safety,serial,replay}/` 及 Demo 测试。不要恢复旧 control_system 扁平路径或已删除的 `legacy/`。

## 3. 入口决定权限

| 入口 / 模式 | 输入 | 输出 | 真实动作 |
|---|---|---|---|
| `main.py` | 内置合成数据 | Mock Episode | 无 |
| Demo `analyze` / `camera-analyze` | 文件或 USB 图 | Mask、测量、路径、summary/Episode | 无；加 `--stage2-xy` 也只保存命令预览 |
| Demo `simulate` | 图像 + 模拟条件 | FakeSerial 回执与模拟后状态 | 无；后图变化由模拟产生 |
| Demo `ping-only` | 图像来源 + 人指定的泵协议端口 | PING/STATUS | 不发 PUMP/MOVEXY；探测须与匹配固件配套 |
| Demo `arm-pump` | 图像、固定喷头申请、人工确认 | ActionRequest、审批、泵回执 | controller=stm32 且确认/武装条件满足时可发 PUMP |
| Demo `stage2-move` | 图像、尺度 JSON、已知位置、人指定端口 | 运动申请、审批、意图和发送回执 | 人确认 + `--arm-stage2-xy` 后可发 MOVEXY |
| `--live` 预览 | 指定 USB 视频设备 | 冻结帧分析、可视化 | 默认无；显式武装泵模式有受限短喷；不发 Stage 2 |

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

Demo 先规划、检查每轴累计最多 1600 步和相对人工零点 ±3200 步软限位，缺标定/有警告/位置未知时拒发。全部满足仍先给 HUMAN；人在场输入 YES 后才获得短时一次性 ALLOW，发送器核对内容摘要与武装状态再打开 COM。

协议先确认 `STEP_OK v0.3`，按段发送 MOVEXY，轮询 READXY，两轴都停才发下一段。两轴频率相同、步数不同，较短轴先停；**没有按比例插补斜线**。对角路线的图上预览和真实轨迹可能不同，应实测。

发送中途异常会尝试 STOP，并记录已完成、在途指令和回复；位置账本变为未知。STOP 回复也只是协议证据，不代表独立物理断电或位置反馈。

## 6. 明确的架构缺口

| 缺口 | 具体事实 | 下一步位置 |
|---|---|---|
| GAP-CTRL-01 | `scripts/center_object.py` 默认 COM5，直接 serial 写 MOVEXY；标定脚本也直接发运动。二者不经过 Demo motion_gate | ROADMAP：先说明调试入口，再由 C/硬件组提统一关卡方案 |
| GAP-CTRL-02 | Stage 2 运动是局部合同和侧文件；共享 Episode 不能独立容纳完整运动回执 | INTERFACES：合同债务；变更需提案，不能塞成喷射 ActionRequest |
| GAP-VERIFY-01 | 面积复检依赖调用者给 `images_comparable`；没有真实配准验收和自动损伤检测 | B 用真实前后图验证可比性 |
| GAP-INTEGRATION-01 | Stage 1 泵与 Stage 2 运动没有统一固件/会话 | 先固定点闭环候选，再评估移动喷洗集成 |
| GAP-DATA-01 | 旧样本来源未知，正式同条件 U500 数据及现场证据未完整共享 | A 建采集批次与证据包 |

本系统当前是确定性软件流程，不是多 Agent 自动控制机器人。研发 Agent 可审计、实现和评审；本次没有增加 Agent Graph 运行代码，也没有授予物理执行权。
