# 阶段 1 报告：点提示流水线与 seed_v0 草稿

日期：2026-11-09（Asia/Shanghai）。**阶段 1 工具与两图 v0 草稿已交付，停在阶段 1，等待负责人确认；阶段 2 未开始。**种子集状态为 **draft / 未经人工核验 / 不得用作测试真值**。Labelme 官方格式读取验证通过；Labelme GUI 实开仍未验，不能把格式校验写成人工验收。

## 做了什么

在本地 `D:\大创\3d` 的 `exp/probe-annotation`、HEAD `a5b304903e54e008e948375d84f3f4c04a0290e0` 上继续阶段 0 成果。复用实验 `.venv\Scripts\python.exe`（CPython 3.12.4）、原 SAM 源码 commit `2b90b9f5ceec907a1c18123530e92e794ad901a4`、原 SAM 2.1 Hiera tiny 官方权重，锁定 SHA256 `7402e0d864fa82708a20fbd15bc84245c2f26dff0eb43a4b5b93452deb34be69`。没有重建环境、重复下载权重、安装/升级依赖或修改 vendor。

完整读了项目 AGENTS、六份长期记忆并核对 Git、阶段 0 源码/证据和两张实拍。按本轮仅实验目录授权，公共项目记忆、project_state.yaml 与项目进度文档只读核对，未在实验目录外写入阶段事实；本报告承担阶段交接。独立子代理实现/测试 manifest，另一子代理只读审查导出与计时链；主线程负责点选、GPU 推理、图像审查、修复和整合。

新增 `annotate/`：

1. 以原图 + JSON 像素 xy 正点/负点配置为输入；固定四类 `structure/residue/substrate/ignore`，实例 ID 在同图内唯一。校验图像 SHA、坐标、类别、重复/矛盾点和 ID。
2. 用 `SAM2ImagePredictor.set_image` 一次编码，逐实例 `predict`。**不是 AMG，不读取阶段 0 proposal 或 mask，不把自动候选转标签。**只复用阶段 0 GPU 占用检查函数。全部 v0 实例使用单掩码；无框提示、外来 mask 输入、手画/裁剪/填孔掩码，无训练。
3. 导出 Labelme mask JSON（内嵌原图、bbox 内 base64 二值 PNG）、每实例原尺寸 0/255 PNG、全图编号叠加图、点图、实例记录、原配置字节、原始候选、环境/源码哈希及计时记录。`ignore` 的 shapes 最后写，独立 mask 保留重叠。
4. 在开始选择点前由工具启动持久计时；每次修订记录配置全文和 hash。最终导出关闭会话。若导出后修复，显式恢复计时并保留首次导出档案与原因，原始起点不重置。
5. manifest 按共享件 ID **或**共享批次形成传递连通组后切分，尺寸从真实文件读取；独立审计拒绝跨 split 泄漏。两图只有一个保守占位组，不能拆开凑测试集。

SAM 不提供本项目四类的材料语义，标签由点配置作者指定。本轮是 Codex 目视选点、以坐标录入配置的辅助草稿，不是已经完成的人工鼠标标注。未标像素明确保持 unknown，不用补集生成 substrate 或负样本。

## 证据在哪

| 交付 / 证据 | 位置 |
|---|---|
| 输入输出规范、ID、重叠规则、可复跑命令 | [README.md](README.md) |
| 点配置/计时/predictor/导出/校验工具 | [annotate/predict.py](annotate/predict.py)、[export.py](annotate/export.py)、[session.py](annotate/session.py)、[verify.py](annotate/verify.py)、[check_stage1.py](annotate/check_stage1.py) |
| manifest 工具与测试 | [manifest.py](annotate/manifest.py)、[test_manifest.py](annotate/test_manifest.py)；另有 [标注工具测试](annotate/test_annotation.py) |
| 两图来源/尺寸/原始 SHA | 阶段 0 原 [logs/inputs.json](logs/inputs.json)、[sources.json](sources.json)；原图仍在 inputs/，没有替换 |
| 清单与身份不确定性 | [manifest.csv](dataset/seed_v0/manifest.csv)、[metadata.json](dataset/seed_v0/metadata.json)、[种子集说明](dataset/seed_v0/README.md) |
| 图 1 最终点配置/计时 | [points/realpart_img1.json](dataset/seed_v0/points/realpart_img1.json)、[sessions/realpart_img1.json](dataset/seed_v0/sessions/realpart_img1.json) |
| 图 1 Labelme / 编号图 / 实例数据 | [realpart_img1.json](dataset/seed_v0/realpart_img1/realpart_img1.json)、[overlay_ids.png](dataset/seed_v0/realpart_img1/overlay_ids.png)、[instances.json](dataset/seed_v0/realpart_img1/instances.json)、[metrics.json](dataset/seed_v0/realpart_img1/metrics.json)；同目录 masks/ 为11张独立 PNG |
| 图 2 最终点配置/计时 | [points/realpart_img2.json](dataset/seed_v0/points/realpart_img2.json)、[sessions/realpart_img2.json](dataset/seed_v0/sessions/realpart_img2.json) |
| 图 2 Labelme / 编号图 / 实例数据 | [realpart_img2.json](dataset/seed_v0/realpart_img2/realpart_img2.json)、[overlay_ids.png](dataset/seed_v0/realpart_img2/overlay_ids.png)、[instances.json](dataset/seed_v0/realpart_img2/instances.json)、[metrics.json](dataset/seed_v0/realpart_img2/metrics.json)；同目录 masks/ 为10张独立 PNG |
| 13项相关测试原日志 | [logs/stage1_tests.log](logs/stage1_tests.log)：4项阶段0既有测试、4项标注工具测试、5项manifest测试；全部通过 |
| 两图像素/ID/计时/分组审计 | [logs/stage1_verify.json](logs/stage1_verify.json) |
| 独立 Labelme 官方读取器验证 | [roundtrip_final.json](logs/labelme_format_reference/roundtrip_final.json)、[来源和hash](logs/labelme_format_reference/provenance.json)；每实例还原与 PNG 一致，ignore 顺序保留 |
| 范围、运行环境、旧证据保留 | [logs/stage1_scope_integrity.json](logs/stage1_scope_integrity.json)、[标注工具源码清单](logs/stage1_tool_source_sha256.json)、[种子集逐文件hash](logs/seed_v0_inventory.json) |
| 修订/首次导出/修复前源码 | `results/stage1_review/img1_r1/`、`img1_r2/`、`img2_r1/`；两份 `realpart_img*_first_export/`；[logs/stage1_before_export_fixes/](logs/stage1_before_export_fixes/) |
| 失败、许可登记 | [failures.md](failures.md) 的 S1-01–S1-07、[MODELS.md](MODELS.md) 阶段1补充 |

所有证据当前仅在这个本地实验目录，未提交、未推送、未提 PR，不能宣称负责人已取得或人工验收原始标注。

## 数字：提示条目与实例

两张原图均为 640×480。以下点数是最终配置中**各实例的正/负点条目数**，同坐标用于不同实例时分别计数；不是鼠标点击日志，也不把每次相同配置重跑又算一次选点。

| 原图 | 正点 | 负点 | 总提示条目 | structure | residue | substrate | ignore | 实例总数 | 不同点配置版本 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| realpart_img1.jpg | 77 | 53 | 130 | 5 | 0 | 0 | 6 | 11 | 2 |
| realpart_img2.jpg | 49 | 49 | 98 | 3 | 2 | 0 | 5 | 10 | 1 |
| 合计 | 126 | 102 | 228 | 8 | 2 | 0 | 11 | 21 | — |

交付有 **2 份 Labelme JSON、21 张实例掩码 PNG、2 张全图编号叠加图**，另有点图、原始候选及审计文件。原始 mask 与导出 PNG 一致；修复前首次导出与最终导出的 **21 张实例 mask 逐像素一致**。这些是文件/可重放性检查，**不是标注准确率**。

### 工具计时

| 原图 | 开始选点（UTC+8） | 最终导出（UTC+8） | 首次导出秒数（含后来发现的导出缺陷） | 最终导出总秒数 |
|---|---|---|---:|---:|
| 图1 | 16:42:44.402 | 16:52:51.512 | 285.141 | **607.110** |
| 图2 | 16:47:33.628 | 16:53:20.434 | 157.531 | **346.812** |

由工具的单调时钟记录，UTC 时间用于追溯。总时间包含观察原图、填写坐标、修改点、看掩码、进程/模型加载、保存输出、独立审查、导出缺陷修复及等待；两个会话恢复后有重叠，**不能把两项相加当单人有效工时**。本轮不是人工点击速度实验，也没有纯手工对照，因此无法从这两个数断言辅助标注提高了多少效率。首次导出时间也不能代替修复后交付时间。

最终一次推理的单独计时（同步 CUDA，排除人工/代理选点与等待）：

| 原图 | 模型载入 s | 一次图像编码 s | 所有实例解码合计 s |
|---|---:|---:|---:|
| 图1 | 2.694157 | 0.857351 | 1.128014 |
| 图2 | 2.872295 | 1.091841 | 1.107162 |

解码合计不包括保存候选 PNG、Labelme、叠加图和进程初始化；这不是整次命令耗时，也不能与阶段 0 AMG generate 秒数直接作公平速度比较。固定 seed=0、bfloat16 autocast、关闭 TF32/benchmark、确定性算法、不 compile；点模式关闭动态后处理与可选小孔/杂点清理。完整参数与逐实例时间在 metrics。

## 定性观察与保守标注

- **图1**：0001 描圆台可见顶面，0002 描中央曲折沟槽及细茎，0003/0005 分段描右蛇形沟槽，0004 描左侧被画幅截断的部分。首轮右侧有局部碎裂和6个未满足正点，修点并分段后改善；当前结构实例均满足配置正负点。这只说明局部提示约束满足，不证明完整边界或设计尺寸正确。相较阶段0缺少中央圆台/曲折沟槽候选，本轮点提示能得到对应对象草稿，但没有量化覆盖率。
- **图2**：0001 描圆台顶面，0002 描水平连接细槽，0003 描 T 形直槽。直槽水平支路仍有斑驳孔洞，未为追求视觉光滑而填孔。
- **残胶只有图2的0020/0021**：上团块输出 bbox 为 `[435,80]–[455,117]`，下团块为 `[441,317]–[461,335]`，局限在两处明显深绿成团区域。这是图像中“可疑残胶”的保守视觉类别，未由老师确认材料；没有把槽边阴影、圆台根部或全图暗点扩标为 residue。
- **ignore**：图1的0010–0015覆盖圆台暗根、中央/左右槽边、细裂纹及部分麻点；图2的0010–0014覆盖根部、直槽/连接槽暗边和麻点。部分 ignore 会压住结构，这是保守排除语义，不应为了保留结构面积而移除它。
- **substrate=0**：没有把未标区域自动当成基底。两图平面纹理包含麻点、细裂纹和不确定边界，当前未经人工逐块确认。

仍有提示约束问题的实例：图1 **11/12/13/14/15**，图2 **10/12/14**，均属于 ignore。包括少量正点不在预测内，以及图1 ID15、图2 ID12/14 的部分负点被包含。保留原始预测和逐点检查，不以人工裁 mask 或改标签消除这些现象。宽 ignore 会牺牲可用区域，细裂纹也可能漏描；未标模糊区域仍为 unknown，不得在下游默认用作正常样本。因此不能宣称“两图已无遗漏”。

没有人工真值，**不报告 IoU、准确率、P/R/F1 或虚构精度数字**。metrics 中模型内部质量分数不是本实验测得的精度；提示点命中、mask 像素一致和组间无泄漏分别验证输入约束、文件完整性和切分规则，不替代语义正确性。

## 分组清单与验证边界

`manifest.csv` 的两行都使用保守共享占位组 `handoff_part_001 / handoff_batch_unknown_001`，device 为 unknown。实际件身份和拍摄批次缺核验材料，不能从画面相似或文件名推断。两个图保守视为一个独立组，结果是 **train=2、val=0、test=0、group=1、leakage=none**；只是切分占位演示，不是有独立留出的实验数据集。每行都有 draft、False/False 与 placeholder_demo 状态。

5项manifest测试用合成图片/元数据验证：同件跨批次不拆组、同批次不同件连组、传递关联整体隔离、固定seed和输入顺序稳定、尺寸来自图片、重复/缺元数据/通用unknown身份拒绝、人工篡改成跨split后审计报错。这些合成测试验证软件规则，不是拍摄事实证据。总13项相关测试通过，没有默认跑主项目全量回归。

另用固定版本 **Labelme 官方读取示例**还原21个实例，分别与 PNG 比较并核对 ignore 顺序覆盖。未安装 Labelme GUI，因此尚无“实际窗口打开并编辑成功”的证据；交付 JSON 已按其 mask 格式通过读取与像素核验，GUI 实开作为负责人审核时的明确未验项保留。

## 问题、修复与下一步

已修复两项导出/审计问题：点配置重排版引起 hash 不同，改为原字节保存；Labelme ignore 顺序缺陷改为 shapes 最后写并补交叠回归。独立审查还促使计时与配置 hash/全文、原会话与导出副本完全绑定。修复前源码和首次导出均保留，恢复计时未重置起点；见 failures 的 S1-03–S1-05。

最终范围检查：tracked 主线改动 **0**，新增文件都在实验目录；24个阶段0锁定依赖版本逐项一致，旧输入/权重/vendor/阶段0结果保留。本轮只补实验 README、failures、MODELS 和新增阶段1代码/数据/证据。没有训练，没有修改主线依赖、motion_gate、contracts 或固件；没有相机/串口/实物操作，没有提交/推送/PR。

交给负责人确认的下一步是：在支持 mask 的 Labelme 中实开两份 JSON，逐区域确认结构/残胶语义，修核 ignore 过宽与裂纹遗漏；确认真实件/批次/设备元数据。若要评价效率，应在工具稳定后由标注员按同一协议计有效选点/修订时间，并设纯手工对照；当前总墙钟数据只用于暴露本轮流程成本。要建立测试集还须补独立件或独立采集组与人工核验真值。

这与阶段0的结论形成清楚的递进：阶段0证明环境可复跑，但固定AMG候选不足；阶段1证明点提示可以进入可追溯的标注草稿流程。**可运行、格式完整、看起来对应结构，与人工核验真值仍是不同的关卡。报告完成后停止，等待负责人确认再讨论阶段2。**
