"""Failure-path integration tests using a dummy probe and real output files."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from probes.base import BaseProbe, ModelInfo, ProbeBlocked, ProbeOutput
from probes import registry
from probes.runner import run_config


class DummyProbe(BaseProbe):
    events = []

    def __init__(self, params, root):
        super().__init__(params, root)
        self.model_info = ModelInfo("dummy", "test-v1", "no-weights", None, None, 0,
                                    "test-fixture", "not-applicable")

    def load(self):
        self.events.append((self.params.get("tag"), "load"))
        if self.params.get("load_blocked"):
            raise ProbeBlocked("fixture missing prerequisite", "blocked-reference")
        if self.params.get("load_failed"):
            raise RuntimeError("fixture load failure")

    def infer(self, image, output_dir):
        self.events.append((self.params.get("tag"), image.image_id))
        if self.params.get("mixed") and image.image_id == "blocked":
            raise ProbeBlocked("no accepted clean reference", "blocked-reference")
        if self.params.get("mixed") and image.image_id == "failed":
            raise RuntimeError("deliberate inference failure")
        if self.params.get("bad_status"):
            return ProbeOutput(status="imaginary-status")
        if self.params.get("missing_output"):
            return ProbeOutput({}, ["never_written.png"])
        if self.params.get("non_finite"):
            return ProbeOutput({"bad": float("nan")})
        if self.params.get("return_blocked"):
            return ProbeOutput({"existing_metric": "preserved"}, status="blocked",
                               status_reason="missing accepted reference", reason_codes=["blocked-data"])
        (output_dir / "raw.txt").write_text("dummy raw output\n", encoding="utf-8")
        return ProbeOutput({"synthetic": True, "image_id": image.image_id}, ["raw.txt"])

    def release(self):
        self.events.append((self.params.get("tag"), "release"))
        if self.params.get("release_failed"):
            raise RuntimeError("deliberate release failure")


class FrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="probe_framework_")
        self.root = Path(self.temp.name)
        (self.root / "inputs").mkdir()
        for image_id in ("blocked", "failed", "ok"):
            (self.root / "inputs" / f"{image_id}.txt").write_text(image_id, encoding="utf-8")
        self.saved_registry = registry.REGISTRY.copy()
        self.saved_errors = registry.BUILTIN_IMPORT_ERRORS.copy()
        registry.REGISTRY.clear()
        registry.BUILTIN_IMPORT_ERRORS.clear()
        DummyProbe.events = []
        for name in ("dummy", "later", "loadbad", "releasebad"):
            registry.register(name)(DummyProbe)

    def tearDown(self):
        registry.REGISTRY.clear()
        registry.REGISTRY.update(self.saved_registry)
        registry.BUILTIN_IMPORT_ERRORS.clear()
        registry.BUILTIN_IMPORT_ERRORS.update(self.saved_errors)
        self.temp.cleanup()

    def config(self, probes, images=("blocked", "failed", "ok")):
        value = {"schema": "probe-config-v1", "output_root": "results/run",
                 "images": [{"image_id": name, "raw_image_ref": f"inputs/{name}.txt"} for name in images],
                 "probes": probes}
        (self.root / "config.json").write_text(json.dumps(value), encoding="utf-8")
        return value

    def run_batch(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_config("config.json", root=self.root, load_builtins=False)

    def test_registration_lookup_and_duplicate_rejection(self):
        self.assertIs(registry.get_probe("dummy"), DummyProbe)
        class Other(DummyProbe):
            pass
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register("dummy")(Other)
        with self.assertRaises(KeyError):
            registry.get_probe("unknown")

    def test_blocked_failed_and_ok_images_continue_and_have_complete_records(self):
        self.config([{"name": "dummy", "params": {"mixed": True, "tag": "first"}},
                     {"name": "later", "params": {"tag": "last"}, "images": ["ok"]}])
        summary = self.run_batch()
        self.assertEqual([row["status"] for row in summary["results"]], ["blocked", "failed", "ok", "ok"])
        self.assertEqual(summary["status_counts"], {"ok": 2, "blocked": 1, "failed": 1})
        self.assertEqual(DummyProbe.events, [("first", "load"), ("first", "blocked"), ("first", "failed"),
                                            ("first", "ok"), ("first", "release"), ("last", "load"),
                                            ("last", "ok"), ("last", "release")])
        for row in summary["results"]:
            directory = self.root / "results/run" / row["probe_name"] / row["frame_id"]
            saved = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, row)
            self.assertEqual(saved["interface_version"], "probe-result-v1")
            self.assertFalse(saved["test_ground_truth_allowed"])
            self.assertIn("weights_sha256", saved["model_info"])
            self.assertIn("weights_bytes", saved["model_info"])
            self.assertIn("model_load_ms", saved)
            self.assertIsNone(saved["peak_allocated_mib"])
            self.assertIsNone(saved["peak_reserved_mib"])
            self.assertEqual(saved["input_sha256"], hashlib.sha256(row["frame_id"].encode()).hexdigest())
            for artifact in saved["output_files"]:
                data = (directory / artifact["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), artifact["sha256"])
                self.assertEqual(len(data), artifact["bytes"])
        self.assertIn("deliberate inference failure", (self.root / "results/run/dummy/failed/failure.txt").read_text())
        self.assertTrue((self.root / "results/run/run_table.md").is_file())

    def test_load_blocked_releases_and_records_all_images_then_continues(self):
        self.config([{"name": "loadbad", "params": {"load_blocked": True, "tag": "blocked-load"}},
                     {"name": "later", "params": {"tag": "after"}, "images": ["ok"]}])
        summary = self.run_batch()
        self.assertEqual([r["status"] for r in summary["results"]], ["blocked", "blocked", "blocked", "ok"])
        self.assertTrue(all(r["duration_ms"] is None for r in summary["results"][:3]))
        self.assertIn(("blocked-load", "release"), DummyProbe.events)
        self.assertNotIn(("blocked-load", "ok"), DummyProbe.events)

    def test_load_and_release_failures_do_not_stop_next_probe(self):
        self.config([{"name": "loadbad", "params": {"load_failed": True}, "images": ["ok"]},
                     {"name": "releasebad", "params": {"release_failed": True}, "images": ["ok"]},
                     {"name": "later", "images": ["ok"]}])
        summary = self.run_batch()
        self.assertEqual([r["status"] for r in summary["results"]], ["failed", "failed", "ok"])
        self.assertIn("release-failed", summary["results"][1]["reason_codes"])
        saved = json.loads((self.root / "results/run/releasebad/ok/metrics.json").read_text())
        self.assertEqual(saved["status"], "failed")
        self.assertTrue(any(f["path"] == "raw.txt" for f in saved["output_files"]))
        self.assertTrue(any(f["path"] == "release_failure.txt" for f in saved["output_files"]))

    def test_unknown_probe_is_failed_but_later_probe_runs(self):
        self.config([{"name": "missing", "images": ["ok"]}, {"name": "later", "images": ["ok"]}])
        summary = self.run_batch()
        self.assertEqual([r["status"] for r in summary["results"]], ["failed", "ok"])
        self.assertIn("Unregistered probe", summary["results"][0]["status_reason"])

    def test_bad_optional_import_becomes_blocked_env_not_batch_abort(self):
        registry.BUILTIN_IMPORT_ERRORS["unavailable"] = "ModuleNotFoundError: optional_lib"
        self.config([{"name": "unavailable", "images": ["ok"]}, {"name": "later", "images": ["ok"]}])
        summary = self.run_batch()
        self.assertEqual(summary["results"][0]["reason_codes"], ["blocked-env"])
        self.assertEqual(summary["results"][1]["status"], "ok")

    def test_invalid_probe_outputs_are_failure_records(self):
        self.config([{"name": "dummy", "params": {"bad_status": True}, "images": ["ok"]},
                     {"name": "loadbad", "params": {"missing_output": True}, "images": ["ok"]},
                     {"name": "releasebad", "params": {"non_finite": True}, "images": ["ok"]},
                     {"name": "later", "images": ["ok"]}])
        summary = self.run_batch()
        self.assertEqual([r["status"] for r in summary["results"]], ["failed", "failed", "failed", "ok"])

    def test_missing_input_and_identity_mismatch_fail_before_infer(self):
        config = self.config([{"name": "dummy"}])
        (self.root / "inputs/blocked.txt").unlink()
        config["images"][1]["metadata"] = {"input_sha256": "0" * 64}
        (self.root / "config.json").write_text(json.dumps(config))
        summary = self.run_batch()
        self.assertEqual([r["status"] for r in summary["results"]], ["failed", "failed", "ok"])
        self.assertIsNone(summary["results"][0]["input_sha256"])
        self.assertEqual([e[1] for e in DummyProbe.events], ["load", "ok", "release"])

    def test_existing_outputs_are_preserved(self):
        self.config([{"name": "dummy", "images": ["ok"]}])
        self.run_batch()
        record = self.root / "results/run/dummy/ok/metrics.json"
        before = record.read_bytes()
        with self.assertRaises(FileExistsError):
            self.run_batch()
        self.assertEqual(before, record.read_bytes())

    def test_escape_and_duplicate_ids_rejected_before_output_creation(self):
        config = self.config([{"name": "dummy"}])
        config["output_root"] = "../outside"
        (self.root / "config.json").write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, "inside"):
            self.run_batch()
        config["output_root"] = "results/run"
        config["images"].append(config["images"][0])
        (self.root / "config.json").write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, "Duplicate image_id"):
            self.run_batch()
        self.assertFalse((self.root / "results/run").exists())

    def test_gpu_occupied_preflight_prevents_load_without_importing_torch(self):
        self.config([{"name": "dummy", "params": {"device": "cuda"}, "images": ["ok"]},
                     {"name": "later", "images": ["ok"]}])
        with patch("probes.runner._gpu_preflight", side_effect=ProbeBlocked("GPU occupied", "blocked-resource")), \
             patch("probes.runner._cuda_module") as cuda:
            summary = self.run_batch()
        cuda.assert_not_called()
        self.assertEqual([r["status"] for r in summary["results"]], ["blocked", "ok"])
        self.assertIsNone(summary["results"][0]["peak_allocated_mib"])
        self.assertIsNone(summary["results"][0]["model_load_ms"])

    def test_builtin_requires_explicit_device_before_output_creation(self):
        registry.register("dino")(DummyProbe)
        self.config([{"name": "dino", "images": ["ok"]}])
        with self.assertRaisesRegex(ValueError, "device must be explicit"):
            self.run_batch()
        self.assertFalse((self.root / "results/run").exists())
        self.config([{"name": "dino", "params": {"device": "cpu"}, "images": ["ok"]}])
        self.assertEqual(self.run_batch()["results"][0]["status"], "ok")

    def test_release_inventory_failure_still_runs_next_probe(self):
        from probes import runner
        original_inventory = runner._inventory
        def broken_inventory(directory, declared=None):
            if directory.parent.name == "releasebad":
                raise ValueError("malformed artifact cannot be inventoried")
            return original_inventory(directory, declared)
        self.config([{"name": "releasebad", "params": {"release_failed": True}, "images": ["ok"]},
                     {"name": "later", "images": ["ok"]}])
        with patch("probes.runner._inventory", side_effect=broken_inventory):
            summary = self.run_batch()
        self.assertEqual([r["status"] for r in summary["results"]], ["failed", "ok"])
        self.assertIn("release-failed", summary["results"][0]["reason_codes"])
        self.assertIn("result-validation-failed", summary["results"][0]["reason_codes"])
        self.assertEqual(summary["results"][0]["output_files"], [])
        saved = json.loads((self.root / "results/run/releasebad/ok/metrics.json").read_text())
        self.assertEqual(saved["status"], "failed")

    def test_blocked_attempt_does_not_claim_gpu_inference_measurements(self):
        self.config([{"name": "dummy", "params": {"device": "cuda", "mixed": True}, "images": ["blocked"]},
                     {"name": "later", "params": {"device": "cuda", "return_blocked": True}, "images": ["ok"]}])
        fake_torch = MagicMock()
        fake_torch.cuda.max_memory_allocated.return_value = 4 * 2**20
        fake_torch.cuda.max_memory_reserved.return_value = 8 * 2**20
        with patch("probes.runner._gpu_preflight"), patch("probes.runner._cuda_module", return_value=fake_torch):
            summary = self.run_batch()
        for row in summary["results"]:
            self.assertEqual(row["status"], "blocked")
            self.assertIsNone(row["duration_ms"])
            self.assertIsNone(row["peak_allocated_mib"])
            self.assertIsNone(row["peak_reserved_mib"])
            self.assertGreaterEqual(row["metrics"]["blocked_check_duration_ms"], 0)
        self.assertEqual(summary["results"][1]["metrics"]["existing_metric"], "preserved")

    def test_per_image_preflight_follows_synchronize_and_cache_release(self):
        self.config([{"name": "dummy", "params": {"device": "cuda"}, "images": ["ok", "failed"]}])
        order = []
        fake_torch = MagicMock()
        fake_torch.cuda.synchronize.side_effect = lambda: order.append("synchronize")
        fake_torch.cuda.empty_cache.side_effect = lambda: order.append("empty_cache")
        fake_torch.cuda.reset_peak_memory_stats.side_effect = lambda: order.append("reset_peak")
        fake_torch.cuda.max_memory_allocated.return_value = 4 * 2**20
        fake_torch.cuda.max_memory_reserved.return_value = 8 * 2**20
        def preflight(directory, label, params):
            order.append(label)
        with patch("probes.runner._gpu_preflight", side_effect=preflight), \
             patch("probes.runner._cuda_module", return_value=fake_torch):
            summary = self.run_batch()
        self.assertEqual([row["status"] for row in summary["results"]], ["ok", "ok"])
        # First occupation check still precedes any CUDA operation/import hook.
        self.assertEqual(order[0], "before_load")
        positions = [index for index, event in enumerate(order) if event == "before_image"]
        self.assertEqual(len(positions), 2)
        for index in positions:
            self.assertEqual(order[index - 2:index], ["synchronize", "empty_cache"])
            self.assertEqual(order[index + 1:index + 3], ["reset_peak", "synchronize"])


if __name__ == "__main__":
    unittest.main()
