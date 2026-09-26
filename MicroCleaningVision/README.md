# MicroCleaningVision

显微表面的视觉、路径和 STM32 步进实验。一帧画面经过识别和路径规划，变成 `MOVEXY`，由 F103 的 Stage 2 固件驱动两台电机。喷水用另一份 Stage 1 固件，默认不发送。

协议和参数：[说明文档/硬件组/串口协议与参数.md](说明文档/硬件组/串口协议与参数.md)。流程：[说明文档/总流程说明/团队总流程与输入输出.md](说明文档/总流程说明/团队总流程与输入输出.md)。

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

人在电机旁边，并且 24V 已接在 DM542 上时，才武装发送。`COMx` 用设备管理器里的 USB 转 TTL，不要扫描端口。

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --mode analyze --stage2-xy --arm-stage2-xy --serial-port COMx --camera-index 0
```

输出在 `output/demo/<run_id>/`，其中 `stage2_xy_pulses.txt` 是实际准备发出的句子。`--live` 只预览，不能同时发步进。
