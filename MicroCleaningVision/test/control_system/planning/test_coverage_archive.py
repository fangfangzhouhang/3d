"""P3 complete-mask archives, replay, damage detection and no overwrite."""

import json
import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from control_system.planning.test_coverage_visualization import sample_plan
from microcleaning.control_system.planning.coverage_archive import export_coverage_preview, load_coverage_preview
from microcleaning.control_system.planning.coverage_model import Evidence, FootprintKernel
from microcleaning.control_system.planning.coverage_planner import CoverageConstraints, CoveragePolicy, PositionUncertainty, plan_coverage
from microcleaning.control_system.planning.coverage_visualization import RenderOptions


class CoverageArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "完整覆盖预览"

    def export(self, **kwargs):
        self.image, self.plan = sample_plan()
        return export_coverage_preview(self.image, self.plan, self.folder, **kwargs)

    def rewrite_manifest(self, edit):
        path = self.folder / "coverage_preview.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        edit(data)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_round_trip_full_mask_metrics_profile_and_prefix(self):
        options = RenderOptions(overlap=True, route=False)
        self.export(options=options, step_count=2)
        loaded = load_coverage_preview(self.folder)
        np.testing.assert_array_equal(self.image, loaded.image_rgb)
        np.testing.assert_array_equal(self.plan.input.mask, loaded.plan.input.mask)
        self.assertEqual(self.plan.to_dict(), loaded.plan.to_dict())
        self.assertEqual(options, loaded.options)
        self.assertEqual(2, loaded.step_count)
        self.assertFalse(loaded.plan.executable)
        with self.assertRaises(ValueError):
            loaded.image_rgb.setflags(write=True)
        with Image.open(self.folder / "coverage_overlay.png") as png:
            self.assertIn("executable=false", png.info["safety"])
            self.assertEqual(self.plan.to_dict()["plan_id"], png.info["plan_id"])

    def test_constraint_masks_and_error_envelope_are_replayed(self):
        image, first = sample_plan()
        forbidden = np.zeros(first.input.mask.shape, bool)
        forbidden[:, 30:33] = True
        constraints = CoverageConstraints(forbidden_mask=forbidden, evidence=Evidence("assumed", "simulation-region"),
            position_uncertainty=PositionUncertainty(1, "px", Evidence("assumed", "error-box")))
        plan = plan_coverage(first.input, first.model, policy=first.policy, constraints=constraints)
        with self.assertRaisesRegex(ValueError, "ORIGINAL_CONSTRAINT"):
            export_coverage_preview(image, plan, self.folder)
        export_coverage_preview(image, plan, self.folder, constraints=constraints)
        loaded = load_coverage_preview(self.folder)
        self.assertEqual(plan.to_dict(), loaded.plan.to_dict())
        np.testing.assert_array_equal(forbidden, loaded.constraints.forbidden_mask)
        self.assertIsNotNone(loaded.plan.metrics.conservative_coverage_rate)

    def test_explicit_asymmetric_kernel_not_replaced_by_circle(self):
        image, first = sample_plan()
        kernel = FootprintKernel(np.array([[1, 0, 1], [0, 1, 0]], bool), (2, 0), first.model.profile)
        plan = plan_coverage(first.input, kernel, policy=CoveragePolicy(max_stations=3))
        export_coverage_preview(image, plan, self.folder)
        loaded = load_coverage_preview(self.folder)
        np.testing.assert_array_equal(kernel.mask, loaded.plan.model.kernel.mask)
        self.assertEqual((2, 0), loaded.plan.model.kernel.anchor_px)

    def test_unknown_model_can_be_archived_without_fabricated_metrics(self):
        image, plan = sample_plan(diameter=None)
        export_coverage_preview(image, plan, self.folder)
        loaded = load_coverage_preview(self.folder)
        self.assertEqual("MODEL_UNAVAILABLE", loaded.plan.status)
        self.assertIsNone(loaded.plan.metrics)
        self.assertIsNone(loaded.plan.model.profile.diameter)

    def test_existing_destination_is_never_overwritten(self):
        self.export()
        before = (self.folder / "coverage_preview.json").read_bytes()
        with self.assertRaises(FileExistsError):
            export_coverage_preview(self.image, self.plan, self.folder)
        self.assertEqual(before, (self.folder / "coverage_preview.json").read_bytes())

    def test_damaged_file_is_rejected(self):
        self.export()
        path = self.folder / "source_image.png"
        path.write_bytes(path.read_bytes() + b"damaged")
        with self.assertRaisesRegex(ValueError, "FILE_HASH"):
            load_coverage_preview(self.folder)

    def test_modified_numeric_results_are_rejected_by_recomputation(self):
        self.export()
        self.rewrite_manifest(lambda data: data["plan"]["metrics"].update(geometric_coverage_rate=1.0))
        with self.assertRaisesRegex(ValueError, "REPLAY_MISMATCH"):
            load_coverage_preview(self.folder)

    def test_hardware_flags_and_arbitrary_file_names_are_rejected(self):
        self.export()
        self.rewrite_manifest(lambda data: data.update(executable=True))
        with self.assertRaisesRegex(ValueError, "HARDWARE_FLAGS"):
            load_coverage_preview(self.folder)
        self.rewrite_manifest(lambda data: data.update(executable=False, files={"../escape": {}}))
        with self.assertRaisesRegex(ValueError, "FIXED_FILE"):
            load_coverage_preview(self.folder)

    def test_failed_export_has_no_completed_manifest(self):
        image, plan = sample_plan()
        with patch("microcleaning.control_system.planning.coverage_archive.np.savez_compressed", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                export_coverage_preview(image, plan, self.folder)
        self.assertFalse((self.folder / "coverage_preview.json").exists())
        second_folder = Path(self.temp.name) / "partial_manifest"
        original_write = Path.write_text
        def short_write(path, value, **kwargs):
            original_write(path, value[:80], **kwargs)
            raise OSError("short manifest write")
        with patch.object(Path, "write_text", short_write):
            with self.assertRaisesRegex(OSError, "short manifest"):
                export_coverage_preview(image, plan, second_folder)
        self.assertFalse((second_folder / "coverage_preview.json").exists())
        self.assertTrue((second_folder / "coverage_preview.json.tmp").exists())

    def test_playback_metadata_damage_is_rejected(self):
        self.export()
        self.rewrite_manifest(lambda data: data["view"].update(step_count=0))
        with self.assertRaisesRegex(ValueError, "VISUALIZATION_MISMATCH"):
            load_coverage_preview(self.folder)

    def test_declared_huge_array_is_rejected_before_numpy_load(self):
        self.export()
        path = self.folder / "coverage_masks.npz"
        replacement = io.BytesIO()
        with zipfile.ZipFile(path) as original, zipfile.ZipFile(replacement, "w") as modified:
            for name in original.namelist():
                data = original.read(name)
                if name == "target_mask.npy":
                    header = io.BytesIO()
                    np.lib.format.write_array_header_1_0(header, {"descr": "|b1", "fortran_order": False, "shape": (100000, 100000)})
                    data = header.getvalue()
                modified.writestr(name, data)
        blob = replacement.getvalue()
        path.write_bytes(blob)
        self.rewrite_manifest(lambda data: data["files"]["coverage_masks.npz"].update(
            sha256=hashlib.sha256(blob).hexdigest(), size_bytes=len(blob)))
        with patch("microcleaning.control_system.planning.coverage_archive.np.load") as loader:
            with self.assertRaisesRegex(ValueError, "RESOURCE_OR_DTYPE"):
                load_coverage_preview(self.folder)
            loader.assert_not_called()
