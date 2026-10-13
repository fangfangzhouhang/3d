"""Stage 2 运动关卡：从不直接 ALLOW；缺标定、位置未知、报文超长一律 DENY。主机不再用步数边界拦住。"""

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from microcleaning.contracts import SafetyOutcome
from microcleaning.control_system.safety.motion_gate import (
    STAGE2_RUN_STEP_CAP,
    MotionLimits,
    MotionRequest,
    approve_motion_gate,
    evaluate_motion,
    explain_travel_block,
    format_side_clearance,
    motion_request_digest,
    new_motion_request_id,
    require_motion_allow,
    summarize_motion,
)


def _request(lines, **overrides):
    base = MotionRequest(
        request_id=new_motion_request_id(),
        task_id="task",
        lines=tuple(lines),
        position_before_steps=(0, 0),
        start_reference="image_center",
        calibration_ref="cal/mm_per_px.json",
        calibration_sha256="a" * 64,
    )
    return replace(base, **overrides)


class MotionGateTests(unittest.TestCase):
    def test_valid_motion_is_human_never_allow(self):
        decision = evaluate_motion(_request(["MOVEXY 320 FWD 160 REV"]))
        self.assertIs(SafetyOutcome.HUMAN, decision.outcome)
        self.assertIn("STAGE2_MOTION_REQUIRES_HUMAN", decision.reason_codes)
        self.assertIsNone(decision.approval_token)

    def test_separate_moves_are_not_added_together(self):
        decision = evaluate_motion(_request(["MOVEXY 4001 FWD 0 FWD", "MOVEXY 4001 REV 0 FWD"]))
        self.assertIs(SafetyOutcome.HUMAN, decision.outcome)
        self.assertNotIn("RUN_STEPS_OVER_CAP", decision.reason_codes)
        self.assertNotIn("LEG_STEPS_OVER_CAP", decision.reason_codes)

    def test_single_leg_over_cap_is_denied(self):
        decision = evaluate_motion(_request([f"MOVEXY {STAGE2_RUN_STEP_CAP + 1} FWD 0 FWD"]))
        self.assertIn("LEG_STEPS_OVER_CAP", decision.reason_codes)

    def test_missing_or_warned_calibration_is_denied(self):
        missing = evaluate_motion(_request(["MOVEXY 10 FWD 0 FWD"], calibration_ref=None))
        self.assertIn("CALIBRATION_MISSING", missing.reason_codes)
        warned = evaluate_motion(
            _request(["MOVEXY 10 FWD 0 FWD"], calibration_warnings=("MATCH_SCORE_LOW",))
        )
        self.assertIn("CALIBRATION_HAS_WARNINGS", warned.reason_codes)

    def test_unknown_position_is_denied(self):
        decision = evaluate_motion(_request(["MOVEXY 10 FWD 0 FWD"], position_before_steps=None))
        self.assertIs(SafetyOutcome.DENY, decision.outcome)
        self.assertIn("POSITION_UNKNOWN", decision.reason_codes)

    def test_old_step_fence_does_not_block_a_move(self):
        text = format_side_clearance((0, 0))
        self.assertIn("账本位置 X=0 步，Y=0 步", text)
        self.assertIn("主机不再按步数边界拦住这一步", text)
        outbound = _request(["MOVEXY 300 FWD 0 FWD"], position_before_steps=(9800, 0))
        returning = _request(["MOVEXY 300 REV 0 FWD"], position_before_steps=(10100, 0))
        self.assertIsNone(explain_travel_block(outbound, returning))
        self.assertIsNone(explain_travel_block(_request(["MOVEXY 10 FWD 0 FWD"]), _request(["MOVEXY 10 REV 0 FWD"])))

    def test_unknown_position_block_does_not_invent_a_margin(self):
        outbound = _request(["MOVEXY 10 FWD 0 FWD"], position_before_steps=None)
        text = explain_travel_block(outbound, _request([], position_before_steps=None))
        self.assertIn("当前位置未知", text)
        self.assertNotIn("SOFT_LIMIT", text)

    def test_intermediate_excursion_is_not_denied_by_a_step_fence(self):
        request = _request(
            ["MOVEXY 300 FWD 0 FWD", "MOVEXY 300 REV 0 FWD"],
            position_before_steps=(9800, 0),
        )
        plan = summarize_motion(request)
        self.assertEqual((9800, 0), plan.position_after)
        decision = evaluate_motion(request)
        self.assertNotIn("SOFT_LIMIT_EXCEEDED", decision.reason_codes)
        self.assertIs(SafetyOutcome.HUMAN, decision.outcome)

    def test_pump_text_or_malformed_line_is_denied(self):
        for line in ("MCV1|PUMP|a|100", "MOVEXY 10 XYZ 0 FWD"):
            decision = evaluate_motion(_request([line]))
            self.assertIn("MALFORMED_OR_UNSAFE_LINE", decision.reason_codes)

    def test_empty_motion_is_denied(self):
        self.assertIn("NO_MOTION", evaluate_motion(_request([])).reason_codes)

    def test_truncated_path_is_flagged_for_human(self):
        decision = evaluate_motion(_request(["MOVEXY 10 FWD 0 FWD"], dispatch_truncated=True))
        self.assertIs(SafetyOutcome.HUMAN, decision.outcome)
        self.assertIn("PATH_TRUNCATED_BY_BUDGET", decision.reason_codes)

    def test_limits_cannot_be_raised_above_code_cap(self):
        with self.assertRaises(ValueError):
            evaluate_motion(
                _request(["MOVEXY 10 FWD 0 FWD"]),
                MotionLimits(max_abs_steps_per_axis=STAGE2_RUN_STEP_CAP * 4),
            )

    def test_deny_cannot_be_rewritten_by_human(self):
        request = _request(["MOVEXY 10 FWD 0 FWD"], position_before_steps=None)
        with self.assertRaises(PermissionError):
            approve_motion_gate(request, evaluate_motion(request), confirmed=True)

    def test_unconfirmed_stays_human_and_confirmed_issues_bound_allow(self):
        request = _request(["MOVEXY 10 FWD 0 FWD"])
        human = evaluate_motion(request)
        self.assertIs(human, approve_motion_gate(request, human, confirmed=False))
        allow = approve_motion_gate(request, human, confirmed=True)
        self.assertIs(SafetyOutcome.ALLOW, allow.outcome)
        self.assertTrue(allow.approval_token)
        self.assertEqual(motion_request_digest(request), allow.request_digest)
        self.assertIn("HUMAN_GATE_CONFIRMED", allow.reason_codes)

    def test_expired_or_replayed_allow_is_refused(self):
        request = _request(["MOVEXY 10 FWD 0 FWD"])
        allow = approve_motion_gate(request, evaluate_motion(request), confirmed=True)
        later = datetime.now(timezone.utc) + timedelta(minutes=5)
        with self.assertRaises(PermissionError):
            require_motion_allow(request, allow, request.lines, now=later)
        require_motion_allow(request, allow, request.lines)
        with self.assertRaises(PermissionError):
            require_motion_allow(request, allow, request.lines)

    def test_allow_for_one_request_cannot_cover_another(self):
        request = _request(["MOVEXY 10 FWD 0 FWD"])
        allow = approve_motion_gate(request, evaluate_motion(request), confirmed=True)
        other = replace(request, lines=("MOVEXY 1500 FWD 0 FWD",))
        with self.assertRaises(PermissionError):
            require_motion_allow(other, allow, other.lines)


if __name__ == "__main__":
    unittest.main()
