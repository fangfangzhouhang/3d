"""Contract and failure-path tests for the mock-only Minimum Closed Loop."""

import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from microcleaning.control_system.mock_mcl import (
    MockMCLRunner,
    MockScenario,
    MockController,
    MockSafetyGovernor,
    RuleDecision,
    canonical_request_digest,
    write_episode,
)
from microcleaning.contracts import NextRoute, SafetyOutcome


class MockMCLTests(unittest.TestCase):
    def setUp(self):
        self.runner = MockMCLRunner()

    def test_nominal_mock_episode_is_traceable_and_stops_after_verification(self):
        episode = self.runner.run(MockScenario(cleaning_efficiency=0.90), task_id="nominal")
        self.assertEqual("mock", episode.mode)
        self.assertEqual(SafetyOutcome.ALLOW, episode.safety_decision.outcome)
        self.assertTrue(episode.execution_receipt.success)
        self.assertEqual(NextRoute.STOP, episode.verification.next_route)
        self.assertIsNotNone(episode.observation_post)
        self.assertEqual(episode.task_id, episode.state.task_id)
        self.assertEqual(episode.action_request.action_id, episode.execution_receipt.action_id)

    def test_low_confidence_requires_human_and_never_executes(self):
        episode = self.runner.run(MockScenario(confidence=0.20), task_id="low-confidence")
        self.assertEqual(SafetyOutcome.HUMAN, episode.safety_decision.outcome)
        self.assertIn("OBSERVATION_LOW_QUALITY", episode.safety_decision.reason_codes)
        self.assertIsNone(episode.execution_receipt)
        self.assertEqual(NextRoute.HUMAN, episode.verification.next_route)

    def test_out_of_workspace_is_denied_and_never_executes(self):
        episode = self.runner.run(MockScenario(centroid_mm=(101.0, 20.0)), task_id="outside")
        self.assertEqual(SafetyOutcome.DENY, episode.safety_decision.outcome)
        self.assertIn("TARGET_OUT_OF_WORKSPACE", episode.safety_decision.reason_codes)
        self.assertIsNone(episode.execution_receipt)

    def test_expired_calibration_is_denied(self):
        episode = self.runner.run(MockScenario(calibration_valid=False), task_id="stale-calibration")
        self.assertEqual(SafetyOutcome.DENY, episode.safety_decision.outcome)
        self.assertIn("CALIBRATION_EXPIRED", episode.safety_decision.reason_codes)

    def test_retry_budget_exhausted_requires_human(self):
        episode = self.runner.run(MockScenario(prior_action_count=1), task_id="retry-limit")
        self.assertEqual(SafetyOutcome.HUMAN, episode.safety_decision.outcome)
        self.assertIn("RETRY_BUDGET_EXHAUSTED", episode.safety_decision.reason_codes)

    def test_estop_has_deny_precedence_over_low_confidence(self):
        episode = self.runner.run(MockScenario(e_stop_active=True, confidence=0.20), task_id="estop")
        self.assertEqual(SafetyOutcome.DENY, episode.safety_decision.outcome)
        self.assertIn("ESTOP_ACTIVE", episode.safety_decision.reason_codes)
        self.assertIsNone(episode.execution_receipt)

    def test_interlock_and_controller_failure_are_denied(self):
        episode = self.runner.run(MockScenario(interlock_ok=False, controller_connected=False), task_id="interlock")
        self.assertEqual(SafetyOutcome.DENY, episode.safety_decision.outcome)
        self.assertIn("INTERLOCK_OPEN", episode.safety_decision.reason_codes)
        self.assertIn("CONTROLLER_UNAVAILABLE", episode.safety_decision.reason_codes)

    def test_candidate_cannot_relax_governor_limits(self):
        episode = self.runner.run(task_id="forged-constraints")
        forged = replace(
            episode.action_request,
            duration_ms=9000,
            constraints={"max_duration_ms": 9999, "max_pressure": 1.0, "retry_budget": 99},
        )
        decision = MockSafetyGovernor().evaluate(episode.state, forged, MockScenario())
        self.assertEqual(SafetyOutcome.DENY, decision.outcome)
        self.assertIn("DURATION_OUT_OF_BOUNDS", decision.reason_codes)

    def test_wrong_coordinate_frame_is_denied(self):
        episode = self.runner.run(task_id="wrong-frame")
        wrong_frame = replace(episode.action_request, coordinate_frame="pixel_xy")
        decision = MockSafetyGovernor().evaluate(episode.state, wrong_frame, MockScenario())
        self.assertEqual(SafetyOutcome.DENY, decision.outcome)
        self.assertIn("UNSUPPORTED_COORDINATE_FRAME", decision.reason_codes)

    def test_approval_cannot_be_replayed_or_applied_to_mutated_action(self):
        episode = self.runner.run(task_id="approval-binding")
        controller = MockController()
        request = episode.action_request
        decision = episode.safety_decision
        with self.assertRaises(PermissionError):
            controller.execute(replace(request, pressure=0.31), decision, MockScenario())
        with self.assertRaises(PermissionError):
            controller.execute(replace(request, action_id="other"), decision, MockScenario())
        receipt = controller.execute(request, decision, MockScenario())
        self.assertTrue(receipt.success)
        with self.assertRaises(PermissionError):
            controller.execute(request, decision, MockScenario())

    def test_default_entrypoint_writes_mock_only_evidence(self):
        import main

        original_directory = os.getcwd()
        with tempfile.TemporaryDirectory() as folder:
            os.chdir(folder)
            try:
                self.assertEqual(0, main.main())
                episodes = list((Path(folder) / "output" / "mock_episodes").glob("*.json"))
                self.assertEqual(1, len(episodes))
                evidence = json.loads(episodes[0].read_text(encoding="utf-8"))
                self.assertEqual("mock", evidence["mode"])
            finally:
                os.chdir(original_directory)

    def test_expired_approval_is_refused(self):
        episode = self.runner.run(task_id="expired-approval")
        expired = replace(
            episode.safety_decision,
            expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        )
        with self.assertRaises(PermissionError):
            self.runner.controller.execute(episode.action_request, expired, MockScenario())

    def test_damage_flag_routes_to_human_and_keeps_failure_record(self):
        episode = self.runner.run(MockScenario(damage_flag=True), task_id="damage")
        self.assertEqual(NextRoute.HUMAN, episode.verification.next_route)
        self.assertTrue(episode.verification.damage_flag)
        self.assertEqual("verification", episode.failures[0].stage)

    def test_no_target_stops_without_action(self):
        episode = self.runner.run(MockScenario(contamination_area_px=0.0), task_id="no-target")
        self.assertIsNone(episode.action_request)
        self.assertEqual(NextRoute.STOP, episode.verification.next_route)

    def test_communication_failure_has_receipt_and_routes_to_human(self):
        episode = self.runner.run(MockScenario(communication_failure=True), task_id="comm-failure")
        self.assertEqual(SafetyOutcome.ALLOW, episode.safety_decision.outcome)
        self.assertFalse(episode.execution_receipt.success)
        self.assertEqual("COMMUNICATION_TIMEOUT", episode.execution_receipt.error_code)
        self.assertEqual(NextRoute.HUMAN, episode.verification.next_route)
        self.assertEqual("execution", episode.failures[0].stage)

    def test_episode_writer_creates_non_overwriting_json_evidence(self):
        episode = self.runner.run(task_id="persistence")
        with tempfile.TemporaryDirectory() as folder:
            path = write_episode(episode, folder)
            evidence = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("persistence", evidence["task_id"])
            digest_path = path.with_suffix(".sha256")
            self.assertTrue(digest_path.exists())
            self.assertIn(path.name, digest_path.read_text(encoding="ascii"))
            with self.assertRaises(FileExistsError):
                write_episode(episode, folder)


if __name__ == "__main__":
    unittest.main()
