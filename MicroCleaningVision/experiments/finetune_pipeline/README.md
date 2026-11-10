# 显影图像语义分割统一工作流

本目录提供 manifest → 实例合成语义图 → 配对增强 → 预处理 → PyTorch 训练 → 主线指标评测 → 检查点 → 原尺寸预测的离线工作流。仅使用标准库、现有 torch / numpy / Pillow，不安装依赖，不下载权重，不接硬件。

**当前交付状态：CPU 专项与合成端到端验证通过；真实种子集的 GPU 冒烟被外部计算进程阻塞。不能宣称本次已经完成 GPU 训练或取得模型效果。** 证据、状态与未完成项见 [PIPELINE_REPORT.md](PIPELINE_REPORT.md)。

> 这是管线连通性验证（过拟合检查）——训练与评测同图是刻意的，产出的指标只证明管线能转，不代表模型效果。
>
> seed_v0 为 `draft_unverified`，未经人工核验。默认 U-Net 从随机初始化训练，并未加载某个预训练骨干，因此本次不构成预训练模型微调成果。课题目标是打印后处理显影；残留材料身份、类别终局和显影合格标准仍待老师确认。

## 运行现有草稿冒烟

以下 PowerShell 命令从仓库根目录运行。解释器只读复用已有环境；`-X utf8 -B` 确保中文日志并避免向主线或探针写入字节码。所有输出必须在本实验目录内的新子目录中；已有运行目录拒绝覆盖。

```powershell
Set-Location 'D:\大创\3d'
$py = '.\MicroCleaningVision\experiments\probe_annotation\.venv\Scripts\python.exe'
$fp = 'MicroCleaningVision/experiments/finetune_pipeline'

# 先查GPU；列表出现任何外部计算进程就停止，不结束别人进程，也不跳过关卡。
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader

# GPU空闲时，一条命令完整执行；程序内部会再次检查进程名单。
& $py -X utf8 -B "$fp/train.py" --config "$fp/configs/smoke.json" --output "$fp/runs/smoke_after_gpu_release"

# 只审计数据和生成增强网格，不使用GPU，不训练。
& $py -X utf8 -B "$fp/train.py" --config "$fp/configs/smoke.json" --output "$fp/runs/audit_new" --audit-only

# 本实验专项测试：包含合成CPU训练、重载及预测；不是GPU种子集验收。
& $py -X utf8 -B "$fp/run_tests.py" --name tests_local
```

默认配置是 12 轮、轻量 U-Net、Adam、CE + Dice、固定种子、批量 2、限长边 320。**这些全部是冒烟占位值，非正式方案、非调优结果。** 所有概率、强度、归一化、优化器与学习率在配置中。`noise_std` 是 `[0,1]` 图像单位，`0.006` 约等于 1.53 个灰度级；`blur_radius` 单位为像素；`crop_fraction` 是每边保留比例；`rotate90_choices` 是四分之一圈的次数。

`--device cpu` 是显式 CPU 实验选项，绝不自动降级；以它运行的结果不能满足任务要求的 GPU 冒烟。

## 数据格式与标签纪律

输入分为现有 CSV 图片清单和由其文件名定位的实例清单。原图读取 `images_root/filename`，实例读取 `manifest.parent/文件名去扩展名/instances.json`，掩码路径相对于该实例清单所在目录。程序既不复制原图，也不修改 probe_annotation 的任何文件。

```text
reviewed_dataset/
  manifest.csv
  sample_001/
    instances.json
    masks/0001.png
  sample_002/...
reviewed_images/
  sample_001.png
  sample_002.png
```

CSV 必需字段：`filename,part_id,capture_batch,width,height,split,annotation_status,human_verified,test_ground_truth_allowed`；建议同时保留 `device,split_role`，支持额外 `group_id`。`split` 只能为 train / val / test，完全沿用输入，不重新抽签。同 part_id、同 capture_batch 或同 group_id 的图片不得跨集；串联的同组关系同样会被检查。通用 unknown 不能作为分组身份；无法确认身份时只能由数据负责人给保守共享组，并说明其来源。两张 seed 图当前共享同一件和批次占位组，因此不能拆开伪造独立测试集。

`instances.json` 是列表，每项包含 `id`、`label`、`mask`；可选 `mask_sha256` 会在读取时校验。`label` 必须存在于唯一的 [类别配置](configs/classes.json)。实例 PNG 必须是同原图尺寸的单通道图，非零表示该实例区域；尺寸错误、未知类别、重复实例 ID、掩码路径越界均拒绝。

类别名称、索引、忽略索引、优先级和显示颜色只由 `configs/classes.json` 定义，代码按配置排序，不按类别名称分支。合成先全图初始化 ignore，再按优先级覆盖：ignore > residue > structure > substrate。**没有实例覆盖的像素保持 ignore，不能用空白处推导基底。** ignore 为未知/不参与监督，不是第四个网络输出类别；网络仅输出配置中的可学习类别。

`Dataset.__getitem__` 返回 `image`（RGB float CHW）、`mask`（long HW）、`ignore_mask`（bool HW）、`metadata`。预处理保持比例，原尺寸大于限边才缩小，右侧/底部填充到配置倍数；掩码始终最近邻，填充为 ignore。图像归一化使用配置 RGB mean/std。不同尺寸批量再补到该批最大尺寸，额外标签区域同样为 ignore。

## 训练、评测和预测的含义

- 几何增强同时操作图和掩码；光度只改图。随机数由固定 seed / epoch / 文件名派生，不依赖 Python 随机哈希；同条件逐像素可复现。增强网格只来自 train，未把 val/test 调试预览混入训练。
- 默认损失为无类别权重 CE + Dice；ignore 不贡献损失或梯度。全 ignore 批次跳过，整个 epoch 无监督像素会明确失败，不报告假训练成功。缺某类不会崩溃。
- 每轮记录增强训练 loss、未增强 train 上各已出现类别的原尺寸 IoU。最佳检查点按 val 上有样本类别的平均 IoU 选择；val 空时明确使用 train 指标，只是训练内选择，不能称为最佳泛化模型。test 只在最终最佳检查点评测，不参与选择。
- `evaluation.py` **直接加载主线 `microcleaning/data_learning/mask_evaluation.py` 并调用原 `evaluate_masks`**。其顶层 `import cv2` 在指定环境不可用；实验只在该模块自己的导入命名空间提供“不可用 OpenCV”占位，禁止调用依赖它的文件解码路径。真实数组指标函数原样执行，无源码改写、无指标公式复制、无全局 cv2 冒充安装；PNG 由 Pillow 解码。来源路径/SHA 和适配事实进入指标。
- 评测恢复原图尺寸、排除参考标签 ignore 像素。逐类聚合是所有图有效像素的 pixel micro，包含没有该类的图片上产生的误检。完全无参考样本的类，IoU / Precision / Recall / F1 为 null、状态“无样本”，保留 FP 计数；空 val/test 同样显示无样本。
- 预测的 semantic.png 是原尺寸类别索引图；overlay.png 只是展示；areas.json 是像素面积，不是物理面积或材料测量。网络在标注未知区域输出类别，不能使该区域成为人工真值。

训练成功后输出 `run.log`、`events.jsonl`、数据审计、输入及源码哈希、增强网格、逐轮 history、metrics、best/latest 检查点、每图预测与产物清单。检查点包含训练配置、类别快照、模型和优化器状态，单独预测不需要另配归一化。快照属于运行二进制证据，类别定义仍只有一份人工维护配置。`.pt` 被本目录 `.gitignore` 排除；其路径、字节数、SHA 会写进 status.json。每次失败保存状态及 traceback，不覆盖已有结果。

```powershell
# 必须先有一次真正完成训练的检查点；当前被阻塞的运行没有这些文件。
& $py -X utf8 -B "$fp/predict.py" --checkpoint "$fp/runs/smoke_after_gpu_release/checkpoints/best.pt" --input 'MicroCleaningVision/experiments/probe_annotation/inputs/realpart_img1.jpg' --output "$fp/runs/predict_img1_new" --device cpu
& $py -X utf8 -B "$fp/predict.py" --checkpoint "$fp/runs/smoke_after_gpu_release/checkpoints/best.pt" --input 'MicroCleaningVision/experiments/probe_annotation/inputs/realpart_img2.jpg' --output "$fp/runs/predict_img2_new" --device cpu
```

## 真数据到位后如何正式训练

1. 数据负责人先完成人工核验、冻结类别定义和分组切分，保留实际核验依据；不要为了通过程序把草稿字段改成 true。正式 manifest 行应为已核验状态、`human_verified=True`，不再用 `split_role=placeholder_demo`；test 还须明确 `test_ground_truth_allowed=True`。输入图与实例按上述结构提供。
2. 将 `configs/formal.template.json` **在本目录内**另存为 `configs/formal.local.json`，填写所有 null 决策：seed、模型及参数、优化器/lr、epochs、batch_size、尺寸与归一化，并按批准方案配置增强/可选学习率调度。模板不会把冒烟数值当定版。`classes` 继续指向唯一类别配置；配置内相对路径一律相对于本实验根目录，CLI `--manifest/--images-root` 相对路径则相对于命令当前目录。
3. 用以下确切命令运行；示例文件名是待提供的正式数据路径，不代表数据已经存在。正式数据也可在仓库外，加载只读。

```powershell
Set-Location 'D:\大创\3d'
$py = '.\MicroCleaningVision\experiments\probe_annotation\.venv\Scripts\python.exe'
$fp = 'MicroCleaningVision/experiments/finetune_pipeline'

# 先完成 formal.local.json 的未定参数，再只读自检。
& $py -X utf8 -B "$fp/train.py" --config "$fp/configs/formal.local.json" --manifest "$fp/data/reviewed_v1/manifest.csv" --images-root "$fp/data/reviewed_v1/images" --output "$fp/runs/formal_v1_audit" --audit-only

# 外部计算进程列表必须为空；训练内部再次检查。
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader
& $py -X utf8 -B "$fp/train.py" --config "$fp/configs/formal.local.json" --manifest "$fp/data/reviewed_v1/manifest.csv" --images-root "$fp/data/reviewed_v1/images" --output "$fp/runs/formal_v1"
```

正式模式拒绝未经核验/占位行。某集为空仍会如实输出“无样本”；没有带有效标签的独立 test，结果不会标成具有可用独立测试。没有 train 时退出为 `no_training_samples`、训练未执行；不能仅看命令 exit 0 宣称训练完成。

## 扩展点与待定边界

- 模型：在 `models.py` 用 `@register_model("名称")` 注册工厂，再改配置 model。训练循环不依赖具体 U-Net。DINO 只有显式待定注册位，调用时报未实现，无下载、无接线。
- 损失：在 `losses.py` 用注册接口增加组件，再改配置 loss。类别不平衡策略及任何权重由后续实验决定；没有预设类别权重。
- 调度：配置 scheduler=null 表示本次冒烟恒定学习率；可显式配置已装 PyTorch 的 StepLR / CosineAnnealingLR。是否使用及数值未定。
- 部署：`export.py` 的 ONNX 接口显式抛未实现。目标 i3-5005U 无 GPU，模型、算子集、输入规格、延迟与精度预算尚未确定。本轮没有导出实测或部署结论。

`verify_scope.py` 核对原有文件 SHA、原未跟踪集合和 Git 目录边界；它读取本轮 `evidence/protected_before.json`，不是任意仓库版本的通用完整性基线。
