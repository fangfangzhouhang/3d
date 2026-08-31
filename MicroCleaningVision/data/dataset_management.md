# MicroCleaningVision 数据集管理说明（v0.2）

> 负责人：成员 A（数据与模型）
> 本文件是 `data/` 目录的权威说明。先保留事实，再谈模型。

## 1. 这套数据到底用来做什么

成员 A 不是“给 B 找几张图”，而是在制作一把可靠的尺子：

```text
原始图片
  ↓ 质量检查：像素是否能读、是否明显失焦或过暗
可追溯图片 + metadata
  ↓ 成员 A 亲手画污染边界
人工 Ground Truth Mask（参照答案）
  ↓ 与 B 的 Predicted Mask 比较
IoU / 面积误差 / 中心误差
  ↓
判断问题来自数据、成像还是算法
```

`Ground Truth Mask` 指人工确认的参照区域，不等于绝对真理；边界模糊时应保留争议，不能让算法自己生成“人工答案”。

当前目标不是训练 YOLO，而是先让 OpenCV 基线有可检查的真实输入和可量化的参照答案。

## 2. 截至 2026-08-28 的真实状态

- `data/raw_images/` 下共有 **13 张 JPG**。
- 13 张都能被 OpenCV 解码，未发现损坏图片。
- 按 SHA-256 检查，未发现内容完全相同的重复组。
- `data/metadata.csv` 已有 **13 条记录**；来源、设备、日期和倍率仍待核实，暂时保持 `unknown`。
- 当前有 **13 份 Labelme JSON 文件**，批量工具已根据每份 JSON 的 `imagePath` 自动生成 **13 张二值 Mask**。
- `metadata.csv` 中目前只有 `public_001` 标记为 `labeled`，并且只有它完成了与 B 算法 Mask 的定量评价。其余 Mask 必须逐份人工复核和肉眼检查后，才能更新状态；不能因为 Mask 文件生成成功就宣布标注已验收。
- 12 张触发当前 Gate 1 暂定质量 flag，只有 1 张未触发。
- 质量 flag 只是“建议人再看一眼”，不是科学上的合格/不合格结论；当前阈值尚未用 USB 显微镜实验标定。
- `public/` 只是文件夹名，不足以证明图片真实来源。设备、倍率、日期、来源不知道就填 `unknown`。

机器报告位于：

- `output/data_learning/quality_report.json`：每张图的完整质量指标。
- `output/data_learning/quality_summary.csv`：适合成员直接在 Excel 中筛选。
- `output/data_learning/data_audit.json`：登记、重复、损坏和 metadata 审计。

## 3. 当前目录及职责

```text
data/
├── raw_images/                 # 原图：只读保留，不自动删除或有损转换
│   └── public/                 # 当前13张来源待人工核实的图片
├── dataset/                    # 旧版预留的人工分类目录；本轮不自动搬运
│   ├── normal/
│   ├── particle-like/
│   ├── film-like/
│   ├── fiber-like/
│   ├── spot-like/
│   ├── scratch-like/
│   └── unknown/
├── annotations/
│   ├── README.md               # Labelme五分钟说明
│   ├── labelme/                # 成员A人工绘制的Polygon JSON
│   └── masks/                  # JSON转出的二值Mask
└── metadata.csv                # 来源、设备、日期等人工登记清单
```

`data/dataset/` 中的分类词是旧版暂定的形态描述，并未经过科学验证。本轮不要为了把文件夹填满而自动分类或搬运图片。

## 4. metadata 怎样填写

| 字段 | 必填 | 大白话解释 | 不知道时怎么写 |
|---|---:|---|---|
| image_name | 是 | 文件名，含扩展名 | 必须核对，不能空 |
| category | 是 | 人工观察到的暂定类别 | `unknown` |
| source | 是 | 图片从哪里来 | `unknown` |
| capture_date | 是 | 拍摄日期，建议 YYYY-MM-DD | `unknown` |
| device | 是 | 手机或 USB 显微镜型号 | `unknown` |
| resolution | 是 | 宽×高像素 | 可从质量报告抄真实值 |
| magnification | 否 | 显微倍率 | `unknown` 或 `none` |
| annotation_status | 是 | 是否完成人工标注 | `unlabeled` / `labeled` |
| remark | 否 | 反光、模糊、来源疑问等 | 可留空 |

三条硬规则：

1. 不根据图片内容猜设备、倍率、来源或污染材料。
2. 不删除失焦、反光、过曝等失败图；它们是后续改进成像和质量门的证据。
3. 原图优先保留原文件名和原始字节。正式导入清单制数据集时，复用现有 `dataset_manifest.py` 的 SHA-256 和稳定 ID，不另造一套去重系统。

## 5. 成员 A 本轮实际流程

### 步骤 1：质量检查

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.inspect_images data\raw_images --recursive `
  --output output\data_learning\quality_report.json `
  --csv-output output\data_learning\quality_summary.csv
```

报告中的主要指标：

- `laplacian_variance`：画面边缘变化的强弱，常用于粗略发现失焦；它不是“清晰度真理”。
- `mean_intensity`：整张图的平均明暗。
- `dark_fraction` / `bright_fraction`：特别暗/特别亮像素所占比例。
- `focus` / `illumination` / `confidence`：现有规则把原始指标压缩成 0–1 分数，方便程序使用。
- `flags`：当前规则认为值得人工复查的问题。

### 步骤 2：数据审计

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.data_audit
```

这个命令只报告，不会移动或删除图片，也不会替人补 metadata。它回答：有多少图片、能否解码、是否重复、哪些未登记、哪些登记字段缺失。

### 步骤 3：成员 A 人工登记

先让程序预览它准备新增的行；默认不会修改 CSV：

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.metadata_builder --dry-run
```

确认文件名、分辨率和默认 `unknown` 没有问题后再写入：

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.metadata_builder --apply
```

程序只自动填写文件名、分辨率和初始标注状态，不覆盖已有人工行。然后打开 `data/metadata.csv`，人工核实来源、设备、拍摄日期和倍率；无法核实的字段继续保持 `unknown`。完成一张标注后，把该图的 `annotation_status` 从 `unlabeled` 改成 `labeled`。

### 步骤 4：用 Labelme 画污染边界

操作见 `data/annotations/README.md`。当前只允许一个标签：`contamination`，意思是“这块区域由成员 A 暂定为污染”。

先选 3–5 张边界清楚、外观差异明显的图验证流程，再选 2–3 张失焦、反光或边界模糊图记录失败。不要一开始标 50 张，避免标注规则尚未稳定就产生大量返工。

### 步骤 5：Labelme JSON 自动批量转二值 Mask

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.annotation_tools --batch
```

程序扫描 `data/annotations/labelme/`，根据每份 JSON 的 `imagePath` 自动寻找 `data/raw_images/` 中的原图，并在 `data/annotations/masks/` 生成同名 Mask。批量报告保存到 `output/data_learning/annotation_conversion_report.json`。

输出中污染区域为 255（白色），背景为 0（黑色），尺寸必须与原图一致。工具支持 Polygon 和 Circle，拒绝错误标签、其他形状、数据集外路径、真正越界的坐标和尺寸不一致，避免“看起来能用，实际上错位”。

### 步骤 6：与 B 的算法 Mask 比较

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.mask_evaluation `
  data\annotations\masks\public_001.png `
  output\vision\public_001_mask.png `
  --output output\data_learning\public_001_evaluation.json
```

- `IoU`：两块区域交集 ÷ 并集。1 表示完全重合，0 表示完全不重合。
- `area_error_px`：算法面积减人工面积。正数说明算法圈大了，负数说明圈小了。
- `centroid_error_px`：两个区域中心相隔多少像素。若只有一方为空，中心误差不可定义，输出 `null`，不会伪造数字。

这些指标暂时使用像素单位。没有像素到毫米标定前，不得把它们写成毫米误差。

## 6. 什么时候才训练模型

正确顺序是：

```text
人工 Mask
  ↓
评价 OpenCV 基线
  ↓
整理稳定失败类型，例如反光误检、弱对比漏检
  ↓
确认简单规则确实无法满足 KPI
  ↓
再决定是否扩充标注、划分训练/验证集并训练分割模型
```

如果未来训练模型，必须按拍摄批次划分训练集和验证集，不能把同一视频相邻帧随机拆到两边，否则会产生数据泄漏（模型在考试时见过几乎相同的画面）。具体选择 YOLO segmentation、其他分割网络还是继续改成像，应由失败证据决定。

## 7. `data/` 与清单制数据集的关系

项目目前有两个用途不同的入口：

| 目录 | 用途 | 当前做法 |
|---|---|---|
| `data/` | A 日常检查、人工登记与人工 Mask | 本轮主工作区 |
| `dataset/datasets/microcleaning_v0_1/` | SHA-256、稳定 ID、批次与闭环证据追溯 | 复用 `dataset_manifest.py`，不重写 |

不要把同一图片复制成多份后再分别修改。需要正式纳入闭环证据时，通过既有 manifest 导入，并记录它与 `data/raw_images/` 原图的对应关系。

## 8. 成员 A 下一步人工任务

1. 核实当前 13 张图片的来源并完善 `metadata.csv`；不知道就继续保留 `unknown`。
2. 逐份打开批量生成的 13 张 Mask，检查图片是否对应、标签含义是否一致、边界是否贴合；确认后再更新 metadata。
3. 先冻结 3 张开发图给 B 调试，再保留至少 2 张不参与调参的留出图，用来检查算法是否真的改善。
4. 让 B 为同样图片导出 predicted mask，保证尺寸和文件名对应。
5. 运行评价工具，把 IoU、面积误差、中心误差与图片一起查看。
6. 写下最常出现的一种失败，再决定下一轮是补拍、重标、调简单算法，还是将来训练模型。

做到这一步，A 交付的就不是“一堆照片”，而是可追溯、能评价、能推动算法决策的数据资产。
