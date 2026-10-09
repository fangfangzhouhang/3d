# EXPERIMENTS｜做过什么，结果怎样

## EXP-SW-WORKBENCH-20261009（E2 软件证据）

本机 `D:\大创\3d\MicroCleaningVision`，`fzh-branch@8c422cf` 加未提交改动，Python3.13.14。3候选、确认S0001/S0002、排除S0003：原分割/编排/授权/协议替身产生2次PUMP，报告保留3行；S0001无前景需复核，S0002去除率约0.623仍有残留，最终FAIL。未用硬编码verification，也未连接相机或COM。任务结束无reports目录，显式导出才创建版本目录。

Tk五页按钮/mainloop心跳贯通；软件等待取消覆盖运动/泵/回程以及DONE后，保留回执、原STOP与位置未知保护。新旧Tk连续测试暴露图片析构线程问题，按主线程生命周期修正；最终读档完成后才发布idle。最终全量399项45.084秒OK；独立可视窗口2项17.645秒OK。固定原档案 `output/workbench_qa/station_20261009_154821_ea60ed96/`，3候选/2输出/3报告行，最终FAIL且读档issues为空；截图在screenshots-final。具体日志见实时进度；本机 `output/workbench_qa/` 被Git忽略，其他机器不能假装持有此证据。历史真实记录只读核对不是本轮物理复验。HTML资产/结构通过软件测试，内置浏览器拒绝file协议，浏览器排版/真实打印未验收。

核对日期：2026-10-05；基线 main `341224f`（PR #24 后），`feat/single-entry-cleaning-v1` worktree 的主机代码/说明改动尚未提交。固件未在此轮修改或重建，真实设备未操作。 各历史实验保留当时日期、版本及证据边界。

## 1. 证据能带到另一台电脑吗

当前 `.gitignore` 忽略 output/；本机历史评价、Demo 和调参 JSON **没有进入 Git**。原图、Labelme、Mask、metadata 当前部分已被 Git 跟踪，但 ignore 规则会影响新增数据文件。不要为了本轮记忆自动放行全部数据和输出。

本文件保存重要数值、文件位置和哈希，能随 Git 携带结论索引；它不是原始实验包的替代。另一台电脑缺文件时标记“原始证据未共享”，不能伪造重建同一实验。后续只共享经成员确认的小型证据包或有版本/哈希的存储索引。

## EXP-SW-SINGLEENTRY-20261005｜新 CLI 与失败边界（本轮实际运行）

源码：main `341224f` 加 `feat/single-entry-cleaning-v1` 未提交改动。目录 `D:\大创\wt-single-entry-cleaning\MicroCleaningVision`，复用原 `.venv\Scripts\python.exe`；无真实相机/COM/固件重建/烧录/泵或电机操作，未提交/推送。

- 全量：`python -m unittest discover -s test -v`，**346 项，16.594 秒，OK**；原 Demo API/模式兼容、现有算法回归与新闭环均通过。日志 `D:\大创\tmp\single-entry-final-tests.log`。
- 新增边界：满计数、零轴旧计数、泵移动前预检、授权过期、回程预留/失败、STOP 全链、串口排空失败、后图中断保留 Episode、同策略、新帧、目标重编号/合并/分裂/重现、有限重试。数量不是清洗效果指标。
- CLI 默认命令：`python -m demo.closed_loop_station --mock`（已有 local 算法）；重试命令另加 `--mock-scenario retry --max-retries-per-target 1 --max-cycles 4`；故障命令另加 `--mock-scenario motion-short`。

| CLI 场景 | 任务结果 | 轮数 | PUMP 条数 | 含回程累计 X/Y 步 | 本机运行目录名 |
|---|---|---|---|---|---|
| success | SUCCESS | 3 | 3 | [768, 640] | `station_20261005_224118_9a020a8a` |
| retry | SUCCESS | 4 | 4 | [1152, 704] | `station_20261005_224117_884adfc9` |
| motion-short | ERROR | 1 | 0 | [0, 0] | `station_20261005_224118_25ef2d54` |

目录均在本 worktree `output/closed_loop/`，Git 忽略。success/retry 结束账本均回 `(0,0)`；motion-short 位置信息变未知，PUMP=0。serial.json 保存原文，summary、每轮文件及 Episode 保存证据。

- success summary SHA256：`ff1c99210b8b068d651cafade3b36c901e9c3b5fa3fe25a1201383373d86b805`。
- retry summary SHA256：`3fa6a5a96cb9064a23eda46ff42511fe9689ea9c59b19489759a4b98ed494284`。
- motion-short summary SHA256：`acf7a59a975b75077632586341bb36b6f1b32add9ac8d982263551f23d1d32af`。

支持软件集成 E2。Mock 后图污染变化由替身产生；实际回程精度、偏移、清洗率、配准与无损伤均未证明。原 EXP-FW 的 C/Keil 结果是历史证据，未在本次主机改动中重新运行。旧 center_object/标定脚本未改。

## EXP-SW-20261005｜单串口顺序、单目标复检与序列规划的合成干跑

源码：`feat/closed-loop-cleaning-v1`，闭环测试提交 `4f23918`。没有打开真实 COM，没有相机，没有烧录，没有电机。证据级别是软件集成，不升硬件。

命令（工作目录 `MicroCleaningVision`，使用项目 `.venv`）：

```text
python -m unittest discover -s test/control_system/serial -v
python -m unittest discover -s test/control_system/safety -v
python -m unittest discover -s test/vision -v
python -m unittest discover -s test/control_system/planning -v
python -m unittest discover -s test/integration -v
```

结果：serial 47、safety 25、vision 48、planning 41、integration 12，全部通过。干跑断言包括：步进失败或未批准时喷水字节为 0；只有 `DONE` 才算喷水输出流程成功；STEP 与 MCV1 回复不能被对方解析器当成成功；一个会话不能被两个控制器同时占用；T2/T3 变化不能把仍在的 T1 判成已洗净；配不准是 HUMAN 或未匹配；排序可重复；空 Mask 不发运动也不发喷水；主机步数预算仍是 1600。

还不能说：真实前后图已经配准、清洗率已验收、电机按这些步数走过、喷头偏移已标定。

## EXP-FW-20261005｜固件结构整理、安全连接与协议对接（此前固件阶段实际运行）

源码：fix/stage2-motion-gate@f31e445 + 本轮未提交工作区修改；没有fetch、提交、推送、烧录、真实COM或电机/泵操作。保留此前未提交修改，A/B/C Python业务及其测试未改。证据级别仍是组件/软件连接，不升硬件E3。

- 先迁移再修复：65个非生成文件在纯迁移阶段SHA256一致；最终65个旧文件全部有对应新路径，48个仍字节相同，17个为明确代码/路径/说明修订。26个保护文件（泵低层、原安全核心/协议、板级及旧入口业务）均字节不变。生成/历史本地产物移动保留，不递归删除源码。
- 当前C：5个程序通过，包含真实main的计时、急停、TO往返、STOP、非法输入、过长行注入、BUSY、挂起中断、按钮配置。
- 旧兼容C：7个程序共648断言通过；迁移前本机快照的原3个程序也通过。
- Python编码 → 真实C入口 → Python回复解析：19项离线交互通过（16个协议请求+3个模拟时间/中断/急停推进），保存逐项收发。芯片GPIO/时钟为替身，无串口硬件。
- Node工程布局检查通过；两份Keil分别完整重建，均0错误0警告。联合工程Code=13148，旧兼容Code=8296；这些尺寸不用于评估实物性能。
- .venv全量：233 tests，2.513秒，OK，无跳过。本轮Python回归不会产生实物动作。
- 审查过程中发现：旧/新HAL同名头文件混用会使旧Keil编译失败；改为各自隔离include后重建通过。保留这个失败，不把纯C回归当全部工程证据。

本机原始证据（Git忽略，尚未共享）：output/firmware_audit/2026-10-05/ 的 before、before_manifest.json、migration_manifest.json、final_mapping.json、c_regression.log、python_regression.log、keil_unified.log、keil_compat.log；协议逐帧在 firmware/common/.tools/host-tests/f1ce2a3f8baa412aa4104113aabada48/host_contract_transcript.json。

| 文件 | SHA256 |
|---|---|
| migration_manifest.json | BB9F6FAEE284E0C0550936089F6A0C6E0762C746B5A78143EBB2C17694852934 |
| final_mapping.json | F0039FA66287DE21261C1670A148E76C7BA1264652890E619F86FC23B014AE8A |
| c_regression.log | F4C12243A42C183B17F7C8761468C6283D4E0DDBEFC790E2B3BFEF26EC5A6EBA |
| python_regression.log | 885476703E7B109487D92F80B05764E3DD44D9964DCD7A11D22BFCEFE77C3B40 |
| host_contract_transcript.json | 4FC5EDB8155A072FEDFF0F92DACF46E556B21E0EFCFC0CC16873B160AD57FB6B |

复跑命令和完整审查见 [硬件验收记录](../硬件组/结构整理与验收记录_2026-10-05.md)。不依赖本机快照的默认C测试仍能在别的电脑运行，需本地编译器；Keil依赖显式路径。

未证明：PB2常闭接线实际匹配、真实电气停止、输出时长/液量、位移/偏移标定、真实前后图和清洗效果。电脑端顺序会话是后来的软件干跑，见上面的 EXP-SW-20261005，当时还没有；后续 Demo 接入见 EXP-SW-SINGLEENTRY-20261005。默认按钮非强制；轮询不是独立硬件急停。center_object.py 等直接入口仍未整改。尚无第二位成员独立签字。

## EXP-SW-01｜本次软件回归（2026-10-03，实际运行）

- 环境：本项目 `.venv`，Python 3.13.14；当前 HEAD 为 `17bf617`，运行前业务代码无未提交差异，文档已有未提交修改。
- 命令：`.\.venv\Scripts\python.exe -m unittest discover -s test -p "test*.py" -q`。
- 结果：**233 tests，25.324 秒，OK，无跳过**。包含数据工具、分割、Live/Demo、MCV1、Stage 2 运动关卡、发送异常与位置账本。
- 输入：测试夹具、程序生成像素和相机/串口替身；部分用临时目录保存结果。
- 支持：当前软件合同和错误路径能重复运行，组件 E1、软件交接 E2。
- 不支持：真实相机身份、毫米定位、急停硬件、喷水效果或硬件 E3。本轮未打开真实 COM、未驱动水泵/电机，未重新烧录或构建固件。
- 原状态的 150 项是历史数字，本轮同步为 233。以后必须写运行日期与源码版本，不只替换数字。

## EXP-DATA-01｜旧图、标注与来源（本次文件核对）

- `data/metadata.csv`：13 行，全部 labeled；公共目录 11 张 public 图和 M9/M12 两张，共 13 张，均有人工 JSON/Mask。
- category、source、capture_date、device、magnification 仍 unknown；无法从文件夹名 public 或画面内容补出来源。
- 冻结划分：开发 9 张；留出 public_002、public_011、M9、M12 共 4 张。
- `data/raw_images/` 现有 **14 张图片**：上述 13 张 + 1 张 USB probe PNG。第 14 张未在这份 13 行 metadata 中登记。
- 历史质量/数据审计曾记录旧 13 图可解码、0 损坏、0 相同 SHA256 重复组；本轮只核对文件和登记，不把历史检查写成对所有新增图片重新完成审计。
- metadata SHA256：`c15b40b4fc0ee689918ddf6a276a630a2944914e689d330eb9d1d5fa208bcf5c`。

## EXP-VISION-01｜13 图三算法历史比较（报告 2026-09-17，本次读取）

原文件：`output/data_learning/evaluations/comparison_summary.json`；13 张图 × 3 算法 = 39 条评价，不是 39 张独立样本。下面是分算法、分数据划分的结果。

| 算法 | 开发 9 张 mean IoU | 留出 4 张 mean IoU | 留出 Precision | 留出 Recall | 留出 mean 中心误差 px |
|---|---:|---:|---:|---:|---:|
| HSV | 0.060184 | 0.080424 | 0.751041 | 0.302717 | 2.2983 |
| Otsu | 0.299156 | 0.169028 | 0.279752 | 0.198195 | 171.2350 |
| local | 0.440557 | 0.209436 | 0.269356 | 0.551290 | 100.1807 |

IoU 是人工/算法区域的重合程度；Precision 低说明误检多，Recall 低说明漏检多。报告的所有算法混合 mean IoU=0.231657 不能当某一个算法的性能。

结论：在这份历史旧图上 local 的平均重合度较高，但留出效果仍弱、误检多；不能宣称已达到真实 U500 清洗定位要求。HSV 的中心误差看起来较小，也不能抹掉漏检问题；中心不存在时的统计处理需要同时检查，不能跨方法只挑好看的数字。

局限：样本来源未知、样本很少、分辨率混合；按文件名划分不证明样本/采集批次隔离。本次未重跑三算法，也未证明历史报告的策略等于未来一次运行加载的本机参数。

报告 SHA256：`60d40012cc196333ba86bab270d4d7f4153bae6691edf74167ec1aeb6b547266`。算法版本/参数要从对应 summary 和运行目录追溯，不从汇总数字倒推。

## EXP-TUNE-01｜单轮复核与留出保护（历史本机输出）

- `output/data_learning/tuning/trial_public_002/review_round.json` 实际处理的是开发图 public_001：IoU 0.547804，前后参数相同、changes 空、applied=false。**输出目录名不是输入样本身份**，没有提升。
- `trial_public_002_inspect/review_round.json` 实际处理留出 public_002：inspect_only=true，IoU 0.631356，changes 空、applied=false；工具拒绝用这张图改参数。
- 两条都是单图调试/检查，不能代替整组统计。不能因为文件名出现 public_002 就认定发生数据泄漏；也不能反过来保证未来反复看留出调参不会泄漏。

## EXP-CAMERA-01｜已有 USB 像素链文件（历史，本次核对）

Git 已跟踪一张图片：

`data/raw_images/usb_probe/usb-camera-probe/pre_20260917T032956050812Z_b7687363.png`

SHA256：`18cb3c06e5abb0a6803668f8646cada3962a3399d9fd5308e0e76a21038d2ecc`。

本机另有 `output/demo/demo_20260926T171027Z_2d069565/summary.json`：source_kind=camera、usb-camera:index=0、640×480、local；面积 35111 px、28 个连通块、FOCUS_LOW/CONFIDENCE_LOW。该 summary 的 SHA256 是 `2d30c578fb257b035a4f9f9d415c87bcbbb8602f6da3c749b40f5003aa0861fe`。

支持：本机存在 USB 抓帧/分析链的历史文件证据；“从未有真实像素”或“完全没有相机图”已经不是准确现状。

未证明：设备 index=0 对应 U500 的稳定身份、分辨率/FPS、条件、原图来源审查和重复性。质量 flags 来自暂定阈值，不是科学判废标准。摄像头实际型号正式验收仍等待成员补记录。

## EXP-HW-01｜现场进展与缺失证据（报告，不是本轮实机）

| 日期 / 来源 | 记录内容 | 能接受的有限结论 | 缺什么 |
|---|---|---|---|
| 09-09 进度记录 | 电源—继电器—蠕动泵功率回路可转 | 现场报告部件动作 | 接线版本、控制时序、原始记录 |
| 09-15 进度/状态 | 同学机报告 F103 短喷 | 保留现场报告 | 同任务 PONG/ACK/DONE、前后图与 Episode |
| 09-26 `0830bb3` / `0363c79` | 双轴协议与“XY 可控制”提交 | 源码和试验标题在历史 | 实际运动录像、步数/位移、方向和停止记录 |
| 09-28 `32be276` | 位移标定工具加入 | 工具存在 | 本次核对未找到 output 下 mm_per_px.json；不能声称已完成可复查标定 |
| 09-29/30 `650a2d4` / `42aae20` | “成功版”“上市版”运动提交 | 代码演进存在 | 标题不证明定位误差、限位或清洗 |
| 10-02 `91c3809` / `17bf617` | 异常 STOP、运动关卡 | 软件保障有回归证据 | 新关卡下的现场位移和效果验收 |

Stage 1/2 没有统一运动喷水闭环证据。现有 READXY 是脉冲计数，不能当编码器位置。尚无已验收 E3；不能把报告全部丢掉，也不能按报告自动升级。

## 2. 下一份真实实验最小记录格式

```text
实验编号 / 日期 / 执行成员 / 问题和可推翻的假设
源码提交 + 工作区是否有差异 + 环境与参数版本
设备型号 / 固件版本与烧录记录 / 接线版本
样本、污染和采集批次 / 光照倍率工作距离
前图路径和 SHA256 / 人工参考 Mask（评价用）
动作或运动申请 / 审批 / 原始收发 / 已执行或未知部分
后图路径和 SHA256 / 前后是否可比与判断依据
结果数值 / 所有失败 / 本次证明与未证明内容
证据共享位置、访问方法、文件清单与 SHA256
```

没有实物时允许记录纯软件实验，但 type 必须明确为 synthetic/mock/replay/manual_change，不能混进真实自动清洗效果统计。新条目使用独立编号，重复试验逐次保留，不只保存最漂亮的一次。
