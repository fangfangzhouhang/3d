"""Independently verify stage-2 artifacts, synthetic assertions and protected files."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify(output):
    summary = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
    errors, records = [], []
    expected = {(probe, f'realpart_img{i}'): ('blocked' if probe == 'patchcore' else 'ok')
                for probe in ('micro_sam', 'dino', 'patchcore') for i in (1, 2)}
    expected.update({('patchcore', name): 'ok' for name in ('synthetic_normal', 'synthetic_foreign')})
    actual = {(r['probe_name'], r['frame_id']): r['status'] for r in summary['results']}
    if actual != expected or len(summary['results']) != len(expected):
        errors.append('Required stage2 per-image status matrix is not complete')
    for row in summary['results']:
        directory = output / row['probe_name'] / row['frame_id']
        stored = json.loads((directory / 'metrics.json').read_text(encoding='utf-8'))
        if row != stored:
            errors.append(f"Summary/metrics mismatch: {directory}")
        if sha(ROOT / row['raw_image_ref']) != row['input_sha256']:
            errors.append(f"Input identity mismatch: {directory}")
        if row['status'] not in {'ok', 'blocked', 'failed'} or row['test_ground_truth_allowed']:
            errors.append(f"Invalid status or truth flag: {directory}")
        for artifact in row['output_files']:
            path = directory / artifact['path']
            if not path.is_file() or sha(path) != artifact['sha256'] or path.stat().st_size != artifact['bytes']:
                errors.append(f'Artifact mismatch: {path}')
        info = row['model_info']
        if info['weights_ref'] and (sha(ROOT / info['weights_ref']) != info['weights_sha256']):
            errors.append(f"Weight identity mismatch: {directory}")
        if row['status'] == 'ok' and row['probe_name'] == 'micro_sam':
            if len(row['metrics']['instances']) != (11 if row['frame_id'] == 'realpart_img1' else 10):
                errors.append(f'Incomplete Micro-SAM instance count: {directory}')
            for record in row['metrics']['instances']:
                micro = np.asarray(Image.open(directory / record['mask_ref'])) > 0
                sam2 = np.asarray(Image.open(ROOT / record['sam2_mask_ref'])) > 0
                intersect = np.count_nonzero(micro & sam2)
                union = np.count_nonzero(micro | sam2)
                agreement = intersect / union if union else None
                if (micro.shape != (480, 640) or np.count_nonzero(micro) != record['micro_sam_area_px']
                        or np.count_nonzero(sam2) != record['sam2_area_px']
                        or agreement != record['model_output_agreement_iou_not_accuracy']):
                    errors.append(f'Mask/area/agreement mismatch: {directory} {record["id"]}')
        if row['status'] == 'ok' and row['probe_name'] == 'dino':
            from probes.feature_utils import draft_statistics
            from probes.dino import DinoProbe
            from probes.base import ImageInput
            features = np.load(directory / 'features.npy', allow_pickle=False)
            seed = f'dataset/seed_v0/{row["frame_id"]}'
            image = ImageInput(row['frame_id'], ROOT / row['raw_image_ref'], {'seed_dir': seed})
            masks, _ = DinoProbe({}, ROOT)._draft_masks(image, (480, 640))
            stats, _ = draft_statistics(features, masks, (480, 640))
            recorded = row['metrics']['draft_feature_statistics']
            if stats['classes'] != recorded['classes'] or stats['comparisons'] != recorded['comparisons']:
                errors.append(f'DINO feature statistics could not be reproduced: {directory}')
        if row['probe_name'] == 'patchcore' and row['frame_id'].startswith('realpart'):
            if row['status'] != 'blocked' or row['duration_ms'] is not None or (directory / 'heatmap.npy').exists():
                errors.append(f'Real PatchCore must be blocked without a heatmap: {directory}')
        records.append({'probe': row['probe_name'], 'image': row['frame_id'], 'status': row['status'],
                        'artifacts_verified': len(row['output_files'])})
    synthetic = {r['frame_id']: r for r in summary['results'] if r['probe_name'] == 'patchcore' and r['frame_id'].startswith('synthetic')}
    selfcheck = {}
    if set(synthetic) == {'synthetic_normal', 'synthetic_foreign'}:
        if any(r['status'] != 'ok' for r in synthetic.values()):
            errors.append('Synthetic PatchCore did not complete')
        else:
            normal = np.load(output / 'patchcore/synthetic_normal/heatmap.npy', allow_pickle=False)
            foreign = np.load(output / 'patchcore/synthetic_foreign/heatmap.npy', allow_pickle=False)
            mask = np.asarray(Image.open(ROOT / 'dataset/stage2_synthetic/synthetic_mask.png')) > 0
            selfcheck = {'synthetic_only': True, 'normal_max': float(normal.max()),
                         'foreign_max': float(foreign.max()), 'inside_mean': float(foreign[mask].mean()),
                         'outside_mean': float(foreign[~mask].mean()),
                         'foreign_max_gt_normal_max': bool(foreign.max() > normal.max()),
                         'inside_mean_gt_outside_mean': bool(foreign[mask].mean() > foreign[~mask].mean())}
            if not all(selfcheck[k] for k in ('foreign_max_gt_normal_max', 'inside_mean_gt_outside_mean')):
                errors.append('Predeclared synthetic contrast selfcheck did not pass')
    before = json.loads((ROOT / 'logs/stage2/before.json').read_text(encoding='utf-8'))
    changed = [ref for ref, value in before['protected_files'].items() if not (ROOT / ref).is_file() or sha(ROOT / ref) != value]
    tracked_changed = [ref for ref, value in before['tracked_files'].items() if not (ROOT.parents[2] / ref).is_file() or sha(ROOT.parents[2] / ref) != value]
    packages = {d.metadata['Name']: d.version for d in importlib.metadata.distributions()}
    if changed or tracked_changed or packages != before['packages']:
        errors.append('Protected stage0/1 files, tracked files or installed packages changed')
    report = {'status': 'passed' if not errors else 'failed', 'errors': errors, 'records': records,
              'synthetic_selfcheck': selfcheck,
              'scope': {'protected_stage01_files': len(before['protected_files']), 'changed_stage01': changed,
                        'tracked_files': len(before['tracked_files']), 'changed_tracked': tracked_changed,
                        'installed_packages_unchanged': packages == before['packages']}}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', default='results/stage2')
    parser.add_argument('--log', default='logs/stage2/artifact_verification.json')
    args = parser.parse_args()
    report = verify(ROOT / args.results)
    (ROOT / args.log).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    raise SystemExit(bool(report['errors']))


if __name__ == '__main__':
    main()
