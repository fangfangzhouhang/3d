"""A-owned offline focus calibration; real fixtures are development data."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from microcleaning.data_learning.focus_calibration import (
    evaluate_focus_manifest, load_focus_manifest, load_focus_profile,
    main, write_candidate_profile,
)
from microcleaning.data_learning.image_quality import ImageQualityPolicy
from microcleaning.data_learning.replay_camera import ReplayCamera, ReplayFrame

HAS_DEPS = all(importlib.util.find_spec(name) is not None for name in ("cv2", "numpy"))
FIXTURES = Path(__file__).parent / "fixtures" / "gate1_focus"
# Explicit regression-only input. This value is NOT a selected device calibration.
TEST_REFERENCE_ONLY = 1200.0


@unittest.skipUnless(HAS_DEPS, "Requires perception-opencv dependencies")
class FocusCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.body = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8-sig"))
        for item in self.body["images"]:
            shutil.copyfile(FIXTURES / item["path"], self.root / item["path"])
        self.manifest = self.root / "manifest.json"
        self.write_manifest()

    def tearDown(self):
        self.temporary.cleanup()

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.body, ensure_ascii=False), encoding="utf-8")

    def candidate_report(self):
        return evaluate_focus_manifest(
            self.manifest,
            candidate_policy=ImageQualityPolicy(focus_reference=TEST_REFERENCE_ONLY),
        )

    def profile_path(self):
        path = self.root / "candidate.json"
        write_candidate_profile(self.candidate_report(), path)
        return path

    def rewrite_profile(self, path, mutate, *, resign=False):
        body = json.loads(path.read_text(encoding="utf-8"))
        mutate(body)
        if resign:
            unhashed = {k: v for k, v in body.items() if k != "content_sha256"}
            canonical = json.dumps(unhashed, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False)
            body["content_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        path.write_text(json.dumps(body), encoding="utf-8")

    def test_real_fixture_bytes_reproduce_default_defocus_miss_without_devices(self):
        import cv2

        with patch.object(cv2, "VideoCapture", side_effect=AssertionError("camera forbidden")):
            with patch.dict(sys.modules, {"serial": None}):
                report = evaluate_focus_manifest(self.manifest)
        self.assertEqual(1, report["default_counts"]["missed_defocused"])
        self.assertEqual(0, report["default_counts"]["flagged_clear"])
        self.assertIsNone(report["candidate_policy"])
        self.assertIsNone(report["reference_interval"]["chosen_reference"])
        self.assertFalse(report["can_claim_validated"])
        by_id = {row["sample_id"]: row for row in report["rows"]}
        self.assertGreater(by_id["normal_000"]["metrics"]["laplacian_variance"],
                           by_id["defocus"]["metrics"]["laplacian_variance"])
        self.assertGreater(by_id["restored"]["metrics"]["laplacian_variance"],
                           by_id["defocus"]["metrics"]["laplacian_variance"])
        self.assertEqual([1.0, 1.0, 1.0],
                         [row["default"]["focus"] for row in report["rows"]])

    def test_explicit_regression_policy_distinguishes_real_defocus_and_reuses_replay_camera(self):
        report = self.candidate_report()
        self.assertEqual(0, report["candidate_counts"]["missed_defocused"])
        self.assertEqual(0, report["candidate_counts"]["flagged_clear"])
        profile = load_focus_profile(self.profile_path(), expected_context=self.body["context"])
        camera = ReplayCamera(
            (ReplayFrame("pre", "real-defocus", "defocus.png"),),
            base_dir=self.root, quality_policy=profile.quality_policy,
        )
        observation = camera.capture("offline-focus-regression", "pre")
        self.assertIn("FOCUS_LOW", observation.quality_flags)
        self.assertEqual(100.0, ImageQualityPolicy().focus_reference)

    def test_same_session_and_three_conditions_never_become_independent_validation(self):
        report = self.candidate_report()
        self.assertEqual(1, report["declared_session_count"])
        self.assertEqual(3, report["declared_batch_count"])
        self.assertEqual("not_performed", report["independent_validation_status"])
        self.assertFalse(report["can_claim_validated"])
        path = self.profile_path()
        body = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("candidate", body["status"])
        self.rewrite_profile(path, lambda b: b.update(
            status="validated", independent_validation_status="passed"), resign=True)
        with self.assertRaisesRegex(ValueError, "ONLY_OFFLINE_CANDIDATE"):
            load_focus_profile(path)

    def test_export_requires_explicit_candidate_and_never_overwrites(self):
        path = self.root / "candidate.json"
        with self.assertRaisesRegex(ValueError, "EXPLICIT_CANDIDATE"):
            write_candidate_profile(evaluate_focus_manifest(self.manifest), path)
        self.assertFalse(path.exists())
        write_candidate_profile(self.candidate_report(), path)
        original = path.read_bytes()
        with self.assertRaises(FileExistsError):
            write_candidate_profile(self.candidate_report(), path)
        self.assertEqual(original, path.read_bytes())

    def test_profile_content_tampering_is_rejected(self):
        path = self.profile_path()
        self.rewrite_profile(path, lambda b: b["quality_policy"].update(focus_reference=999.0))
        with self.assertRaisesRegex(ValueError, "PROFILE_CONTENT_SHA256"):
            load_focus_profile(path)

    def test_loaded_profile_is_frozen_and_context_mismatch_is_rejected(self):
        path = self.profile_path()
        profile = load_focus_profile(path, expected_context=self.body["context"])
        with self.assertRaises(FrozenInstanceError):
            profile.quality_policy.focus_reference = 1.0
        returned = profile.context
        returned["resolution_px"][0] = 99
        self.assertEqual([640, 480], profile.context["resolution_px"])
        changed = copy.deepcopy(self.body["context"])
        changed["setup_id"] = "different-setup"
        with self.assertRaisesRegex(ValueError, "PROFILE_CONTEXT_MISMATCH"):
            load_focus_profile(path, expected_context=changed)
        self.rewrite_profile(path, lambda b: b["quality_policy"].update(focus_reference=800.0))
        self.assertEqual(TEST_REFERENCE_ONLY, profile.quality_policy.focus_reference)

    def test_signed_but_incomplete_profile_or_invalid_policy_is_rejected(self):
        mutations = (
            lambda b: b["quality_policy"].pop("focus_reference"),
            lambda b: b["quality_policy"].update(unexpected_field=3),
            lambda b: b["quality_policy"].update(dark_pixel_max=1.5),
            lambda b: b.update(source_images=[]),
            lambda b: b.update(source_manifest_sha256="invalid"),
            lambda b: b["context"].pop("magnification"),
        )
        original = self.profile_path().read_bytes()
        path = self.root / "invalid.json"
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                path.write_bytes(original)
                self.rewrite_profile(path, mutate, resign=True)
                with self.assertRaises(ValueError):
                    load_focus_profile(path)

    def test_nonfinite_boolean_and_nonpositive_candidates_are_rejected(self):
        for value in (float("nan"), float("inf"), -float("inf"), 0.0, -1.0, True, 10**400):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    evaluate_focus_manifest(
                        self.manifest, candidate_policy=ImageQualityPolicy(focus_reference=value))
        with self.assertRaisesRegex(ValueError, "POSITIVE_FOCUS_THRESHOLD"):
            evaluate_focus_manifest(self.manifest,
                                    candidate_policy=ImageQualityPolicy(minimum_quality=0.0))
        self.manifest.write_text('{"schema": NaN}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "NONFINITE_JSON"):
            load_focus_manifest(self.manifest)

    def test_missing_changed_and_undecodable_images_are_rejected(self):
        missing = self.root / self.body["images"][0]["path"]
        original = missing.read_bytes()
        missing.unlink()
        with self.assertRaises(FileNotFoundError):
            load_focus_manifest(self.manifest)
        missing.write_bytes(original + b"changed")
        with self.assertRaisesRegex(ValueError, "IMAGE_SHA256_MISMATCH"):
            load_focus_manifest(self.manifest)
        missing.write_bytes(b"not an image")
        self.body["images"][0]["sha256"] = hashlib.sha256(missing.read_bytes()).hexdigest()
        self.write_manifest()
        with self.assertRaises(ValueError):
            evaluate_focus_manifest(self.manifest)

    def test_image_change_after_manifest_loading_is_rejected(self):
        loaded = load_focus_manifest(self.manifest)
        shutil.copyfile(self.root / "normal.png", self.root / "defocus.png")
        with self.assertRaisesRegex(ValueError, "IMAGE_CHANGED_DURING_REPLAY"):
            evaluate_focus_manifest(loaded)

    def test_resolution_mismatch_is_not_silently_resized(self):
        self.body["context"]["resolution_px"] = [641, 480]
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "IMAGE_RESOLUTION_CONTEXT_MISMATCH"):
            evaluate_focus_manifest(self.manifest)

    def test_empty_or_single_class_manifest_is_not_calibratable(self):
        original = copy.deepcopy(self.body)
        self.body["images"] = []
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "MANIFEST_IMAGES_REQUIRED"):
            load_focus_manifest(self.manifest)
        self.body = original
        self.body["images"] = [item for item in self.body["images"] if item["label"] == "clear"]
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "CLEAR_AND_DEFOCUSED"):
            load_focus_manifest(self.manifest)

    def test_paths_operator_labels_and_sample_identity_are_checked(self):
        mutations = (
            lambda b: b["images"][0].update(path="../normal.png"),
            lambda b: b["images"][0].update(path=str(self.root / "normal.png")),
            lambda b: b["images"][0].update(label_source="model_guess"),
            lambda b: b["images"][0].update(label={"not": "a label"}),
            lambda b: b["images"][1].update(sample_id=b["images"][0]["sample_id"]),
            lambda b: b.update(purpose="validation"),
        )
        original = copy.deepcopy(self.body)
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.body = copy.deepcopy(original)
                mutate(self.body)
                self.write_manifest()
                with self.assertRaises(ValueError):
                    load_focus_manifest(self.manifest)

    def test_uncertain_label_is_not_counted_as_correct_clear_or_defocus(self):
        self.body["images"][2]["label"] = "uncertain"
        self.write_manifest()
        report = evaluate_focus_manifest(self.manifest)
        self.assertEqual(1, report["default_counts"]["uncertain"])
        self.assertIsNone(report["rows"][2]["default"]["focus_matches_operator_label"])

    def test_uncertain_duplicate_does_not_hide_conflicting_operator_labels(self):
        reference = self.body["images"][0]
        for index, label in enumerate(("uncertain", "defocused")):
            item = dict(reference, sample_id="duplicate-" + str(index),
                        path="duplicate-" + str(index) + ".png", label=label)
            shutil.copyfile(self.root / "normal.png", self.root / item["path"])
            self.body["images"].append(item)
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "CONFLICTING_LABELS"):
            load_focus_manifest(self.manifest)

    def test_nonseparable_development_data_returns_no_selected_reference(self):
        for item in self.body["images"]:
            item["label"] = "clear" if item["sample_id"] == "defocus" else "defocused"
        self.write_manifest()
        report = evaluate_focus_manifest(self.manifest)
        self.assertFalse(report["reference_interval"]["separable_on_development_images"])
        self.assertIsNone(report["reference_interval"]["chosen_reference"])

    def test_cli_default_reports_miss_without_exporting_or_changing_input(self):
        output = self.root / "default-report"
        before = {p.name: p.read_bytes() for p in self.root.glob("*.png")}
        with contextlib.redirect_stdout(io.StringIO()):
            result = main([str(self.manifest), "--output-dir", str(output)])
        self.assertEqual(0, result)
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(1, report["default_counts"]["missed_defocused"])
        self.assertFalse((output / "candidate_profile.json").exists())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.glob("*.png")})
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2, main([str(self.manifest), "--output-dir", str(output)]))

    def test_cli_explicit_export_and_replay_preserve_candidate_results(self):
        output, replay = self.root / "explicit", self.root / "profile-replay"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, main([
                str(self.manifest), "--output-dir", str(output),
                "--candidate-reference", str(TEST_REFERENCE_ONLY), "--export-profile",
            ]))
            self.assertEqual(0, main([
                str(self.manifest), "--output-dir", str(replay),
                "--profile", str(output / "candidate_profile.json"),
            ]))
        one = json.loads((output / "report.json").read_text(encoding="utf-8"))
        two = json.loads((replay / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(one["candidate_counts"], two["candidate_counts"])
        self.assertEqual(one["candidate_policy"], two["candidate_policy"])
        self.assertEqual("not_performed", two["independent_validation_status"])

    def test_cli_invalid_profile_or_export_arguments_create_no_output(self):
        output = self.root / "must-not-exist"
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main([str(self.manifest), "--output-dir", str(output), "--export-profile"])
            self.assertEqual(2, caught.exception.code)
            self.assertEqual(2, main([
                str(self.manifest), "--output-dir", str(output),
                "--profile", str(self.root / "missing.json"),
            ]))
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
