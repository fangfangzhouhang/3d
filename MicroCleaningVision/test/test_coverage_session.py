"""P4 complete binary inputs, replaceable requests and hardware isolation."""

import ast
import contextlib
import io
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from demo import coverage_panel, coverage_session
from demo.coverage_session import CoverageDataset, CoverageRequest, LatestCoverageWorker, compute_request, load_image_mask, synthetic_dataset
from microcleaning.control_system.planning import coverage_archive, coverage_visualization
from microcleaning.control_system.planning.coverage_archive import load_coverage_preview
from microcleaning.control_system.planning.coverage_model import Evidence, FootprintProfile


def request(generation):
    mask = np.zeros((24, 32), bool)
    mask[5:16, 5:17] = True
    from microcleaning.control_system.planning.coverage_planner import CoverageInput
    dataset = CoverageDataset(np.full((24, 32, 3), 70, np.uint8), CoverageInput(mask, "small", "synthetic", "test"))
    return CoverageRequest(generation, dataset, FootprintProfile("test", 8, "px", Evidence("assumed", "test")))


def wait_response(worker):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = worker.poll()
        if result is not None:
            return result
        time.sleep(.005)
    raise AssertionError("worker response timed out")


class CoverageSessionTests(unittest.TestCase):
    def worker(self, processor=compute_request):
        worker = LatestCoverageWorker(processor)
        self.addCleanup(lambda: worker.thread.join(timeout=2))
        self.addCleanup(worker.close)
        return worker

    def test_pending_requests_are_bounded_and_stale_results_discarded(self):
        entered, release = threading.Event(), threading.Event()
        processed = []
        def processor(value):
            processed.append(value.generation)
            if value.generation == 1:
                entered.set()
                if not release.wait(3):
                    raise AssertionError("release missing")
            return compute_request(value)
        worker = self.worker(processor)
        self.addCleanup(release.set)
        worker.submit(request(1))
        self.assertTrue(entered.wait(2))
        worker.submit(request(2))
        worker.submit(request(3))
        release.set()
        response = wait_response(worker)
        self.assertEqual([1, 3], processed)
        self.assertEqual(3, response.generation)
        self.assertFalse(response.plan.executable)

    def test_close_during_inflight_does_not_publish_or_block(self):
        entered, release = threading.Event(), threading.Event()
        def processor(value):
            entered.set()
            release.wait(3)
            return compute_request(value)
        worker = self.worker(processor)
        self.addCleanup(release.set)
        worker.submit(request(1))
        self.assertTrue(entered.wait(2))
        start = time.monotonic()
        worker.close()
        self.assertLess(time.monotonic() - start, .2)
        release.set()
        worker.thread.join(2)
        self.assertIsNone(worker.poll())
        with self.assertRaises(ValueError):
            worker.submit(request(2))

    def test_error_does_not_poison_next_request(self):
        def processor(value):
            if value.generation == 1:
                raise ValueError("intentional failure")
            return compute_request(value)
        worker = self.worker(processor)
        worker.submit(request(1))
        self.assertIn("intentional failure", wait_response(worker).error)
        worker.submit(request(2))
        self.assertIsNotNone(wait_response(worker).plan)
        with self.assertRaises(ValueError):
            worker.submit(request(2))

    def test_full_split_mask_and_manual_region_provenance_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            mask = np.zeros((20, 30), np.uint8)
            mask[2:8, 2:8] = 255
            mask[15:17, 24:26] = 255
            Image.fromarray(np.full((20, 30, 3), 90, np.uint8)).save(folder / "original.png")
            Image.fromarray(mask).save(folder / "mask.png")
            dataset = load_image_mask(folder / "original.png", folder / "mask.png", mask_kind="manual_region")
            self.assertEqual(40, np.count_nonzero(dataset.input.mask))
            self.assertEqual("manual_region_px", dataset.input.to_dict()["area_kind"])
            self.assertIsNotNone(dataset.input.segmentation_sha256)
            mask[0, 0] = 100
            Image.fromarray(mask).save(folder / "mask.png")
            with self.assertRaisesRegex(ValueError, "BINARY"):
                load_image_mask(folder / "original.png", folder / "mask.png", mask_kind="segmented_stain")

    def test_synthetic_shapes_are_explicit_and_immutable(self):
        for name in coverage_session.SYNTHETIC_SHAPES:
            dataset = synthetic_dataset(name)
            self.assertEqual("synthetic", dataset.input.mask_kind)
            self.assertGreater(np.count_nonzero(dataset.input.mask), 0)
            with self.assertRaises(ValueError):
                dataset.image_rgb.setflags(write=True)
        with self.assertRaises(ValueError):
            synthetic_dataset("not-a-shape")

    def test_cli_has_no_implicit_diameter(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as context:
            coverage_panel.main(["--synthetic", "split"])
        self.assertEqual(2, context.exception.code)

    def test_headless_cli_export_and_replay_preserve_exact_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            first, replay = Path(temp) / "first", Path(temp) / "replay"
            with contextlib.redirect_stdout(io.StringIO()):
                coverage_panel.main(["--synthetic", "split", "--diameter-px", "36", "--export-only", str(first)])
                coverage_panel.main(["--archive", str(first), "--export-only", str(replay)])
            self.assertEqual(load_coverage_preview(first).plan.to_dict(), load_coverage_preview(replay).plan.to_dict())

    def test_new_modules_have_no_hardware_or_workbench_dependencies(self):
        prohibited = ("serial", "hardware", "executor", "workbench", "station_window", "ports", "contracts", "camera")
        for module in (coverage_panel, coverage_session, coverage_archive, coverage_visualization):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
            for name in imports:
                self.assertFalse(any(word in name for word in prohibited), f"{module.__name__}: {name}")
