# 阶段 2：统一探针框架与三个冻结模型探针

核验日期：2026-11-09。工作分支 `exp/probe-annotation`，基线 HEAD `a5b304903e54e008e948375d84f3f4c04a0290e0`。这是本地未提交实验，运行版本还由 [summary.json](results/stage2/summary.json) 中逐文件源码 SHA256、配置 SHA256、模型 commit 与权重 SHA256 共同固定，不能仅用 Git HEAD 代表本轮脚本。

**本阶段交付已完成：统一框架可运行，Micro-SAM 两图共 21 个实例、DINO 两图特征与 PatchCore 合成自检均完成；真实两图 PatchCore 按要求 blocked。最终批次为 6 ok / 2 blocked / 0 failed。到此停止阶段 2，等待负责人确认。**

这不等于已经具备残胶识别能力。SAM 系列给的是提示条件下的轮廓，DINO 给的是外观特征，PatchCore 给的是相对正常参考的偏离；三者都不能自行证明材料身份或显影合格。残胶定义仍待顾老师确认，种子集 v0 仍为 draft，未升级为真值。

## 1. 交付入口与范围

| 交付 | 内容 / 证据 |
|---|---|
| 统一接口 | [probes/base.py](probes/base.py)：BaseProbe、ImageInput、ModelInfo、ProbeOutput、ProbeResult、ProbeBlocked；统一 load / infer / release |
| 注册与调度 | [registry.py](probes/registry.py)、[runner.py](probes/runner.py)、[run_probes.py](run_probes.py)；[本轮 JSON 配置](configs/stage2.json) |
| 探针模块 | [micro_sam.py](probes/micro_sam.py)、[dino.py](probes/dino.py)、[patchcore.py](probes/patchcore.py)；特征分析见 [feature_utils.py](probes/feature_utils.py) |
| 复现与新增教程 | [README.md](README.md)：环境、单命令运行、测试、三个方法/注册/配置示例、干净参考库接入 |
| 固定官方来源 | [stage2_sources.json](stage2_sources.json)、[setup_stage2.py](setup_stage2.py)、[MODELS.md](MODELS.md)；官方许可与模型卡存入 [logs/stage2](logs/stage2) |
| 全方法对照 | [comparison.md](results/stage2/comparison.md)：四经典基线、SAM 2 AMG、SAM 2 点选、三个新探针 |
| 运行与测试 | [最终运行日志](logs/stage2/run.log)、[核心 34 项测试](logs/stage2/all_tests.log)、[新增对照表回归及框架复查](logs/stage2/comparison_tests.log)、[独立产物验证](logs/stage2/artifact_verification.json) |
| 问题与历史尝试 | [failures.md](failures.md)、[首轮汇总](results/stage2_attempt1/summary.json)、[首轮日志](logs/stage2/run_attempt1.log) |

仅在 `experiments/probe_annotation/` 增加和修改文件。未改主线依赖、contracts、motion_gate 或固件；未训练网络、未操作实物、未提交/推送/提 PR。只运行本阶段相关测试，没有扩大为主线全量回归。既有阶段 0/1 已验收事实仅引用，没有重复验证旧算法表现或改写 seed_v0。

## 2. 框架如何工作

调度流程是配置解析 → 注册表按名取探针 → load 一次 → 各图 infer → release → 汇总。一个探针加载失败会给它的图逐一记录原因，后续探针继续；单图阻塞或失败也不终止整批。正常 release 和异常清理均有生命周期记录。输出目录已存在时拒绝覆盖，输入/输出路径限制在实验范围。

字段参考主线 `contracts.py` 的 `task_id / observation_id / frame_id / raw_image_ref / reason_codes / interface_version` 命名习惯，但保持独立实验契约，不修改、不冒充主线正式接口。每图记录：

- `probe_name`、模型/权重版本、代码/权重许可证、实际权重字节数与 SHA256、实际输入图 SHA256。
- `duration_ms`、`model_load_ms`、CUDA `peak_allocated_mib / peak_reserved_mib`；加载阶段峰值单列在 `lifecycle.json`。
- 原始输出、叠加图等 `output_files` 的路径/实际字节数/SHA256，以及额外的探针特定 `metrics`。
- `status` 只允许 `ok / blocked / failed`，另有 `status_reason / reason_codes`。环境缺失用 `status=blocked, reason_codes=["blocked-env"]`，不引入第四个顶层状态。
- `annotation_status=not_ground_truth`、`test_ground_truth_allowed=false`，避免下游把探针结果误当真值。

落盘为 `results/stage2/<probe>/<image>/metrics.json` 和同目录产物，根目录有 `summary.json / run_table.md / comparison.md`。日志和异常也留在相应目录，不用空热图冒充成功输出。框架用 dummy 覆盖注册、配置调度、结果契约、blocked/failed 隔离，并额外检查导入/释放错误、路径和 GPU 检查顺序；详见 [15 项框架测试](probes/tests/test_framework.py)。

本轮命令为以下配置运行；复跑请使用一个不存在的新输出目录：

```powershell
.\.venv\Scripts\python.exe -B run_probes.py --config configs/stage2.json --output results/stage2_rerun_001
.\.venv\Scripts\python.exe -B -m unittest discover -s probes/tests -p 'test*.py' -v
.\.venv\Scripts\python.exe -B verify_stage2.py --results results/stage2_rerun_001 --log logs/stage2/artifact_verification_rerun_001.json
```

第三条审计前面命令产生的复跑目录；如需审计本次交付，用 `--results results/stage2`。验证脚本要求本阶段相同的八条状态矩阵，它不是任意新探针批次的通用验收器。自定义批次由框架的逐图契约检查负责。

## 3. 环境、资源预算与许可证

复用原 `.venv`：CPython 3.12.4、PyTorch 2.7.1+cu128、torchvision 0.22.1+cu128、NumPy 2.2.6、Pillow 11.2.1。本轮未执行 pip 安装/升级，未新建 `.venv-probes`。新增官方源码固定 commit 放在实验 `vendor/`，以限定模块路径读取；没有把 Micro-SAM GUI/训练依赖或完整 PatchCore 训练包装入已验收环境。

显卡 RTX 5060 Laptop 8 GB，驱动 577.02，nvidia-smi 显示总容量 8151 MiB。GPU 前置检查复用阶段 0 规则，并记录探针加载前、单图前的占用；不终止其他进程。顺序运行三个探针，Micro-SAM 预算 4600 MiB，DINO/PatchCore 预算 2400 MiB；模型/输入大小与预算依据见 MODELS。最终运行无 OOM、无降变体。WDDM 采样和 PyTorch 峰值有不同口径，不能把 allocator 峰值当整卡使用上限。

| 实际使用项 | 官方版本 / 权重 | 代码许可证 | 权重许可证 | 本机字节数 / MiB |
|---|---|---|---|---:|
| Micro-SAM SAM LM Generalist ViT-B | Micro-SAM `cb88e8d662bc0d988ff8d5f6f03d42f13dc07a51`；BioImage.IO `diplomatic-bug/1.2` | Micro-SAM MIT；官方 SAM predictor Apache-2.0 | CC-BY-4.0（模型卡明确） | 375,023,499 / 357.650 |
| DINOv2 ViT-S/14 | `7764ea0f912e53c92e82eb78a2a1631e92725fc8`；distilled LVD-142M，无 registers | 所用 DINOv2 骨干 Apache-2.0 | Apache-2.0 | 88,283,115 / 84.193 |
| PatchCore 式 DINO 变体 | 本地参考库/距离实现；方法参考 Amazon `fcaa92f124fb1ad74a7acf56726decd4b27cbcad`；复用上行骨干 | 官方 PatchCore Apache-2.0；DINO Apache-2.0 | 复用同一 DINO 文件 | 无新增网络权重 |

权重实际 SHA256：

```text
Micro-SAM ffe862fa5b58d78375506041faf0eb3927ced5b5391e8be383e6d7ac07aed924
DINOv2    b938bf1bc15cd2ec0feacfe3a1bb553fe8ea9ca46a7e1d8d00217f29aef60cd9
```

Micro-SAM 从官方 [BioImage.IO 模型卡](https://uk1s3.embassy.ebi.ac.uk/public-datasets/bioimage.io/diplomatic-bug/1.2/files/rdf.yaml)指向的 [vit_b.pt](https://uk1s3.embassy.ebi.ac.uk/public-datasets/bioimage.io/diplomatic-bug/1.2/files/vit_b.pt) 下载，实测 hash 与卡内公布值一致；DINO 从 Meta 官方 [dinov2_vits14_pretrain.pth](https://dl.fbaipublicfiles.com/dinov2/dinov2_vits14/dinov2_vits14_pretrain.pth) 下载，其上述 hash 是本机实测，未冒称上游发布值。完整许可、来源快照、版本和 hash 见 [MODELS](MODELS.md) 与 [setup_verified.json](logs/stage2/setup_verified.json)。

Micro-SAM 官方模型卡的 predictor adaptor 本身使用官方 SAM predictor，因此本轮直接加载相同架构和严格匹配的 state dict，保留点预测能力并避免引入 GUI/训练依赖；不是拿普通 SAM 权重改名。DINO 直接导入固定骨干入口，不执行当前仓库聚合其他模型的 `hubconf.py`，未引入另有许可的 CELL-DINO/XRay 模型。

**AGPL 直接出局规则已执行：代码或权重确认为 AGPL 即不能安装/下载/执行，原因是违反本阶段许可约束。本轮选中版本均非 AGPL，没有实际 AGPL 候选被使用，也不虚构“淘汰了某模型”的记录。** Micro-SAM 旧 Zenodo 页面一度不足以确认许可，未据此下载；改查官方 registry 指向的当前模型卡后确定 CC-BY-4.0。代码许可与权重许可分开登记。

## 4. 逐图数字及口径

所有数值来自最终同一批次 [summary.json](results/stage2/summary.json)，不是挑选首轮较好数字拼表。无预热、各图一次，不能据此估计稳定吞吐。MiB = 2²⁰ bytes。

| 探针 | 输入 | 状态 | 单图总耗时 s | allocated / reserved 峰值 MiB | 部分计算时间 s |
|---|---|---|---:|---:|---:|
| Micro-SAM | 图1，11实例 | ok | 5.627 | 2535.171 / 3422.000 | 编码+逐实例解码 3.235 |
| Micro-SAM | 图2，10实例 | ok | 3.069 | 2535.901 / 3434.000 | 编码+逐实例解码 1.013 |
| DINOv2 | 图1 | ok | 2.213 | 145.933 / 164.000 | 特征提取 0.207 |
| DINOv2 | 图2 | ok | 1.958 | 145.933 / 164.000 | 特征提取 0.087 |
| PatchCore | 真实图1 | blocked | 不适用 | 不适用 | 未推理 |
| PatchCore | 真实图2 | blocked | 不适用 | 不适用 | 未推理 |
| PatchCore | 合成正常查询 | ok | 1.708 | 123.498 / 148.000 | 查询推理 0.048，另含首次参考初始化 1.563 |
| PatchCore | 合成带斑块查询 | ok | 0.094 | 123.498 / 148.000 | 查询推理 0.026，复用参考库 |

`duration_ms` 包含读图、身份校验、计算、可视化和写盘，不含常规 load；CUDA 前后同步。Micro-SAM load 9.664 s、DINO load 1.578 s；PatchCore 的 load 0.166 s 仅登记元数据，实际骨干加载和参考库构建延迟到第一次可推理的合成查询，已包含在该图总耗时，未藏入第二张图。常规 load 的 allocated / reserved 峰值分别为 Micro-SAM 357.586 / 402 MiB、DINO 116.139 / 142 MiB；逐图峰值包括驻留权重，排除驱动/context 和其他进程。

真实 blocked 的推理时间/峰值字段为 null，阻塞判断的少量 CPU 开销另记 `blocked_check_duration_ms`。旧 SAM2 指标来自阶段 0/1 的不同计时范围；**不得据此表直接排序算法速度**。旧经典基线缺计时、峰值和原始图证，阶段 1 缺显存峰值，全部在 comparison 明示缺口，没有补造或重跑。

## 5. Micro-SAM：同点输出一致性，不能解释为精度

复用 `dataset/seed_v0/points/` 的原字节点配置，校验原图 hash、实例 ID/类别、点配置 hash 和 SAM2 mask hash；没有调点、手修掩码、以 IoU 挑候选或使用 SAM2 mask 作模型提示。仍为单候选配置，CUDA 用 bf16 autocast，权重保持 float32。图1 11 个、图2 10 个实例均成功。

下面交并比仅为 `|Micro ∩ SAM2| / |Micro ∪ SAM2|`，两个模型对应的**原始实例掩码一致性**；ignore 实例也原样比较。它不是 against-ground-truth 的分割 IoU，不代表其中任一模型正确；两者同时错也能一致。标签由原点配置提供，模型没有识别材料类别。

| 图 | 实例ID / 草稿标签 | SAM2 面积 px | Micro-SAM 面积 px | 两模型一致性交并比（非精度） |
|---|---|---:|---:|---:|
| 1 | 0001 structure | 4762 | 5454 | 0.8704 |
| 1 | 0002 structure | 6708 | 6754 | 0.7923 |
| 1 | 0003 structure | 8344 | 7721 | 0.8375 |
| 1 | 0004 structure | 7654 | 7696 | 0.8620 |
| 1 | 0005 structure | 6502 | 6459 | 0.8270 |
| 1 | 0010 ignore | 2855 | 4319 | 0.4865 |
| 1 | 0011 ignore | 7606 | 8389 | 0.6665 |
| 1 | 0012 ignore | 477 | 670 | 0.6201 |
| 1 | 0013 ignore | 1324 | 860 | 0.4687 |
| 1 | 0014 ignore | 2231 | 6868 | 0.2338 |
| 1 | 0015 ignore | 5249 | 5402 | 0.4485 |
| 2 | 0001 structure | 10389 | 11536 | 0.8889 |
| 2 | 0002 structure | 2364 | 2934 | 0.7911 |
| 2 | 0003 structure | 19884 | 20013 | 0.8075 |
| 2 | 0010 ignore | 4607 | 3160 | 0.4968 |
| 2 | 0011 ignore | 2471 | 5545 | 0.3773 |
| 2 | 0012 ignore | 7136 | 21152 | 0.1681 |
| 2 | 0013 ignore | 2576 | 1719 | 0.4500 |
| 2 | 0014 ignore | 8905 | 2638 | 0.2326 |
| 2 | 0020 residue | 556 | 358 | 0.6439 |
| 2 | 0021 residue | 326 | 221 | 0.6779 |

图证：[图1并排对照](results/stage2/micro_sam/realpart_img1/sam2_vs_micro_sam.png)、[图2并排对照](results/stage2/micro_sam/realpart_img2/sam2_vs_micro_sam.png)、[图2 ignore 0012](results/stage2/micro_sam/realpart_img2/agreement/0012_ignore.png)、[图2 residue 0020](results/stage2/micro_sam/realpart_img2/agreement/0020_residue.png)。全部候选、面积、原始掩码和逐实例对照保存在各图目录；[图1实例记录](results/stage2/micro_sam/realpart_img1/instances.json)、[图2实例记录](results/stage2/micro_sam/realpart_img2/instances.json)含完整数值。

目视上，两者都能沿圆台和沟槽给出结构草稿；Micro-SAM 在部分沟槽边界和不确定区域改变范围，不能据此断言更准确。图2右侧 ignore 0012 从 7136 px 扩至 21152 px，并纳入较大片平面；图1 ignore 0014 也明显扩大，图2 ignore 0014 则明显缩小。残胶草稿两实例范围比 SAM2 小，属于待复核分歧。**显微预训练不自动意味着在这些实拍上优于 SAM2**；当前价值是提供第二个轮廓建议和分歧图，帮助人工聚焦复核，不替换已验收 seed_v0。

## 6. DINO：有外观响应，三类可分性仍未得到证据

采用 ViT-S/14 的 384 维冻结 patch 特征。原图 640×480 不缩放，仅右/下边缘 padding 至 644×490，网格 35×46，共 1610 个 patch。每图独立 PCA 三通道、独立 k=4 无标签聚类，并保留 `features.npy / pca_projection.npz / cluster_patch_ids.png / cluster_centers.npy` 等原始分析产物。PCA 和聚类不更新网络参数，不拟合类别预测器；四种颜色不是项目四类。

图证：[图1原图/PCA/聚类](results/stage2/dino/realpart_img1/original_pca_clusters.png)、[图2原图/PCA/聚类](results/stage2/dino/realpart_img2/original_pca_clusters.png)。图中沟槽走向、圆台轮廓与大尺度外观差异可辨，但圆台内部和周围区域常属同簇，图2残胶团块没有形成清晰独立的“残胶簇”。前三主成分解释方差分别约 38.31% / 37.11%；三通道图不代表完整 384 维特征，不能据颜色混杂直接否定其全部用途。

统计只引用 seed_v0 草稿：先合并同类 mask，排除 ignore 和类别冲突，未标注仍为 unknown。对 patch 特征单位归一化，按该类有效像素覆盖量加权求中心；类内为样本到归一化类中心的加权余弦距离均值，类间为两个归一化类中心的余弦距离。像素共享 patch 特征，不能把数百像素当数百独立样本。

| 图 / 草稿类 | 有效像素 | 有贡献 patch 数 | 类内余弦离散 | 限制 |
|---|---:|---:|---:|---|
| 图1 structure | 20144 | 247 | 0.255242 | 无 residue / substrate 草稿，不能做三类比较 |
| 图2 structure | 25751 | 221 | 0.261943 | 仅草稿统计 |
| 图2 residue | 851 | 12 | 0.150194 | 有效 patch 数 7.592；面积仅相当 4.342 个完整 patch |
| 两图 substrate | 无 | 无 | 不适用 | 不把掩码补集补造为基底 |

图2 structure/residue 中心余弦距离 **0.391011**，两类平均类内离散 **0.206068**，两者相对比 **1.897486**。这是描述性几何差异，不是“通过可分性阈值”，不是显著性检验或识别精度。residue 仅 4 个 patch 的像素占比过半，且 4 个 patch 同时贡献 structure，混合 patch 和全局上下文会影响统计。详见 [图1 metrics](results/stage2/dino/realpart_img1/metrics.json)、[图2 metrics](results/stage2/dino/realpart_img2/metrics.json) 的 `draft_feature_statistics`。

对“将来值不值得拿 DINO 当微调底座”的回答：**值得保留为小规模受控比较候选，目前不能批准其优于其他底座或已具备三类分离能力。** 支持保留的证据是外观结构有响应、图2两个草稿类中心存在相对差异；阻止更强结论的是两图样本量、残胶定义未定、基底草稿缺失、混合 patch 和草稿边界。后续需在人工核验且包含三类的多件/多批次区域上比较相同表征统计与近邻案例；本阶段没有启动微调或分类器。

## 7. PatchCore：合成通路通过，真实图保持 blocked

实现采用 **PatchCore 式 DINO 变体**：冻结 DINO 最后一层 patch → 3×3 局部均值 → 最多 128 向量的唯一 farthest-first 核心集 → 分块精确最近邻欧氏距离 → 双线性上采样。它没有使用原论文常见的 WideResNet 多层卷积特征、随机投影近似核心集或图像级重加权，不能称原论文配置复现或引用其性能作本项目证据。参考库存储/选子集不更新神经网络、分类器或阈值。

[合成数据](dataset/stage2_synthetic)由固定随机种子和周期纹理生成：正常参考 `normal.png` 为 256×256；正常查询 `query_normal.png` 为独立 280×280 纹理；带异物查询在正常查询上加入三块异物，脚本同时提供 `synthetic_mask.png`。参考与查询不是同一文件或同一像素副本。[生成器](probes/make_synthetic.py)可复建且拒绝覆盖已有图像。所有这些文件明确标记 synthetic，不用于真实残胶结论。

| 合成检查 | 实测 |
|---|---:|
| 正常查询热图最大原始距离 | 28.816294 |
| 带斑块查询热图最大原始距离 | 51.091328 |
| 带斑块查询：脚本斑块内平均距离 | 45.589630 |
| 带斑块查询：脚本斑块外平均距离 | 26.598511 |
| 内 / 外平均距离比 | 1.713992 |
| 预先指定的两个自检断言 | foreign_max > normal_max；inside_mean > outside_mean，均通过 |

证据：[正常查询热图](results/stage2/patchcore/synthetic_normal/heatmap.png)、[带斑块叠加](results/stage2/patchcore/synthetic_foreign/overlay.png)、[原始热图/结果](results/stage2/patchcore/synthetic_foreign/metrics.json)、[独立复算](logs/stage2/artifact_verification.json)。两查询绑定相同 `memory_bank_identity`。距离是特征空间数值，不是概率或材质类别。

圆斑附近有高响应，矩形与三角斑块附近的响应连成片且向外扩散，符合 patch、3×3 平滑和上采样后的粗粒度响应。两图分别用自身 p99 色标显示，**不能跨图按红黄颜色比较异常严重性**；应看原始距离。斑块外均值也高于正常查询整体均值 20.359213，可能受 DINO 全局上下文及局部平滑影响。合成自检只证明流程能产生合理方向的异常响应，不证明精确轮廓、没有误报或真实残胶可检。

真实图1/2均为 `status=blocked, reason_codes=["missing-clean-reference"]`，没有热图：[图1阻塞记录](results/stage2/patchcore/realpart_img1/metrics.json)、[图2阻塞记录](results/stage2/patchcore/realpart_img2/metrics.json)。没有把带残胶待测图、其改名副本或合成纹理自充金标准正常库。

**解阻条件**：提供同设计、经人工/工艺验收彻底显影干净的件若干张照片，覆盖正常倍率/光照/焦面变化，保存实际设计 ID、确认人与判断依据。按 [README 的 manifest 格式](README.md)写入 `dataset/clean_reference/manifest.json`，登记每图真实 SHA256，并把 [configs/patchcore_clean.json](configs/patchcore_clean.json) 中待测设计 ID 改成核验后的匹配值。然后执行：

```powershell
.\.venv\Scripts\python.exe -B run_probes.py --config configs/patchcore_clean.json --output results/patchcore_clean_run_001
```

程序拒绝缺确认、设计不一致、hash不符、已知合成图和待测图充当参考；声明本身仍须由人如实提供，软件不能证明一张新照片的件已经干净。即使解阻，异常也可能来自光照、形貌等变化，不能自动等同残胶。

## 8. 验证、失败处理与现存缺口

相关测试共 **43 个不同测试用例通过**：核心 34 项在 8.457 s 内通过（[日志](logs/stage2/all_tests.log)），包括框架 dummy 15 项、Micro-SAM 3 项、DINO 6 项、PatchCore 10 项；最终对照表修复新增 9 项，并连同框架 15 项复查，24 项在 3.084 s 内通过（[日志](logs/stage2/comparison_tests.log)）。没有把两次重复执行的 15 项加进“不同用例”数量。覆盖异常隔离、原点配置/输入匹配、草稿掩码规则、确定性降维/聚类、参考库隔离、query 冒充 normal 防护、缓存身份、核心集唯一性、未来参考库解阻后的正确报告等。没有把 mock 测试当 CUDA 实测；实际 CUDA 证据是独立的最终运行目录。

独立产物核验通过：[verify_stage2.py](verify_stage2.py) / [结构化日志](logs/stage2/artifact_verification.json)。核对八条状态矩阵、129 条产物条目的 hash/大小、输入/权重身份；从 21 个实际掩码重算面积与两模型一致性，从原始 DINO 特征重算草稿统计，从 `.npy` 重算两个合成断言。范围审计对比开始前快照：**284 个阶段 0/1 受保护文件未变、380 个 Git 跟踪文件未变、已安装包版本集合未变**。README / MODELS / failures 为实验共享说明，本阶段只增加/更新对应阶段说明，不改旧实验证据。

首轮曾得到 5 ok / 3 blocked / 0 failed：Micro-SAM 图2在推理前被资源门槛挡住，4235 MiB 可用显存低于预设 4600 MiB。原因是框架先查卡空闲、后释放本进程上一图缓存；不是 OOM。修正为同步、释放空闲缓存、检查资源、重置峰值，再推理；新增顺序回归。预算没有降低，首轮目录完整保留，修正后才执行最终整批，未覆盖旧证据。具体见 failures。

GPU 运行结束后的审查还修正了对照表生成器：状态与原因改为随实际记录生成，避免日后真实 PatchCore 已成功却仍显示缺参考阻塞，并补充明确标为 synthetic 的直接图证。运行时原始生成器保存为 [comparison_run_source.py](logs/stage2/comparison_run_source.py)，其 SHA256 与 summary 内记录一致；新版仅重生成 Markdown，不改模型输出或运行 summary，不重跑 GPU。后续文件差异与文档链接检查见 [final_delivery_check.json](logs/stage2/final_delivery_check.json)。

仍然存在的证据缺口：

1. 四个经典基线仅有负责人 2026-11-06 交接数值；旧 mask/overlay、耗时与显存未随当前目录交接。本轮遵照“不重复验证”未重跑，comparison 链接 [baseline_handoff.md](baseline_handoff.md)并明确缺失；这几行暂不能提供算法输出图证。
2. 阶段 1 未记录峰值显存，不挪用阶段 0 AMG 数字。历史计时与本轮口径不同。
3. 两张实拍、draft 类别与实例不支持精度或泛化结论；无 substrate 草稿，residue 定义仍待确认。
4. PatchCore 真实参考库缺失是数据条件阻塞，不是环境失败；本轮三个探针均无未解决的 blocked-env。

## 9. 本阶段结论与后续关卡

统一框架已经把“模型是否跑通、用了什么输入/权重、输出是什么、何时必须阻塞”变成可审计的统一记录，这是本阶段的主要工程成果。与阶段 1 的联系是直接复用已验收点配置和草稿身份，增加模型分歧与特征分析，没有把草稿悄悄提升成标签真值。

方法上，Micro-SAM 可作轮廓分歧复核工具，尚不能替换 SAM2；DINO 可保留为后续表征候选，三类可分尚未成立；PatchCore 的下一步瓶颈是干净参考件，而非继续对当前两图调参。负责人确认后再分别推进残胶定义/基底与残胶人工核验、同设计干净参考采集、受控表征比较。**本轮不执行这些后续任务，报告完成后停止。**
