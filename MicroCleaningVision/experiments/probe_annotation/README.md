# 离线标注与探针实验：阶段 2

本目录独立于主程序。阶段 0 环境冒烟与阶段 1 点选工具/两图种子草稿已由负责人验收。当前只执行阶段 2：统一探针框架、Micro-SAM 点选、DINO 特征与 PatchCore 合成自检/真实数据阻塞。**阶段 2 报告完成后停止，等待负责人确认**。既有 `.venv` 与 SAM 2 权重保留，没有安装/升级依赖。

阶段 2 见 [stage2_report.md](stage2_report.md)、[方法对照](results/stage2/comparison.md)。阶段 1 原交付保留：[stage1_report.md](stage1_report.md)、[dataset/seed_v0/README.md](dataset/seed_v0/README.md)。种子集仍是 **draft / 未经人工核验 / 不得用作测试真值**。不是训练成果、物理显影效果或自动语义识别验收。阶段 0 的自动掩码（AMG）仅保留作历史证据，**没有进入阶段 1 标注输入**。

项目任务是微纳 3D 打印后处理显影；SAM 的预定岗位是结构描绘和辅助标注。这里的自动掩码没有 `structure/residue/substrate/ignore` 类别，不能作为人工真值或评测答案。打印工艺、材料、显影液、合格规格均**待老师确认**。

## 阶段 2：一个配置运行全部探针

在本实验目录执行；复用已验收 `.venv`。当前依赖已足够：Micro-SAM 使用官方显微微调权重加官方 SAM 点选推理源码，DINO 只导入官方骨干；不安装GUI、训练栈、xformers、sklearn或其它环境依赖。**没有发生依赖冲突，因此未建立 `.venv-probes`**。后续新探针若需冲突依赖，须另建此隔离环境，不能在现有 `.venv` 安装覆盖。

```powershell
# 只下载/核对固定官方源码与权重，不调用pip、不改阶段0/1依赖；已有文件逐项校验
.\.venv\Scripts\python.exe -B setup_stage2.py
# 新机器首次才生成合成fixture；本机已有，重跑会拒绝覆盖
.\.venv\Scripts\python.exe -B -m probes.make_synthetic
# 一条命令：Micro-SAM两图、DINO两图、PatchCore两实拍blocked与两合成图
# 已有results/stage2时使用新输出名，以保留历史结果
.\.venv\Scripts\python.exe -B run_probes.py --config configs/stage2.json --output results/stage2_rerun
# 针对性框架/探针测试，不调用实物或GPU模型
.\.venv\Scripts\python.exe -B -m unittest discover -s probes/tests -p 'test*.py' -v
# 独立读回产物、重算一致性/特征统计、验证合成断言与阶段0/1保护快照
.\.venv\Scripts\python.exe -B verify_stage2.py --results results/stage2_rerun --log logs/stage2/artifact_verification_rerun.json
```

`configs/stage2.json` 是完整配置，所有相对路径以**实验根目录**为基准。所有正式探针都必须显式 `device: "cuda"` 或 `"cpu"`，无隐式GPU或CPU降级。结果保存于 `<output>/<probe>/<image>/`：原始输出、叠加图、`metrics.json` 和 GPU 检查快照；根目录写 `config.json`、`summary.json`、`run_table.md`、`comparison.md`。已有输出目录一律拒绝。CLI 先完成全部任务，出现failed最后退出码1；只有可解释的blocked仍为退出码0，须查看汇总而非只看进程退出码。

统一结果使用 `interface_version=probe-result-v1`，沿用主线 contracts 的 `raw_image_ref`、`*_version`、`duration_ms`、`reason_codes`、`started_at/ended_at` 风格，但**不导入/修改主线合同**。每条包含：`probe_name`、输入sha、`model_info`（模型/权重版本、权重sha、体积及各自许可）、逐图耗时、allocated/reserved显存峰值、带sha/bytes的输出清单、`status ∈ {ok, blocked, failed}` 与原因。`blocked-env`、`missing-clean-reference` 等是 `reason_codes`，不是破坏三值枚举的第四种状态。

- GPU只顺序跑一份模型，复用阶段0 `gpu_guard`，load前、每图前检查外来计算进程；读取free/total并与配置预算比较。Micro-SAM预留4600MiB、DINO/PatchCore 2400MiB。检查失败就blocked，不结束他人进程。OOM记录为failed/cuda-oom、释放资源，不能静默换变体或把失败改成功。
- `duration_ms` 是同步包围整个infer的墙钟时间，包含读盘、校验、运算和落盘；`model_load_ms` 单列。Micro-SAM另有纯编码+解码时间，DINO另有forward+特征转CPU时间。PatchCore首次合成图的infer包含延迟加载/参考库构建，该时间额外列出。
- 峰值含驻留权重、不含驱动/context/其它进程；load峰值在 `<probe>/lifecycle.json`。真实blocked的推理时间、显存是null，判断耗时单列；不能拿CPU的null理解为0MiB实测。
- ignore优先，剩余类别冲突排除；未标像素为unknown。当前v0无substrate、图1无residue，三类特征可分性因此不完整。PCA/聚类按图独立计算，颜色没有跨图类别含义。模型一致性交并比只比较两模型原掩码，非精度。

## 如何新增一个探针

1. 在 `probes/my_probe.py` 实现 `BaseProbe` 的三个方法：`load()` 加载/设置 `self.model_info`；`infer(image, output_dir)` 保存单图输出并返回 `ProbeOutput`；`release()` 释放资源，须允许部分load失败。模型依赖在load内导入，防止一个可选依赖拖垮其它探针。
2. 类上写一行 `@register("my_probe")`，然后在 `registry.py` 的 `BUILTINS` 映射增加 `"my_probe": "my_probe"`（加载器会加上 `probes.` 前缀）。注册只决定路由，不替代独立的模型/权重许可登记。
3. 配置 `probes` 增加一段 `{ "name": "my_probe", "params": {"device":"cpu"}, "images": ["realpart_img1"] }`。需要GPU时显式cuda和预算；当前框架对三个内置GPU探针强制device，新模块也应遵守同一约定。

```python
from .base import BaseProbe, ModelInfo, ProbeOutput, ProbeBlocked
from .registry import register

@register("my_probe")
class MyProbe(BaseProbe):
    def load(self):
        # 验证官方来源、版本、实际权重sha/size；填入ModelInfo
        self.model_info = ModelInfo(model_name="my-model", model_version="fixed-version")
    def infer(self, image, output_dir):
        # image.path是真实Path，image.metadata保留points_ref/seed_dir等声明
        # 若缺必要条件：raise ProbeBlocked("具体解阻条件", "blocked-env")
        (output_dir / "note.txt").write_text("example only", encoding="utf-8")
        return ProbeOutput(metrics={"example_only": True}, output_files=["note.txt"])
    def release(self):
        pass
```

`output_files` 相对单图目录；框架核查存在性并生成实际哈希清单。通常抛 `ProbeBlocked` 记录缺前提，意外异常由框架记录failed及traceback；不要在探针内吞异常然后返回ok。具体dummy示例和失败隔离测试见 [test_framework.py](probes/tests/test_framework.py)。

## PatchCore 真实图解阻

需要**同设计、经人工/工艺验收彻底显影干净的件若干张照片**，记录设计ID、拍摄倍率/光照/焦面、确认人和判断依据；覆盖正常成像变化。当前两张待测图、它们改名副本、合成纹理都不是正常库。照片数量须能覆盖正常变化，本阶段不把任意张数宣称为充分样本量。

当前 DINO 的 **2400 MiB 资源预算按 640×480 输入**制定并验证。后续参考照片应保持与查询图一致的已约定采集分辨率和尺度；任意高分辨率输入不在本轮资源验证范围内，需要重新估算显存，不能仅沿用该预算。程序不会替人判断拍摄尺度是否相同。

拿到后在 `dataset/clean_reference/` 保存原图与 `manifest.json`，格式如下（示例不代表已经取得人工验收，不要照抄虚假身份）：

```json
{"schema":"patchcore-reference-v1", "synthetic":false,
 "design_id":"由负责人确认的设计ID", "verified_clean":true,
 "verified_by":"实际确认人及记录引用",
 "images":[{"raw_image_ref":"dataset/clean_reference/clean_01.png",
            "sha256":"该文件实际SHA256", "design_id":"同一设计ID"}]}
```

把 `configs/patchcore_clean.json` 两张query的 `metadata.design_id` 改成经确认且与库一致的ID；该配置已关闭synthetic模式并指向上述manifest。随后一条命令：

```powershell
.\.venv\Scripts\python.exe -B run_probes.py --config configs/patchcore_clean.json --output results/patchcore_clean_run_001
```

新参考图不在库中真实存在、hash不对、设计不一致、合成标志或query自充参考均会拒绝。JSON声明只是可追溯的人工输入，程序不能替人证明材料已经显影干净。输出仍是相对参考的异常距离，不自动解释为残胶，也不自动取得真值。

## 阶段 1：图像 + 点配置 → 实例草稿

从本实验目录执行。工具不依赖 Labelme、OpenCV 或 skimage；只用阶段 0 已有运行依赖。`annotate.predict` 先用 `SAM2ImagePredictor.set_image(RGB)` 编码一次，再逐实例调用 `predict(point_coords, point_labels)`；不是自动生成器。上游接口参考 [固定版 predictor](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/sam2/sam2_image_predictor.py)。

配置为 UTF-8 JSON，最小例子（hash 必须换成输入图的真实 SHA256）：

```json
{
  "schema": "sam2-points-v1",
  "image_sha256": "图片文件的完整sha256",
  "annotations": [
    {
      "id": 1,
      "label": "structure",
      "name": "visible circular platform top",
      "positive": [[160, 256], [137, 235]],
      "negative": [[242, 255]],
      "multimask": false,
      "candidate_index": 0
    }
  ]
}
```

- 坐标是**原图像素 `[x,y]`**：左上原点，x 向右、y 向下；不使用预览缩放后的坐标。正点至少一个；负点可以为空。越界、重复、正负矛盾、非有限坐标、重复 ID、未知类别均拒绝。
- 类别只允许 `structure / residue / substrate / ignore`。类别由点配置的作者指定，SAM 只预测视觉范围；深色本身不意味着残胶。当前残胶仅是图 2 两处明显深绿团块的视觉草稿，材料身份未经人工核验。
- `id` 是**同图唯一的正整数**，不随标签改变而重用；PNG 文件名用四位补零，Labelme `group_id=id`，叠加图显示同一编号。同一实体分段提示时是不同“标注实例”，不宣称不同物理对象；图 1 的 0003/0005 就是同一连续沟槽的上下段。跨图 ID 不代表跨图配对。
- 默认单掩码 `multimask=false, candidate_index=0`；可显式设 `multimask=true` 并选择 0/1/2，每个候选都保留。内部质量分数只作模型记录，不能当实测精度。当前 v0 全部用单掩码，无 box、无外来 mask 输入、无动态后处理/填孔/轮廓简化。

一个可直接复跑的新会话示例：

```powershell
.\.venv\Scripts\python.exe -m annotate.session start --image inputs/realpart_img1.jpg --session results/my_annotation/session.json --actor human_or_agent_name
# 从此开始选择/编辑点。可用现有点配置回放；若要测选点时间，必须在start之后才开始选点。
.\.venv\Scripts\python.exe -m annotate.session record --session results/my_annotation/session.json --config dataset/seed_v0/points/realpart_img1.json
.\.venv\Scripts\python.exe -m annotate.predict --image inputs/realpart_img1.jpg --config dataset/seed_v0/points/realpart_img1.json --output results/my_annotation/review_r1 --session results/my_annotation/session.json
# 看overlay_ids.png、points.png、单实例masks；需要修点时，每次修改后重新record，再换新output。
.\.venv\Scripts\python.exe -m annotate.predict --image inputs/realpart_img1.jpg --config dataset/seed_v0/points/realpart_img1.json --output results/my_annotation/final --session results/my_annotation/session.json --final
```

输出目录必须不存在。最终导出后计时会话关闭，不能悄悄覆盖结果。若已导出后发现问题，先把原导出**保留到新路径**，再显式恢复计时，记录原因和历史；起始时间不重置：

```powershell
.\.venv\Scripts\python.exe -m annotate.session resume --session results/my_annotation/session.json --archived-output results/my_annotation/archived_final --reason '具体修复原因'
```

计时使用工具记录的单调时钟，跨进程保留；检查它与 UTC 墙钟差是否异常。`start` 到最终导出是包含审查、修点、模型加载、等待和修复的总墙钟时间，**不是纯推理耗时，也不是人工有效点击工时**。`record` 保存每版配置全文、哈希、时间；`metrics.json` 另记模型加载、图像编码和逐实例解码时间。只回放现成配置不能用于评估人工标注速度。

每张图的导出包含：

```text
realpart_img1.json       # Labelme mask JSON，内嵌原图字节和bbox内二值PNG
masks/0001_structure.png # 每标注一个原尺寸uint8 0/255掩码，可重叠
overlay_ids.png          # 原尺寸编号叠加：蓝structure、红residue、绿substrate、黄ignore
points.png               # 正点绿色、负点红色；每实例首点旁为ID
points.json              # 本次点配置原字节，不能重排JSON后冒充同一文件hash
instances.json           # ID、名称、提示点、面积、提示点命中/违约检查
candidates/0001_0.png    # 原始predictor候选，供核对未裁剪/手改输出
metrics.json             # 环境、源码/配置hash、时间、标签/点数、提示约束问题
session.json             # --final时保存的计时会话与修订/导出历史
nvidia_smi_*.txt         # 延续阶段0的GPU占用快照；有外来计算进程就停止
failure.json             # 若失败则保留，不能据此当完成结果
```

Labelme JSON 的 `shape_type=mask`、两个整数角点（含端点）和 base64 PNG 与 [Labelme 官方示例](https://github.com/wkentaro/labelme/blob/d187c87b7d49e2e69fd86ae3b5c25bbcc4b0bd63/examples/utils.py)对应，避免 polygon 近似丢失细槽/孔洞。使用支持 mask 的 Labelme 5.8.1 或兼容版本，现有 Labelme 应用可执行 `labelme dataset/seed_v0/realpart_img1/realpart_img1.json` 打开。**本机未安装 Labelme GUI，本轮验证到官方读取器逐实例像素还原，未声称已打开/操作 GUI。**官方读取示例的固定源码、GPL-3.0-only 许可和哈希只保存在 `logs/labelme_format_reference/` 作独立格式验证，不是标注工具运行依赖。

**重叠规则**：独立实例 PNG 保留全部重叠；`ignore` 在 overlay 与 Labelme shapes 中排最后。使用者先排除所有 ignore 像素，再处理其余类别冲突；其余冲突需要复核，不能靠最后写入者决定真值。未标像素是 unknown，**不得把补集当 substrate 或负样本**。这些 PNG 是草稿预测，不是可直接训练/评价的单通道类别真值。

## 阶段 1：manifest 与验证

元数据显式登记在 [metadata.json](dataset/seed_v0/metadata.json)。工具从原图读宽高，按共享件 ID **或**共享拍摄批次构造传递连通组，再以固定 seed 在组级切分，防止“同件不同批次/同批次不同件”的间接泄漏。比例是组数目标，不保证小样本有三套数据。

```powershell
.\.venv\Scripts\python.exe -m annotate.manifest --metadata dataset/seed_v0/metadata.json --images-root inputs --output dataset/seed_v0/manifest.csv
.\.venv\Scripts\python.exe -m unittest annotate.test_manifest -v
.\.venv\Scripts\python.exe -m annotate.verify --dataset dataset/seed_v0 --inputs inputs --output logs/stage1_verify.json
# 完整的本阶段相关检查（13项测试+独立官方格式读取+范围/依赖/旧证据核对；不做GPU推理）：
.\.venv\Scripts\python.exe -m annotate.check_stage1
```

CSV 字段：`filename,part_id,capture_batch,device,width,height,split,split_role,annotation_status,human_verified,test_ground_truth_allowed`。当前两张图同属一个**保守占位组** `handoff_part_001 / handoff_batch_unknown_001`，不是已核验同件身份或实际拍摄批次；设备 unknown。两行均 train，`split_role=placeholder_demo`，val/test 为空，**只是占位演示，draft / 未经人工核验 / 不得用作测试真值**。不能为了凑齐 train/val/test 拆开同组图片。此实验版 manifest 工具刻意只接受草稿安全状态，不提供升级人工真值/训练资格的开关。

分组测试覆盖同件跨批次、同批次跨件、传递分组、输入次序不影响结果、尺寸实读、重复/未知身份拒绝，以及篡改成跨 split 时被审计拒绝。实际输出为 `rows=2 groups=1 leakage=none`，证据见阶段报告。

## 阶段 0 历史：重建独立环境（本轮不要执行）

当前验证平台：Windows x64，CPython **3.12.4**（`py -3.12` 指向此版本），RTX 5060 Laptop，驱动 577.02。PyTorch **2.7.1+cu128** / torchvision **0.22.1+cu128**；CUDA runtime 随 wheel 提供，不需要为了本实验修改系统 CUDA 或主环境。依赖的完整固定版本在 [requirements.txt](requirements.txt)，SAM 源码 commit、模型 config、权重 URL 与 SHA256 在 [sources.json](sources.json)。

从本目录运行（或先 `Set-Location 'D:\大创\3d\MicroCleaningVision\experiments\probe_annotation'`）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_env.ps1
.\.venv\Scripts\python.exe -m unittest discover -s . -p test_stage0.py -v
.\.venv\Scripts\python.exe run_stage0.py --output results/stage0_rerun
.\.venv\Scripts\python.exe verify_artifacts.py results/stage0_rerun --compare results/stage0_initial
```

在真正新环境验证安装可用不同的本地目录名，不覆盖已有环境：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_env.ps1 -Environment .venv-rebuild
.\.venv-rebuild\Scripts\python.exe run_stage0.py --output results/stage0_rebuild
.\.venv-rebuild\Scripts\python.exe verify_artifacts.py results/stage0_rebuild --compare results/stage0_initial
```

`setup_env.ps1` 只安装到指定环境。首次需联网、Git、Windows 自带 `curl.exe`、约数 GB wheel 下载和足够磁盘；`download_torch.py` 从官方 wheel URL 分段下载并核对官方索引 SHA256，已完成分段可复用，wheel/分段均留在忽略的 `.cache/`。源码下载到 `vendor/sam2`（不入 Git），锁定完整 commit 并拒绝有改动的 vendor 源码。官方建议 Windows 用 WSL；本次用原生 Windows 并设置 `SAM2_BUILD_CUDA=0`，不编译可选连通域扩展。自动掩码 `min_mask_region_area=0`，不启用小孔/杂点清理；没有改上游源码。不要加 notebooks/dev/training extras。

每次推理自动保存 `nvidia-smi` 和 compute-app 列表；导入 torch 前发现任何计算进程就停止，每张图前再次检查（只允许本进程），不结束别人的进程。占用检查是 WDDM 可见进程的即时快照，无法保证检查之后没有新进程启动。结果目录已存在会拒绝，失败记录保留，不覆盖首次证据。

## 输入与输出

```text
inputs/realpart_img1.jpg           # 交接指定的老师实拍，原字节保留
inputs/realpart_img2.jpg
weights/sam2.1_hiera_tiny.pt        # 忽略，不提交
vendor/sam2/                       # 忽略，按完整 commit 重建
.venv*/ .cache/                    # 忽略
logs/                             # 安装、显卡、验证证据
results/stage0_initial/
  metrics.json                    # 环境、参数、来源、每图耗时/峰值显存
  nvidia_smi_*.txt                 # 本次推理前后快照
  realpart_img1/                   # 图2结构相同
    masks/0001.png ...            # 每个独立 proposal；uint8 0/255，原图尺寸，可重叠
    proposals.json                # ID/面积/bbox/提示点/模型内部质量分数
    proposal_ids.png              # uint16 可视索引，0无proposal，小块优先覆盖；会丢重叠信息
    proposal_union.png            # 全proposal并集，仅供检查，绝非结构或残胶分类
    overlay.png                   # 固定颜色35%填充，保留65%原图
    overlay_ids.png               # 同一叠加加proposal编号
```

模型内部 `predicted_iou` / `stability_score` 是筛选信号，**不是人工真值上实测的 IoU / 精度**。真正完整预测在独立 masks 里，不能用 ID 图替代重叠掩码。颜色仅表示 proposal ID，与四类标签没有对应关系。`verify_artifacts.py` 核验原图 hash、掩码原尺寸/二值/面积、并集、输出数量；可比较两次输出的所有 PNG 像素。

阶段 0 当时未生成清单或切分；当前阶段 1 已实现，见文首。下面数字口径仍描述阶段 0 历史运行。

## 数字的口径

- 固定 32×32 点网格，16 points/batch，原生 1024 模型输入变换，不做多层 crop，不手动挑点，不用真值调参；全部参数写进 metrics。
- 固定 seed=0、确定性算法、关闭 TF32/cudnn benchmark、bfloat16 autocast、不使用 torch.compile。
- 每次进程依次图1、图2，每图一次 `generate`，**无预热**。图1包括模型驻留后的首次 kernel/cache 开销；图2较暖，两图内容也不同，不能把它们当重复样本平均。
- 耗时用 `perf_counter` 且前后 CUDA synchronize，包含生成器内预处理、编码、网格解码、过滤和 CPU masks；排除图片读盘/转RGB、模型加载、CUDA初始化、保存PNG。`model_load_seconds` 另记模型及生成器构建，不含导入/CUDA context 初始化。
- 峰值显存是 `torch.cuda.max_memory_allocated/reserved`，单位 MiB=2²⁰字节，含常驻权重；每图前 empty_cache/reset。**不含 CUDA driver/context 或其他进程，不等于整卡峰值**。nvidia-smi 是快照，不能代替连续峰值。
- 两张图没有人工真值，当前只做可运行性与结构外观定性判读，精度、IoU、P/R/F1 一律不报。没有评测框架接入，不另起评测体系。

许可证、模型大小与状态见 [MODELS.md](MODELS.md)；阶段结果见 [probe_report.md](probe_report.md)，异常与兼容性问题见 [failures.md](failures.md)。所有文件仅在本地分支，未提交、未推送、未提 PR。

本机验证已完成：`.venv` 与 `.venv-rebuild` 均安装成功并通过 pip check；修复首次只读数组警告后，新环境复跑的17张PNG与首次逐像素一致，2图9张独立掩码的尺寸/二值/面积/并集校验通过，4项针对性测试通过。首次 runner 的精确副本保留在 `logs/run_stage0_initial.py`，区别只是修正版对输入增加可写拷贝；首次与复跑的源码 hash 分别记录于 metrics，不将基线 Git HEAD 冒充未提交脚本的唯一版本标识。
