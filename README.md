# 显微智能自清洗科研平台

项目在 [`MicroCleaningVision/`](MicroCleaningVision/)。当前实验链是：抓一帧，识别污渍，规划路径，经串口发给 STM32F103，驱动 X/Y 步进电机。喷水是另一份固件，不和步进同时烧、不同一次发送。

先读：

1. [项目长期记忆总入口](MicroCleaningVision/说明文档/项目记忆/PROJECT.md)（六份中文内容的共同上下文）
2. [Agent 开工与更新规则](MicroCleaningVision/AGENTS.md)
3. [当前总流程](MicroCleaningVision/说明文档/总流程说明/团队总流程与输入输出.md)
4. [串口协议与参数](MicroCleaningVision/说明文档/硬件组/串口协议与参数.md)

在 `MicroCleaningVision` 目录下，只抓图、分析和保存双轴路径预览：

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --mode analyze --stage2-xy --camera-index 0
```

`analyze` 不打开运动串口；不能与 `--arm-stage2-xy` 组合。真实运动使用 `stage2-move`，须具备尺度 JSON、人工零点、运动关卡、现场确认和显式武装，步骤见当前总流程。普通分析预览的毫米仍是 0.01 mm/像素占位，不能写成已标定。
