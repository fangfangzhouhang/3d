# MicroCleaningVision

显微表面的视觉、路径和 STM32 步进实验。一帧画面经过识别和路径规划，变成 `MOVEXY`，由 F103 的 Stage 2 固件驱动两台电机。当前联合固件包含限时喷水；正式电脑运动入口仍不发泵，默认不发送。

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

## 从一帧到电机

不打开串口，只保存路径：

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --mode analyze --stage2-xy --camera-index 0
```

`analyze` 不会打开步进串口。真要转电机，只能用 `--mode stage2-move`，而且要依次满足：人对好位置后归零、有电机位移标定 JSON、运动关卡通过、人在电机旁输入 `YES`、加 `--arm-stage2-xy`。`COMx` 用设备管理器里的 USB 转 TTL，不要扫描端口。24V 接在 DM542 上时，手要能立刻断电。

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline --stage2-set-zero
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --mode stage2-move --stage2-calibration output\calibration\cal_<时间>\mm_per_px.json --arm-stage2-xy --serial-port COMx --camera-index 0
```

输出在 `output/demo/<run_id>/`：`stage2_xy_pulses.txt` 是计划的句子，`stage2_intent.json` 写于打开串口前，`stage2_receipt.json` 写于发送后（成败都写）；是否真的发过，以 `summary.json` 的 `hardware_actions` 为准。每轴每次最多 1600 步，离零点超过 ±3200 步拒发。发送失败会先 STOP，位置记为未知，需要重新对位归零。`--live` 只预览，不能同时发步进。规则细节见[团队总流程与输入输出](说明文档/总流程说明/团队总流程与输入输出.md)。
