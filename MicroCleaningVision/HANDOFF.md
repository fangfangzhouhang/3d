# MicroCleaningVision 项目交接

## 一句话目标

让系统在显微图像下识别污染区域、生成可审查的处理请求、通过确定性控制层执行或模拟动作，再用处理后图像验证效果，最终形成可追溯 Episode。

项目根：`D:\大创\3d\MicroCleaningVision`

## 2026-08-31 实时状态快照

- 当前阶段：P1 硬件交接模块已完成，等待 U500 实机和 NUCLEO 固件。
- 软件组件：E1；A/B/C 组合链：E2。
- 项目 `.venv` 中曾记录 87 tests passed、无 skip；这只是当时的软件收据，继续工作前应复跑。
- `public_001.jpg` 的人工 mask 与 HSV 基线 IoU 为 0.0739：证明流水线能跑，也证明当前 HSV 在该样本失败；不证明整体数据集性能。
- U500 adapter 已通过 FakeVideoCapture 测试；尚无真实设备 probe 收据。
- MCV1 host protocol、只读 PING/STATUS 测试在无硬件下通过；实际 firmware 与硬件回包仍缺失。
- 标定尚未验证，硬件证据仍为 E0。

## 数据状态

- 13 张 raw JPG。
- 13 个 Labelme JSON。
- 13 个转换 mask。
- 只有 1 张进入预测对比，另 12 张仍需复核/评估。
- 数据来源、设备、放大倍数和日期尚未完全核验。

## 正确的下一闸门

1. 用真实 U500 做 probe/capture，保存设备、参数、时间和样图收据。
2. 核验 13 张图的来源、设备、放大倍数和采集日期。
3. 人工审查 mask，冻结 dev/holdout，避免后续泄漏。
4. 做多图 HSV v0.1/v0.2 A/B；只有稳定失败后再讨论学习模型。
5. 采集两批、约 50 张图，覆盖照明和污染变化。
6. 上传/核验 CubeIDE 固件，保留真实 PONG/STATUS 证据。

不要把“重写算法”排在真实设备、数据来源和评估协议之前。

## 可运行入口

模拟闭环：

```powershell
.venv\Scripts\python.exe -m demo.demo_pipeline --generate-sample --mode simulate
```

真实图片分析但不生成毫米动作：

```powershell
.venv\Scripts\python.exe -m demo.demo_pipeline --input <image> --mode analyze
```

命令是历史快照；执行前用当前 `README.md` 和 CLI help 核对。

## 分工与接口

| 角色 | 所有权 | 交付物 | 不应越界 |
|---|---|---|---|
| A 数据/模型 | `microcleaning/data_learning/` | 数据清单、标注、划分、baseline/模型评估 | 未建立独立评估就上复杂模型 |
| B 视觉/测量 | `microcleaning/vision/` | mask、面积、质心、标定和误差 | 把像素结果冒充毫米精度 |
| C 规划/控制 | `microcleaning/control_system/` | 路径、动作请求、仿真和安全约束 | 把仿真冒充实机闭环 |
| 共同评审 | `contracts.py`, `ports.py` | 稳定契约、版本和兼容测试 | 单人私改共享接口 |

## 证据边界

推荐始终用这句话自检：

> 当前证据究竟证明“代码能运行”“模块能连接”“设备真实响应”“闭环可重复”，还是“真实清洗有效”？

这五层不能省略，也不能互相代替。

