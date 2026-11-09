"""Build a provenance-aware comparison table; never create accuracy measures."""
import json
import os
from pathlib import Path


def write_comparison(root: Path, output: Path, summary: dict) -> None:
    def link(label, path):
        target = root / path
        if not target.is_file():
            return f'{label}（文件未交接）'
        relative = os.path.relpath(target, output).replace('\\', '/')
        return f'[{label}]({relative})'

    def existing_metrics(path):
        target = root / path
        return json.loads(target.read_text(encoding='utf-8')) if target.is_file() else None

    config_path = output / 'config.json'
    config = json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.is_file() else {}
    image_metadata = {item['image_id']: item.get('metadata', {}) for item in config.get('images', [])}

    def is_synthetic(row):
        return (row.get('metrics', {}).get('synthetic_only') is True
                or image_metadata.get(row['frame_id'], {}).get('evidence_kind') == 'synthetic'
                or row['frame_id'].startswith('synthetic'))

    def selected_rows(name, synthetic=False):
        return sorted((r for r in summary['results'] if r['probe_name'] == name
                       and is_synthetic(r) == synthetic), key=lambda r: r['frame_id'])

    def run_values(name, field, scale=1, synthetic=False):
        rows = selected_rows(name, synthetic)
        def value(row):
            if row.get(field) is not None:
                return f'{row[field] / scale:.3f}'
            if row['status'] == 'blocked':
                return '未推理'
            if field.startswith('peak_'):
                return '未测（CPU或不适用）'
            return '未记录'
        return ' / '.join(value(row) for row in rows) or '未配置'

    def current_evidence(name, filename, synthetic=False):
        rows = selected_rows(name, synthetic)
        values = []
        for row in rows:
            base = output / name / row['frame_id']
            # Partial output left by a failed run is not successful image evidence.
            artifact = base / filename if row['status'] == 'ok' and (base / filename).is_file() else base / 'metrics.json'
            kind = '记录' if artifact.name == 'metrics.json' else '图证'
            label = f'{"合成" if synthetic else ""}{kind}：{row["frame_id"]}'
            if synthetic:
                label += '（synthetic）'
            if artifact.is_file():
                values.append(f'[{label}]({artifact.relative_to(output).as_posix()})')
            else:
                values.append(f'{label}（文件缺失）')
        return '、'.join(values) or '未配置'

    def current_description(name, filename):
        rows = selected_rows(name)
        if not rows:
            description = '本批未配置此探针的真实图。'
        else:
            details = '；'.join(f'{r["frame_id"]}={r["status"]}' for r in rows)
            description = details + '。'
            unfinished = [r for r in rows if r['status'] != 'ok']
            if unfinished:
                description += '原因：' + '；'.join(f'{r["frame_id"]}：{r.get("status_reason", "未提供原因")}' for r in unfinished) + '。'
            if any(r['status'] == 'ok' for r in rows):
                description += {
                    'micro_sam': '完成的记录提供同点掩码及对应实例一致性；结构/残胶标签由提示配置指定，不代表模型语义识别。',
                    'dino': '完成的记录提供特征可视化及已有草稿的相对距离；颜色分组不等于类别，缺失类别不得用未标像素补造。',
                    'patchcore': '完成的记录给出相对人工声明干净且经来源校验的参考库的异常距离；不是残胶类别或精度。',
                }[name]
            description += current_evidence(name, filename)
        if name == 'patchcore' and selected_rows(name, synthetic=True):
            synthetic_status = '；'.join(f'{r["frame_id"]}={r["status"]}' for r in selected_rows(name, synthetic=True))
            description += '合成自检：' + synthetic_status + '；只证明合成流程。' + current_evidence(name, 'overlay.png', synthetic=True)
        return description

    amg = existing_metrics('results/stage0_initial/metrics.json')
    amg_time = ' / '.join(f"{r['generate_seconds']:.3f}" for r in amg['images']) if amg else '未交接'
    amg_mem = ' / '.join(f"{r['peak_allocated_mib']:.3f}" for r in amg['images']) if amg else '未交接'
    point_times = []
    for i in (1, 2):
        data = existing_metrics(f'dataset/seed_v0/realpart_img{i}/metrics.json')
        point_times.append(f"{data['encode_seconds'] + sum(r['decode_seconds'] for r in data['prediction_records']):.3f}" if data else '未交接')
    old = link('负责人交接数值及图证缺口', 'baseline_handoff.md')
    samfig = '、'.join(link(f'图{i}', f'results/stage0_initial/realpart_img{i}/overlay_ids.png') for i in (1, 2))
    seedfig = '、'.join(link(f'图{i}', f'dataset/seed_v0/realpart_img{i}/overlay_ids.png') for i in (1, 2))
    rows = [
        ['HSV-red', '二值阈值前景', '0 / 0 像素；无前景。' + old, '未记录', '未记录', '无权重；权重许可不适用'],
        ['ExG', '颜色指数前景', '18.2% / 37.4%；面积爆炸，不能当残胶面积。' + old, '未记录', '未记录', '无权重；权重许可不适用'],
        ['LocalContrast', '局部差异前景', '4.97% / 0.84%；主要跟随结构边缘。' + old, '未记录', '未记录', '无权重；权重许可不适用'],
        ['Otsu', '全局阈值前景', '0.83% / 0.27%；无残胶语义证据。' + old, '未记录', '未记录', '无权重；权重许可不适用'],
        ['SAM 2 AMG', '独立候选掩码', '5 / 4 个候选，不足以描全结构；无类别。' + samfig, amg_time, amg_mem, '148.781 MiB；代码/权重 Apache-2.0'],
        ['SAM 2 点选', '21 实例 v0 草稿；标签由配置提供', '结构草稿已验收；残胶身份待核、非真值。' + seedfig, ' / '.join(point_times) + '（编码+解码）', '阶段1未记录峰值，不能移用AMG值', '复用 148.781 MiB；代码/权重 Apache-2.0'],
        ['Micro-SAM LM 点选', '同点实例掩码 + 同ID模型输出一致性', current_description('micro_sam', 'sam2_vs_micro_sam.png'), run_values('micro_sam', 'duration_ms', 1000), run_values('micro_sam', 'peak_allocated_mib'), '357.650 MiB；Micro-SAM MIT / SAM Apache-2.0；权重 CC-BY-4.0'],
        ['DINOv2 特征', '384维 patch 特征、PCA伪彩、聚类图、草稿相对距离', current_description('dino', 'original_pca_clusters.png'), run_values('dino', 'duration_ms', 1000), run_values('dino', 'peak_allocated_mib'), '84.193 MiB；所用骨干代码/权重 Apache-2.0'],
        ['PatchCore（DINO变体）', '相对参考库的异常距离热图', current_description('patchcore', 'overlay.png'), run_values('patchcore', 'duration_ms', 1000), run_values('patchcore', 'peak_allocated_mib'), '复用 DINO 84.193 MiB，无新增网络权重；官方PatchCore代码 Apache-2.0'],
    ]
    text = ['# 阶段 2 方法对照', '',
            '历史图1 / 图2指 realpart_img1 / realpart_img2；本批探针数值按图像ID排序，状态和链接明确标出ID。经典基线来自负责人 2026-11-06 实测；阶段0/1仅引用已有记录，未重跑。若本批改用其它输入，不能直接与旧图数值横比。没有人工真值，本表不含识别精度。', '',
            '| 方法 | 输出是什么 | 对结构/残胶的定性表现与证据 | 单图耗时 s（按图像ID排序） | 已分配显存峰值 MiB（同序） | 权重大小与许可 |',
            '|---|---|---|---|---|---|']
    text += ['| ' + ' | '.join(cell.replace('|', '\\|').replace('\n', ' ') for cell in row) + ' |' for row in rows]
    text += ['', '## 测量与证据边界', '',
             '- 阶段2总耗时包含读图、校验、推理、统计、可视化和输出写盘，排除正常load阶段；阶段0/1耗时只含模型编码/解码等对应操作，**不可直接拿不同口径排速度名次**。阶段2纯模型时间另见逐图 metrics。',
             '- PatchCore首次有效图采用延迟加载，duration含骨干加载及参考库构建，reference_setup_seconds另列；参考身份不变时后续图复用参考库。blocked记录的耗时/显存为null，判断耗时另记，绝不伪装推理结果。',
             '- 显存为 PyTorch allocated 峰值，含驻留权重；reserved另在metrics。均不含驱动/context/其他进程；无预热、单次观测，不能当稳定性能基准。',
             '- Micro-SAM/SAM2交并比是两个模型原始对应实例掩码的一致性（含ignore实例），不是对真值的IoU；同时错也可能一致。',
             '- DINO PCA/聚类每图独立，颜色及簇ID无跨图语义对应；未标注区域保持unknown。若基底类缺失，不能用补集补造。',
             '- 旧经典基线原始mask/overlay、运行计时与峰值没有在本次目录交接，明确留空；输入原图链接不能替代算法图证。',
             '', '## 本批逐图运行记录', '',
             '| 探针 | 图像 | 状态 | 总耗时 s | allocated / reserved MiB | 记录 |',
             '|---|---|---|---:|---:|---|']
    for r in summary['results']:
        value = lambda k, scale=1: '—' if r[k] is None else f'{r[k] / scale:.3f}'
        ref = f"{r['probe_name']}/{r['frame_id']}/metrics.json"
        record_link = f'[metrics]({ref})' if (output / ref).is_file() else '记录文件缺失'
        text.append(f"| {r['probe_name']} | {r['frame_id']} | {r['status']} | {value('duration_ms',1000)} | {value('peak_allocated_mib')} / {value('peak_reserved_mib')} | {record_link} |")
    text += ['', '详细定性判断、逐实例面积/一致性、参考库解阻命令见 ' + link('stage2_report.md', 'stage2_report.md') + '；逐项来源/许可见 ' + link('MODELS.md', 'MODELS.md') + '。', '']
    (output / 'comparison.md').write_text('\n'.join(text), encoding='utf-8')
