# MicroCleaningVision 数据集管理说明（v0.1）

> 负责人：成员 A（数据与模型）
> 本文件是 `data/` 目录的唯一权威说明。修改本文件前先读 `AGENTS.md`。

---

## 1. 数据集目的

视觉模块第一阶段的目标**不是训练复杂 AI 模型**，而是建立可靠的数据资产：

1. 为 OpenCV 规则算法（HSV 分割、质量门）提供验证图片
2. 为后续机器学习 / YOLO 等算法预留标注接口
3. 让每一张进入闭环的图片可追溯（来源、设备、日期、分类）

当前优先级：**最快形成第一个视觉闭环 Demo**——即一张真实图片走通
`质量检查 → 分类存放 → HSV 分割 → 输出 mask`。

---

## 2. 当前版本

- 版本：**MicroCleaning Dataset v0.1**
- 状态：结构已建立，正式记录数为 **0**（`data/metadata.csv` 只有表头）
- 现有图片资产：`data/raw_images/` 下有 11 张公共样例图
  （`public_001.jpg` ~ `public_011.jpg`，2026-08-21 从旧清单制数据集迁入，原件已迁移非复制），
  **尚未分类、尚未登记**，等成员 A 人眼分类后移入 `data/dataset/`

---

## 3. 文件结构

```text
data/
├── raw_images/          # 原始未分类图片（新图片先落这里，默认不进 Git）
├── dataset/             # 已分类图片（按下述六类存放）
│   ├── clean/           # 无污染样本
│   ├── particle/        # 颗粒、粉尘类污染
│   ├── residue/         # 材料残留、树脂残留等
│   ├── oil_stain/       # 油污污染
│   ├── scratch/         # 划痕、机械损伤
│   └── unknown/         # 暂时无法分类样本
├── annotations/         # 未来标注存放处（YOLO label / mask / 分类标签）
└── metadata.csv         # 全部已分类图片的登记清单
```

### 分类定义

| 类别 | 含义 | 判断标准 |
|---|---|---|
| clean | 无污染样本 | 表面无可识别异物 |
| particle | 颗粒、粉尘类污染 | 离散点状物 |
| residue | 材料残留、树脂残留 | 成片附着物 |
| oil_stain | 油污污染 | 半透明浸润状 |
| scratch | 划痕、机械损伤 | 线状痕迹（非污染，是损伤） |
| unknown | 暂时无法分类 | 拿不准就放这里，禁止猜测 |

**规则：拿不准的图片一律放 `unknown/`，在 metadata 的 remark 里写明疑问，
后续再重新分类。禁止为了"数据好看"把不确定的图硬塞进某个类别。**

---

## 4. 图片命名规范

格式：`MC_类别_编号.jpg`

```text
MC_particle_001.jpg      # 第 1 张颗粒污染图
MC_clean_001.jpg         # 第 1 张无污染图
MC_residue_012.jpg       # 第 12 张残留图
```

规则：

1. 类别名与 `data/dataset/` 下的文件夹名一致（全小写）
2. 编号三位数字，从 `001` 开始，每类独立递增
3. 文件放哪个文件夹，类别段就必须写哪个（`MC_clean_001.jpg` 必须在 `clean/` 里）
4. 保留原图扩展名，不强制转格式
5. **禁止**用相机默认文件名（`IMG_20260821.jpg`）直接入库——先重命名再入库

---

## 5. 数据采集规范

### 5.1 入库流程（每张图都要走）

```text
拍摄/收集
   ↓
放入 data/raw_images/           # 原始暂存，可批量堆放
   ↓
人工判断类别（拿不准 → unknown）
   ↓
重命名为 MC_类别_编号.jpg
   ↓
移动到 data/dataset/类别/
   ↓
在 data/metadata.csv 追加一行登记
   ↓
（可选）跑质量检查确认图片可用
```

### 5.2 metadata.csv 字段说明

| 字段 | 必填 | 说明 | 示例 |
|---|---|---|---|
| image_name | ✅ | 文件名（含扩展名，不含路径） | `MC_particle_001.jpg` |
| category | ✅ | 六类之一 | `particle` |
| source | ✅ | 图片来源 | `public_dataset` / `usb_microscope` / `phone` |
| capture_date | ✅ | 采集日期（YYYY-MM-DD） | `2026-08-21` |
| device | ✅ | 设备型号 | `unknown` |
| resolution | ✅ | 宽x高（像素） | `640x480` |
| magnification | ❌ | 显微倍率（非显微设备填 none） | `50x` / `none` |
| annotation_status | ✅ | 标注状态 | `unlabeled` / `labeled` |
| remark | ❌ | 备注 | `来自公共数据集` |

**不知道的字段写 `unknown`，不要猜。** 虚构的倍率/设备信息会污染后续实验。

### 5.3 采集纪律（沿用项目既有规则）

1. 保留失败图（失焦、反光、过曝），删掉就失去了质量门的验证材料
2. 至少分两个采集批次（重新摆放样本/重启设备算新批次）
3. 同一视频的连续帧不要拆到训练集和测试集两边（未来划分时执行）

### 5.4 质量检查命令（分类后可选执行）

```powershell
.\.venv\Scripts\python.exe -m microcleaning.data_learning.inspect_images data\dataset\particle --output output\quality\particle_v0.json
```

---

## 6. 后续如何扩展到 YOLO 标注

`data/annotations/` 已预留，**当前不建立复杂标注流程**。扩展路径：

### 阶段 1（现在）：只分类，不标注

`annotation_status` 全部保持 `unlabeled`。用六类文件夹做最简单的分类资产。

### 阶段 2（HSV 基线出现稳定失败后）：人工多边形标注

- 工具：labelme
- 输出：labelme JSON，放 `data/annotations/labels/`，文件名与图片同名
- 对象：污染区域多边形（沿边缘，不画外接矩形框）
- 登记方式：`metadata.csv` 中该图的 `annotation_status` 改为 `labeled`

```text
data/annotations/
├── labels/       # labelme JSON（阶段 2）
├── masks/        # 导出的二值 mask（阶段 2）
└── yolo/         # YOLO txt 格式（阶段 3）
```

### 阶段 3（确有证据需要检测模型时）：转 YOLO 格式

- labelme JSON → YOLO detection txt（`class x_center y_center w h`，归一化坐标）
- 类别映射表固定写在转换脚本里（particle=0, residue=1, oil_stain=2, scratch=3）
- 划分 train/val 时按**来源批次**隔离，不按单张图片随机分

**升级到阶段 2/3 的前置条件（写进 AGENTS.md 的证据原则）：必须先回答
"当前 HSV 规则算法在哪些图上稳定失败"。没有失败清单，不启动标注工程。**

---

## 7. 与现有 `dataset/datasets/microcleaning_v0_1/` 的关系

项目里存在两套数据目录，**分工不同，不互相替代**：

| 目录 | 性质 | 用途 | 管理方式 |
|---|---|---|---|
| `data/`（本文件管理） | 分类数据集 | 视觉算法验证：按六类组织图片 | 人工分类 + `data/metadata.csv` |
| `dataset/datasets/microcleaning_v0_1/` | 清单制数据集 | MCL 闭环证据：SHA-256 追溯、采集批次 | `dataset_manifest.py` CLI |

### 现有 11 张公共样例图的处理建议（成员 A 的下一步）

这 11 张图目前在 `microcleaning_v0_1/images/`，未分类未登记。建议流程：

1. 逐张人眼判断类别（拿不准 → `unknown`）
2. 重命名为 `MC_类别_001~0xx.jpg`
3. 复制到 `data/dataset/对应类别/`（`microcleaning_v0_1/images/` 原件不动）
4. 在 `data/metadata.csv` 登记 11 行，source 填 `public_dataset`
5. 跑一次质量检查，把质量差的图记入 remark

这样第一批分类数据当天就能到位，视觉 Demo 的输入就有了。

---

## 8. 快速参考

```powershell
# 质量检查某个类别目录
.\.venv\Scripts\python.exe -m microcleaning.data_learning.inspect_images data\dataset\particle

# 用分类后的图跑视觉分析 Demo
.\.venv\Scripts\python.exe -m demo.demo_pipeline --input data\dataset\particle\MC_particle_001.jpg --mode analyze
```
