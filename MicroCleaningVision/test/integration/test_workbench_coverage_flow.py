"""Actual frozen-ROI loop/protocol replacement plus optional coverage observer."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from integration.test_interactive_rewash import execute
from integration.test_workbench_flow import make_runtime
from demo.closed_loop_fixture import MockFrames
from demo.workbench_coverage import freeze_source
from demo.coverage_session import CoverageRequest, compute_request
from microcleaning.control_system.orchestration.cleaning_loop import CleaningLoop
from microcleaning.control_system.planning.coverage_model import Evidence, FootprintProfile
from microcleaning.control_system.planning.coverage_planner import CoverageConstraints
from microcleaning.control_system.planning.coverage_visualization import RenderOptions
from microcleaning.control_system.reporting.coverage_evidence import export_bound_preview
from microcleaning.control_system.reporting import read_run


def with_observer(folder, observer, **kwargs):
    original = CleaningLoop.__init__
    def initialize(loop, **options):
        original(loop, **{**options, "on_coverage_input": observer})
    with patch.object(CleaningLoop, "__init__", initialize):
        return execute(folder, **kwargs)


def actuation_trace(raw):
    result = []
    for data in raw.writes:
        text = data.decode("ascii").strip()
        if text.startswith("MCV1|PUMP|"):
            fields = text.split("|")
            fields[2] = "ACTION_ID"
            result.append("|".join(fields))
        elif text.startswith("MOVEXY ") or text in {"STOP", "MCV1|STOP"}:
            result.append(text)
    return result


class WorkbenchCoverageFlowTests(unittest.TestCase):
    def test_on_off_and_failing_preview_keep_same_actuation_choices_and_quality(self):
        def decide(facts):
            return {"choice": "retake" if facts["recheck_index"] == 1 else "next", "reason": "测试复核"}
        seen = []
        def observer(payload):
            seen.append(freeze_source(**payload))
        def failing(payload):
            # Mutating the copies, then throwing, cannot alter loop masks or decisions.
            payload["metadata"].clear()
            raise RuntimeError("preview renderer failed")
        with tempfile.TemporaryDirectory() as tmp:
            off = execute(Path(tmp) / "off", decide=decide)
            on = with_observer(Path(tmp) / "on", observer, decide=decide)
            broken = with_observer(Path(tmp) / "broken", failing, decide=decide)
            for result, raw, frames, _ in (on, broken):
                self.assertEqual(actuation_trace(raw), actuation_trace(off[1]))
                self.assertEqual(raw.pump_count, 1)
                self.assertEqual(frames.capture_phases, ["pre", "post", "post"])
                for key in ("status", "workflow_status", "quality_status", "report_ready"):
                    self.assertEqual(result[key], off[0][key], key)
            self.assertEqual([source.binding["stage"] for source in seen], ["pre", "post", "post"])
            self.assertEqual([source.binding["recheck"] for source in seen], [None, 1, 2])
            # Old retake mirror post.png points to recheck 2. Binding must keep recheck 1.
            source = seen[1]
            constraints = CoverageConstraints()
            plan = compute_request(CoverageRequest(1, source.dataset,
                FootprintProfile("virtual", 16, "px", Evidence("assumed", "explicit-integration-test")), constraints=constraints))
            export_bound_preview(source.dataset.image_rgb, plan, source.folder, source.binding,
                generation=1, constraints=constraints, options=RenderOptions(), step_count=len(plan.stations))
            snapshot = read_run(source.folder)
            self.assertFalse(snapshot["coverage_preview_issues"], snapshot["coverage_preview_issues"])
            self.assertEqual(snapshot["coverage_previews"][0]["recheck"], 1)
            self.assertEqual(snapshot["quality_status"], off[0]["quality_status"])

    def test_fragmented_residue_all_planned_neighbors_excluded_legacy_largest_execution_kept(self):
        class SplitFrames(MockFrames):
            def preview(self):
                image = super().preview()
                if self.pumps and self.selected is not None:
                    x, y = self.centers[self.selected]
                    image[y-14:y+15, x-14:x+15] = self.background[y-14:y+15, x-14:x+15]
                    # Both fragments exceed the real frozen HSV 30px filter.
                    image[y-4:y+4, x-9:x-3] = (18, 18, 205)
                    image[y-3:y+4, x+3:x+8] = (18, 18, 205)
                return image
        sources = []
        def decide(facts):
            return {"choice": "rewash" if facts["cycle"] == 1 else "next", "reason": "完整残留已核对"}
        with tempfile.TemporaryDirectory() as tmp:
            result, raw, frames, loop = with_observer(Path(tmp), lambda data: sources.append(freeze_source(**data)),
                decide=decide, frame_type=SplitFrames)
            self.assertEqual(raw.pump_count, 2, result["reasons"])
            pre = next(source for source in sources if source.binding["stage"] == "pre" and source.binding["cycle"] == 2)
            self.assertEqual(cv2.connectedComponents(pre.dataset.input.mask.astype(np.uint8))[0]-1, 2)
            self.assertEqual(np.count_nonzero(pre.dataset.input.mask), 83)
            self.assertFalse(np.any(pre.dataset.input.mask[95:125, 185:215]), "S0002 excluded from S0001 mask")
            executed = cv2.imdecode(np.frombuffer((Path(tmp) / "cycle_002/selected_target_mask.png").read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
            self.assertEqual(np.count_nonzero(executed), 48)
            self.assertEqual(loop.task.targets["S0001"].retry_count, 1)
            plan = compute_request(CoverageRequest(1, pre.dataset,
                FootprintProfile("virtual", 8, "px", Evidence("assumed", "explicit-split-test"))))
            self.assertEqual(plan.metrics.target_area_px, 83)
            self.assertFalse(plan.executable)
            self.assertGreaterEqual(len(plan.stations), 2)

    def test_workbench_runtime_review_publishes_frozen_initial_source_without_preview_commands(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            runtime = make_runtime(Path(tmp))
            runtime.start({"operator": "Mock reviewer"})
            deadline = time.monotonic() + 10
            sources = []
            while time.monotonic() < deadline and len(sources) < 3:
                for event in runtime.bus.drain():
                    if event.kind == "capture_ready":
                        runtime.submit("capture")
                    elif event.kind == "coverage_source":
                        sources.append(event.payload["source"])
                time.sleep(.015)
            self.assertEqual(len(sources), 3)
            self.assertEqual({source.binding["stage"] for source in sources}, {"initial"})
            for source in sources:
                plan = compute_request(CoverageRequest(1, source.dataset,
                    FootprintProfile("virtual", None, "px", Evidence("assumed", "explicit-unknown"))))
                self.assertIsNone(plan.metrics)
                self.assertFalse(plan.executable)
            runtime.submit("manual_add", bbox=(25, 25, 16, 16), reason="手动区域测试")
            manual = None
            while time.monotonic() < deadline and manual is None:
                for event in runtime.bus.drain():
                    if event.kind == "coverage_source":
                        manual = event.payload["source"]
                time.sleep(.015)
            self.assertIsNotNone(manual)
            self.assertEqual(manual.dataset.input.mask_kind, "manual_region")
            self.assertEqual(np.count_nonzero(manual.dataset.input.mask), 256)
            self.assertEqual(manual.dataset.input.mask.shape, sources[0].dataset.input.mask.shape)
            runtime.submit("cancel")
            runtime.worker.join(5)
            self.assertFalse(runtime.active)
            self.assertEqual(json.loads((runtime.folder / "serial.json").read_text(encoding="utf-8"))["events"], [])


if __name__ == "__main__":
    unittest.main()
