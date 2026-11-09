# 阶段 0 汇报：环境通过，自动结构候选不足

日期：2026-11-09，Asia/Shanghai。只执行阶段 0，阶段 1/2 未开始。

## 做了什么

沿用已准备的本地 `exp/probe-annotation`，基线为 `a5b304903e54e008e948375d84f3f4c04a0290e0`（与本地 origin/main 相同）；没有再次 fetch/新建分支。两张老师实拍原字节保留，未找图或替换输入。完成项目 AGENTS、六份项目记忆及相关日志/源码的阅读核对；本轮明确限定只改实验目录，因此公共文档和 project_state.yaml 保持原样，阶段事实以本报告交接。

建立 `.venv`，固定 CPython 3.12.4、torch 2.7.1+cu128、torchvision 0.22.1+cu128 及完整传递依赖；SAM-2 1.0 对应完整源码 commit `2b90b9f5ceec907a1c18123530e92e794ad901a4`，仅下载使用 SAM 2.1 Hiera tiny。来源与代码/权重许可证逐项登记在 [MODELS.md](MODELS.md)。没有 AGPL 模型、训练或硬件操作。

首次 GPU 前的 `nvidia-smi` 显示 RTX 5060 Laptop、驱动 577.02、**0 / 8151 MiB、0% GPU、No running processes found**；推理进程在导入 torch 前和每图前都再次保存占用快照，未发现其他计算 PID。实测 torch CUDA runtime 12.8、设备计算能力 12.0、wheel 含 sm_120。

以同一模型、同一参数依次对两图各调用一次自动生成器，保存独立二值掩码、ID 图、并集图与原图叠加。未手工挑点，未根据结果调筛选阈值。另建 `.venv-rebuild` 按同一个 setup 脚本重建，作阶段 0 复跑验证；两次运行分目录保存，不覆盖首次结果。

## 证据在哪

| 内容 | 文件 |
|---|---|
| 重建与运行命令 / 输出语义 | [README.md](README.md)、[setup_env.ps1](setup_env.ps1)、[requirements.txt](requirements.txt)、[sources.json](sources.json) |
| 初始 GPU 与进程 | [完整快照](logs/nvidia_smi_initial.txt)、[计算进程列表](logs/nvidia_compute_initial.csv)；每次运行目录另有 before_import / before_img / after_generate 快照 |
| 安装与依赖检查 | [首次安装日志](logs/setup_initial.log)、[重建日志](logs/setup_rebuild.log)，两者均包含 pip check 与权重 hash 核验 |
| 输入身份 | [两图来源/尺寸/哈希记录](logs/inputs.json)，来源为用户指定的老师实拍，设备与倍率未由看图推断 |
| 首次原始数字 | [metrics.json](results/stage0_initial/metrics.json)、[推理日志](logs/inference_initial.log) |
| 图1叠加 / 候选说明 | [无编号叠加](results/stage0_initial/realpart_img1/overlay.png)、[带编号叠加](results/stage0_initial/realpart_img1/overlay_ids.png)、[proposals.json](results/stage0_initial/realpart_img1/proposals.json)；同目录 `masks/` 为5张独立掩码 |
| 图2叠加 / 候选说明 | [无编号叠加](results/stage0_initial/realpart_img2/overlay.png)、[带编号叠加](results/stage0_initial/realpart_img2/overlay_ids.png)、[proposals.json](results/stage0_initial/realpart_img2/proposals.json)；同目录 `masks/` 为4张独立掩码 |
| 首次产物校验 | [verify_initial.json](logs/verify_initial.json)：2图、9张二值掩码的原尺寸、面积、并集和源图 hash 通过 |
| 新环境复跑 / 像素一致性 | [重建环境 metrics](results/stage0_rebuild/metrics.json)、[复跑日志](logs/inference_rebuild.log)、[verify_rebuild.json](logs/verify_rebuild.json)：2图9掩码校验通过，**17张PNG逐像素相同** |
| 单元验证 | [unit_tests.log](logs/unit_tests.log)：4项针对性测试；没有扩大运行主仓库全量回归 |
| 范围 / 锁定依赖核验 | [scope_integrity.json](logs/scope_integrity.json)：24个固定依赖版本逐项一致，两环境完整 freeze 相同，tracked 文件改动0，新增仅实验目录，权重已忽略，上游源码干净 |
| 异常与局限 | [failures.md](failures.md)；首次 runner 快照在 [logs/run_stage0_initial.py](logs/run_stage0_initial.py)，与首次 metrics 的代码 SHA256 对应 |

所有证据当前仅在此本地分支目录，未提交、未推送、未提 PR，不能宣称其他成员已取得原始文件。

## 数字（实测，与定性判读分开）

固定自动掩码参数：32×32 网格，16 points/batch，pred_iou_thresh=0.8，stability_score_thresh=0.95，crop_n_layers=0，min_mask_region_area=0；bfloat16 autocast、确定性算法、关闭 TF32、不 torch.compile、无预热。其余参数完整见 metrics。

| 实拍输入 | 生成器单张耗时 s | 已分配显存峰值 MiB | 保留显存峰值 MiB | 候选数 | 全候选并集像素 | 数字来源 |
|---|---:|---:|---:|---:|---:|---|
| realpart_img1.jpg，640×480 | 6.522455 | 810.330566 | 1030.000000 | 5 | 8,917 | 首次运行 metrics.images[0]，本机实测 |
| realpart_img2.jpg，640×480 | 5.133353 | 832.585449 | 1030.000000 | 4 | 62,633 | 首次运行 metrics.images[1]，本机实测 |

模型参数量实测 **38,962,498**；checkpoint 实测 **156,008,466 bytes（148.781 MiB）**；模型+生成器构建/载入实测 **3.904661 s**（不含 torch 导入、CUDA context 初始化）。来源均为首次 metrics / `logs/checkpoint.json`，不是引用官方视频 FPS。

独立新环境复跑（单独样本，不与上表取平均）：图1 **6.153547 s**，图2 **4.807399 s**；已分配峰值仍分别为 **810.330566 / 832.585449 MiB**，保留峰值均为 **1030 MiB**；模型+生成器构建/载入 **3.061806 s**。来源 `results/stage0_rebuild/metrics.json`。复跑未出现首次的只读数组警告；9张掩码及8张索引/并集/叠加PNG共17张均逐像素相同。

计时在 `generate(RGB)` 前后 CUDA synchronize，包含内部变换、编码、网格解码、过滤和 CPU masks；排除图片读盘/转RGB、模型加载、保存PNG。图1是模型驻留后未预热的第一张，图2更暖，二者不能当重复样本平均。首次推理时重建环境仍在后台安装，CPU/磁盘负载未控制；本表是环境冒烟观测，不是稳定性能基准。

显存由 PyTorch max_memory_allocated / max_memory_reserved 读取，包含驻留权重，每图前清缓存/重置峰值；**不含驱动/context/其他进程，不等于整卡峰值**。nvidia-smi 文件是采样快照，不是连续峰值测量。并集像素只描述模型输出量，不能解释为结构覆盖率或残胶面积。

没有人工真值，本阶段不报告 IoU、Precision、Recall、F1 或准确率。模型内部 predicted_iou 即使很高，也不是这两图上的实测精度。未接入主线评测合同或建立另一套评测体系。

## 定性判读：描出的东西像不像结构

**结论：这个固定自动配置不能直接提供完整结构草稿。** 这是本轮目视候选与原图对应的定性判断，不是带真值的召回率结论，也不是对点选模式的结论。

- 图1：5个候选的 bbox 全位于画面边缘/截断区域。左侧截断圆形区域有一个候选，但中央完整圆台、与之相连的细通路、右侧连续曲折沟槽没有成为完整候选。不能说“已把设计结构描全”。
- 图2：候选1/2主要填充右侧下/上两块大平面，边界部分顺着明显的直槽轮廓；候选3与右侧区域重叠，候选4是左上方84px纹理小点。左侧完整圆台及其相连细通路未被单独提取。因此看到了部分结构分隔轮廓，但输出对象并不等于所需结构类。
- 没有证据说明这些候选识别出了残胶。SAM 自动生成器给的是依赖视觉边界的候选区域，不提供这里需要的显影语义；颜色和高内部质量分数都不能把它变成人工标签。

## 问题与下一步

环境通路通过；首次下载停滞已用同一官方文件分段并经官方 SHA256 解决。原生 Windows 明确关闭可选 CUDA 扩展。首次 NumPy 只读警告已通过显式可写副本修复；首次输出和原代码快照保留，修正版在新环境复跑验证。

自动模式候选不足，本轮没有通过放宽阈值或反复挑参数掩盖它。若负责人确认阶段1，下一步才验证点选结构掩码、四类人工审核及按件/批次划分；当前不生成 Labelme、manifest 或种子集，不训练，不进入候选扩展。打印工艺、材料、显影液和合格规格继续待老师确认。

**阶段边界：停在阶段 0，等待负责人确认。**
