# 成员 B 工作流程与命令百科

> 成员 B 的目标：把 A 提供的原图变成可检查的算法 Mask、污染面积和污染中心，并让 A 能量化评价、让 C 能继续规划。

## 目录

1. [B 在整条链中的位置](#1-b-在整条链中的位置)
2. [B 的总体输入和输出](#2-b-的总体输入和输出)
3. [B 负责的代码文件](#3-b-负责的代码文件)
4. [开工前检查](#4-开工前检查)
5. [接收 A 数据](#5-接收-a-数据)
6. [运行单张真实图片](#6-运行单张真实图片)
7. [找到并查看输出](#7-找到并查看输出)
8. [怎样使用人工 Mask](#8-怎样使用人工-mask)
9. [修改算法的正确循环](#9-修改算法的正确循环)
10. [把结果交给 C](#10-把结果交给-c)
11. [测试与 Git](#11-测试与-git)
12. [常见失败](#12-常见失败)
13. [完整复制命令](#13-完整复制命令)
14. [B 的完成标准](#14-b-的完成标准)

## 1. B 在整条链中的位置

```text
A提供原图
↓
B读取像素
↓
HSV基线筛选候选颜色
↓
去除小噪点并形成算法Mask
↓
计算污染面积、中心和连通块数
↓
A用人工Mask评价
↓
B依据多张开发图修改算法
↓
冻结算法版本
↓
A在留出图上评价
↓
C读取冻结版本的算法Mask规划路线
```

B 不是“把人工 Mask 再复制一遍”。算法推理只能读原图，人工 Mask 只能用于开发后的比较。

## 2. B 的总体输入和输出

### 输入

| 输入 | 路径示例 | 用途 |
|---|---|---|
| 原图 | `data/raw_images/public/public_001.jpg` | 唯一的视觉推理输入 |
| metadata | `data/metadata.csv` | 知道来源、设备、倍率和批次 |
| 质量报告 | `output/data_learning/quality_report.json` | 算法失败时判断是否与模糊、过曝有关 |
| 人工Mask | `data/annotations/masks/public_001.png` | 只用于评价，不得在推理中读取 |

### 输出给 A

| 输出 | 当前位置 | 含义 |
|---|---|---|
| 算法Mask | `output/demo/<run_id>/mask.png` | B认为是污染的白色区域 |
| 叠加图 | `contamination_overlay.png` | 在原图上检查误检、漏检和中心 |
| 测量字段 | `summary.json` 的 `contamination` | 面积、中心、置信信息、算法版本 |

### 输出给 C

```text
算法Mask
area_px
centroid_px
uncertainty_px
component_count
algorithm_version
coordinate_frame = image_px
```

B 不输出毫米坐标、喷射时长、COM口或电机指令。

## 3. B 负责的代码文件

```text
microcleaning/vision/
├── hsv_baseline.py       # HSV分割、去噪、面积和中心
├── contamination.py      # 污染测量数据结构
├── state_estimator.py    # 测量变成状态
└── verification.py       # 动作前后视觉结果比较

test/vision/
└── test_vision.py
```

当前算法版本位于 `hsv_baseline.py`：

```python
HSV_BASELINE_VERSION = "hsv-red-baseline-v0.1"
```

算法行为发生变化时必须升级版本，不能覆盖结果后仍写旧版本。

## 4. 开工前检查

所有命令从项目根目录运行：

```powershell
cd "D:\大创\3d\MicroCleaningVision"
Test-Path ".\.venv\Scripts\python.exe"
.\.venv\Scripts\python.exe --version
```

检查视觉依赖：

```powershell
.\.venv\Scripts\python.exe -c "import cv2, numpy; print('OpenCV', cv2.__version__); print('NumPy', numpy.__version__)"
```

## 5. 接收 A 数据

B 开始真实图片分析前检查：

```text
[ ] 原图存在并能打开
[ ] metadata有同名记录
[ ] A说明图片来源和采集方式；不知道的字段诚实写unknown
[ ] 有质量flag时先看原图，不直接删除
[ ] 开发图和留出图已经分开
[ ] 人工Mask不会被传入B的推理函数
```

快速检查文件：

```powershell
Test-Path "data\raw_images\public\public_001.jpg"
Test-Path "data\annotations\masks\public_001.png"
```

第二个文件不存在不妨碍 B 推理，但会妨碍 A 做定量评价。

## 6. 运行单张真实图片

当前官方入口是集成 Demo：

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --input "data\raw_images\public\public_001.jpg" `
  --mode analyze
```

这个命令内部执行：

```text
图片质量计算
→ B的HSV分割
→ B的面积/中心测量
→ C的像素路线预览
→ 保存summary和Episode
```

其中 B 对自己的三项输出负责：`mask.png`、`contamination_overlay.png`、`summary.json` 中的 `contamination`。`path_overlay.png` 属于 C 的结果。

`analyze` 模式不会生成真实硬件动作。

## 7. 找到并查看输出

找到最新运行目录：

```powershell
$demoDir = Get-ChildItem "output\demo" -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

$demoDir.FullName
Get-ChildItem $demoDir.FullName
```

查看测量结果：

```powershell
$summary = Get-Content -Raw -Encoding UTF8 `
  (Join-Path $demoDir.FullName "summary.json") | ConvertFrom-Json

$summary.contamination | Format-List
```

B 必须同时打开：

```text
input.png                    输入副本
mask.png                     算法黑白Mask
contamination_overlay.png    Mask和中心叠加图
summary.json                 数值与算法版本
```

不能只看面积数字，因为错误位置也可能得到“看起来合理”的面积。

## 8. 怎样使用人工 Mask

A 负责运行评价命令，但 B 必须会读结果：

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.mask_evaluation `
  "data\annotations\masks\public_001.png" `
  (Join-Path $demoDir.FullName "mask.png") `
  --output "output\data_learning\public_001_evaluation.json"
```

主要指标：

- `iou`：区域空间重合程度；
- `area_error_px`：算法多算或少算多少像素；
- `centroid_error_px`：中心相差多少像素。

B 收到指标后，还必须看图并将失败分成：误检、漏检、边界偏移、碎裂、粘连、中心偏移。单个 IoU 不能告诉你错误为什么发生。

## 9. 修改算法的正确循环

```text
选择3张开发图
→ 运行v0.1
→ A评价并标记主要失败
→ B只修改一个明确规则
→ 重新运行3张开发图
→ 保存改前/改后结果
→ 冻结参数为v0.2候选
→ A在至少2张留出图上评价
→ 留出结果改善才接受
```

当前参数集中在 `HSVSegmentationPolicy`：颜色范围、最小饱和度、最低亮度、最小连通块面积和形态学核。不要在多个函数里散落数字。

每次只解决一个可复现问题，例如：

```text
问题：铜色横线被误检
候选改动：增加连通区域形状过滤
主要指标：留出图IoU和假阳性面积
删除条件：留出图没有改善或漏检明显增加
```

不要在一张图片上反复调到完美，再拿同一张图证明算法优秀。

## 10. 把结果交给 C

B 交付的最小文件组：

```text
output/demo/<run_id>/mask.png
output/demo/<run_id>/contamination_overlay.png
output/demo/<run_id>/summary.json
```

交接说明至少写：

```text
输入图片：
算法版本：
mask路径：
面积与中心：
坐标单位：image_px
已知误检/漏检：
是否为开发图或留出图：
```

只有通过留出评价的算法版本才应作为当前推荐版本。C 可以用失败 Mask 测试鲁棒性，但不能把失败结果写成“可靠定位”。

## 11. 测试与 Git

只运行 B 测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover `
  -s test\vision `
  -p "test*.py" `
  -v
```

运行完整回归：

```powershell
.\.venv\Scripts\python.exe -m unittest discover `
  -s test `
  -p "test*.py" `
  -v
```

确认没有修改 A/C 文件：

```powershell
git diff --name-only
git diff -- microcleaning/vision test/vision
```

分支示例：

```powershell
git switch -c feat/b-hsv-v0-2
git add microcleaning/vision test/vision
git commit -m "feat(vision): 增加HSV基线候选规则"
```

## 12. 常见失败

| 现象 | 先检查 | 处理方向 |
|---|---|---|
| Mask全黑 | 原图颜色、过暗比例、HSV范围 | 记录漏检，不要伪造中心 |
| 背景大面积变白 | 反光、背景颜色、饱和度阈值 | 保存误检图，检查颜色和形状 |
| 污染被切碎 | 形态学核、最小面积 | 小步修改，检查是否误合并 |
| 中心落在背景 | 多个区域被平均 | 明确多目标策略，不静默平均 |
| 开发图很好、留出图很差 | 过拟合 | 回退参数，增加不同批次数据 |
| A人工Mask有争议 | 原图边界模糊 | 让A复核，不修改算法迎合错误答案 |

## 13. 完整复制命令

```powershell
cd "D:\大创\3d\MicroCleaningVision"

.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --input "data\raw_images\public\public_001.jpg" `
  --mode analyze

$demoDir = Get-ChildItem "output\demo" -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

$summary = Get-Content -Raw -Encoding UTF8 `
  (Join-Path $demoDir.FullName "summary.json") | ConvertFrom-Json

$summary.contamination | Format-List

.\.venv\Scripts\python.exe -m microcleaning.data_learning.mask_evaluation `
  "data\annotations\masks\public_001.png" `
  (Join-Path $demoDir.FullName "mask.png") `
  --output "output\data_learning\public_001_evaluation.json"
```

## 14. B 的完成标准

```text
[ ] 原图没有读取人工Mask参与推理
[ ] 输出算法Mask
[ ] 输出面积、中心、连通块数和算法版本
[ ] 保存叠加图并肉眼检查
[ ] 开发图与留出图分开
[ ] A可以复现评价结果
[ ] C知道mask路径和坐标单位
[ ] B测试和完整回归通过
[ ] 没有声称像素结果已经是毫米定位
```

