# 种子集 v0：draft / 未经人工核验 / 不得用作测试真值

这是老师实拍上的 SAM 2 点提示草稿。点由 Codex 看原图后输入像素坐标；不是人已审定的轮廓，也不是模型自动判定的材料类别。原图保存在 `../../inputs/`；每份 Labelme JSON 另内嵌原图，离开原图目录也能读到同一字节。任务定义是打印后处理显影；残胶的材料身份与显影合格规格仍须老师确认。

| 文件 | structure | residue | substrate | ignore | 最终正/负提示点条目 | 总耗时 |
|---|---:|---:|---:|---:|---:|---:|
| realpart_img1 | 5 | 0 | 0 | 6 | 77 / 53 | 607.110 s |
| realpart_img2 | 3 | 2 | 0 | 5 | 49 / 49 | 346.812 s |

总耗时由工具从开始选点到最终导出记录，包含导出器修复、等待、复查与再次导出；不是人工点击速度。两个会话修复期间有重叠，不能相加当单人有效工时。首次导出的时间和恢复原因见 `sessions/`，完整解释见 [stage1_report.md](../../stage1_report.md)。

- `points/` 是最终配置；`sessions/` 保存选择起点、每版提示和导出历史。`realpart_img1/`、`realpart_img2/` 各有 Labelme JSON、每实例 PNG、编号图、点图和 metrics；不得只拿叠加图替代掩码。
- ID 在同图内唯一，PNG `0001_structure.png`、Labelme `group_id=1`、叠加编号 `0001` 对齐；图 1 的 0003/0005 是同一连续右侧沟槽的分段标注，不是两个已核实物体。ID 不承担跨图跟踪。
- 图 1 的 0001 是中央圆台顶面，0002 是中央曲折沟槽/细茎，0003/0005 是右侧沟槽上下段，0004 是左侧截断结构。0010–0015 为根部、暗边、裂纹、麻点等保守 ignore。
- 图 2 的 0001 是圆台顶面，0002 是水平连接细槽，0003 是 T 形直槽；0010–0014 为根部、槽边阴影和不确定麻点。**残胶只有 0020 和 0021**：上方团块约在 x435–455/y80–117，下方团块约在 x441–461/y317–335（模型输出包围盒，不是材料测量规格）。其余暗色没有补成 residue。
- 部分 ignore 掩码过宽或没有满足全部正负点，详见 `instances.json` 和报告。未标的模糊纹理继续视为 unknown，不能擅自补为 substrate；当前 substrate 两图均为 0。
- **ignore 优先排除**，每实例 PNG 可重叠。JSON 把 ignore shapes 放最后以兼容 Labelme 顺序渲染；后续若制作类别图，也必须先排除 ignore。非 ignore 类别之间若有冲突，应回人工复核。

`manifest.csv` 每行明确 `annotation_status=draft,human_verified=False,test_ground_truth_allowed=False,split_role=placeholder_demo`。件/批次字段是保守共享占位组，不是已确认拍摄元数据；device unknown。两张图只有一个独立组，全部 train，val/test 为空，不能作为训练/测试泛化的实验切分。

可复查证据在 `../../logs/stage1_verify.json`、`../../logs/labelme_format_reference/roundtrip_final.json` 和 `../../logs/seed_v0_inventory.json`。仅本地交付，尚未提交/推送；Labelme GUI 实开与人工逐区域核验仍待完成，官方读取器已逐实例还原像素通过。
