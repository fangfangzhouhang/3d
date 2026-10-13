"""Headless P5 archives: full replay, CSV/NPZ consistency and observable failures."""

import ast
import csv
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

HAS_DEPS = all(importlib.util.find_spec(v) is not None for v in ("numpy", "cv2", "PIL"))
if HAS_DEPS:
    import numpy as np
    from PIL import Image
    from microcleaning.control_system.planning import coverage_analysis_report
    from microcleaning.control_system.planning.coverage_analysis import AnalysisSpec, analyze_coverage
    from microcleaning.control_system.planning.coverage_analysis_report import export_analysis_report, load_analysis_report, main
    from microcleaning.control_system.planning.coverage_archive import export_coverage_preview, load_coverage_preview
    from microcleaning.control_system.planning.coverage_model import Evidence, FootprintProfile, build_kernel
    from microcleaning.control_system.planning.coverage_planner import CoverageConstraints, CoverageInput, CoveragePolicy, plan_coverage


@unittest.skipUnless(HAS_DEPS, "需要覆盖规划图像依赖")
class CoverageAnalysisReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "覆盖规划"
        self.root.mkdir()
        target = np.zeros((17, 23), bool)
        target[4:10, 4:10] = True
        target[12:14, 18:20] = True
        self.input = CoverageInput(target, "S0001", "synthetic", "test:complete-mask")
        self.model = build_kernel(FootprintProfile("virtual", 4, "px", Evidence("assumed", "test:explicit-diameter")))
        forbidden = np.zeros_like(target)
        forbidden[8, 11] = True
        self.constraints = CoverageConstraints(forbidden_mask=forbidden, evidence=Evidence("assumed", "test:no-spray"))
        self.image = np.full((*target.shape, 3), 75, np.uint8)
        plan = plan_coverage(self.input, self.model, constraints=self.constraints, policy=CoveragePolicy(max_stations=8))
        self.input_dir = export_coverage_preview(self.image, plan, self.root / "输入", constraints=self.constraints)
        self.archive = load_coverage_preview(self.input_dir)
        self.spec = AnalysisSpec(station_budgets=(1, 4), diameters=(2, 6), position_errors=(0, 1))
        self.analysis = analyze_coverage(self.input, self.model, spec=self.spec, constraints=self.constraints)
        self.output = self.root / "分析"

    def export(self, figures=False):
        return export_analysis_report(self.archive, self.analysis, self.output, include_figures=figures)

    def rewrite(self, callback):
        path = self.output / "analysis.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        callback(data)
        path.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")

    def update_file_hash(self, manifest, name):
        blob = (self.output / name).read_bytes()
        manifest["files"][name] = {"sha256": hashlib.sha256(blob).hexdigest(), "size_bytes": len(blob)}

    def test_complete_input_masks_constraints_timing_csv_and_results_round_trip(self):
        input_sha = hashlib.sha256((self.input_dir / "coverage_preview.json").read_bytes()).hexdigest()
        self.export()
        loaded = load_analysis_report(self.output)
        self.assertEqual(self.analysis.to_dict(), loaded.analysis.to_dict())
        np.testing.assert_array_equal(self.input.mask, loaded.archive.plan.input.mask)
        np.testing.assert_array_equal(self.constraints.forbidden_mask, loaded.archive.constraints.forbidden_mask)
        self.assertEqual(input_sha, hashlib.sha256((self.input_dir / "coverage_preview.json").read_bytes()).hexdigest())
        self.assertFalse(loaded.analysis.executable)
        rows = list(csv.DictReader(io.StringIO((self.output / "analysis.csv").read_text(encoding="utf-8-sig"))))
        self.assertEqual(54, len(rows))
        for row, result in zip(rows, self.analysis.runs):
            self.assertEqual(result.to_dict()["run_id"], row["run_id"])
            self.assertEqual(str(result.metrics.covered_area_px), row["covered_area_px"])
            self.assertEqual("False", row["executable"])
        with np.load(self.output / "analysis_masks.npz", allow_pickle=False) as arrays:
            np.testing.assert_array_equal(self.analysis.runs[0].evaluation.uncovered_mask, arrays["r000_uncovered_mask"])

    @unittest.skipUnless(importlib.util.find_spec("matplotlib") is not None, "需要 requirements/coverage-analysis.txt")
    def test_actual_agg_figures_have_matching_identity_and_can_be_verified(self):
        self.export(figures=True)
        loaded = load_analysis_report(self.output)
        self.assertTrue(loaded.include_figures)
        for name in ("comparison.png", "sensitivity.png"):
            with Image.open(self.output / name) as image:
                self.assertGreater(image.width, 1000)
                self.assertGreater(image.height, 800)
                self.assertEqual(self.analysis.to_dict()["analysis_id"], image.info["analysis_id"])
                self.assertIn("executable=false", image.info["safety"])
                image.verify()

    def test_existing_directory_is_not_overwritten_and_wrong_archive_is_rejected(self):
        self.output.mkdir()
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.export()
        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))
        other = CoverageInput(np.ones((17, 23), bool), "S0001", "synthetic", "test:other-mask")
        analysis = analyze_coverage(other, self.model, spec=AnalysisSpec(station_budgets=(1,)))
        with self.assertRaisesRegex(ValueError, "MATCHING_COMPLETE_INPUT_ARCHIVE"):
            export_analysis_report(self.archive, analysis, self.root / "错配", include_figures=False)
        self.assertFalse((self.root / "错配").exists())

    def test_failed_export_has_no_complete_manifest(self):
        original_save = np.savez_compressed
        def fail_report(path, **arrays):
            if Path(path).name == "analysis_masks.npz":
                raise OSError("disk full")
            return original_save(path, **arrays)
        with patch.object(coverage_analysis_report.np, "savez_compressed", side_effect=fail_report):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.export()
        self.assertTrue((self.output / "input" / "coverage_preview.json").exists())
        self.assertFalse((self.output / "analysis.json").exists())

    def test_damaged_file_and_modified_metrics_are_rejected(self):
        self.export()
        csv_path = self.output / "analysis.csv"
        old = csv_path.read_bytes()
        csv_path.write_bytes(old + b"damage")
        with self.assertRaisesRegex(ValueError, "ANALYSIS_FILE_HASH_MISMATCH"):
            load_analysis_report(self.output)
        csv_path.write_bytes(old)
        self.rewrite(lambda d: d["analysis"]["runs"][0]["metrics"].update(geometric_coverage_rate=1))
        with self.assertRaisesRegex(ValueError, "ANALYSIS_DETERMINISTIC_REPLAY_MISMATCH"):
            load_analysis_report(self.output)

    def test_csv_and_mask_changes_cannot_pass_by_updating_file_hashes(self):
        self.export()
        csv_path = self.output / "analysis.csv"
        original = csv_path.read_bytes()
        csv_path.write_bytes(original.replace(b"CENTER_POINT_COMPONENTS", b"WRONG_METHOD", 1))
        self.rewrite(lambda d: self.update_file_hash(d, "analysis.csv"))
        with self.assertRaisesRegex(ValueError, "ANALYSIS_CSV_REPLAY_MISMATCH"):
            load_analysis_report(self.output)
        csv_path.write_bytes(original)
        self.rewrite(lambda d: self.update_file_hash(d, "analysis.csv"))
        path = self.output / "analysis_masks.npz"
        with np.load(path, allow_pickle=False) as data:
            arrays = {k: data[k] for k in data.files}
        arrays["r000_covered_mask"][0, 0] ^= True
        np.savez_compressed(path, **arrays)
        def alter(d):
            self.update_file_hash(d, "analysis_masks.npz")
            from microcleaning.control_system.planning.coverage_model import array_hash
            d["arrays"]["r000_covered_mask"]["sha256"] = array_hash(arrays["r000_covered_mask"])
        self.rewrite(alter)
        with self.assertRaisesRegex(ValueError, "ANALYSIS_REPLAY_ARRAY_HASH_MISMATCH"):
            load_analysis_report(self.output)

    def test_declared_huge_array_is_rejected_before_numpy_load(self):
        self.export()
        path = self.output / "analysis_masks.npz"
        original = path.read_bytes()
        header = io.BytesIO()
        np.lib.format.write_array_header_1_0(header, {"descr": "|b1", "fortran_order": False, "shape": (10**9, 10**9)})
        with zipfile.ZipFile(io.BytesIO(original)) as before, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as after:
            for member in before.infolist():
                after.writestr(member.filename, header.getvalue() if member.filename == "r000_covered_mask.npy" else before.read(member))
        self.rewrite(lambda d: self.update_file_hash(d, "analysis_masks.npz"))
        # P3 input replay uses np.load first; the report's compressed data must
        # be rejected before the second allocation call.
        original_load = np.load
        loads = []
        def guarded(*args, **kwargs):
            loads.append(args[0])
            if len(loads) > 1:
                raise AssertionError("oversized report array reached np.load")
            return original_load(*args, **kwargs)
        with patch.object(np, "load", side_effect=guarded):
            with self.assertRaisesRegex(ValueError, "ANALYSIS_ARRAY_SHAPE_OR_DTYPE_INVALID"):
                load_analysis_report(self.output)
        self.assertEqual(1, len(loads))

    def test_hardware_flags_file_paths_and_nonfinite_timing_are_rejected(self):
        self.export()
        original = (self.output / "analysis.json").read_bytes()
        for change, reason in ((lambda d: d["analysis"]["runs"][0].update(executable=True), "HARDWARE_FLAGS"),
                (lambda d: d["files"].update({"../escape": {}}), "FIXED_FILE_SET"),
                (lambda d: d["analysis"]["runs"][0]["timing"].update(audit_seconds=-1), "OUT_OF_RANGE")):
            (self.output / "analysis.json").write_bytes(original)
            self.rewrite(change)
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                load_analysis_report(self.output)

    def test_unknown_model_report_has_empty_metrics_and_replays(self):
        unknown = build_kernel(FootprintProfile("unknown", None, "px", Evidence("assumed", "test:unknown")))
        plan = plan_coverage(self.input, unknown)
        folder = export_coverage_preview(self.image, plan, self.root / "未知输入")
        archive = load_coverage_preview(folder)
        analysis = analyze_coverage(self.input, unknown, spec=AnalysisSpec(station_budgets=(1,)))
        export_analysis_report(archive, analysis, self.output, include_figures=False)
        restored = load_analysis_report(self.output)
        self.assertTrue(all(r.metrics is None for r in restored.analysis.runs))
        rows = list(csv.DictReader(io.StringIO((self.output / "analysis.csv").read_text(encoding="utf-8-sig"))))
        self.assertTrue(all(row["geometric_coverage_rate"] == "" for row in rows))

    def test_cli_exports_and_verifies_without_camera_serial_or_gui(self):
        with patch("cv2.VideoCapture", side_effect=AssertionError("offline")), patch("sys.stdout", new=io.StringIO()) as output:
            self.assertEqual(0, main(["--archive", str(self.input_dir), "--output", str(self.output),
                "--budgets", "1", "4", "--diameters", "2", "6", "--position-errors", "0", "1", "--no-figures"]))
            self.assertIn('"executable":false', output.getvalue())
            self.assertEqual(0, main(["--verify", str(self.output)]))
        with patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                main(["--verify", str(self.output), "--diameters", "2"])
            self.assertEqual(2, main(["--archive", str(self.input_dir), "--output", str(self.output), "--no-figures"]))
        tree = ast.parse(Path(coverage_analysis_report.__file__).read_text(encoding="utf-8"))
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(any(v in name for v in ("serial", "demo", "orchestration", "workbench", "firmware", "tkinter")) for name in imports))


if __name__ == "__main__":
    unittest.main()
