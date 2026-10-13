"""P6 adapter fixtures and data identity; no camera, COM or Tk required."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from demo.workbench_coverage import WorkbenchCoverageHub, freeze_source
from demo.coverage_session import CoverageRequest, compute_request


def source_fixture(folder, *, target="S0001", stage="initial", cycle=1, recheck=None,
                   frame="frame-1", mask=None, manual=False, valid=True):
    folder = Path(folder)
    if mask is None:
        mask = np.zeros((48, 72), bool)
        mask[10:19, 10:19] = True
        mask[26:30, 26:30] = True
    rgb = np.indices((*mask.shape, 3)).sum(axis=0).astype(np.uint8) + 40
    path = folder / f"cycle_{cycle:03d}"
    if stage == "post":
        path /= f"recheck_{recheck:03d}"
    path.mkdir(parents=True, exist_ok=True)
    phase = "post" if stage == "post" else "pre"
    image = path / (phase + ".png")
    Image.fromarray(rgb).save(image)
    metadata = {"observation": {"task_id": "test-task", "observation_id": "obs-" + frame,
        "frame_id": frame, "raw_image_ref": str(image)}, "raw_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "segmentation_sha256": "a" * 64, "roi_segmentation": {"sha256": "b" * 64},
        "captured_at": "2026-10-13T01:00:00+08:00"}
    (path / (phase + ".json")).write_text(json.dumps(metadata), encoding="utf-8")
    task = {"task_id": "test-task", "targets": {target: {"source": "manual" if manual else "algorithm"}},
        "references": {"initial_image": str(image)}}
    (folder / "task_manifest.json").write_text(json.dumps(task), encoding="utf-8")
    kind = ("manual_region" if stage == "initial" else "manual_roi_segmented") if manual else "segmented_stain"
    return freeze_source(task_id="test-task", folder=folder, target_id=target, stage=stage, cycle=cycle,
        recheck=recheck, frame_id=frame, image_bgr=rgb[..., ::-1], mask=mask, mask_kind=kind,
        metadata=metadata, input_valid=valid, input_reasons=() if valid else ("ROI_POSITION_SHIFTED",))


class WorkbenchCoverageTests(unittest.TestCase):
    def test_full_fragmented_mask_is_frozen_and_mutable_callers_cannot_change_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            mask = np.zeros((48, 72), np.uint8)
            mask[4:10, 5:11], mask[30:33, 40:43] = 255, 255
            source = source_fixture(tmp, mask=mask)
            mask[:] = 0
            self.assertEqual(np.count_nonzero(source.dataset.input.mask), 45)
            with self.assertRaises(ValueError):
                source.dataset.input.mask.setflags(write=True)
            with self.assertRaises(ValueError):
                source.dataset.image_rgb[0, 0] = 0
            binding = source.binding
            binding["target_id"] = "different"
            self.assertEqual(source.binding["target_id"], "S0001")

    def test_manual_region_and_frozen_roi_segmentation_keep_distinct_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            initial = source_fixture(tmp, manual=True)
            post = source_fixture(tmp, stage="post", recheck=1, manual=True, frame="frame-2")
            self.assertEqual(initial.dataset.input.to_dict()["area_kind"], "manual_region_px")
            self.assertEqual(post.dataset.input.mask_kind, "manual_roi_segmented")
            self.assertEqual(post.dataset.input.to_dict()["area_kind"], "target_mask_px")

    def test_hub_bounded_latest_and_old_task_or_observation_cannot_replace_current(self):
        owner = SimpleNamespace(task={"task_id": "test-task"}, coverage_changed=lambda: None)
        hub = WorkbenchCoverageHub(owner)
        self.addCleanup(hub.close)
        with tempfile.TemporaryDirectory() as tmp:
            initial = source_fixture(tmp)
            latest = source_fixture(tmp, stage="post", cycle=3, recheck=2, frame="frame-4")
            older = source_fixture(tmp, stage="pre", cycle=2, frame="frame-3")
            for source in (initial, latest, older):
                hub.ingest(source)
            self.assertIs(hub.get("S0001", latest=True), latest)
            self.assertEqual(len(hub.sources["S0001"]), 2)
            same_epoch_wrong_frame = source_fixture(tmp, stage="post", cycle=3, recheck=2, frame="stale-collision")
            hub.ingest(same_epoch_wrong_frame)
            self.assertIs(hub.get("S0001", latest=True), latest)
            owner.task = {"task_id": "new-task"}
            hub.reset()
            hub.ingest(initial)
            self.assertFalse(hub.sources)

    def test_default_workbench_diameter_is_unknown_and_never_infers_real_nozzle(self):
        owner = SimpleNamespace(task={}, coverage_changed=lambda: None)
        hub = WorkbenchCoverageHub(owner)
        self.addCleanup(hub.close)
        with tempfile.TemporaryDirectory() as tmp:
            source = source_fixture(tmp)
            plan = compute_request(CoverageRequest(1, source.dataset, hub.profile))
            self.assertIsNone(hub.profile.diameter)
            self.assertEqual(plan.status, "MODEL_UNAVAILABLE")
            self.assertIsNone(plan.metrics)
            self.assertFalse(plan.executable)

    def test_shifted_source_retains_warning_and_original_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = source_fixture(tmp, stage="post", recheck=1, valid=False)
            self.assertFalse(source.binding["input_valid"])
            self.assertIn("ROI_POSITION_SHIFTED", source.binding["input_reasons"])
            self.assertEqual(source.dataset.input.mask.shape, (48, 72))


if __name__ == "__main__":
    unittest.main()
