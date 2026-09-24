"""PUMP_IN_PLACE 无标定申请、治理器与人工关卡测试。"""

from dataclasses import replace
import unittest

from microcleaning.contracts import SafetyOutcome
from microcleaning.control_system.safety.fixed_rule import (
    IN_PLACE_TARGET_MM,
    NOZZLE_FIXED_FRAME,
    PUMP_IN_PLACE,
    propose_action,
    propose_pump_in_place,
)
from microcleaning.control_system.safety.governor import approve_human_gate, evaluate_action
from microcleaning.data_learning.image_quality import ImageQuality, build_observation
from microcleaning.vision.contamination import ContaminationMeasurement
from microcleaning.vision.state_estimator import estimate_state


class PumpInPlaceTests(unittest.TestCase):
    def setUp(self):
        quality = ImageQuality(0.95, 0.95, 0.95)
        self.pre = build_observation(
            task_id="in-place",
            frame_id="pre",
            raw_image_ref="replay://pre.png",
            quality=quality,
        )
        self.measurement = ContaminationMeasurement(100.0, (40.0, 50.0), 0.2, 0.95)
        self.state = estimate_state(
            self.pre,
            self.measurement,
            device_state={"controller_connected": True, "interlock_ok": True},
        )

    def test_spray_at_point_still_requires_work_mm(self):
        self.assertFalse(self.state.calibration_valid)
        self.assertEqual("image_px", self.state.coordinate_frame)
        self.assertIsNone(self.state.target_centroid_mm)
        self.assertIsNone(propose_action(self.state))

    def test_pump_in_place_does_not_require_millimetres(self):
        request = propose_pump_in_place(self.state)
        self.assertIsNotNone(request)
        self.assertEqual(PUMP_IN_PLACE, request.primitive)
        self.assertEqual(NOZZLE_FIXED_FRAME, request.coordinate_frame)
        self.assertEqual(IN_PLACE_TARGET_MM, request.target_centroid_mm)
        self.assertFalse(request.constraints["xy_motion"])
        self.assertFalse(request.constraints["work_mm"])
        self.assertEqual(200, request.duration_ms)

    def test_no_target_does_not_propose_pump(self):
        empty = estimate_state(
            self.pre,
            ContaminationMeasurement(0.0, None, 1.0, 0.20),
            device_state={"controller_connected": True, "interlock_ok": True},
        )
        self.assertIsNone(propose_pump_in_place(empty))

    def test_governor_returns_human_not_allow_for_first_pump(self):
        request = propose_pump_in_place(self.state)
        decision = evaluate_action(self.state, request)
        self.assertEqual(SafetyOutcome.HUMAN, decision.outcome)
        self.assertIn("PUMP_IN_PLACE_REQUIRES_HUMAN", decision.reason_codes)
        self.assertIsNone(decision.approval_token)

    def test_unconfirmed_human_gate_does_not_allow(self):
        request = propose_pump_in_place(self.state)
        human = evaluate_action(self.state, request)
        still_human = approve_human_gate(self.state, request, human, confirmed=False)
        self.assertEqual(SafetyOutcome.HUMAN, still_human.outcome)
        self.assertIsNone(still_human.approval_token)

    def test_confirmed_human_gate_issues_single_use_allow(self):
        request = propose_pump_in_place(self.state)
        human = evaluate_action(self.state, request)
        allowed = approve_human_gate(self.state, request, human, confirmed=True)
        self.assertEqual(SafetyOutcome.ALLOW, allowed.outcome)
        self.assertIn("HUMAN_GATE_CONFIRMED", allowed.reason_codes)
        self.assertIsNotNone(allowed.approval_token)
        self.assertIsNotNone(allowed.request_digest)

    def test_deny_cannot_be_rewritten_by_human_gate(self):
        disconnected = estimate_state(
            self.pre,
            self.measurement,
            device_state={"controller_connected": False, "interlock_ok": True},
        )
        request = propose_pump_in_place(disconnected)
        denied = evaluate_action(disconnected, request)
        self.assertEqual(SafetyOutcome.DENY, denied.outcome)
        with self.assertRaises(PermissionError):
            approve_human_gate(disconnected, request, denied, confirmed=True)

    def test_non_origin_in_place_target_is_denied(self):
        request = propose_pump_in_place(self.state)
        forged = replace(request, target_centroid_mm=(12.0, 0.0))
        decision = evaluate_action(self.state, forged)
        self.assertEqual(SafetyOutcome.DENY, decision.outcome)
        self.assertIn("IN_PLACE_TARGET_NOT_NOZZLE_ORIGIN", decision.reason_codes)

    def test_in_place_duration_above_300_ms_is_denied(self):
        request = propose_pump_in_place(self.state)
        forged = replace(request, duration_ms=500)
        decision = evaluate_action(self.state, forged)
        self.assertEqual(SafetyOutcome.DENY, decision.outcome)
        self.assertIn("DURATION_OUT_OF_BOUNDS", decision.reason_codes)

    def test_xy_not_forbidden_is_denied(self):
        request = propose_pump_in_place(self.state)
        forged = replace(request, constraints={**request.constraints, "xy_motion": True})
        decision = evaluate_action(self.state, forged)
        self.assertEqual(SafetyOutcome.DENY, decision.outcome)
        self.assertIn("XY_MOTION_NOT_FORBIDDEN", decision.reason_codes)

    def test_human_gate_rejects_mismatched_decision(self):
        request = propose_pump_in_place(self.state)
        human = evaluate_action(self.state, request)
        mismatched = replace(human, action_id="other")
        with self.assertRaises(PermissionError):
            approve_human_gate(self.state, request, mismatched, confirmed=True)


if __name__ == "__main__":
    unittest.main()
