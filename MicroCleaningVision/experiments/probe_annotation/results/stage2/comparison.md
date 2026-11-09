# 阶段 2 方法对照

历史图1 / 图2指 realpart_img1 / realpart_img2；本批探针数值按图像ID排序，状态和链接明确标出ID。经典基线来自负责人 2026-11-06 实测；阶段0/1仅引用已有记录，未重跑。若本批改用其它输入，不能直接与旧图数值横比。没有人工真值，本表不含识别精度。

| 方法 | 输出是什么 | 对结构/残胶的定性表现与证据 | 单图耗时 s（按图像ID排序） | 已分配显存峰值 MiB（同序） | 权重大小与许可 |
|---|---|---|---|---|---|
| HSV-red | 二值阈值前景 | 0 / 0 像素；无前景。[负责人交接数值及图证缺口](../../baseline_handoff.md) | 未记录 | 未记录 | 无权重；权重许可不适用 |
| ExG | 颜色指数前景 | 18.2% / 37.4%；面积爆炸，不能当残胶面积。[负责人交接数值及图证缺口](../../baseline_handoff.md) | 未记录 | 未记录 | 无权重；权重许可不适用 |
| LocalContrast | 局部差异前景 | 4.97% / 0.84%；主要跟随结构边缘。[负责人交接数值及图证缺口](../../baseline_handoff.md) | 未记录 | 未记录 | 无权重；权重许可不适用 |
| Otsu | 全局阈值前景 | 0.83% / 0.27%；无残胶语义证据。[负责人交接数值及图证缺口](../../baseline_handoff.md) | 未记录 | 未记录 | 无权重；权重许可不适用 |
| SAM 2 AMG | 独立候选掩码 | 5 / 4 个候选，不足以描全结构；无类别。[图1](../stage0_initial/realpart_img1/overlay_ids.png)、[图2](../stage0_initial/realpart_img2/overlay_ids.png) | 6.522 / 5.133 | 810.331 / 832.585 | 148.781 MiB；代码/权重 Apache-2.0 |
| SAM 2 点选 | 21 实例 v0 草稿；标签由配置提供 | 结构草稿已验收；残胶身份待核、非真值。[图1](../../dataset/seed_v0/realpart_img1/overlay_ids.png)、[图2](../../dataset/seed_v0/realpart_img2/overlay_ids.png) | 1.985 / 2.199（编码+解码） | 阶段1未记录峰值，不能移用AMG值 | 复用 148.781 MiB；代码/权重 Apache-2.0 |
| Micro-SAM LM 点选 | 同点实例掩码 + 同ID模型输出一致性 | realpart_img1=ok；realpart_img2=ok。完成的记录提供同点掩码及对应实例一致性；结构/残胶标签由提示配置指定，不代表模型语义识别。[图证：realpart_img1](micro_sam/realpart_img1/sam2_vs_micro_sam.png)、[图证：realpart_img2](micro_sam/realpart_img2/sam2_vs_micro_sam.png) | 5.627 / 3.069 | 2535.171 / 2535.901 | 357.650 MiB；Micro-SAM MIT / SAM Apache-2.0；权重 CC-BY-4.0 |
| DINOv2 特征 | 384维 patch 特征、PCA伪彩、聚类图、草稿相对距离 | realpart_img1=ok；realpart_img2=ok。完成的记录提供特征可视化及已有草稿的相对距离；颜色分组不等于类别，缺失类别不得用未标像素补造。[图证：realpart_img1](dino/realpart_img1/original_pca_clusters.png)、[图证：realpart_img2](dino/realpart_img2/original_pca_clusters.png) | 2.213 / 1.958 | 145.933 / 145.933 | 84.193 MiB；所用骨干代码/权重 Apache-2.0 |
| PatchCore（DINO变体） | 相对参考库的异常距离热图 | realpart_img1=blocked；realpart_img2=blocked。原因：realpart_img1：No gold-standard clean reference photographs of the same design; real inference is blocked.；realpart_img2：No gold-standard clean reference photographs of the same design; real inference is blocked.。[记录：realpart_img1](patchcore/realpart_img1/metrics.json)、[记录：realpart_img2](patchcore/realpart_img2/metrics.json)合成自检：synthetic_foreign=ok；synthetic_normal=ok；只证明合成流程。[合成图证：synthetic_foreign（synthetic）](patchcore/synthetic_foreign/overlay.png)、[合成图证：synthetic_normal（synthetic）](patchcore/synthetic_normal/overlay.png) | 未推理 / 未推理 | 未推理 / 未推理 | 复用 DINO 84.193 MiB，无新增网络权重；官方PatchCore代码 Apache-2.0 |

## 测量与证据边界

- 阶段2总耗时包含读图、校验、推理、统计、可视化和输出写盘，排除正常load阶段；阶段0/1耗时只含模型编码/解码等对应操作，**不可直接拿不同口径排速度名次**。阶段2纯模型时间另见逐图 metrics。
- PatchCore首次有效图采用延迟加载，duration含骨干加载及参考库构建，reference_setup_seconds另列；参考身份不变时后续图复用参考库。blocked记录的耗时/显存为null，判断耗时另记，绝不伪装推理结果。
- 显存为 PyTorch allocated 峰值，含驻留权重；reserved另在metrics。均不含驱动/context/其他进程；无预热、单次观测，不能当稳定性能基准。
- Micro-SAM/SAM2交并比是两个模型原始对应实例掩码的一致性（含ignore实例），不是对真值的IoU；同时错也可能一致。
- DINO PCA/聚类每图独立，颜色及簇ID无跨图语义对应；未标注区域保持unknown。若基底类缺失，不能用补集补造。
- 旧经典基线原始mask/overlay、运行计时与峰值没有在本次目录交接，明确留空；输入原图链接不能替代算法图证。

## 本批逐图运行记录

| 探针 | 图像 | 状态 | 总耗时 s | allocated / reserved MiB | 记录 |
|---|---|---|---:|---:|---|
| micro_sam | realpart_img1 | ok | 5.627 | 2535.171 / 3422.000 | [metrics](micro_sam/realpart_img1/metrics.json) |
| micro_sam | realpart_img2 | ok | 3.069 | 2535.901 / 3434.000 | [metrics](micro_sam/realpart_img2/metrics.json) |
| dino | realpart_img1 | ok | 2.213 | 145.933 / 164.000 | [metrics](dino/realpart_img1/metrics.json) |
| dino | realpart_img2 | ok | 1.958 | 145.933 / 164.000 | [metrics](dino/realpart_img2/metrics.json) |
| patchcore | realpart_img1 | blocked | — | — / — | [metrics](patchcore/realpart_img1/metrics.json) |
| patchcore | realpart_img2 | blocked | — | — / — | [metrics](patchcore/realpart_img2/metrics.json) |
| patchcore | synthetic_normal | ok | 1.708 | 123.498 / 148.000 | [metrics](patchcore/synthetic_normal/metrics.json) |
| patchcore | synthetic_foreign | ok | 0.094 | 123.498 / 148.000 | [metrics](patchcore/synthetic_foreign/metrics.json) |

详细定性判断、逐实例面积/一致性、参考库解阻命令见 [stage2_report.md](../../stage2_report.md)；逐项来源/许可见 [MODELS.md](../../MODELS.md)。
