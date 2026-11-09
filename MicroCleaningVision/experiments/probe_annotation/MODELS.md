# 阶段 0 模型登记

阶段 0/1 核对日期：2026-11-09；该阶段仅使用 SAM 2。阶段 2 新增登记见文末，具体运行状态以 stage2_report.md 和逐图 metrics 为准。AGPL 模型未使用。

| 项目 | 固定来源 / 许可 / 口径 |
|---|---|
| 模型 | SAM **2.1 Hiera tiny**，SAM 2 系列最小变体 |
| 代码 | [facebookresearch/sam2 @ 2b90b9f5ceec907a1c18123530e92e794ad901a4](https://github.com/facebookresearch/sam2/tree/2b90b9f5ceec907a1c18123530e92e794ad901a4)，安装包名 SAM-2 1.0；包版本不能单独区分源码 |
| 代码许可证 | [固定版 LICENSE：Apache 2.0](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/LICENSE) |
| 权重 | [Meta 官方 sam2.1_hiera_tiny.pt](https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt)，[固定版官方下载清单](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/checkpoints/download_ckpts.sh) |
| 权重许可证 | [官方 README 的 License 节](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/README.md#license)：明确代码及 model checkpoints 适用 Apache 2.0；另有第三方许可说明，不能只看仓库徽章 |
| 第三方 | [LICENSE_cctorch：BSD-3-Clause](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/LICENSE_cctorch)，关联可选连通域扩展；本次未编译；SAM README 还列出示例字体 OFL-1.1，未使用示例字体 |
| config | `configs/sam2.1/sam2.1_hiera_t.yaml` |
| 权重 SHA256 | `7402e0d864fa82708a20fbd15bc84245c2f26dff0eb43a4b5b93452deb34be69`；本机下载实测，非声称官方发布的校验值，重建时固定核验 |
| 权重文件体积 | 实测 156,008,466 bytes（约 148.781 MiB）；这是 checkpoint 体积，不含 Python/PyTorch 环境，不代表 ONNX 文件大小 |
| 参数量 | 实测 `sum(p.numel())` = **38,962,498**，含本次加载模型的所有模块；来源 `results/stage0_initial/metrics.json` |
| 状态 | **可用：两图 CUDA 推理完成；结构自动候选质量不足**。可用仅指环境与推理通路，非结构/残胶识别验收 |
| 角色 | 离线结构候选 / 辅助标注，不直接判残胶，不训练 |

PyTorch 版本选用依据：[官方 2.7.1 安装组合](https://pytorch.org/get-started/previous-versions/#v271)；[PyTorch 2.7 发布说明](https://pytorch.org/blog/pytorch-2-7/)说明 CUDA 12.8 / Blackwell 支持。[SAM 2 安装文档](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/INSTALL.md)说明版本限制、Windows/WSL 与可选扩展。[setup.py](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/setup.py)定义 `SAM2_BUILD_CUDA=0`。

本实验不改变或重新授权上游代码/权重；若将来再分发，需保留上游 LICENSE、NOTICE 与适用第三方告知。实际参数量、运行状态与每图数字由阶段报告引用 `metrics.json`，不拿官方视频 FPS 代替本次自动掩码耗时。工控机 CPU/ONNX 可部署性尚未验证。

## 阶段 1 补充登记（2026-11-09）

SAM 模型、源码、权重、许可证与上述固定版本完全相同；复用 `.venv` 与本地权重，无新模型/训练。阶段 1 用 `SAM2ImagePredictor` 点提示，不使用 AMG 候选充当标注；语义类别由配置作者指定，输出是未经人工核验的草稿。

另下载 Labelme 官方 `examples/utils.py` 作**独立格式读取验证**，没有安装 Labelme 应用或改运行依赖。固定上游 commit `d187c87b7d49e2e69fd86ae3b5c25bbcc4b0bd63`；[源码](https://github.com/wkentaro/labelme/blob/d187c87b7d49e2e69fd86ae3b5c25bbcc4b0bd63/examples/utils.py)、[许可证 GPL-3.0-only](https://github.com/wkentaro/labelme/blob/d187c87b7d49e2e69fd86ae3b5c25bbcc4b0bd63/LICENSE)。该原样参考代码、完整 LICENSE、URL 和 SHA256 均保存在 `logs/labelme_format_reference/`，不复制进 `annotate/` 的推理/导出实现。官方读取器用于核验嵌入 mask 的像素和 ignore 顺序；它不是新增模型，也不是 GUI 已运行的证据。

## 阶段 2：代码与权重分别登记（2026-11-09）

固定来源、版本、文件体积与实测 SHA256 的机器可读清单：[stage2_sources.json](stage2_sources.json)；本机复核：[setup_verified.json](logs/stage2/setup_verified.json)。所有下载只使用作者官方仓库、Meta 官方文件站或由 Micro-SAM 官方注册表明确指向的 BioImage.IO/EBI 存储。

### Micro-SAM LM Generalist ViT-B v1.2

| 项目 | 登记 |
|---|---|
| 微调模型发布方 | Computational Cell Analytics，模型 ID `diplomatic-bug` / version `1.2`；用于 light microscopy，非专门微纳打印显影模型 |
| Micro-SAM 代码 | [固定 commit cb88e8d](https://github.com/computational-cell-analytics/micro-sam/tree/cb88e8d662bc0d988ff8d5f6f03d42f13dc07a51)，[MIT LICENSE](https://github.com/computational-cell-analytics/micro-sam/blob/cb88e8d662bc0d988ff8d5f6f03d42f13dc07a51/LICENSE)；当前官方代码不是 AGPL |
| 实际推理代码 | [Meta Segment Anything @ dca509fe](https://github.com/facebookresearch/segment-anything/tree/dca509fe793f601edb92606367a655c15ac00fdf)，[Apache-2.0](https://github.com/facebookresearch/segment-anything/blob/dca509fe793f601edb92606367a655c15ac00fdf/LICENSE)；加载 `vit_b` 架构后严格加载下述显微微调权重，使用官方 `SamPredictor` |
| 为什么可直接适配 | 官方模型包 [PredictorAdaptor](https://uk1s3.embassy.ebi.ac.uk/public-datasets/bioimage.io/diplomatic-bug/1.2/files/predictor_adaptor.py) 本身就是 `SamPredictor(sam_model_registry['vit_b']())` 加同一 state dict；其源码 SHA256 `a5870bb3df17bf3f58dc95fd2d30c9078217c30f41ed4b2639c14728097552e1` 与模型卡匹配。没有安装 napari、训练栈或改上游源码；只用其点选推理语义 |
| 权重官方链 | [Micro-SAM固定注册表](https://github.com/computational-cell-analytics/micro-sam/blob/cb88e8d662bc0d988ff8d5f6f03d42f13dc07a51/micro_sam/util.py) → [BioImage.IO v1.2 模型卡](https://uk1s3.embassy.ebi.ac.uk/public-datasets/bioimage.io/diplomatic-bug/1.2/files/rdf.yaml) → [vit_b.pt](https://uk1s3.embassy.ebi.ac.uk/public-datasets/bioimage.io/diplomatic-bug/1.2/files/vit_b.pt) |
| **权重许可证** | 模型卡明确 `license: CC-BY-4.0`；[本地原始模型卡](logs/stage2/microsam_rdf.yaml)。这不是从代码 MIT 推断出来的许可。引用作者/论文并保留权重来源和修改说明；本轮不修改权重 |
| 署名 | Anwai Archit、Constantin Pape 等；模型卡指定 [Segment Anything for Microscopy, Nature Methods](https://doi.org/10.1038/s41592-024-02580-4) |
| 本地文件 | `weights/micro_sam_vit_b_lm_1.2.pt`，**375,023,499 bytes**；权重文件，不含环境 |
| SHA256 | `ffe862fa5b58d78375506041faf0eb3927ced5b5391e8be383e6d7ac07aed924`；本机实测，并与官方模型卡 `weights.pytorch_state_dict.sha256` 一致 |
| 资源计划 | batch=1，原图点坐标，官方1024输入变换；约94M参数的float32常驻权重约0.36GiB，bf16中间计算；全局注意力矩阵额外约0.38GiB/个，预留 **4600 MiB** 可用显存才启动。估计不是实测峰值，实际逐图数字见报告 |

检索时旧 Zenodo 10524791 记录没有明确显示权重许可，**未下载该旧文件**。继续追到当前固定注册表的 v1.2 模型卡后取得明确 CC-BY-4.0 和官方 SHA256；没有因旧页面缺字段而把模型误写为 AGPL，也没有使用许可不明权重。

### DINOv2 ViT-S/14 distilled，LVD-142M，无 registers

| 项目 | 登记 |
|---|---|
| 官方代码 | [facebookresearch/dinov2 @ 7764ea0f](https://github.com/facebookresearch/dinov2/tree/7764ea0f912e53c92e82eb78a2a1631e92725fc8)，[LICENSE Apache-2.0](https://github.com/facebookresearch/dinov2/blob/7764ea0f912e53c92e82eb78a2a1631e92725fc8/LICENSE) |
| 权重 | [Meta 官方 dinov2_vits14_pretrain.pth](https://dl.fbaipublicfiles.com/dinov2/dinov2_vits14/dinov2_vits14_pretrain.pth)，[固定版 README 模型表](https://github.com/facebookresearch/dinov2/blob/7764ea0f912e53c92e82eb78a2a1631e92725fc8/README.md) |
| **权重许可证** | 同一 README 的 License 节明确 **code and model weights** 为 Apache-2.0；没有使用分类头、分割头或其它衍生模型 |
| 本地文件 | `weights/dinov2_vits14_pretrain.pth`，**88,283,115 bytes** |
| SHA256 | `b938bf1bc15cd2ec0feacfe3a1bb553fe8ea9ca46a7e1d8d00217f29aef60cd9`；本机下载实测并固定，未声称这是上游发布的校验值 |
| 源码执行范围 | 直接导入官方 `dinov2.hub.backbones.dinov2_vits14(pretrained=False)`，strict加载固定权重；不执行 `hubconf.py`，不导入当前仓库中另有许可的 CELL-DINO/XRay 等入口。关闭可选xformers，不安装额外依赖 |
| 资源计划 | 640×480仅右/下padding到644×490，35×46=1610个patch、384维；float32，batch=1，权重约84MiB，注意力矩阵约60MiB/个，预留 **2400 MiB** 可用显存。单次分配/保留峰值见metrics |
| 岗位 | 冻结特征底座候选；PCA和无语义聚类只是描述性分析，不训练分类器，不更新网络参数 |

### PatchCore：采用 DINO 特征的本地变体

| 项目 | 登记 |
|---|---|
| 方法来源 | [Towards Total Recall in Industrial Anomaly Detection](https://arxiv.org/abs/2106.08265)，[Amazon官方代码](https://github.com/amazon-science/patchcore-inspection) |
| 官方代码许可证 | [固定 commit fcaa92f1 的 Apache-2.0 LICENSE](https://github.com/amazon-science/patchcore-inspection/blob/fcaa92f124fb1ad74a7acf56726decd4b27cbcad/LICENSE)；[本地原文](logs/stage2/patchcore_official_LICENSE)及[来源hash](logs/stage2/license_evidence.json)。本轮未安装官方整套包、未复制其训练/评价入口，局部实验按方法实现 |
| 实际实现 | 冻结DINO patch特征 → 3×3局部聚合 → farthest-first coreset（覆盖特征空间的子集，最多128向量）→ 精确最近邻欧氏距离 → 热图；网络不训练。参考库构建是存储/子集选择，不是更新网络权重 |
| 骨干代码与权重 | 复用上节同一个 DINOv2 文件、SHA256、Apache-2.0 许可，无新增 checkpoint；未把 torchvision 代码许可冒充其预训练权重许可 |
| 与论文区别 | 未使用论文常见 WideResNet 多层卷积特征、随机投影近似核心集或论文的图像级重加权；采用DINO最后层特征、精确贪心子集与原始最近邻距离。称 **PatchCore式 DINO 变体**，不冒充原论文配置/性能 |
| 数据边界 | 合成纹理参考库只用于合成查询；真实两图因无金标准干净件参考库而blocked。synthetic、待测原图副本、query/reference相同hash均不能成为真实正常库 |

**AGPL 规则**：任何代码或权重一经确认为 AGPL 即在安装/下载/执行前出局；理由是违反本阶段明确许可约束。本轮选定版本没有 AGPL 项，因而不虚构某个候选“因AGPL被淘汰”。代码和权重许可逐项独立核查，非“仓库开源便能任意使用”。本地新代码不重新授权上游代码、权重或训练数据。
