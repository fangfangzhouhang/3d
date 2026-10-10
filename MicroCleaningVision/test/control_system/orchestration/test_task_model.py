import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from demo.closed_loop_fixture import MockFrames
from demo.image_ops import FrozenSegmenter
from microcleaning.control_system.orchestration.task_model import TaskModel
from microcleaning.control_system.orchestration.target_adapter import TargetLedger
from microcleaning.vision.target_instance import extract_target_instances


class TaskModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        frame = MockFrames().capture("pre")
        mask = FrozenSegmenter("local", use_tuned_policy=False)(frame.image).mask
        entries = TargetLedger("obs", mask, extract_target_instances(mask)).entries
        self.model = TaskModel(folder=Path(self.temp.name), task_id="task-test", image_shape=mask.shape,
            entries=entries, metadata={"operator": "Tester"}, references={}, policy={"step_budget": 20000})
        for stable in entries:
            self.model.set_plan(stable, {"action": "CENTER_POINT", "geometry": {"cycle_abs_steps": (20, 20)}}, ())

    def test_no_implicit_approval_empty_queue_refused(self):
        self.assertTrue(all(t.decision == "PENDING_REVIEW" for t in self.model.targets.values()))
        with self.assertRaisesRegex(ValueError, "EMPTY_EXECUTION_LIST"):
            self.model.lock()
        with self.assertRaises(PermissionError):
            self.model.begin()

    def test_subset_stable_ids_and_immutable_lock(self):
        self.model.decide("S0001", "APPROVED")
        self.model.decide("S0002", "EXCLUDED", "not selected")
        self.assertEqual(self.model.lock(), ("S0001",))
        snapshot = self.model.to_dict()
        snapshot["locked_snapshot"]["targets"]["S0001"]["plan"]["action"] = "BAD"
        self.assertEqual(self.model.locked_snapshot["targets"]["S0001"]["plan"]["action"], "CENTER_POINT")
        with self.assertRaises(PermissionError):
            self.model.decide("S0002", "APPROVED")
        with self.assertRaises(PermissionError):
            self.model.add_manual((10, 10, 5, 5))
        self.assertEqual(tuple(self.model.targets), ("S0001", "S0002", "S0003"))

    def test_disk_failure_never_starts_or_changes_to_locked(self):
        self.model.decide("S0001", "APPROVED")
        with patch("microcleaning.control_system.orchestration.task_model.atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.model.lock()
        self.assertEqual(self.model.state, "DRAFT")
        self.assertEqual(self.model.execution_ids, ())

    def test_manual_region_boundaries_area_kind_and_validation(self):
        for bbox in ((-1, 1, 5, 5), (0, 0, 0, 5), (319, 239, 2, 2)):
            with self.assertRaises(ValueError):
                self.model.add_manual(bbox)
        stable = self.model.add_manual((20, 20, 5, 6), "遗漏区域")
        self.assertEqual(stable, "S0004")
        self.assertEqual(self.model.targets[stable].to_dict()["area_kind"], "manual_region_px")
        with self.assertRaises(ValueError):
            self.model.decide(stable, "APPROVED")

    def test_roster_sum_does_not_block_lock(self):
        self.model.set_plan("S0001", {"geometry": {"cycle_abs_steps": (15000, 5)}}, ())
        self.model.set_plan("S0002", {"geometry": {"cycle_abs_steps": (15000, 5)}}, ())
        self.model.decide("S0001", "APPROVED")
        self.model.decide("S0002", "APPROVED")
        self.assertEqual(("S0001", "S0002"), self.model.lock())

