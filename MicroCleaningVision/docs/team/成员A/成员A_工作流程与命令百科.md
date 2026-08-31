# 成员 A 工作流程与命令百科

> 用途：成员 A 不需要每次询问“下一条命令是什么”。从图片进入项目，到交给 B，再到评价 B 的算法，全部按本文件执行。  
> 当前边界：本文件只记录仓库中已经存在、可以运行的命令；尚未实现的批量评价和模型训练不会伪装成现成功能。

## 目录

1. [先看懂 A 的完整工作流](#1-先看懂-a-的完整工作流)
2. [命令使用规则](#2-命令使用规则)
3. [每次开工的环境检查](#3-每次开工的环境检查)
4. [图片采集与目录准备](#4-图片采集与目录准备)
5. [质量检查](#5-质量检查)
6. [数据审计](#6-数据审计)
7. [半自动生成 metadata](#7-半自动生成-metadata)
8. [Labelme 人工标注](#8-labelme-人工标注)
9. [Labelme JSON 转人工 Mask](#9-labelme-json-转人工-mask)
10. [A 向 B 交接](#10-a-向-b-交接)
11. [运行 B 的视觉算法](#11-运行-b-的视觉算法)
12. [评价人工 Mask 与算法 Mask](#12-评价人工-mask-与算法-mask)
13. [查看结果与判断失败](#13-查看结果与判断失败)
14. [测试和 Git 命令](#14-测试和-git-命令)
15. [常见报错](#15-常见报错)
16. [单张图片完整复制版](#16-单张图片完整复制版)
17. [A 的完成标准](#17-a-的完成标准)

## 1. 先看懂 A 的完整工作流

A 不是只负责拍照。A 的工作是把原始图片变成可追溯、可标注、可评价的数据资产。

```text
手机 / USB显微镜 / 合法公共图片
                ↓
data/raw_images/<批次>/原图
                ↓
图片质量检查 → quality_report.json + quality_summary.csv
                ↓
数据审计 → data_audit.json
                ↓
metadata登记 → data/metadata.csv
                ↓
Labelme人工Polygon → data/annotations/labelme/<id>.json
                ↓
二值Mask转换 → data/annotations/masks/<id>.png
                ↓
交给B：原图 + metadata + 人工Mask + 质量说明
                ↓
B输出：算法Mask + summary + algorithm_version
                ↓
A评价：IoU + 面积误差 + 中心误差
                ↓
记录误检/漏检 → 决定补拍、重标、改简单算法或未来训练模型
```

以 `public_001` 为例，一套文件应当对应：

```text
data/raw_images/public/public_001.jpg
data/annotations/labelme/public_001.json
data/annotations/masks/public_001.png
output/demo/<本次run_id>/mask.png
output/data_learning/public_001_evaluation.json
```

## 2. 命令使用规则

### 2.1 所有命令从项目根目录运行

```powershell
cd "D:\大创\3d\MicroCleaningVision"
```

确认：

```powershell
Get-Location
```

应显示 `D:\大创\3d\MicroCleaningVision`。

### 2.2 本文中的占位符不能原样复制

例如：

```text
<图片ID>
<批次目录>
<你的设备型号>
```

它们表示需要替换的内容。`<图片ID>` 可以替换成 `public_001`。

### 2.3 优先明确调用项目 Python

统一使用：

```powershell
.\.venv\Scripts\python.exe
```

不要只写 `python` 或 `pip`，否则可能调用全局环境。

### 2.4 原图只复制，不自动删除

失焦、反光和过曝图也是失败证据。整理目录时优先 `Copy-Item` 或文件管理器复制，不要批量删除原图。

## 3. 每次开工的环境检查

### 3.1 检查虚拟环境

```powershell
Test-Path ".\.venv\Scripts\python.exe"
.\.venv\Scripts\python.exe --version
```

第一条应输出 `True`。

### 3.2 检查核心依赖

```powershell
.\.venv\Scripts\python.exe scripts\check_environment.py --profile perception
```

如果这里只支持 `mock` profile，则至少检查 OpenCV 和 NumPy：

```powershell
.\.venv\Scripts\python.exe -c "import cv2, numpy; print(cv2.__version__, numpy.__version__)"
```

### 3.3 检查 Labelme

```powershell
.\.venv\Scripts\python.exe -m pip show labelme
```

未安装时：

```powershell
.\.venv\Scripts\python.exe -m pip install "labelme>=7,<8"
```

Labelme 是独立桌面窗口。它可以从 Trae 终端启动，但不是在 Trae 编辑器中直接画标注。

## 4. 图片采集与目录准备

### 4.1 数据来源的证据等级

| 来源 | 当前用途 | 不能宣称什么 |
|---|---|---|
| USB 显微镜 | 项目主数据、未来真实证据 | 没记录设备/倍率时不能说条件可复现 |
| 手机 | 验证真实像素链路和光照变化 | 不能冒充显微镜数据 |
| 网上图片 | 辅助测试格式和算法失败 | 不能冒充团队采集或真实清洗证据 |

网上图片必须在 metadata 的 `remark` 中记录来源网址和许可证。文件夹名 `public` 本身不是来源证据。

### 4.2 为一次 USB 采集建立批次目录

```powershell
New-Item -ItemType Directory -Force `
  "data\raw_images\usb_20260829_b01" | Out-Null
```

建议文件名全项目唯一：

```text
MCV_20260829_B01_001.jpg
MCV_20260829_B01_002.jpg
```

不同批次不要都使用 `IMG_001.jpg`，否则 metadata 无法唯一对应。

### 4.3 查看已经进入项目的图片

```powershell
Get-ChildItem -File -Recurse "data\raw_images" |
  Where-Object { $_.Extension -match '^\.(jpg|jpeg|png)$' }
```

输入：手机、显微镜或公共图片。  
输出：`data/raw_images/<批次>/<图片>`。

## 5. 质量检查

### 5.1 运行全部原图检查

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.inspect_images `
  "data\raw_images" `
  --recursive `
  --output "output\data_learning\quality_report.json" `
  --csv-output "output\data_learning\quality_summary.csv"
```

输入：`data/raw_images/`。  
输出：

```text
output/data_learning/quality_report.json
output/data_learning/quality_summary.csv
```

主要字段：

| 字段 | 大白话解释 |
|---|---|
| `laplacian_variance` | 边缘变化强弱，只能粗略筛查失焦 |
| `mean_intensity` | 整张图平均明暗 |
| `dark_fraction` | 特别暗的像素比例 |
| `bright_fraction` | 特别亮的像素比例 |
| `focus` | 当前暂定清晰度分数 |
| `illumination` | 当前暂定光照分数 |
| `flags` | 建议人工复查的问题 |

质量 flag 不是科学结论。不要为了消除 flag 私自调低阈值，也不要自动删除失败图。

### 5.2 查看 CSV

在 Trae 文件区打开：

```text
output/data_learning/quality_summary.csv
```

也可以在终端查看前几行：

```powershell
Get-Content -Encoding UTF8 "output\data_learning\quality_summary.csv" -TotalCount 6
```

## 6. 数据审计

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.data_audit
```

输入：

```text
data/raw_images/
data/metadata.csv
```

输出：

```text
output/data_learning/data_audit.json
```

查看：

```powershell
Get-Content -Raw -Encoding UTF8 "output\data_learning\data_audit.json"
```

它回答：图片多少、能否解码、是否重复、哪些未登记、metadata 是否缺字段。它不会移动文件或猜污染类别。

## 7. 半自动生成 metadata

### 7.1 默认先预览

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.metadata_builder --dry-run
```

程序可以证明并填写：

- 文件名；
- 真实分辨率；
- 新图初始状态 `unlabeled`。

程序不能证明的字段保持 `unknown`：来源、设备、日期、倍率和污染类别。

### 7.2 确认后才写入

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.metadata_builder --apply
```

输出：`data/metadata.csv`。已有人工行不会被覆盖。

### 7.3 为一个事实明确的批次填入共同信息

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.metadata_builder `
  --raw-root "data\raw_images\usb_20260829_b01" `
  --source "usb_microscope" `
  --capture-date "2026-08-29" `
  --device "<你的设备型号>" `
  --magnification "unknown" `
  --apply
```

如果目录混合了手机、USB 和网上图片，不能用同一组参数填写整个目录。

### 7.4 写入后再次审计

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.data_audit
```

目标是让 `unregistered_raw_image_count` 变为 0，同时未知信息仍诚实保持 `unknown`。

## 8. Labelme 人工标注

### 8.1 建立输出目录

```powershell
New-Item -ItemType Directory -Force `
  "data\annotations\labelme", `
  "data\annotations\masks" | Out-Null
```

### 8.2 启动 Labelme

```powershell
& ".\.venv\Scripts\labelme.exe"
```

Labelme 窗口中：

1. `Open Dir` 选择原图批次目录；
2. 输出目录选择 `data/annotations/labelme/`；
3. 不规则污染使用 `Create Polygons` 沿边缘画多边形；近似圆形的小斑点也可以使用 `Create Circle`；
4. 标签只使用 `contamination`；
5. JSON 主文件名必须与原图一致；
6. 保存后关闭或标下一张。

当前协议不使用多类别，也不使用 `ignore` 标签。边界拿不准时不应假装精确，在 metadata `remark` 中写明争议。

输入：

```text
data/raw_images/<批次>/<图片ID>.jpg
```

输出：

```text
data/annotations/labelme/<图片ID>.json
```

检查：

```powershell
Test-Path "data\annotations\labelme\public_001.json"
```

## 9. Labelme JSON 自动批量转人工 Mask

### 9.1 日常只运行这一条命令

标注完成后，不需要逐张填写原图、JSON 和 Mask 文件名。Labelme JSON 已经保存 `imagePath`，程序会自动找到对应原图：

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.annotation_tools --batch
```

默认输入：`data/annotations/labelme/` 中的全部 JSON。  
自动寻找：每份 JSON 的 `imagePath`，但只允许指向 `data/raw_images/` 内部。  
默认输出：`data/annotations/masks/<同名图片ID>.png`。  
转换报告：`output/data_learning/annotation_conversion_report.json`。

查看批量结果：

```powershell
$report = Get-Content -Raw -Encoding UTF8 `
  "output\data_learning\annotation_conversion_report.json" | ConvertFrom-Json

$report | Select-Object total_count, converted_count, failed_count
$report.items | Where-Object status -eq "failed" | Format-List
```

只有 `failed_count = 0` 才表示所有 JSON 都完成格式转换。

### 9.2 输出含义

```text
白色255 = 人工认为是污染
黑色0   = 背景
```

程序支持 `Polygon` 和 `Circle`，拒绝错误标签、其他形状、真正越界的坐标、非法 JSON、数据集外路径和尺寸不一致。一份 JSON 失败不会阻止其他标注转换，具体原因写入报告。

### 9.3 单张模式只用于排错

如果报告中只有某一张失败，可以单独运行旧命令查看详细错误：

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.annotation_tools `
  "data\raw_images\public\public_001.jpg" `
  "data\annotations\labelme\public_001.json" `
  "data\annotations\masks\public_001.png"
```

### 9.4 转换成功不等于标注验收

批量程序只负责把你画的形状变成像素，不知道边界是否画对。转换后仍需抽查或打开 Mask 肉眼检查；确认贴合后，才把 metadata 中该图的 `annotation_status` 改成 `labeled`。

## 10. A 向 B 交接

A 不通过聊天重新发送一套图片；A/B 在同一仓库按路径交接。

### 10.1 A 必须交给 B 的五项内容

| 内容 | 示例路径 | 用途 |
|---|---|---|
| 原图 | `data/raw_images/public/public_001.jpg` | B 的算法输入 |
| metadata | `data/metadata.csv` | 来源和采集条件 |
| 质量报告 | `output/data_learning/quality_report.json` | 判断输入质量 |
| 人工 JSON | `data/annotations/labelme/public_001.json` | 标注来源追溯 |
| 人工 Mask | `data/annotations/masks/public_001.png` | 独立评价参照 |

### 10.2 交接前检查

```text
[ ] 原图可以解码
[ ] metadata已有该图片
[ ] JSON、Mask和原图主文件名一致
[ ] Mask尺寸与原图一致
[ ] Mask已由A肉眼复核
[ ] 质量flag和不确定内容已经说明
```

人工 Mask 不能作为 B 推理时的输入。B 只能在开发阶段用它分析误差，留出图片的 Mask 只能在参数冻结后评价。

## 11. 运行 B 的视觉算法

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --input "data\raw_images\public\public_001.jpg" `
  --mode analyze
```

输入：原图。  
输出目录：

```text
output/demo/<run_id>/
├── input.png
├── mask.png
├── contamination_overlay.png
├── path_overlay.png
├── summary.json
└── episode_<id>.json
```

其中 `mask.png` 是 B 的算法 Mask，`summary.json` 记录面积、中心、算法版本和证据边界。`analyze` 模式不会执行真实硬件。

找到最新一次目录：

```powershell
$demoDir = Get-ChildItem "output\demo" -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

$demoDir.FullName
```

## 12. 评价人工 Mask 与算法 Mask

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.mask_evaluation `
  "data\annotations\masks\public_001.png" `
  (Join-Path $demoDir.FullName "mask.png") `
  --output "output\data_learning\public_001_evaluation.json"
```

输入：人工 Mask + B 的算法 Mask。  
输出：

```text
output/data_learning/public_001_evaluation.json
```

主要指标：

| 指标 | 含义 |
|---|---|
| `iou` | 两块区域交集除以并集，越接近1越重合 |
| `area_error_px` | 算法面积减人工面积；正数表示圈大了 |
| `centroid_error_px` | 两个区域中心相差多少像素 |
| `intersection_px` | 人工和算法重合像素 |
| `union_px` | 两个区域合起来的像素 |

没有像素到毫米标定前，只能报告像素误差。

## 13. 查看结果与判断失败

```powershell
Get-Content -Raw -Encoding UTF8 `
  "output\data_learning\public_001_evaluation.json"
```

A 需要同时查看：原图、人工 Mask、算法 Mask、叠加图和评价 JSON。

| 失败类型 | 大白话解释 |
|---|---|
| 误检（False Positive） | 把正常结构当成污染 |
| 漏检（False Negative） | 污染存在但没有圈出来 |
| 边界错误 | 找到目标但边缘不准 |
| 中心错误 | 目标位置被其他区域拉偏 |
| 碎裂 | 一个目标被切成很多块 |
| 粘连 | 多个目标被合并成一块 |

第一轮至少准备 5 张人工 Mask：3 张开发图供 B 分析，2 张留出图在参数冻结后评价。不能在同一张图上反复调参后再用它宣布算法优秀。

## 14. 测试和 Git 命令

### 14.1 只运行 A 的测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover `
  -s test\data_learning `
  -p "test*.py" `
  -v
```

### 14.2 运行全项目回归

```powershell
.\.venv\Scripts\python.exe -m unittest discover `
  -s test `
  -p "test*.py" `
  -v
```

测试通过证明软件回归正常，不证明真实显微、清洗或硬件成功。

### 14.3 查看自己改了什么

```powershell
git status --short
git diff --name-only
git diff --check
```

### 14.4 A 不应出现的修改路径

```text
microcleaning/vision/
microcleaning/control_system/
test/vision/
test/control_system/
microcleaning/contracts.py
microcleaning/ports.py
```

### 14.5 AI 开工模板

```text
我是成员A。本次任务只允许修改 microcleaning/data_learning、test/data_learning、data和A直接相关文档。
输入是：<写真实文件路径>。
预期输出是：<写文件或指标>。
禁止修改B/C和共享接口。先解释方案、文件清单、失败路径和测试，等我批准后再改。
完成后运行A测试和全部回归，并区分合成测试、真实像素和硬件证据。
```

## 15. 常见报错

### `No module named cv2`

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements\perception-opencv.txt
```

### `labelme.exe` 不存在

```powershell
.\.venv\Scripts\python.exe -m pip install "labelme>=7,<8"
.\.venv\Scripts\python.exe -m pip show labelme
```

### `图片目录不存在`

先确认当前目录：

```powershell
Get-Location
Test-Path "data\raw_images"
```

### `Labelme JSON 尺寸与原图不一致`

通常表示 JSON 对应了另一张图片、图片被缩放或重命名后配错。不要修改 JSON 数字强行通过；重新打开正确原图标注。

### metadata 出现 `同名文件`

不同批次存在相同文件名。为新文件增加批次前缀，并在 remark 记录原文件名；不要让程序猜对应关系。

### 算法 Mask 全黑

这可能是算法没有找到符合 HSV 条件的区域，不代表程序没运行。打开 `contamination_overlay.png` 和 `summary.json`，作为漏检记录。

### 算法 Mask 出现很多横线或背景

这是误检。不要修改人工 Mask 去迎合算法；保存结果并交给 B 分析颜色、形状和连通区域。

## 16. 单张图片完整复制版

下面以 `public_001` 为例。执行前确认 Labelme JSON 已由人工保存。

```powershell
cd "D:\大创\3d\MicroCleaningVision"

.\.venv\Scripts\python.exe -m microcleaning.data_learning.inspect_images `
  "data\raw_images" --recursive `
  --output "output\data_learning\quality_report.json" `
  --csv-output "output\data_learning\quality_summary.csv"

.\.venv\Scripts\python.exe -m microcleaning.data_learning.data_audit

.\.venv\Scripts\python.exe -m microcleaning.data_learning.metadata_builder --dry-run

  .\.venv\Scripts\python.exe -m microcleaning.data_learning.annotation_tools --batch

.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --input "data\raw_images\public\public_001.jpg" `
  --mode analyze

$demoDir = Get-ChildItem "output\demo" -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

.\.venv\Scripts\python.exe -m microcleaning.data_learning.mask_evaluation `
  "data\annotations\masks\public_001.png" `
  (Join-Path $demoDir.FullName "mask.png") `
  --output "output\data_learning\public_001_evaluation.json"

Get-Content -Raw -Encoding UTF8 `
  "output\data_learning\public_001_evaluation.json"
```

注意：这里没有自动执行 metadata `--apply`，因为写入前仍需要 A 确认未知字段和批次范围。

## 17. A 的完成标准

一张图片只有同时具备以下内容，才算完成 A→B→A 的数据闭环：

```text
[ ] 原图
[ ] metadata记录
[ ] 质量报告
[ ] Labelme JSON
[ ] 人工二值Mask
[ ] 人工肉眼复核
[ ] B的算法Mask
[ ] B的algorithm_version
[ ] IoU、面积误差、中心误差
[ ] 误检/漏检等失败解释
```

A 和 B 的关系可以记成一句话：

> A 制作可信的题目和人工参照答案，B 在不偷看答案的情况下解题，A 再独立判卷。
