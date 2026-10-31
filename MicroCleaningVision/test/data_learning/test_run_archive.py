"""Archive tests use synthetic fixtures only; no camera or serial connection."""
import hashlib
import json
import shutil
import struct
import zlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from microcleaning.data_learning.run_archive import (
    ArchiveError, RunArchiveExporter, main, verify_archive,
)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def png(value):
    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes([0, value, value, 0, value, value]))) + chunk(b"IEND", b""))


def snapshot(root):
    return {p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_size,
            p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


class RunArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.data = self.base / "MicroCleaningData"
        self.exporter = RunArchiveExporter(self.data)

    def fixture(self, name="task-1", mode="real", cycles=1, rechecks=0):
        root = self.source / name
        root.mkdir(parents=True)
        write_json(root / "run_config.json", {"task_id": name, "segmentation_sha256": "seg-v1",
            "metadata": {"sample_id": "sample-A", "mode": mode, "data_origin": mode,
                         "git_commit": "abc", "git_dirty": True, "source_sha256": "source-v1",
                         "firmware_identity": "unconfirmed"}})
        write_json(root / "summary.json", {"task_id": name, "mode": mode, "workflow_status": "COMPLETED"})
        write_json(root / "task_manifest.json", {"task_id": name, "metadata": {"mode": mode}})
        write_json(root / "serial.json", {"events": []})
        (root / "stage_events.jsonl").write_text('{"phase":"END"}\n', encoding="utf-8")
        (root / "empty-directory").mkdir()
        for n in range(1, cycles + 1):
            directory = root / f"cycle_{n:03d}"
            write_json(directory / "cycle.json", {"cycle": n, "target_id": f"S{n:04d}"})
            write_json(directory / "execution.json", {"pump_request": {"action_id": f"action-{n}"},
                "receipt": {"action_id": f"action-{n}", "success": True, "controller_state": "DONE"}})
            write_json(directory / "verification.json", {"match_status": "matched", "removal_rate": .9})
            for check in range(rechecks + 1):
                folder = directory if check == 0 else directory / f"recheck_{check:03d}"
                folder.mkdir(parents=True, exist_ok=True)
                for stage in ("pre", "post"):
                    image = folder / (stage + ".png")
                    image.write_bytes(png(100))
                    mask = folder / (stage + "_mask.png")
                    mask.write_bytes(png(255))
                    write_json(folder / (stage + ".json"), {
                        "observation": {"observation_id": f"obs-{n}-{check}-{stage}",
                            "frame_id": f"frame-{n}-{check}-{stage}", "task_id": name,
                            "raw_image_ref": str(image), "quality_flags": []},
                        "captured_at": "2026-10-30T10:00:00+08:00", "source_id": "fixture",
                        "settings": {"exposure": 10}, "segmentation_sha256": "seg-v1",
                        "raw_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                        "measurement": {"mask_ref": str(mask)}})
            episode = directory / "episodes" / f"episode-{n}.json"
            write_json(episode, {"episode_id": f"episode-{n}", "task_id": name,
                "action_request": {"action_id": f"action-{n}"},
                "observation_pre": {"observation_id": f"obs-{n}-0-pre"},
                "observation_post": {"observation_id": f"obs-{n}-0-post"}})
            episode.with_suffix(".sha256").write_text(hashlib.sha256(episode.read_bytes()).hexdigest())
        return root

    def checked_export(self, root):
        before = snapshot(root)
        result = self.exporter.export_run(root)
        folder = Path(result["path"])
        manifest = verify_archive(folder)
        # Independent verification: no exporter hashing/verification helper used.
        self.assertEqual({p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()},
                         {f["path"] for f in manifest["files"]})
        for item in manifest["files"]:
            raw = (folder / "raw" / item["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item["sha256"])
            self.assertEqual(len(raw), item["size_bytes"])
        self.assertEqual(before, snapshot(root))
        return folder, manifest

    def test_complete_real_run(self):
        folder, manifest = self.checked_export(self.fixture())
        self.assertEqual(folder.parent.name, "real")
        self.assertEqual(manifest["schema"], "microcleaning-run-archive-v1")
        self.assertEqual(manifest["run_id"], "task-1")
        self.assertEqual(manifest["sample_id"], "sample-A")
        self.assertIsNone(manifest["experiment_id"])
        self.assertTrue(manifest["git_dirty"])
        self.assertEqual(manifest["source_sha256"], "source-v1")
        self.assertEqual(manifest["cycles"][0]["action_ids"], ["action-1"])
        self.assertEqual(manifest["episodes"][0]["episode_id"], "episode-1")
        self.assertTrue((folder / "raw" / "empty-directory").is_dir())

    def test_mock_routing_and_eligibility(self):
        real, _ = self.checked_export(self.fixture())
        mock, manifest = self.checked_export(self.fixture("mock-task", "mock"))
        self.assertNotEqual(real.parent, mock.parent)
        self.assertEqual(mock.parent.name, "mock")
        self.assertFalse(manifest["training_eligibility"]["real_cleaning_effect"])
        self.assertEqual(manifest["observations"][0]["mask_semantics"], "predicted_segmentation")

    def test_multiple_cycles_rechecks(self):
        _, manifest = self.checked_export(self.fixture(cycles=2, rechecks=2))
        self.assertEqual(len(manifest["observations"]), 12)
        self.assertEqual(len(manifest["cycles"]), 2)
        for cycle in manifest["cycles"]:
            self.assertEqual(len(cycle["observation_refs"]), 6)
            self.assertEqual(len(cycle["rechecks"]), 2)
            for obs in manifest["observations"]:
                if obs["cycle_id"] == cycle["cycle_id"]:
                    self.assertEqual(obs["target_id"], cycle["target_id"])
        self.assertEqual({o["recheck_id"] for o in manifest["observations"]}, {None, "recheck_001", "recheck_002"})

    def test_same_pixels_preserve_observation_events(self):
        _, manifest = self.checked_export(self.fixture())
        a, b = manifest["observations"]
        self.assertEqual(a["image_sha256"], b["image_sha256"])
        self.assertNotEqual(a["observation_id"], b["observation_id"])
        self.assertNotEqual(a["frame_id"], b["frame_id"])

    def test_original_image_mask_hashes(self):
        folder, manifest = self.checked_export(self.fixture())
        for obs in manifest["observations"]:
            for name in ("image", "mask"):
                self.assertEqual(obs[name + "_sha256"], hashlib.sha256((folder / "raw" / obs[name + "_ref"]).read_bytes()).hexdigest())
            self.assertEqual(obs["mask_semantics"], "predicted_segmentation")
            self.assertFalse(obs["visual_ground_truth_eligible"])

    def test_missing_metadata_and_unknown_origin(self):
        root = self.fixture()
        write_json(root / "run_config.json", {"task_id": "task-1"})
        _, manifest = self.checked_export(root)
        self.assertIsNone(manifest["sample_id"])
        self.assertEqual(manifest["data_origin"], "unknown")
        self.assertIn("MISSING_METADATA", manifest["quality_issues"])

    def test_missing_image_retained_with_issue(self):
        root = self.fixture()
        (root / "cycle_001" / "pre.png").unlink()
        _, manifest = self.checked_export(root)
        self.assertTrue(any("IMAGE_FILE_MISSING" in x for x in manifest["quality_issues"]))
        self.assertIsNone(next(o for o in manifest["observations"] if o["stage"] == "pre")["image_sha256"])

    def test_corrupt_json_and_jsonl(self):
        root = self.fixture()
        (root / "cycle_001" / "cycle.json").write_text("{broken")
        (root / "stage_events.jsonl").write_text("{broken")
        _, manifest = self.checked_export(root)
        self.assertTrue(any(x.startswith("INVALID_JSON:") for x in manifest["quality_issues"]))
        self.assertTrue(any(x.startswith("INVALID_JSONL:") for x in manifest["quality_issues"]))

    def test_interrupted_and_anonymous_startup(self):
        root = self.source / "startup"
        root.mkdir(parents=True)
        (root / "stage_events.jsonl").write_text('{"phase":"START"}\n')
        _, manifest = self.checked_export(root)
        self.assertIsNone(manifest["run_id"])
        self.assertTrue(manifest["archive_id"].startswith("orphan-"))
        self.assertEqual(manifest["quality_status"], "INCOMPLETE")
        self.assertFalse(manifest["training_eligibility"]["real_cleaning_effect"])

    def test_failed_cancelled_and_incomplete(self):
        for status in ("FAILED", "CANCELLED", "RUNNING"):
            root = self.fixture(status)
            write_json(root / "summary.json", {"task_id": status, "workflow_status": status})
            _, manifest = self.checked_export(root)
            self.assertIn("WORKFLOW_NOT_COMPLETED", manifest["quality_issues"])
            self.assertFalse(manifest["training_eligibility"]["real_cleaning_effect"])

    def test_idempotent_export(self):
        root = self.fixture()
        folder, _ = self.checked_export(root)
        before = snapshot(folder)
        self.assertEqual(self.exporter.export_run(root)["status"], "skipped")
        self.assertEqual(before, snapshot(folder))

    def test_same_identity_different_content_refused(self):
        root = self.fixture()
        folder, _ = self.checked_export(root)
        before = snapshot(folder)
        (root / "extra.txt").write_text("different")
        with self.assertRaisesRegex(ArchiveError, "CONFLICT"):
            self.exporter.export_run(root)
        self.assertEqual(before, snapshot(folder))

    def test_existing_archive_corruption_refused(self):
        root = self.fixture()
        folder, _ = self.checked_export(root)
        image = folder / "raw/cycle_001/pre.png"
        image.write_bytes(b"tampered")
        with self.assertRaisesRegex(ArchiveError, "CONTENT_MISMATCH"):
            self.exporter.export_run(root)
        self.assertEqual(image.read_bytes(), b"tampered")

    def test_copy_failure_preserves_source_and_partial(self):
        root = self.fixture()
        before = snapshot(root)
        original = shutil.copyfile
        calls = []
        def fail(src, dst):
            calls.append(src)
            if len(calls) == 3:
                raise OSError("injected disk failure")
            return original(src, dst)
        with patch("microcleaning.data_learning.run_archive.shutil.copyfile", side_effect=fail):
            with self.assertRaisesRegex(OSError, "disk failure"):
                self.exporter.export_run(root)
        self.assertEqual(before, snapshot(root))
        partials = list(self.data.glob("runs/real/.partial-*"))
        self.assertEqual(len(partials), 1)
        self.assertFalse((partials[0] / "run_manifest.json").exists())
        with self.assertRaisesRegex(ArchiveError, "UNPUBLISHED"):
            verify_archive(partials[0])
        self.assertEqual(self.exporter.export_run(root)["status"], "exported")

    def test_repository_destination_rejected(self):
        project = Path(__file__).resolve().parents[2]
        for path in (project / "output/archive", project.parent / "MicroCleaningData"):
            with self.assertRaisesRegex(ArchiveError, "INSIDE_REPOSITORY"):
                RunArchiveExporter(path)
        root = self.fixture()
        with self.assertRaisesRegex(ArchiveError, "OVERLAP"):
            RunArchiveExporter(root / "archive").export_run(root)

    def test_source_not_modified(self):
        root = self.fixture(cycles=2, rechecks=2)
        before = snapshot(root)
        self.checked_export(root)
        self.exporter.export_run(root)
        self.assertEqual(before, snapshot(root))

    def test_wrong_declared_hashes_and_episode_digest(self):
        root = self.fixture()
        path = root / "cycle_001/pre.json"
        value = json.loads(path.read_text())
        value.update(raw_sha256="0" * 64, mask_sha256="0" * 64)
        write_json(path, value)
        (root / "cycle_001/episodes/episode-1.sha256").write_text("0" * 64)
        _, manifest = self.checked_export(root)
        self.assertTrue(any("IMAGE_HASH_MISMATCH" in x for x in manifest["quality_issues"]))
        self.assertTrue(any("MASK:" in x and "HASH_MISMATCH" in x for x in manifest["quality_issues"]))
        self.assertTrue(any("EPISODE_HASH" in x for x in manifest["quality_issues"]))

    def test_manual_region_not_ground_truth_and_no_sensor_inference(self):
        root = self.fixture()
        path = root / "cycle_001/pre.json"
        value = json.loads(path.read_text())
        value["mask_semantics"] = "manual_region"
        write_json(path, value)
        _, manifest = self.checked_export(root)
        self.assertEqual(next(o for o in manifest["observations"] if o["stage"] == "pre")["mask_semantics"], "manual_region")
        self.assertEqual(manifest["damage_status"], "not_assessed")
        for cycle in manifest["cycles"]:
            self.assertIsNone(cycle["cleaning_result_label"])
            self.assertIsNone(cycle["measured_pressure"])
            self.assertIsNone(cycle["measured_flow"])

    def test_batch_discovery_and_skip(self):
        self.fixture("one")
        self.fixture("two", "mock")
        self.assertEqual([r["status"] for r in self.exporter.export_new_runs(self.source)], ["exported", "exported"])
        self.assertEqual([r["status"] for r in self.exporter.export_new_runs(self.source)], ["skipped", "skipped"])
        for folder in self.data.glob("runs/*/run-*"):
            verify_archive(folder)

    def test_source_change_during_copy_not_published(self):
        root = self.fixture()
        original = shutil.copyfile
        def mutate(src, dst):
            result = original(src, dst)
            (root / "new-file").write_text("writer still active")
            return result
        with patch("microcleaning.data_learning.run_archive.shutil.copyfile", side_effect=mutate):
            with self.assertRaisesRegex(ArchiveError, "SOURCE_CHANGED"):
                self.exporter.export_run(root)
        self.assertEqual(list(self.data.glob("runs/real/run-*")), [])

    def test_cli_export_verify_batch(self):
        root = self.fixture()
        with patch("builtins.print"):
            self.assertEqual(main(["--run", str(root), "--data-root", str(self.data)]), 0)
            folder = next(self.data.glob("runs/real/run-*"))
            self.assertEqual(main(["--verify", str(folder)]), 0)
            self.assertEqual(main(["--source-root", str(self.source), "--data-root", str(self.data)]), 0)

    def test_origin_change_cannot_bypass_identity_conflict(self):
        root = self.fixture()
        folder, _ = self.checked_export(root)
        before = snapshot(folder)
        config = json.loads((root / "run_config.json").read_text())
        config["metadata"].update(mode="mock", data_origin="mock")
        write_json(root / "run_config.json", config)
        write_json(root / "summary.json", {"task_id": "task-1", "mode": "mock"})
        with self.assertRaisesRegex(ArchiveError, "OTHER_BUCKET"):
            self.exporter.export_run(root)
        self.assertEqual(before, snapshot(folder))

    def test_publish_interruption_never_validates_partial(self):
        root = self.fixture()
        before = snapshot(root)
        with patch("microcleaning.data_learning.run_archive.os.rename", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.exporter.export_run(root)
        self.assertEqual(before, snapshot(root))
        partial = next(self.data.glob("runs/real/.partial-*"))
        self.assertTrue((partial / "run_manifest.json").is_file())
        with self.assertRaisesRegex(ArchiveError, "UNPUBLISHED"):
            verify_archive(partial)
        self.assertEqual(list(self.data.glob("runs/real/run-*")), [])

    def test_batch_failure_isolation(self):
        self.fixture("one")
        self.fixture("two")
        original = self.exporter.export_run
        def export(path):
            if Path(path).name == "one":
                raise OSError("injected unreadable source")
            return original(path)
        with patch.object(self.exporter, "export_run", side_effect=export):
            self.assertEqual([r["status"] for r in self.exporter.export_new_runs(self.source)], ["failed", "exported"])

    def test_external_reference_never_copied(self):
        root = self.fixture()
        external = self.base / "outside.png"
        external.write_bytes(png(0))
        path = root / "cycle_001/pre.json"
        data = json.loads(path.read_text())
        data["observation"]["raw_image_ref"] = str(external)
        data.pop("raw_sha256")
        write_json(path, data)
        _, manifest = self.checked_export(root)
        self.assertTrue(any("OUTSIDE_RUN" in x for x in manifest["quality_issues"]))
        self.assertNotIn("outside.png", {x["path"] for x in manifest["files"]})

    def test_unreviewed_ground_truth_and_coverage_not_labels(self):
        root = self.fixture()
        path = root / "cycle_001/pre.json"
        data = json.loads(path.read_text())
        data["mask_semantics"] = "ground_truth"
        write_json(path, data)
        write_json(root / "coverage_previews/preview.json", {"removal_rate": 1.0, "damage_flag": False})
        _, manifest = self.checked_export(root)
        self.assertFalse(manifest["training_eligibility"]["visual_ground_truth"])
        self.assertTrue(all(o["mask_semantics"] != "ground_truth" for o in manifest["observations"]))
        self.assertTrue(all(c["cleaning_result_label"] is None for c in manifest["cycles"]))

    def test_legacy_crlf_episode_hash_accepted(self):
        root = self.fixture()
        path = root / "cycle_001/episodes/episode-1.json"
        text = json.dumps(json.loads(path.read_text()), indent=2) + "\n"
        path.write_bytes(text.replace("\n", "\r\n").encode())
        path.with_suffix(".sha256").write_text(hashlib.sha256(text.encode()).hexdigest())
        _, manifest = self.checked_export(root)
        self.assertFalse(any("EPISODE_HASH" in x for x in manifest["quality_issues"]))

    def test_all_origins_remain_explicit(self):
        for origin in ("real", "mock", "synthetic", "manual_change", "unknown"):
            root = self.fixture(origin, "mock" if origin == "mock" else "real")
            path = root / "run_config.json"
            config = json.loads(path.read_text())
            config["metadata"]["data_origin"] = origin
            write_json(path, config)
            _, manifest = self.checked_export(root)
            self.assertEqual(manifest["data_origin"], origin)
            self.assertFalse(manifest["training_eligibility"]["real_cleaning_effect"])

    def test_missing_raw_directory_cannot_validate(self):
        folder, _ = self.checked_export(self.fixture())
        # Preserve the bytes elsewhere, with no recursive deletion.
        (folder / "raw").rename(self.base / "preserved-raw")
        with self.assertRaises(ArchiveError):
            verify_archive(folder)

    def test_simulation_mask_keeps_simulation_semantics(self):
        root = self.fixture()
        path = root / "cycle_001/post.json"
        value = json.loads(path.read_text())
        value["mask_semantics"] = "simulation_only"
        write_json(path, value)
        _, manifest = self.checked_export(root)
        self.assertEqual(next(o for o in manifest["observations"] if o["stage"] == "post")["mask_semantics"], "simulation_only")
        self.assertFalse(manifest["training_eligibility"]["real_cleaning_effect"])

    def test_extra_archive_file_detected(self):
        folder, _ = self.checked_export(self.fixture())
        (folder / "raw/extra").write_text("unexpected")
        with self.assertRaises(ArchiveError):
            verify_archive(folder)


if __name__ == "__main__":
    unittest.main()
