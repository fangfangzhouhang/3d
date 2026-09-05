# 成员 B 工作流程与命令百科

> 成员 B 的目标：把 A 提供的原图变成可检查的算法 Mask、污染面积和污染中心，并让 A 能量化评价、让 C 能继续规划。

## 0. 当前该做什么（2026-09-05）

根目录 `p1_baseline.py` 已迁入 `microcleaning/vision/otsu_baseline.py`。拉取后先生成本机 `output/`（该目录不进 Git），再按失败类型改**一个**参数。

```powershell
cd "D:\大创\3d\MicroCleaningVision"
git pull
.\.venv\Scripts\python.exe -m microcleaning.vision.run_baseline --algorithm otsu --input-dir "data\raw_images\public"
.\.venv\Scripts\python.exe -m microcleaning.vision.run_baseline --algorithm hsv --input-dir "data\raw_images\public"
.\.venv\Scripts\python.exe scripts\evaluate_vision_baselines.py
```

打开：

```text
output/data_learning/evaluations/comparison_summary.json
output/vision/<algorithm>_<图名>_<时间>/contamination_overlay.png
```

| 该朝哪努力 | 不要做什么 |
|---|---|
| HSV：很多 public 图整张涂白，先收紧饱和度/颜色范围，或加形状过滤打掉细长横线 | 不要删掉 HSV 只留 Otsu |
| Otsu：中心常对，`public_001` 画成空心圈、面积偏大 | 不要在一张图上调到完美再当证明 |
| 一次只改 `HSVSegmentationPolicy` 或 `OtsuSegmentationPolicy` 里的一个字段，并升级版本号 | 不要改 `contracts.py`、不要输出毫米、不要让 C 改接口 |

开发 / 留出（A 复核完成前只是候选）：

```text
开发图：public_001（唯一 labeled）、public_003、public_008
留出图：public_002、public_011
先放下：M9、M12
```

只有 `public_001` 可写进正式结论。其余 Mask 未人工验收，只用于看图和失败分类。

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
| 算法Mask | `output/vision/<run_id>/mask.png` | B认为是污染的白色区域 |
| 叠加图 | `output/vision/<run_id>/contamination_overlay.png` | 在原图上检查误检、漏检和中心 |
| 测量字段 | `summary.json` 的 `contamination` | 面积、中心、置信信息、算法版本 |
| 评价汇总 | `output/data_learning/evaluations/comparison_summary.json` | A的IoU/面积/中心对照 |

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
├── hsv_baseline.py       # 官方HSV基线：高饱和红色标记物
├── otsu_baseline.py      # Otsu/自适应阈值候选：假设污渍比背景暗
├── run_baseline.py       # B自己跑算法Mask的入口
├── contamination.py      # 污染测量数据结构
├── state_estimator.py    # 测量变成状态
└── verification.py       # 动作前后视觉结果比较

test/vision/
└── test_vision.py
```

当前有两套可比较的算法，版本号分别在：

```python
HSV_BASELINE_VERSION = "hsv-red-baseline-v0.1"      # hsv_baseline.py
OTSU_BASELINE_VERSION = "otsu-v-baseline-v0.1"      # otsu_baseline.py
```

- 官方 Demo 链（给 C 看路线）仍默认 HSV。
- B 对照人工 Mask、做 A/B 和调参时，用 `run_baseline.py`。默认跑 Otsu 候选。
- 算法行为发生变化时必须升级版本，不能覆盖结果后仍写旧版本。
- 根目录旧文件 `p1_baseline.py` 已迁走；不要再从那里运行，也不要把中文 bbox 字典交给 C。

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

B 要得到**自己的算法 Mask**时，用视觉入口，不要再用根目录脚本：

```powershell
.\.venv\Scripts\python.exe -m microcleaning.vision.run_baseline `
  --algorithm otsu `
  --input "data\raw_images\public\public_001.jpg"
```

对照官方 HSV：

```powershell
.\.venv\Scripts\python.exe -m microcleaning.vision.run_baseline `
  --algorithm hsv `
  --input "data\raw_images\public\public_001.jpg"
```

整目录批量：

```powershell
.\.venv\Scripts\python.exe -m microcleaning.vision.run_baseline `
  --algorithm otsu `
  --input-dir "data\raw_images\public"
```

这些命令只做 B 的事：

```text
读取原图
→ 指定算法分割
→ 写出 input.png / mask.png / contamination_overlay.png / summary.json
```

输出在 `output/vision/<algorithm>_<图片名>_<时间>/`。坐标单位是 `image_px`，不是毫米。

需要连上 C 的路线预览时，才用集成 Demo（默认仍是 HSV）：

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --input "data\raw_images\public\public_001.jpg" `
  --mode analyze
```

`analyze` 模式不会生成真实硬件动作。`path_overlay.png` 属于 C 的结果。

## 7. 找到并查看输出

找到最新一次 B 基线输出：

```powershell
$visionDir = Get-ChildItem "output\vision" -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

$visionDir.FullName
Get-ChildItem $visionDir.FullName
```

查看测量结果：

```powershell
$summary = Get-Content -Raw -Encoding UTF8 `
  (Join-Path $visionDir.FullName "summary.json") | ConvertFrom-Json

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
  (Join-Path $visionDir.FullName "mask.png") `
  --output "output\data_learning\public_001_otsu_evaluation.json"
```

主要指标：

- `iou`：区域空间重合程度；
- `area_error_px`：算法多算或少算多少像素；
- `centroid_error_px`：中心相差多少像素。

B 收到指标后，还必须看图并将失败分成：误检、漏检、边界偏移、碎裂、粘连、中心偏移。单个 IoU 不能告诉你错误为什么发生。

## 9. 修改算法的正确循环

先用同一张开发图比较两套已有基线，再决定改哪一套：

```text
同一张开发图
→ 跑 otsu 得到 mask
→ 跑 hsv 得到 mask
→ 分别和人工 Mask 算 IoU / 面积误差 / 中心误差
→ 看叠加图，把失败分成误检、漏检、边界偏移、碎裂、粘连、中心偏移
→ 只改一个明确规则
→ 重新跑至少3张开发图
→ 保存改前/改后结果和版本号
→ 至少2张留出图确认改善后才接受
```

当前 v0.1 对照（看叠加图后再改代码）：

| 失败 | 先改哪套 | 建议动的参数 |
|---|---|---|
| HSV 整图变白 | `hsv_baseline.py` | `saturation_min` 提高，或收窄 hue |
| HSV 把铜色横线当污染 | `hsv_baseline.py` | 连通域长宽比/细长度过滤；升到 v0.2 |
| Otsu 空心圈、面积偏大 | `otsu_baseline.py` | `max_area_ratio`、形态学核；升到 v0.2 |
| 大图 M9/M12 全失败 | 先记录，不作为第一刀 | 等 A 说明这两张是否同一成像条件 |

改完必须升级版本常量，例如 `hsv-red-baseline-v0.2` 或 `otsu-v-baseline-v0.2`。

Demo 默认 HSV 在切换前保持不变。只有留出图对照证明候选更好，才讨论是否让 Demo 改用 Otsu。

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
output/vision/<run_id>/mask.png
output/vision/<run_id>/contamination_overlay.png
output/vision/<run_id>/summary.json
```

给 C 做路线预览时，仍可另跑 Demo，使用 `output/demo/<run_id>/` 里的同一组文件。

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
git pull
git switch -c feat/b-otsu-v0-2
git add microcleaning/vision test/vision
git commit -m "feat(vision): 收紧Otsu最大面积过滤"
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

拉取并在本机重建对照：

```powershell
cd "D:\大创\3d\MicroCleaningVision"
git pull

.\.venv\Scripts\python.exe -m microcleaning.vision.run_baseline `
  --algorithm otsu --input-dir "data\raw_images\public"
.\.venv\Scripts\python.exe -m microcleaning.vision.run_baseline `
  --algorithm hsv --input-dir "data\raw_images\public"
.\.venv\Scripts\python.exe scripts\evaluate_vision_baselines.py
```

改完算法后，用开发图复跑并再评价：

```powershell
.\.venv\Scripts\python.exe -m microcleaning.vision.run_baseline `
  --algorithm otsu --input "data\raw_images\public\public_001.jpg"
.\.venv\Scripts\python.exe -m microcleaning.vision.run_baseline `
  --algorithm otsu --input "data\raw_images\public\public_003.jpg"
.\.venv\Scripts\python.exe -m microcleaning.vision.run_baseline `
  --algorithm otsu --input "data\raw_images\public\public_008.jpg"

.\.venv\Scripts\python.exe scripts\evaluate_vision_baselines.py

.\.venv\Scripts\python.exe -m unittest discover -s test\vision -p "test*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s test -p "test*.py" -v
```

## 14. B 的完成标准

```text
[ ] 原图没有读取人工Mask参与推理
[ ] 输出算法Mask
[ ] 输出面积、中心、连通块数和算法版本
[ ] 保存叠加图并肉眼检查
[ ] 开发图与留出图分开；未labeled的图不写入正式结论
[ ] 改参后升级版本号并复跑 scripts/evaluate_vision_baselines.py
[ ] A可以复现评价结果
[ ] C知道mask路径和坐标单位
[ ] B测试和完整回归通过
[ ] 没有声称像素结果已经是毫米定位
```

