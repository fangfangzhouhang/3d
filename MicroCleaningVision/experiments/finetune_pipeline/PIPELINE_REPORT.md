# 微调训练统一工作流：实现与验证报告

日期：2026-11-10。仓库：`fangfangzhouhang/3d`。工作目录：`D:\大创\3d`。

**结论：所要求工作流的代码已实现，44 项 CPU 专项测试通过，包含合成数据的完整训练—评测—检查点—独立预测链。真实 seed_v0 已完成加载、语义合成、像素自检和增强网格；GPU 冒烟因外部计算进程被按规则阻塞，尚未完成。因此本任务的“真实种子集 GPU 全链实跑”验收未达成。**

> 这是管线连通性验证（过拟合检查）——训练与评测同图是刻意的，产出的指标只证明管线能转，不代表模型效果。
>
> 数据标记：`draft_unverified=true`。两张种子图均为未经人工核验的草稿，不能作为正式测试真值。不能将本报告写成显影分割效果、显影合格或一体机部署验收。

## 1. 起点、范围与保护结果

- 开始时实查 `exp/probe-annotation@18aa016cc4047c7a35117c84b3c8acb8e2a54525`，已跟踪文件无差异；依指令创建 `exp/finetune-pipeline`。
- 新文件全部在 `MicroCleaningVision/experiments/finetune_pipeline/`。未修改/复制 probe_annotation 文件，原图只读；语义标签和网格是从输入计算得到的派生产物。
- 创建前记录 **856 份原有已跟踪/未跟踪文件的 SHA256**、**190 份原未跟踪文件的集合**。完成核验：856 份内容全部一致；190 份原未跟踪文件仍原样未跟踪；新目录外的已跟踪变更 **0**。证据：[保护基线](evidence/protected_before.json)、[核对结果](evidence/scope_verification.json)，可运行 `verify_scope.py` 重查。
- 只提交新目录，不推送远端。最终提交号以 `git log -1 --oneline` 为准，避免文档自引用自身提交号。
- 开工阅读根/项目 AGENTS、六份项目记忆、当前提交和进度记录及总流程，并检查 `project_state.yaml`。它们包含历史主线状态；本次新实验未写入主线共享记忆、进度日志或状态 YAML，原因是当前任务明确要求实验目录外已跟踪变更为 0。本报告承担本次实验的事实交接，不把历史文档当成当前 GPU 成果。

## 2. 已实现工作流

```text
原manifest的图片、分组与split + 实例JSON/PNG + 唯一类别配置
  → 校验路径/尺寸/哈希/组不跨集
  → 按优先级合成原尺寸语义图；未覆盖=ignore
  → 图与掩码同步几何增强；图独享光度增强；固定随机源
  → 保持比例缩放、填充、配置归一化
  → 注册模型 + 可替换损失 + 配置优化器/调度/轮次/批量
  → 每轮训练loss、原尺寸逐类IoU；latest/best检查点
  → 主线evaluate_masks逐类评价；空集/缺类显示无样本
  → 加载best，输出原尺寸语义PNG/叠加PNG/像素面积JSON
```

| 文件 | 实际职责 |
|---|---|
| `configs/classes.json` | 唯一类别名称/索引/ignore索引/覆盖优先级/颜色配置 |
| `configs/smoke.json`、`configs/formal.template.json` | 冒烟占位参数；明确 null 的正式待定模板 |
| `data.py` | Dataset、实例合成、逐图/逐集自检、原分组切分保护 |
| `augment.py`、`preprocess.py`、`visuals.py` | 全部指定增强、可复现随机源、比例/填充/归一化/逆坐标、增强网格 |
| `models.py`、`losses.py` | 注册表、自写轻量U-Net、CE/Dice/组合组件；不平衡策略可替换 |
| `evaluation.py` | 导入主线原评测函数；过滤ignore、逐图/逐类与pixel-micro汇总 |
| `train.py`、`predict.py`、`runtime.py` | 一条命令训练链、独立预测、GPU关卡、日志/检查点/证据标识 |
| `export.py` | ONNX待定接口，调用明确报未实现 |
| `tests/`、`run_tests.py`、`verify_scope.py` | 专项回归、UTF-8结果、文件边界核验 |

默认网络没有预训练权重，从随机初始化训练；这是搭建未来微调所需工作流，不是已经微调某预训练骨干。新增骨干通过模型注册和配置选择，不必修改训练循环。

为复用主线 `mask_evaluation.py`，读取了原源码并发现其 `evaluate_masks` 是纯 NumPy，但模块顶层导入未安装的 cv2。本实验使用 **该模块局部导入适配**隔离不可用的 OpenCV 文件读取路径，原 `evaluate_masks` 原样加载执行，没有重写公式、修改主线或向全局注入 cv2。测试断言函数 `co_filename` 指向原文件，并以可手算的 TP/FP/FN 验证四项指标。正式 metrics 会包含源文件路径和 SHA256。

评测只统计参考标签已知区域；不能用草稿没标的整幅背景评估误检。聚合包含所有图的有效像素，完全无参考样本的类四项指标为 null。val为空时最佳检查点明确按训练内平均IoU选取；这不能证明泛化。test不参与最优选择。正式模式拒绝draft/未核验/占位行，测试集还需显式真值许可。

## 3. 真实输入自检

当前解释器实查：Python **3.12.4**，torch **2.7.1+cu128**，NumPy **2.2.6**，Pillow **11.2.1**；cv2未安装。零新增第三方依赖，未下载模型。解释器仍为 `probe_annotation/.venv/Scripts/python.exe`。

manifest：2 张图、共享保守 part / capture_batch 组，`train=2 / val=0 / test=0`。实例数量实际为 structure 8 / residue 2 / substrate 0 / ignore 11，共21。分组与切分原样保留，没有移动图片来构造验证集。

每图640×480，共307200像素。下表为**实例优先级合成后的语义像素计数**，不是模型预测；ignore含显式忽略区域及全部未覆盖区域：

| 图片 | structure | residue | substrate | ignore |
|---|---:|---:|---:|---:|
| realpart_img1.jpg | 20,144 | 0 | 0 | 287,056 |
| realpart_img2.jpg | 25,751 | 851 | 0 | 280,598 |

基底0实例/0像素是现状，不补标。仅图2存在草稿残留区域，材料身份和标注正确性仍待人工。320限边预处理有效区域为320×240，补齐后为320×256；新增边界标签为ignore。

证据：[逐图审计](runs/smoke_20261110_01/data_audit.json)、[输入哈希](runs/smoke_20261110_01/input_hashes.json)、[增强对照网格](runs/smoke_20261110_01/augmentation_grid.png)、[语义标签目录](runs/smoke_20261110_01/semantic_labels/)。网格每张图原始列+3个增强列，上行为图像，下行为对应标签叠加。代理已查看PNG，观察到结构/草稿残留随几何变换对应，没有发现明显错位；这是代理图像检查，**不替代操作者肉眼验收及人工标注核验**。

## 4. GPU冒烟实际命令与阻塞证据

已从仓库根目录实际执行：

```powershell
& '.\MicroCleaningVision\experiments\probe_annotation\.venv\Scripts\python.exe' -X utf8 -B MicroCleaningVision/experiments/finetune_pipeline/train.py --config MicroCleaningVision/experiments/finetune_pipeline/configs/smoke.json --output MicroCleaningVision/experiments/finetune_pipeline/runs/smoke_20261110_01
```

配置计划为12轮、批量2、seed=20261110、轻量U-Net基宽8、Adam lr=0.003、CE+Dice、限边320；这些值均明确为冒烟用非定版。它们是**请求参数**，不是训练已经执行的证据。

2026-11-10 **12:38:05 UTC（20:38:05北京时间）**，运行在初始化CUDA模型之前执行：

```text
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader
35492, C:\Program Files\WindowsApps\OpenAI.Codex_26.1103.4239.0_x64__2p2nqsd0c76g0\app\ChatGPT.exe, [N/A]
```

这仍属于任务要求检查的外部计算进程。即使是图形程序或显存显示N/A，也没有自行豁免。程序返回 `GPU_BLOCKED`、进程退出码1；没有结束外部进程、抢占GPU或自动降级CPU。

| 验收项 | 本次实际状态 |
|---|---|
| 真实种子加载/语义合成/自检 | 已执行 |
| 增强对照网格 | 已生成、代理已查看 |
| GPU模型训练 | **NOT_PERFORMED，实际0轮** |
| 真实种子训练loss变化 | **无，未产生；不能填写数值** |
| 真实种子模型IoU/P/R/F1 | **无，metrics=null** |
| 最近/最优检查点及路径/大小 | **未生成，无路径与文件大小可报告** |
| 两张真实图的模型预测叠加 | **NOT_PERFORMED，未生成** |
| ONNX/一体机部署实测 | 按任务边界未执行 |

证据：[运行日志](runs/smoke_20261110_01/run.log)、[GPU进程名单](runs/smoke_20261110_01/gpu_before_training.json)、[阻塞状态](runs/smoke_20261110_01/status.json)、[metrics.json](runs/smoke_20261110_01/metrics.json)、[traceback](runs/smoke_20261110_01/traceback.txt)。metrics.json根据保留的关卡失败记录补充了`NOT_PERFORMED`和空指标，注明其为失败记录整理，没有生成或倒填训练结果。修订后的程序在后续被阻塞时会自动输出该类空指标状态。

GPU空闲后，按README的 `smoke_after_gpu_release` 新目录命令复跑，不覆盖本次阻塞证据。成功后才可将本节补为实际轮次、loss变化、逐类指标、checkpoint路径/大小/SHA、两图预测清单，并继续保持draft与管线声明。

## 5. 已运行验证与修正

最终命令：

```powershell
& '.\MicroCleaningVision\experiments\probe_annotation\.venv\Scripts\python.exe' -X utf8 -B MicroCleaningVision/experiments/finetune_pipeline/run_tests.py --name tests_final
& '.\MicroCleaningVision\experiments\probe_annotation\.venv\Scripts\python.exe' -X utf8 -B MicroCleaningVision/experiments/finetune_pipeline/verify_scope.py
```

最终 **44项测试全部通过，0失败、0错误、0跳过**；测试入口墙钟耗时 **11.3845秒**（含发现/导入；测试体时间见日志）。[结构化结果](evidence/tests_final.json)、[完整日志](evidence/tests_final.log)。只运行与本实验直接相关的测试，未运行/宣称主线全量回归。

覆盖内容：每种几何变换的合成地标对应、掩码最近邻及填充、同种子逐像素一致、光度不改掩码、覆盖优先级与未覆盖ignore、part/batch/group跨集拒绝、缺类与空集、尺寸/哈希/路径异常、归一化与逆坐标、模型真实反传、ignore零梯度及全ignore、主线已知指标与空类、可替换注册、正式数据纪律、GPU外部进程阻塞、输出不覆盖与不越界。

端到端测试用**合成CPU夹具**真实训练2轮，实际写出并重载latest/best检查点、评测train/val/test、调用独立predict入口、断言原尺寸mask/overlay/面积总和。夹具与检查点只在本实验`.tmp`临时目录存在并由测试清理，未当作真实GPU冒烟产物提交。该证据证明软件接口接通，不能证明CUDA路径本次已经运行。

独立审查修正：

1. 噪声参数统一为归一化单位0–0.006，避免原占位1.5被乘255后严重破坏图像。
2. 默认U-Net上采样改nearest后接卷积，避开已装PyTorch明确列出的严格确定性CUDA bilinear反传冲突；补了像素和梯度测试。CUDA整体仍待实跑，不以源码审查代替运行。
3. 全空manifest生成“无样本”状态，不在网格生成处崩溃；无训练样本不取得GPU。
4. 增强预览只使用train；独立test可用标记要求确有已知标签像素，不因存在全ignore图片就报可用。
5. 阻塞状态明确记录0轮/未评测/未预测，保留空metrics，防止只凭目录或进程退出码判断成功。

初版42项以及中间43项日志保留，仅最终44项作为修订后验证结果；首份PowerShell转录对unittest的stderr显示了NativeCommandError包装，但实际测试退出码0、42项OK，最终日志由Python直接以UTF-8记录，避免终端包装误读。

## 6. 已完成与仍未完成

已完成：配置化数据口径、全部指定增强/预处理、默认真实可训练U-Net、损失/模型注册、训练循环及日志/检查点代码、主线评测适配、独立预测代码、专项与合成全链测试、真实种子数据审计/增强图、中文复现文档与文件边界保护。

**仍未完成且不能标PASS：真实种子GPU训练、真实种子训练loss记录、训练检查点、真实两图模型预测、基于该模型的评测。** 直接阻塞是NVIDIA列表中仍有外部计算进程。没有以调低显存、选择另一骨干或CPU结果绕过此条件。

## 7. 待定框架清单

| 未定事项 | 当前只提供什么 | 等待的决定/证据 |
|---|---|---|
| 最终结构/骨干 | 注册表+冒烟U-Net；DINO显式TODO | 负责人选型、适配和对照方案，相关权重来源与许可 |
| 正式学习率/曲线/轮次/批量/尺寸/增强 | JSON参数、可选调度接口、null正式模板 | 核验数据量与独立组、实验目标、资源及预先冻结方案 |
| 类别终局与残留材料定义 | 唯一类别配置；代码不按名称硬编码 | 老师确认未显影材料定义、边界与标注规范 |
| 类别不平衡 | 可替换损失组件；没有类别权重预设 | 正式像素/实例分布、失败分析和比较实验 |
| ONNX/CPU一体机 | 显式未实现导出函数与目标背景说明 | 选定模型、算子集、运行时、输入规格、延迟/精度预算 |
| 真正预训练微调方案 | 当前无预训练权重/下载接线 | 骨干和预训练方案批准后新增注册实现 |
| 数据充分性与效果门槛 | 原manifest/组隔离与正式标记检查 | 真实人工核验、独立测试组、评价方案；两张同组草稿不足以决定 |

本次把“可运行的训练软件”“未经核验的标签”“模型泛化效果”“物理显影效果”分开记录。当前最有价值的下一步是先解除GPU计算进程阻塞完成规定冒烟，再由数据负责人核验标签并提供独立组；不能用增加训练轮数补足标注真实性或独立测试证据。
