# 阶段 0 问题与局限

以下保留阶段 0/1 历史；阶段 2 记录在文末。核对日期：2026-11-09。

| 编号 / 状态 | 现象与证据 | 原因判断与本轮处理 | 下一步 / 未证明的内容 |
|---|---|---|---|
| S0-01 已解决 | 直接 pip 下载 3.27GB torch wheel 长时间无落盘进度；`logs/install_torch.log` | 网络整包传输停滞，未断言服务端故障。只停止本任务的下载进程，改用官方同 URL 的 HTTP Range；`logs/download_torch_resumed.log` 最终核对官方 SHA256 成功 | `download_torch.py` 固化分段、超时、重试和校验；不是换源或换版本 |
| S0-02 已处理 / 明确配置差异 | 原生 Windows 下可选扩展的编译依赖较多，见官方 INSTALL 与 setup.py；本轮没有尝试失败后再伪装为编译成功 | 主动 `SAM2_BUILD_CUDA=0`；自动掩码 `min_mask_region_area=0`，未启用连通域小孔/杂点清理；没有修改上游代码 | 输出仅代表这个固定配置，不能与启用扩展/其他参数的结果混称 |
| S0-03 已修复 | 首次推理 torchvision 报 NumPy 数组不可写，见 `logs/inference_initial.log`；首次推理仍正常完成两图 | PIL 的 `np.asarray` 视图为只读。改为显式可写副本 `np.array(..., copy=True)`；原始 runner 保存在 `logs/run_stage0_initial.py`，与首次 metrics 中代码 hash 对应 | 重建环境使用修正版；按像素比较首次与复跑输出，结果见验证日志。未覆盖首次输出 |
| S0-04 输出质量不足，未调参 | 图1的5个候选集中于画面边缘/截断小区域，中央圆台和连续曲折沟槽没有成为完整候选；图2的4个候选以右侧大平面为主，另有84px纹理小点。见两图 overlay_ids 与 proposals | 固定网格、固定筛选参数的预训练 tiny 自动模式未能给出完整结构候选；仅有两张无真值图，不能据此归因于模型容量、阈值或领域差异中的某一项 | 阶段0不能宣称“已能标全结构”。后续若确认阶段1，再单独验证点选提示能否改善；本轮不放宽阈值、不挑参数、不生成语义标签 |
| S0-05 记录格式已统一 | Windows PowerShell 的 `>` 默认生成 UTF-16 日志；直接按默认 GBK 读取验证输出曾失败 | 模型结果和 PNG 不受影响，结构化验证日志改用 Python 显式 UTF-8 写入 | 读早期 PowerShell 文本按 BOM 解码；不要将打印日志的编码错误误报为推理失败 |

本轮没有 CUDA OOM、模型加载失败或推理异常（以两图最终 `metrics.json status=complete` 为依据）。安装时 iopath/antlr4 的 legacy setup.py 弃用提示及 pip 新版本提示不是安装失败；固定 pip 25.2 及本轮依赖，不自动升级。

性能边界：首次采样时另一个独立环境仍在后台安装，CPU/磁盘负载未控制；两图无预热、内容不同，数字只是本次环境冒烟观测。复跑数字单独保存，不把两次样本或两图混成稳定 FPS/性能均值。WDDM 占用检查只证明可见进程采样时的状态，PyTorch allocator 峰值不等于整卡显存峰值。

没有人工真值，没有干净同款件/设计模板参与本次推理，没有验证显影效果、残胶判定或工控机 CPU/ONNX 部署。工艺类型、材料、显影液、合格规格均待老师确认。

## 阶段 1 问题与处理（2026-11-09）

| 编号 / 状态 | 现象与证据 | 处理 / 保留的局限 |
|---|---|---|
| S1-01 结构首轮点约束问题已改善 | `results/stage1_review/img1_r1/instances.json`：右侧沟槽 ID3 有 6 个正点未落在预测内，局部边界碎裂 | 看原图放大与点图后修点，把连续右沟槽分上下两段 ID3/5；r2 的结构实例全部满足配置正负点。当前轮廓仍非真值，不能据提示点命中宣称描全或准确 |
| S1-02 ignore 局部问题保留 | 最终图1 ID11/12/13/14/15、图2 ID10/12/14 存在正点遗漏或负点被包含；`logs/stage1_verify.json` 的 prompt_misses 与每图 instances.json | 根部/阴影/裂纹/麻点均按 ignore 保守处理，部分区域过宽/细纹遗漏。没有裁剪、填孔、手画 mask 或把漏点改成正常类别来凑结果；未标区域保持 unknown，不当负样本/基底。后续须人工逐区域修核 |
| S1-03 已修复 | 首次完整 `annotate.verify` 在 `metrics.config_sha256 == digest(points.json)` 处 AssertionError：源配置与导出副本格式化不同 | `points.json` 改为原字节 copy；会话末版配置 hash/全文、metrics 与副本一起核对。修复前源码在 `logs/stage1_before_export_fixes/`；两份首次导出保存在 `results/stage1_review/realpart_img*_first_export/` |
| S1-04 已修复 | 独立审查发现 overlay 已让 ignore 最后绘制，Labelme shapes 却仍按配置原顺序；与 Labelme 逐 shape 覆盖语义不一致。是可触发的工具缺陷，不据此断言原图已损坏 | JSON 也把 ignore 放最后；verify 按实例ID匹配，增加交叠顺序回归；官方读取器核验所有 ignore 像素保留。原始实例 mask 不变，21张首次/最终 mask 逐像素一致 |
| S1-05 计时归属审计已加强 | 审查发现仅验 session 已导出且同图，仍可能把同图旧点版本的计时误归当前结果 | 加末版 config hash/全文、会话原件/导出副本、metrics 总时长一致检查；显式 resume 保留原始起点、首次导出路径/耗时、归档路径与理由；最终耗时包含修复等待，不能用来宣称人工提效 |
| S1-06 GUI 实开未验 | 实验 `.venv` 无 Labelme、Qt，亦无可调用 Labelme 命令 | 为保持阶段0环境不安装软件。已用固定官方读取示例对21实例逐像素还原、检查内嵌原图/ignore优先；JSON格式验证通过。**未声称已在 Labelme GUI 中打开并操作**，负责人仍需实开并人工审核 |
| S1-07 元数据不足 | 只有两张实拍，实际件身份/批次/设备未提供核验材料 | 同一个保守件/批次占位组，device unknown，2图均 train/placeholder_demo。不能拆图凑测试集；manifest 的无泄漏不证明真实元数据已核验 |

本轮无点预测接口异常、CUDA OOM 或模型下载阻塞。图像编码/点解码与 AMG 不同，已使用独立 predictor 路径；无自动候选转标签的替代路线。修复后再次导出到 seed_v0，首次导出保留，工具计时从原始起点连续计算。所有结果仍是 draft / 未经人工核验 / 不得用作测试真值。

## 阶段 2 问题与处理（2026-11-09）

| 编号 / 状态 | 现象与证据 | 处理 / 仍需满足的条件 |
|---|---|---|
| S2-01 许可来源已澄清 | Micro-SAM 旧 Zenodo 10524791 页面没有足够明确的权重许可证信息，不能从代码 MIT 推断权重许可 | 未以该页为依据下载；沿官方 `micro_sam/util.py` 当前 registry 找到 BioImage.IO `diplomatic-bug/1.2`，保存 rdf.yaml 的 CC-BY-4.0 与官方 SHA256，再从其官方 vit_b.pt URL 下载实测核对。[来源证据](logs/stage2/microsam_rdf.yaml)、[MODELS](MODELS.md) |
| S2-02 Micro-SAM bf16 接口兼容已处理 | 原始 SAM predictor 的高层 `predict()` 将 scores 转 NumPy，bf16 不能直接转 NumPy；不能因此污染已有 torch/NumPy 版本 | 采用相同官方 predictor 的 `predict_torch()`，用其 transform 转换点坐标；scores 显式转 float32 再记录，布尔掩码照常导出；无重训/模型改权重。相关测试与两图实际推理均通过 |
| S2-03 资源检查顺序已修复 | 首轮 Micro-SAM 图2在推理前 blocked-resource：free 4235 MiB < 原定预算4600 MiB。[首轮汇总](results/stage2_attempt1/summary.json)、[日志](logs/stage2/run_attempt1.log) | 框架先检查整卡空闲、后清理本进程上一图缓存造成可避免的阻塞；改为同步→empty_cache→资源检查→峰值reset，新增顺序回归。未放低预算、未硬撑OOM。首轮完整保留，最终两图均ok |
| S2-04 PatchCore 参考数据与实现边界已加强 | 独立审查发现初版核心集可能重复选择；真实/合成模式、query冒充正常参考、缓存键和显示色标需明确。[修复前回归日志](logs/stage2_patchcore_review_before.log) | 唯一 farthest-first；参考manifest/hash/设计/确认人校验；拒绝已知合成图、真实query的同hash副本充当真实参考；缓存绑定输入hash/设计/模型。原始距离落盘，p99色标注明不可跨图比较。最终相关测试及真实阻塞/合成实测均通过 |
| S2-05 真实数据 blocked / missing-clean-reference | 真实两张图没有同设计金标准干净件照片库；[图1记录](results/stage2/patchcore/realpart_img1/metrics.json)、[图2记录](results/stage2/patchcore/realpart_img2/metrics.json) | 不执行真实异常推理、不产生假热图。需要同设计、经实际人工/工艺确认彻底显影干净的件若干照片及拍摄条件/确认依据；填真实manifest与query设计ID后运行 README 的 `configs/patchcore_clean.json` 单命令。合成自检不解除真实数据阻塞 |
| S2-06 DINO 结论受数据限制 | 两图无substrate草稿，图1无residue；图2 residue有效851px仅相当4.342个完整patch，且与structure有混合patch | 不用未标注补集伪造基底，不将PCA颜色/聚类ID解释为类别，不报告精度。只给草稿条件下的中心距离/类内离散和图证；不能确认三类可分或微调优势 |
| S2-07 历史证据缺口保留 | 经典四基线只有负责人2026-11-06数值，当前交接无对应mask/overlay、计时与显存；SAM2点选无峰值显存记录 | comparison明确“未记录”，链接 [baseline_handoff.md](baseline_handoff.md)；遵照任务不重复验证旧基线，不借别的方法数字补齐，也不把输入原图冒充算法图证 |
| S2-08 对照生成与复跑说明已修复 | 最终文档审查发现 PatchCore 对照描述固定写缺参考阻塞，未来参考库就绪仍会误报；README复跑输出与默认审计路径不同 | 按本批逐图状态/原因生成表述，补直接合成图证链接与相关回归；README显式传入复跑的 `--results`。原始运行所用comparison源码另存 `logs/stage2/comparison_run_source.py`，不改运行summary里的原始源码hash，不重跑模型 |

本轮没有未解决的 `blocked-env`，三个探针均复用已有环境完成可执行验证，未安装/升级包。AGPL 项禁止使用；实际选中代码/权重均已逐项核对为非AGPL，没有虚构被淘汰的AGPL模型。最终实际模型批次是 **6 ok / 2 blocked / 0 failed**；两条blocked均是预期真实参考数据缺失，不是隐去失败。最终报告与图证见 [stage2_report.md](stage2_report.md)。
