# EXPERIMENTS｜做过什么，结果怎样

## EXP-SW-THREE-VIEW-20261013：E2三图与导航

基线fzh-branch@1107267，2份demo源码/2份测试增量；未提交/推送/实物操作。最终全量620项190.614秒OK，失败/错误/跳过0，含130项覆盖相关及10项覆盖真实Tk。新增4项Tk测试检查同帧完整输入、重算/无效时输入保留、强度不规划/导出快照、旧Options/最小布局、五页守卫/预览重开/MOVE/复检/结果路由。

实际Mock五页走通、两份绑定归档重放、可选HTML/CSV保存成功，质量FAIL保留；最小1080×730窗口与工作台高DPI布局做自身窗口截图检查。早期播放时序、布局首次1px映射及全量620首跑单项失败保留，输入缓存/空间保留/标题合理最小换行及等待WM映射修复后通过；旧断言未放宽。

本机证据D:/大创/work/coverage_ui_20261013/：full_release_validation.log、panel_mapping_validation.log、navigation_validation.log、visual_release.log、end_to_end_final.log及对应JSON/own-HWND PNG。此前130项专项40.089秒为早期UI版本，最终判据以上述620全量为准。[命令/保护/回滚](../软件组/覆盖预览三图同屏与导航验收_2026-10-13.md)。软件E2，实物E0；不证明真实喷痕、物理位移或清洁效果。

## EXP-SW-COVERAGE-P6-20261013：E2工作台模拟绑定

用户“继续p6”恢复批准范围；fzh-branch@769abab6加未提交成果，开工远端同SHA。最终覆盖专项126项16.545秒、全量616项75.431秒OK，失败/错误/跳过0；新18项含真实Tk2，全部覆盖相关真实Tk共6。242保护文件SHA相同，63份firmware及旧测试未改。早期新fixture/QA驱动失败日志保留，未放宽原视觉或旧测试判据。

全残留83px进入规划，旧执行仍用最大48px，邻居未混入；预览开/关/异常的Mock MOVEXY/PUMP/STOP序列（归一化随机action_id）一致，retake只重拍。测试覆盖过期目标/帧/代次、同worker关闭重开、点击快照、无直径、篡改/路径/不完整/旧档案与质量隔离。

最终320×240 local未tuned Mock任务3目标3次输出，主窗口心跳88；绑定归档两份重算一致：显式16px/stride2/95%，首次896px→6点95.2009%、遗漏43px；后残留359px→4点97.2145%、遗漏10px。原复检约59.9%，流程COMPLETED、质量FAIL保留，多点没有执行，不是97.2%清除。原图/报告与窗口自身截图位于D:/大创/work/coverage_p6_20261013/，仅本机未共享；ui_validation.json SHA 2a4d61bf1f36214872c1ea8c5e2779759e73a6fa1705d981a43bc094a98d7ce4。最终源码指纹76dc621d1de26765caa12cca049f9417a876883b5c2209566743f68c97b7691d；命令/全部日志/限制见[P6验收交接](../软件组/智能覆盖规划_P6.md)。未获得物理喷痕/尺度/定位/清洁证据。

## EXP-SW-COVERAGE-P5-20261012：E1几何比较与重放

`fzh-branch@769abab6f65b10104250a7e6f15b8cc306114fba` 加前序P1–P4及批准的P5未提交成果；本地/跟踪引用/只读GitHub API一致。Python3.13.14、NumPy2.5.2、OpenCV4.14.0、Pillow12.3.0、Matplotlib3.11.1、Tk8.6，环境未安装升级，无设备动作。现有独立Tk回归仍E2，整体实物E0。

- 最终专项：`python -B -m unittest discover -s test -p 'test_coverage*.py' -v`，108项16.674秒OK，新增30项；失败/错误/跳过0，含原4项真实Tk mainloop。
- 最终全量：`python -B -m unittest discover -s test -p 'test*.py' -v`，598项131.465秒OK，含145项planning，失败/错误/跳过0。此前同轮598项133.282秒也通过；只调整缺指标图的横轴为实际情景后再次验收，原日志保留，没有改旧测试判据。
- 独立像素集合核验：三方法并集/重叠、共享误差外包可行域；完整分裂残片、非法质心/粗网格原因、固定方案越界/不可用时保留点、毫米尺度未知、空/人工语义、非圆核拒绝偷换、超限raster生成前拒绝、稳定ID不含耗时。归档覆盖实际Agg PNG、CSV/NPZ/数值篡改、巨大NPY声明、新目录不覆盖和中断无完成标记。
- 五类合成原图/完整Mask，均360×240，虚拟36px assumed、stride1、step16、95%目标、预算64。中心/扫描/贪心：圆1/55/19点及7.60/95.52/95.75%；长条1/36/13及11.00/97.40/96.25%；凹形1/44/14及8.91/96.15/96.29%；分裂3/56/17及21.74/95.16/95.78%；图像边界1/21/11及16.50/76.04/95.04%。边界扫描13提案违规被过滤。
- 分裂贪心17点的访问连线1581.39px，扫描56点1131.86px；一次计算约0.12512s与0.02116s（不是统计结论/真实设备耗时）。更少驻点并不证明路程或总体效率最优。
- 分裂36→24px，固定17点仅58.93%，重规划34点95.11%；36→48px，固定17点100%，重规划11点95.33%。图像边界固定48px虽99.90%覆盖，5点违规不排名；重新8合规点96.07%。
- 另冻结同输入/显式0px参考误差，2px情景的nominal目标重规划17点名义95.78%、条件保守85.33%；conservative目标22点名义99.32%、条件保守95.30%。误差及保守百分比都是条件模型，非实测误差/物理成功率。
- 7份完整报告、363 run全部实际重算一致；不是363独立数据样本。circle/strip/concave各18、split234、border33、split_nominal/conservative各21；split analysis_id=`ea7bdbf38ce14a935f9eee8eacdac322ee766c0ae3b11ce7f6dc7a59542ae139`。主对比/敏感性/边界/保守目标图已目视核验。
- 开始前214份文件，仅批准的planner与其测试两份改变，其他212份SHA相同；15份旧完整Plan（五形状×未知/0/2px）及既有P3归档ID未变。两个原文件副本SHA与开始前相同，支持仅撤销本轮提取；不依赖整仓reset。

本机证据目录 `D:/大创/work/coverage_p5_20261012/`：`coverage_tests_final.log`、`full_regression_final.log`（首轮release日志保留）、`reports_release/`、`inputs/`、`benchmark_summary.json/csv`（最终未提交源码指纹/报告清单SHA）、`release_protection.json`、`baseline_sources/`、`split_export_release.log`/`split_verify_release.log`/`remaining_benchmarks_release.log`/`robustness_release.log`。英文TEMP沿用P3/P4；日志和原始图在仓库外，未自动共享。接口/完整条件/命令/回滚见 [P5交接](../软件组/智能覆盖规划_P5.md)。不能推出实际清除、损伤、能耗、定位、STOP延迟或真实多驻点已验收。

## EXP-SW-COVERAGE-20261012：P3/P4 软件与独立Tk证据

基线`fzh-branch@769abab6f65b10104250a7e6f15b8cc306114fba`加前序P1/P2与本轮未提交改动。Python3.13.14、numpy2.5.2、OpenCV4.14.0、Pillow12.3.0、Tk8.6。新渲染/归档为E1，独立UI联动为E2；实物E0，没有连接真实设备。

- 最终覆盖专项：`python -m unittest discover -s test -p 'test_coverage*.py' -v`，78项5.293秒OK；原47项+本轮31项，失败/错误/跳过0。像素分区/独立前缀坐标并集、非圆核、完整残片/约束重放、损坏/篡改/超大数组/重复目录拒绝、CLI无隐式尺寸及无硬件依赖均覆盖。
- 4项实际Tk mainloop检查：阻塞后台仍有心跳；快速直径防抖与旧结果丢弃；图层不重新规划；逐点/播放；导出同时改直径仍保留点击快照；未知/非法输入恢复；仅改Policy保持measured与精确核；关闭取消after。无显示跳过0。真实截图已目视检查，Windows200%屏幕缩放时先映射Tk坐标再截取全窗。
- 最终全量：`python -m unittest discover -s test -p 'test*.py' -v`，568项95.406秒OK，失败/错误/跳过0；包含全部115项planning用例。原取消/STOP/工作台判据未改；本轮未出现前序记录的等待点超时。
- 实际CLI四件归档生成并读档重算：合成split360×240、虚拟36px、stride2、目标95%、预算64；完整目标11295px，18驻点，覆盖10836px，遗漏459px，几何0.9593625498007968，重叠4134px。保守率/毫米面积None，executable=false。PlanID=`89e17eaf47effe9e0c128e48ddc02c900323c22a92dada259d419d9de7ea16de`；报告PNG已目视检查。
- 102份受保护旧文件与本轮开始前SHA256快照一致，含P1/P2源码/测试、原demo/编排/视觉/固件及既有.gitignore；无旧行为改写。新增文件语法/空白与同步链接另行检查。

本机原始证据在`D:/大创/work/coverage_p3_p4_20261012/`：`coverage_tests_release.log`、`full_regression_release.log`、`baseline_hashes.json`、`screenshots/coverage_panel.png`、`preview_split_d36/`。英文TEMP为`C:/Users/16149/AppData/Local/Temp/mcv_coverage_p3_p4`；不靠更改旧测试绕过Unicode夹具问题。日志/图片位于仓库外，未自动共享；[操作和接口](../软件组/智能覆盖规划_P3_P4.md)。不能由上述合成/UI结果推出喷痕、定位、实际清洁或硬件多驻点验收。

## EXP-SW-COVERAGE-20261011：P1/P2 离线软件证据

基线 `fzh-branch@769abab6f65b10104250a7e6f15b8cc306114fba`，本轮新增未提交。Python3.13.14/numpy2.5.2/OpenCV4.14.0；没有连接实物相机、COM、电机或泵。新增模块证据为 E1；旧工作台的 Mock/Tk 回归仍是软件证据，整体硬件 E0 不变。

- 新覆盖专项：`python -B -m unittest discover -s test/control_system/planning -p 'test_coverage*.py' -q`，47项、0.422秒，OK；规划目录96项、1.245秒，OK。随机并集20例、非对称锚点贪心25例和非对称误差盒16例与独立坐标集合枚举一致；另覆盖完整分裂残留、边界、禁喷/中心域、单位/来源、未知/空/资源限制、不可变和禁止实物打开。
- 文档离线例子实际运行：完整目标174px且含另一小残留；虚拟直径6px为12驻点、10px为7驻点，均几何覆盖1.0且executable=False。原Profile未变。此例不证明真实喷头尺寸或实际清除率。
- 初次全量537项、225.898秒，FAILED(errors=1)：旧`test_workbench_events`取消测试的return-timeout子场景未在2秒内进入等待点，随后临时目录清理与工作线程竞争。原判据/源码未改；单独复跑该旧测试1项、1.548秒OK。失败原始日志保留为`D:/大创/work/coverage_p1_p2_20261011/full_regression.txt`，重跑另存日志，未覆盖失败记录。
- 最终全量复跑：`python -B -m unittest discover -s test -p 'test*.py' -q`，537项、215.820秒，OK，失败/错误/跳过均0。原始日志`D:/大创/work/coverage_p1_p2_20261011/full_regression_retry.txt`。没有改变旧取消/STOP判据；首跑显示既有测试存在时序敏感性，独立通过和全量复跑通过不能抹掉该记录。
- 默认沙箱虚拟TEMP曾造成旧位置测试写入/清理权限失败；改用`C:/Users/16149/AppData/Local/Temp/mcv_coverage_p1_p2`后规划目录通过，没有修改旧测试或权限逻辑。命令与可复现例子见[P1/P2交接](../软件组/智能覆盖规划_P1_P2.md)。日志位于本机仓库外，未自动共享；共享代码中的测试可独立重跑。

最终三份源文件SHA256（本机字节快照；Git换行转换后可不同）：coverage_model=`893022f296a7feb61653b2b3b806d3aa0782901b1ac1c01a861df64e41fe185e`；coverage_metrics=`81b844ad31dff25d725dd47833f2fe6ab110fc7c67446c4413cf33379b88a2b0`；coverage_planner=`b6e809a425d7ce13b567770ffa23d7ba1dd0db1213675979992f15a85111d03d`。相关三份新测试见`test/control_system/planning/test_coverage_*.py`，无需真实数据或串口。

## 当前交接：2026-10-10

### EXP-SW-REWASH-20261010：E2软件回归

本地 `fzh-branch@23bf6ea173885401c375c51b6865bbc7de7ffb5b` 加本轮未提交改动；只读 GitHub API 核对远端为 `19a2a73bd3817ab8b8aa7b6711a9da3acf3c30fe`。没有拉取、合并、提交或推送。 最终全量 490 项，111.520 秒，失败/错误/跳过均为 0；此前全量 483 项也通过。 针对复检多选5项与维护隔离8项通过；完整Tk主事件循环覆盖五页、复洗/重拍/切目标/末块有理由认可，多次复跑。12次额外复洗有13个新动作/帧，所有轮次导出；3px偏移阻止末块认可；0空前景不自动合格。

日志`D:/大创/tmp/mcv_impl_20261010/final_results.txt/json`。固定合成档案`output/workbench_qa/rewash_20261010_601fb173/`，显式验收导出`output/workbench_qa/rewash_20261010_601fb173/reports/report_20261010_192655_4493ae79/`：3候选/2执行/3清洗/4复检，SUCCESS但质量FAIL且report_ready=True，先前残留未被覆盖。索引`output/workbench_qa/rewash_validation_20261010.json`含源码哈希。本机output被忽略，需另共享原始包才能在他机复查。

本轮使用合成图、协议串口替身及真实 Tk 控件，证据等级 E2。未连接真实相机或 COM，未操作电机、水泵、固件或打印机；没有获得实物零偏移、喷头精度或清洁效果验收。 浏览器分页/真实打印尚未验收，未绕过此前本地文件预览策略。旧Unicode临时测试夹具问题用英文TEMP复跑辨别，未包装成算法故障。[本轮详细验收](../软件组/工作台复洗与位置管理验收_2026-10-10.md)。

> 以下保留此前有日期的说明和实验快照。与本节冲突的工作台、复洗、复检、位置和报告规则已被本节替代；历史数字不代表当前验收。

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
