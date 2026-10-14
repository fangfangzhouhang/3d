# MicroCleaningVision

## 2026-10-13：显式直径驻点进入移动

工作树 `D:\大创\wt-coverage-stations`，分支 `feat/coverage-stations-motion`，基线 `c8320cb`。本次驻点移动与复检换行已提交到该分支并推送 `origin` 同名分支。软件验收仍是 Mock，未把脉冲或几何覆盖写成实物位移或洗净。

已填写的覆盖直径会在清洗时变成逐站 MOVEXY 和短喷；针头偏移只加一次。未填写直径时仍是单点中心。CoveragePlan 对象仍不可执行。预览三图改为可上下滚动的较大画面。前图位置未通过时留在监控页，可重拍、改洗下一块或暂停，不会因此放宽位置门槛。复检后再次清洗留在同一污渍；结束这块才转到名单下一行；全部记下后才打开结果报告。全量 630 项 69.461 秒 OK。几何覆盖和脉冲计数都不是清洗有效或标定完成。

## 2026-10-13：覆盖预览三图同屏

既有预览窗口同时显示原图、完整目标Mask和预计覆盖，新UI默认25%轻填色并可调，输入图在重算/无效参数时保留。复用单一FootprintProfile，直径显式输入，无多喷头推荐；旧档案选项/重放及五页导航保持。

最终全量620项190.614秒OK，软件E2/实物E0。本批基线fzh-branch@1107267，未提交/推送；此前P1–P6发布记录仍保留。文件/导航/截图/回滚见[三图验收](说明文档/软件组/覆盖预览三图同屏与导航验收_2026-10-13.md)，操作见[手册](说明文档/软件组/可视化工作台操作手册.md)。

## 2026-10-13：覆盖规划 P1–P6 已提交并推送

用户明确授权后，功能提交 `b36b50544554b60f5952d1a48e00985f164c11ac` 已普通推送到 `origin/fzh-branch`，远端只读核验一致。包含 P1–P6 的代码、测试、可选依赖与交接文档共47文件；源码与最终126项覆盖专项、616项全量回归的验证版本一致，没有新增实物操作。

实际 Git 事件见[提交与拉取日志](说明文档/进度记录/提交与拉取日志.md)。下方“未提交/未推送”描述各阶段验收当时的状态，保留为历史；本次原始测试日志、Mock档案与截图仍仅本机保存。

## 当前交接：2026-10-13，P6 工作台模拟覆盖集成完成

用户明确“继续p6”后完成已批准范围。五页保留，审核页可查看目标原图/完整Mask的模拟覆盖；监控页可对最新完整残留做模拟。虚拟直径初始未知，需明确输入；图层/逐点播放、显式保存的帧/目标/轮次绑定与可选HTML/CSV报告已接通，所有覆盖输出不可执行。

最终覆盖专项126项16.545秒、全量616项75.431秒OK，失败/错误/跳过0，新增18项含2项真实Tk mainloop。242份受保护已有文件SHA不变；原单点、安全/复检及前序未提交成果保留。本地fzh-branch@769abab6，开工远端核对同SHA；无提交/推送或实物动作。工作台模拟证据E2，实物E0。

使用现有Mock工作台启动命令，目标审核→选S→“智能覆盖预览（模拟）”→明确虚拟直径→按需保存；完整接口、分工、证据和回滚见[P6交接](说明文档/软件组/智能覆盖规划_P6.md)，操作见[工作台手册](说明文档/软件组/可视化工作台操作手册.md)。下方暂停和旧阶段记录保留为历史，不再代表当前未实施状态。

## 当前续接：2026-10-13，P6 已批准，按用户要求暂停

P1–P5 成果保留。P6 工作台集成已获批准，目前完成交接阅读、完整 Mask/线程/报告数据流追踪和修改前备份；**尚未修改 P6 业务代码，也未进行 P6 验收**。本地仍为 `fzh-branch@769abab6f65b10104250a7e6f15b8cc306114fba`，未提交/推送或操作实物。

恢复从 [P6 续接记录](说明文档/软件组/智能覆盖规划_P6_续接记录.md) 开始：包含已批准文件、具体分工、源码定位、输入绑定/归档方案、测试顺序及 `D:/大创/work/coverage_p6_20261013/` 的 259 份哈希与 17 份原文件备份。下方“P6 待批准”是各日期历史状态，当前为已批准、暂停在编码前。

## 当前交接：2026-10-12，覆盖规划 P5

已完成同一完整Mask/精确核/约束/预算下的中心、旧扫描点列驻点模拟与贪心对比；直径/定位误差分别支持固定原驻点与重新规划，含名义/条件下保守目标；科学PNG、完整JSON/CSV/NPZ报告校验后重算。所有输出不可执行，情景参数须显式指定。接口、文件所有权、条件、CLI与回滚见 [P5交接](说明文档/软件组/智能覆盖规划_P5.md)。

```powershell
cd D:\大创\3d\MicroCleaningVision
.\.venv\Scripts\python.exe -B -m microcleaning.control_system.planning.coverage_analysis_report --archive D:\coverage\split_input --output D:\coverage\split_analysis --budgets 1 4 8 16 32 64 --diameters 24 36 48 --position-errors 0 1 2
.\.venv\Scripts\python.exe -B -m microcleaning.control_system.planning.coverage_analysis_report --verify D:\coverage\split_analysis
```

先用下方P4入口或已有文件生成完整输入归档；输出目录必须是新目录。直径单位继承Profile，这些数值是人为情景；图表可选依赖为 `requirements/coverage-analysis.txt`，`--no-figures` 不要求Matplotlib。

最终覆盖专项108项16.674秒、全量598项131.465秒OK，失败/错误/跳过0；原4项实际Tk通过。五类合成目标加两组basis对照，7份报告/363 run重算一致。分裂36px下扫描56点/95.16%，贪心17点/95.78%，但贪心访问连线更长；假定2px误差时，保守目标22点达95.30%条件覆盖。212份受保护文件及15份旧完整Plan保持一致，未提交/推送或操作实物。P6五页工作台接入仍待批准。

> 下方P1–P4和旧工作台为各阶段历史快照；本轮状态以P5交接及最新记忆为准。几何比例不代表清除率，条件下保守率不代表物理成功概率。

## 当前交接：2026-10-12，覆盖规划 P3/P4

独立覆盖预览已可运行：调直径后后台重新规划，原尺寸完整 Mask 展示覆盖/遗漏/重叠与编号驻点；支持图层切换、逐点播放、PNG 和完整数据归档、校验后重放。所有计划不可执行，尺寸须显式给定，虚拟几何比例不代表实际清除率。接口、所有权、操作及限制见 [P3/P4 交接](说明文档/软件组/智能覆盖规划_P3_P4.md)。

```powershell
cd D:\大创\3d\MicroCleaningVision
.\.venv\Scripts\python.exe -m demo.coverage_panel --synthetic split --diameter-px 36
```

这里的 36px 是人为指定的模拟尺寸。新旧覆盖专项 78 项、5.293 秒通过，其中本轮新增 31 项、实际 Tk mainloop 4 项；全量 568 项、95.406 秒通过，失败/错误/跳过均 0。102 份受保护旧文件哈希未变；未提交/推送，未连接实物。P5 对比分析和 P6 五页工作台集成仍待批准。

> 下方 2026-10-11 与更早内容保留为当时快照；P3/P4 当前状态以本节及最新项目记忆为准。

## 当前交接：2026-10-11，覆盖规划 P1/P2

在 `fzh-branch@769abab6f65b10104250a7e6f15b8cc306114fba` 上新增离线虚拟喷头模型、完整 Mask 的确定性多驻点覆盖规划与离散覆盖/遗漏指标；本轮改动未提交/推送。直径/尺度/定位误差均显式记录来源，所有 CoveragePlan 固定不可执行。没有修改旧 CENTER_POINT 或工作台/硬件链。可运行示例、接口、限制及分工见 [P1/P2 交接](说明文档/软件组/智能覆盖规划_P1_P2.md)。P3 可视化、P4 Tk、P5 对比、P6 集成尚未实施。

新增专项47项通过，规划96项通过；最终全量537项、215.820秒OK，失败/错误/跳过均0。首跑旧取消测试有等待点超时/清理竞争，独立通过后原判据复跑全量通过，失败记录保留，详见[实验记忆](说明文档/项目记忆/EXPERIMENTS.md)。没有实物操作。

> 下方 2026-10-10 的 Git/测试信息保留为历史验收快照；当前代码基线和覆盖规划结果以本节及最新项目记忆为准。

## 当前交接：2026-10-10

工作台v3已支持复检后人工复洗（无业务次数上限）、仅重拍、下一污渍和暂停；固定首次ROI/分割参数，前后放大及重合图解释清洗率。空前景0%且无效，偏移不作为合格依据。XY pending生命周期和绑定标定的喷头预计图已接入，报告保留每轮清洗与每次复检，由用户选择导出/打印。

```powershell
cd D:\大创\3d\MicroCleaningVision
.\.venv\Scripts\python.exe -m demo.closed_loop_station --mock --workbench --algorithm hsv --mock-scenario workbench-residual
```

最终全量 490 项，111.520 秒，失败/错误/跳过均为 0；此前全量 483 项也通过。 本轮使用合成图、协议串口替身及真实 Tk 控件，证据等级 E2。未连接真实相机或 COM，未操作电机、水泵、固件或打印机；没有获得实物零偏移、喷头精度或清洁效果验收。 详见[操作手册](说明文档/软件组/可视化工作台操作手册.md)及[详细验收](说明文档/软件组/工作台复洗与位置管理验收_2026-10-10.md)。本地 `fzh-branch@23bf6ea173885401c375c51b6865bbc7de7ffb5b` 加本轮未提交改动；只读 GitHub API 核对远端为 `19a2a73bd3817ab8b8aa7b6711a9da3acf3c30fe`。没有拉取、合并、提交或推送。

> 以下保留此前有日期的说明和实验快照。与本节冲突的工作台、复洗、复检、位置和报告规则已被本节替代；历史数字不代表当前验收。

2026-10-09 新工作台：封面 → 实时检测 → 目标审核 → 清洗监控 → 结果与报告。审核并锁定确认子集后才进入原执行链；原图与实时图并列、高亮当前目标、中文解释日志和真实进度。原记录自动保存，报告由用户选择导出/打印。详见 [可视化工作台操作手册](说明文档/软件组/可视化工作台操作手册.md)。

```powershell
.\.venv\Scripts\python.exe -m demo.closed_loop_station --mock --workbench --mock-scenario workbench-mixed
```

真实入口默认启用新工作台，配置与每阶段授权仍须现场确认；本轮仅完成软件 Mock 验证，未操作实物。

显微表面的视觉、路径和 STM32 步进实验。新入口 `demo.closed_loop_station` 将抓图、选污渍、人工确认、移动、定点短喷、回原观察位、单目标复检接在同一进程里。默认 Mock，实物闭环尚未验收；旧 `demo.demo_pipeline` 的单帧模式继续保留。

协议和参数：[说明文档/硬件组/串口协议与参数.md](说明文档/硬件组/串口协议与参数.md)。流程：[说明文档/总流程说明/团队总流程与输入输出.md](说明文档/总流程说明/团队总流程与输入输出.md)。提交与远端 main 的拉取记录：[说明文档/进度记录/提交与拉取日志.md](说明文档/进度记录/提交与拉取日志.md)。

全团队长期记忆从 [PROJECT.md](说明文档/项目记忆/PROJECT.md) 进入，另外五份分别讲架构、交接、设计理由、实验和下一步。Agent 改代码或制定改进计划前完整阅读六份，并按 [AGENTS.md](AGENTS.md) 核对 Git 和原始材料。

## 环境

```powershell
cd D:\大创\3d\MicroCleaningVision
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements\perception-opencv.txt
.\.venv\Scripts\python.exe -m pip install -r requirements\control-serial.txt
.\.venv\Scripts\python.exe -m unittest discover -s test -p "test*.py" -v
```

测试通过只说明软件回归正常，不说明已经标定或已经清洗。

## 单入口闭环

在本次隔离 worktree 复跑，无须另装依赖：

```powershell
cd D:\大创\wt-single-entry-cleaning\MicroCleaningVision
& 'D:\大创\3d\MicroCleaningVision\.venv\Scripts\python.exe' -m demo.closed_loop_station --mock
```

默认使用已有 local 算法，处理三块合成污渍；只替换帧来源和物理串口，排序、单目标路径、安全关卡、F103SerialSession、协议解析和复检均调用生产模块。Mock 自动确认只在软件替身中生效，不打开实物 COM。输出在 `output/closed_loop/station_*/`。

`--mock-scenario retry --max-retries-per-target 1 --max-cycles 4` 可验证新帧、新动作和新审批；`motion-short`、`pump-timeout`、`return-timeout` 等失败样例应停在 ERROR，`decline`/`noncomparable` 应停在 HUMAN。退出码：SUCCESS=0、HUMAN=2、ERROR=3。

V1 只执行单块 CENTER_POINT；扫描路径保存预览后交人工。偏移只来自 `scope_to_nozzle_delta_steps=[dx,dy]`，真实未知为 null；主机不再按整份名单的步数合计拦住，也不再按人工零点两侧的步数边界拦住。一条 MOVEXY 仍不能超过固件报文的 20000 步。RETURN 回到第一帧的原观察位，不能只反转喷头偏移。维护居中/尺度标定/短命令脚本现已显式隔离，发令前作废正式参考；外部控制程序不自动受本项目锁管理。

实物参数、偏移格式、每轮大写 YES 和各组验收材料见 [现场可测入口](说明文档/硬件组/现场可测入口.md)，剩余实物阻塞见 [联调差距](说明文档/总流程说明/一条启动命令的实物联调差距.md)，实现/续做记录见 [续做记录](说明文档/进度记录/单入口闭环续做记录.md)。本轮未操作真实相机、COM、电机或泵，未提交/推送。

## 从一帧到电机

不打开串口，只保存路径：

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --mode analyze --stage2-xy --camera-index 0
```

`analyze` 不会打开步进串口。旧单帧入口转电机使用 `--mode stage2-move`，而且要依次满足：人对好位置后归零、有电机位移标定 JSON、运动关卡通过、人在电机旁输入 `YES`、加 `--arm-stage2-xy`。`COMx` 用设备管理器里的 USB 转 TTL，不要扫描端口。24V 接在 DM542 上时，手要能立刻断电。

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline --stage2-set-zero
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --mode stage2-move --stage2-calibration output\calibration\cal_<时间>\mm_per_px.json --arm-stage2-xy --serial-port COMx --camera-index 0
```

输出在 `output/demo/<run_id>/`：`stage2_xy_pulses.txt` 是计划的句子，`stage2_intent.json` 写于打开串口前，`stage2_receipt.json` 写于发送后（成败都写）；是否真的发过，以 `summary.json` 的 `hardware_actions` 为准。主机不再按步数边界拒发。一条 MOVEXY 仍不能超过固件报文的 20000 步。发送失败会先 STOP，位置记为未知，需要重新对位归零。`--live` 只预览，不能同时发步进。规则细节见[团队总流程与输入输出](说明文档/总流程说明/团队总流程与输入输出.md)。
