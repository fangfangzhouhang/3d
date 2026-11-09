"""Official Micro-SAM LM weights with the official SAM point predictor.

The BioImage.IO v1.2 model architecture is itself a thin SamPredictor wrapper.
Using that same backbone/predictor directly avoids installing the unrelated
napari/training/volume toolchain into the already validated stage 0/1 venv.
"""
from __future__ import annotations

import gc
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .base import BaseProbe, ImageInput, ModelInfo, ProbeBlocked, ProbeOutput
from .registry import register


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def mask_agreement(first, second):
    """Symmetric model-output agreement; never a ground-truth accuracy metric."""
    import numpy as np
    if first.shape != second.shape or first.ndim != 2:
        raise ValueError('Model masks must share the original image dimensions')
    first, second = np.asarray(first, dtype=bool), np.asarray(second, dtype=bool)
    union = int((first | second).sum())
    intersection = int((first & second).sum())
    return {'sam2_area_px': int(second.sum()), 'micro_sam_area_px': int(first.sum()),
            'intersection_px': intersection, 'union_px': union,
            'model_output_agreement_iou_not_accuracy': intersection / union if union else None}


@register('micro_sam')
class MicroSamProbe(BaseProbe):
    def __init__(self, params, root):
        super().__init__(params, root)
        self.predictor = None
        self.model_info = ModelInfo(
            model_name='Micro-SAM vit_b_lm',
            model_version=params.get('source_commit'),
            weights_version='BioImage.IO diplomatic-bug/1.2',
            weights_ref=params.get('checkpoint', 'weights/micro_sam_vit_b_lm_1.2.pt'),
            weights_sha256=params.get('checkpoint_sha256'),
            code_license='MIT (Micro-SAM); Apache-2.0 (official SAM predictor)',
            weights_license='CC-BY-4.0')

    def load(self):
        checkpoint = self.root / self.model_info.weights_ref
        vendor = self.root / self.params.get('source_dir', 'vendor/segment-anything')
        if not checkpoint.is_file() or not vendor.is_dir():
            raise ProbeBlocked('Run setup_stage2.py for the pinned official source and checkpoint')
        actual = sha256(checkpoint)
        if actual != self.model_info.weights_sha256:
            raise ValueError('Micro-SAM checkpoint SHA256 mismatch')
        self.model_info.weights_bytes = checkpoint.stat().st_size
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=vendor, text=True).strip()
        if commit != self.params['sam_source_commit']:
            raise ValueError('Official SAM source commit mismatch')
        if subprocess.check_output(['git', 'status', '--porcelain'], cwd=vendor, text=True).strip():
            raise ValueError('Official SAM source has local changes')
        sys.path.insert(0, str(vendor))
        import torch
        import segment_anything
        from segment_anything import SamPredictor, sam_model_registry
        if not Path(segment_anything.__file__).resolve().is_relative_to(vendor.resolve()):
            raise ValueError('Imported SAM package is not the pinned vendor source')
        torch.set_num_threads(4)
        torch.manual_seed(0)
        torch.backends.cudnn.benchmark = False
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        torch.use_deterministic_algorithms(True)
        state = torch.load(checkpoint, map_location='cpu', weights_only=True)
        # Current official v1.2 export is a SAM state dict; reject unexpected
        # wrappers instead of permissively discarding checkpoint parameters.
        model = sam_model_registry['vit_b'](checkpoint=None)
        model.load_state_dict(state, strict=True)
        model.eval().to(self.params.get('device', 'cuda'))
        self.predictor = SamPredictor(model)
        self.parameter_count = sum(p.numel() for p in model.parameters())

    def infer(self, image: ImageInput, output_dir: Path) -> ProbeOutput:
        import numpy as np
        import torch
        from PIL import Image, ImageDraw
        from annotate.export import COLORS, validate_config

        points = self.root / image.metadata['points_ref']
        seed_dir = self.root / image.metadata['seed_dir']
        config = json.loads(points.read_text(encoding='utf-8'))
        rgb = np.array(Image.open(image.path).convert('RGB'), copy=True)
        height, width = rgb.shape[:2]
        annotations = validate_config(config, width, height, sha256(image.path))
        seed_metrics = json.loads((seed_dir / 'metrics.json').read_text(encoding='utf-8'))
        if (seed_metrics['image_sha256'] != sha256(image.path)
                or seed_metrics['config_sha256'] != sha256(points)):
            raise ValueError('SAM2 seed image or point configuration does not match')
        seeds = json.loads((seed_dir / 'instances.json').read_text(encoding='utf-8'))
        by_id = {row['id']: row for row in seeds}
        if len(by_id) != len(seeds) or set(by_id) != {row['id'] for row in annotations}:
            raise ValueError('Seed instance IDs do not match point configuration')
        (output_dir / 'masks').mkdir()
        (output_dir / 'candidates').mkdir()
        (output_dir / 'agreement').mkdir()
        shutil.copyfile(points, output_dir / 'points.json')
        overlay = rgb.astype(np.float32).copy()
        records, masks, output_files = [], [], ['points.json']
        device = self.params.get('device', 'cuda')
        sync = torch.cuda.synchronize if device.startswith('cuda') else lambda: None
        sync()
        begin = time.perf_counter()
        with torch.inference_mode(), torch.autocast(device_type=device.split(':')[0],
                                                   dtype=torch.bfloat16,
                                                   enabled=device.startswith('cuda')):
            self.predictor.set_image(rgb)
            sync()
            encode_seconds = time.perf_counter() - begin
            for annotation in annotations:
                aid, label = annotation['id'], annotation['label']
                seed = by_id[aid]
                if seed['label'] != label:
                    raise ValueError(f'Class mismatch for same instance ID {aid}')
                seed_path = seed_dir / seed['mask']
                if sha256(seed_path) != seed['mask_sha256']:
                    raise ValueError(f'SAM2 draft mask changed: {aid}')
                second = np.array(Image.open(seed_path)) > 0
                coords = np.array(annotation['positive'] + annotation.get('negative', []), dtype=np.float32)
                labels = np.array([1] * len(annotation['positive']) +
                                  [0] * len(annotation.get('negative', [])), dtype=np.int32)
                sync()
                begin = time.perf_counter()
                transformed = self.predictor.transform.apply_coords(coords, rgb.shape[:2])
                predicted_t, scores_t, _ = self.predictor.predict_torch(
                    point_coords=torch.as_tensor(transformed, dtype=torch.float32, device=device)[None],
                    point_labels=torch.as_tensor(labels, dtype=torch.int64, device=device)[None],
                    boxes=None, mask_input=None,
                    multimask_output=annotation.get('multimask', False))
                # Official predict() converts its bf16 scores/logits directly to
                # NumPy, which has no bf16 dtype. Keep upstream untouched and
                # explicitly cast only the returned score tensor to float32.
                predicted = predicted_t[0].cpu().numpy()
                scores = scores_t[0].float().cpu().numpy()
                sync()
                seconds = time.perf_counter() - begin
                selected = annotation.get('candidate_index', 0)
                mask = predicted[selected].astype(bool)
                masks.append(mask)
                for i, candidate in enumerate(predicted):
                    ref = f'candidates/{aid:04d}_{i}.png'
                    Image.fromarray(candidate.astype(np.uint8) * 255).save(output_dir / ref)
                    output_files.append(ref)
                ref = f'masks/{aid:04d}_{label}.png'
                Image.fromarray(mask.astype(np.uint8) * 255).save(output_dir / ref)
                output_files.append(ref)
                agreement = mask_agreement(mask, second)
                preview = rgb.astype(np.float32) * .65
                preview[mask & ~second] += np.array([255, 0, 255]) * .35
                preview[second & ~mask] += np.array([0, 255, 255]) * .35
                preview[mask & second] += np.array([255, 255, 255]) * .35
                ref_agreement = f'agreement/{aid:04d}_{label}.png'
                Image.fromarray(preview.astype(np.uint8)).save(output_dir / ref_agreement)
                output_files.append(ref_agreement)
                records.append({**annotation, **agreement, 'mask_ref': ref,
                                'sam2_mask_ref': str(seed_path.relative_to(self.root)).replace('\\', '/'),
                                'sam2_mask_sha256': seed['mask_sha256'],
                                'decode_seconds': seconds,
                                'candidate_index': selected,
                                'model_internal_quality_scores_not_measured_accuracy': scores.tolist(),
                                'annotation_status': 'draft', 'human_verified': False})
        for index in sorted(range(len(annotations)), key=lambda i: (annotations[i]['label'] == 'ignore', annotations[i]['id'])):
            mask, label = masks[index], annotations[index]['label']
            overlay[mask] = overlay[mask] * .72 + np.array(COLORS[label]) * .28
        visual = Image.fromarray(overlay.astype(np.uint8))
        draw = ImageDraw.Draw(visual)
        for a in annotations:
            x, y = a['positive'][0]
            draw.text((x + 3, y + 2), f"{a['id']:04d}", fill='white', stroke_width=1, stroke_fill='black')
        draw.rectangle((0, 0, width, 18), fill='black')
        draw.text((4, 3), 'Micro-SAM LM | DRAFT / NOT GROUND TRUTH', fill='yellow')
        visual.save(output_dir / 'overlay.png')
        pair = Image.new('RGB', (width * 2, height))
        pair.paste(Image.open(seed_dir / 'overlay_ids.png').convert('RGB'), (0, 0))
        pair.paste(visual, (width, 0))
        pair.save(output_dir / 'sam2_vs_micro_sam.png')
        (output_dir / 'instances.json').write_text(json.dumps(records, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        output_files += ['overlay.png', 'sam2_vs_micro_sam.png', 'instances.json']
        metrics = {'instance_count': len(records), 'instances': records,
                   'encode_seconds': encode_seconds,
                   'inference_seconds': encode_seconds + sum(r['decode_seconds'] for r in records),
                   'parameter_count': self.parameter_count,
                   'points_sha256': sha256(points),
                   'precision': 'bfloat16 autocast; weights float32' if device.startswith('cuda') else 'float32',
                   'empty_instance_ids': [r['id'] for r in records if r['micro_sam_area_px'] == 0],
                   'agreement_scope': 'raw full-resolution corresponding masks by instance ID; both are model drafts; IoU is model-output agreement, NOT accuracy',
                   'agreement_colors': 'magenta Micro-SAM only; cyan SAM2 only; white both',
                   'model_role': 'microscopy LM pretrained point segmentation; classes supplied by unchanged stage1 prompt author',
                   'adapter': 'official SAM SamPredictor, equivalent state loading to BioImage.IO PredictorAdaptor; no napari/training package installed',
                   'postprocessing': False, 'training_performed': False}
        return ProbeOutput(metrics, output_files)

    def release(self):
        if self.predictor is not None:
            self.predictor.reset_image()
        self.predictor = None
        gc.collect()
