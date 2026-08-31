# U500 USB 数码显微镜接入指南

> 当前事实：设备是 U500 USB 数码显微镜，不是工业相机。尚未在本仓库主机上成功执行实机 probe，因此状态仍是 `integration_not_verified`，不能写“U500 已接入完成”。

## 1. 它接入系统的哪一层

```text
U500 USB数码显微镜 / 普通USB视频设备
↓ OpenCV VideoCapture
USBCamera
↓ 保存原始PNG并计算质量
Observation
↓
B视觉识别（不知道相机型号）
↓
C像素路径（不知道相机型号）
```

能够拍照只增加感知证据，不会授予泵、电机、平台或 STM32 执行权限。

代码中的 `CameraPort` 是“统一图像输入接口”，不是“工业相机接口”。当前不购买、不假设存在工业相机；只有 U500 在真实实验中出现稳定、可复现且阻塞任务的问题时，才评估替换设备。

## 2. 输入和输出

### 输入

- `device_index`：Windows 给视频设备分配的编号，通常从 0 开始，但必须探测；
- `output_root`：原始帧保存目录；
- 可选宽度、高度、预热帧数和 OpenCV backend；
- `task_id` 和 `phase`（如动作前 `pre`、动作后 `post`）。

### 输出

```text
data/raw_images/usb_probe/<task_id>/<phase>_<时间>_<随机ID>.png
+ 标准 Observation
+ ImageInspection（分辨率、SHA256、质量原始指标和暂定分数）
```

## 3. 准备环境

所有命令从项目根目录运行：

```powershell
cd "D:\大创\3d\MicroCleaningVision"
.\.venv\Scripts\python.exe scripts\check_environment.py --profile perception
```

如果缺少 OpenCV：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements\perception-opencv.txt
```

## 4. 第一次只做设备探测

先关闭可能占用相机的软件，例如系统相机、会议软件或厂商预览工具，然后运行：

```powershell
.\.venv\Scripts\python.exe scripts\probe_usb_camera.py
```

默认尝试 `device_index=0～5`，每个结果报告：

```text
device_index
opened
frame_read
width_px
height_px
fps_reported
error
```

只有同时满足：

```text
opened = true
frame_read = true
```

才说明 OpenCV 从该编号读到了一帧。它仍不代表倍率、视野或像素—毫米标定完成。

如果电脑视频设备很多，可扩大范围：

```powershell
.\.venv\Scripts\python.exe scripts\probe_usb_camera.py --max-index 10
```

## 5. 保存一张测试帧并进入质量流程

```powershell
.\.venv\Scripts\python.exe scripts\probe_usb_camera.py --capture-test
```

程序会选择第一个可读设备，重新打开、丢弃 5 张预热帧、保存一张无损 PNG，并输出：

- 图片路径；
- 分辨率；
- SHA256；
- focus/illumination/confidence；
- quality flags。

指定预热数量和输出目录：

```powershell
.\.venv\Scripts\python.exe scripts\probe_usb_camera.py `
  --capture-test `
  --warmup-frames 10 `
  --output-root "data\raw_images\u500_probe"
```

## 6. 把测试帧交给 B/C 软件链

找到最新测试图：

```powershell
$frame = Get-ChildItem "data\raw_images\u500_probe" -Recurse -File -Filter *.png |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

$frame.FullName
```

进入当前 Demo：

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --input $frame.FullName `
  --mode analyze
```

B 只看到一张标准图片；不需要在 `vision/` 中写 `if camera == U500`。

## 7. 可选 backend 排错

第一轮不要指定 backend，让 OpenCV 选择默认方式。如果默认方式打不开，可先查看 Windows DirectShow 常量：

```powershell
.\.venv\Scripts\python.exe -c "import cv2; print(cv2.CAP_DSHOW)"
```

再把输出整数作为候选，例如：

```powershell
.\.venv\Scripts\python.exe scripts\probe_usb_camera.py --backend 700
```

`700` 只是常见 OpenCV 常量值示例，必须以上一条命令在本机实际输出为准；它不是 U500 专用 API。

## 8. 四类明确错误

| 错误码 | 大白话解释 | 优先检查 |
|---|---|---|
| `CAMERA_OPEN_FAILED` | 指定编号打不开 | 编号、USB连接、隐私权限、是否被其他软件占用 |
| `FRAME_READ_FAILED` | 设备打开但读帧失败 | 驱动、带宽、backend、分辨率设置 |
| `EMPTY_FRAME` | 驱动返回“成功”但帧为空 | 驱动异常、设备重插、换USB口 |
| `OUTPUT_WRITE_FAILED` | 已抓到帧但保存失败 | 目录权限、磁盘空间、路径 |

程序不会用合成图片替换失败帧，也不会静默写“接入成功”。

## 9. 什么时候可以更新为实机已验证

必须保存以下证据：

```text
[ ] probe结果中至少一个index opened=true且frame_read=true
[ ] --capture-test退出码为0
[ ] 原始PNG真实存在并能打开
[ ] 输出分辨率和SHA256
[ ] 图像质量流程成功产生Observation
[ ] metadata记录U500、USB、采集日期和批次
[ ] 明确仍未完成像素—毫米标定
```

做到这些后，只能把 `physical_capture_verified` 更新为 `true`；仍不能把整体硬件闭环从 E0 直接升级。

## 10. OpenCV 打不开时的备用路线

如果 U500 只能在厂商软件中预览或拍照，不要阻塞整个项目：

```text
U500厂商软件保存PNG/JPG
→ 复制到 data/raw_images/<采集批次>/
→ A登记metadata和质量
→ B/C继续使用普通图片文件
```

这条路线不能宣称“实时相机接入”，但完全可以支撑第一版真实图片 Demo。拍摄时仍要固定支架高度、放大旋钮、LED亮度、样品方向和批次编号。
