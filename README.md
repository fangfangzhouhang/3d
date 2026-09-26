# 显微智能自清洗科研平台

项目在 [`MicroCleaningVision/`](MicroCleaningVision/)。当前实验链是：抓一帧，识别污渍，规划路径，经串口发给 STM32F103，驱动 X/Y 步进电机。喷水是另一份固件，不和步进同时烧、不同一次发送。

先读：

1. [`MicroCleaningVision/说明文档/总流程说明/团队总流程与输入输出.md`](MicroCleaningVision/说明文档/总流程说明/团队总流程与输入输出.md)
2. [`MicroCleaningVision/说明文档/硬件组/串口协议与参数.md`](MicroCleaningVision/说明文档/硬件组/串口协议与参数.md)
3. [`MicroCleaningVision/AGENTS.md`](MicroCleaningVision/AGENTS.md)

在 `MicroCleaningVision` 目录下，用笔记本摄像头走通到电机（把 `COMx` 换成设备管理器里的 USB 转 TTL）：

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --mode analyze --stage2-xy --arm-stage2-xy --serial-port COMx --camera-index 0
```

只分析和出路径、不转电机时，去掉 `--arm-stage2-xy` 和 `--serial-port`。毫米还是 0.01 mm/像素的占位值。
