# MicroCleaningVision

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

V1 只执行单块 CENTER_POINT；扫描路径保存预览后交人工。偏移只来自 `scope_to_nozzle_delta_steps=[dx,dy]`，真实未知为 null；每一条 MOVEXY 每轴最多 10000 步，去程和回程不相加，软限位是人工零点 ±10000 步。RETURN 回到第一帧的原观察位，不能只反转喷头偏移。旧直接串口脚本尚未统一整改。

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

输出在 `output/demo/<run_id>/`：`stage2_xy_pulses.txt` 是计划的句子，`stage2_intent.json` 写于打开串口前，`stage2_receipt.json` 写于发送后（成败都写）；是否真的发过，以 `summary.json` 的 `hardware_actions` 为准。每轴每次最多 10000 步，离零点超过 ±10000 步拒发。发送失败会先 STOP，位置记为未知，需要重新对位归零。`--live` 只预览，不能同时发步进。规则细节见[团队总流程与输入输出](说明文档/总流程说明/团队总流程与输入输出.md)。
