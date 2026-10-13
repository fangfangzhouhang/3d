"""单偏移、原观察位回程和含回程预算；不接实物。"""

from dataclasses import replace
import unittest

from demo.closed_loop_fixture import mock_offset
from microcleaning.control_system.planning.cleaning_plan import CleaningPlan, CleaningStrategy
from microcleaning.control_system.planning.path_preview import PathPlaceholderConfig
from microcleaning.control_system.planning.stage2_axes import parse_movexy_line
from microcleaning.control_system.planning.stage2_geometry import build_cycle_geometry
from microcleaning.control_system.planning.work_frame import MotorCalibration, WorkFrameConfig
from microcleaning.contracts import SafetyOutcome
from microcleaning.control_system.safety.motion_gate import DEFAULT_SOFT_LIMIT_STEPS, STAGE2_RUN_STEP_CAP, evaluate_motion


class CycleGeometryTests(unittest.TestCase):
    def build(self, *, offset=None, used=(0, 0), budget=1600, base=None, calibration=None, real=False, point=(197.5, 132.5)):
        plan = CleaningPlan(CleaningStrategy.CENTER_POINT, "image_px", (320, 240), 100, (point,), (0,), "fixture")
        return build_cycle_geometry(plan, base=base or PathPlaceholderConfig(), calibration=calibration,
            offset=offset or replace(mock_offset(), scope_to_nozzle_delta_steps=(200, 80)),
            observation_position=(30, -20), task_id="fixture", used_abs_steps=used, budget=budget, real=real)

    def test_target_and_offset_are_applied_once_and_return_to_overview(self):
        geometry = self.build()
        self.assertEqual((120, -40), geometry.target_delta_steps)
        self.assertEqual((200, 80), geometry.offset_delta_steps)
        self.assertEqual((350, 20), geometry.execution_position)
        self.assertEqual((-320, -40), parse_movexy_line(geometry.returning.lines[0]))
        self.assertEqual((640, 160), geometry.cycle_abs_steps)
        self.assertEqual((), geometry.reasons)

    def test_return_is_not_added_onto_the_outbound_move(self):
        geometry = self.build(budget=600)
        self.assertFalse(geometry.outbound.truncated)
        self.assertFalse(geometry.returning.truncated)
        self.assertNotIn("TASK_BUDGET_INCLUDES_RETURN_EXCEEDED", geometry.reasons)
        self.assertNotIn("OUTBOUND_BUDGET_EXCEEDED", geometry.reasons)

    def test_earlier_cycles_are_not_added_onto_this_move(self):
        geometry = self.build(used=(1000, 0))
        self.assertNotIn("TASK_BUDGET_INCLUDES_RETURN_EXCEEDED", geometry.reasons)

    def test_offset_past_the_memory_boundary_stays_one_line(self):
        nominal = DEFAULT_SOFT_LIMIT_STEPS + 200
        geometry = self.build(offset=replace(mock_offset(), scope_to_nozzle_delta_steps=(0, nominal)), budget=STAGE2_RUN_STEP_CAP)
        self.assertFalse(geometry.outbound.truncated)
        self.assertTrue(any(str(nominal) in line for line in geometry.outbound.lines))
        decision = evaluate_motion(geometry.outbound_request)
        self.assertNotIn("SOFT_LIMIT_EXCEEDED", decision.reason_codes)
        self.assertIs(SafetyOutcome.HUMAN, decision.outcome)

    def test_unknown_or_unconfirmed_offset_is_not_zero(self):
        for offset in (replace(mock_offset(), scope_to_nozzle_delta_steps=None), replace(mock_offset(), axes_confirmed=False)):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                self.build(offset=offset)

    def test_conflicting_nozzle_compensations_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "MULTIPLE_NOZZLE_COMPENSATIONS"):
            self.build(base=PathPlaceholderConfig(work=WorkFrameConfig(nozzle_offset_mm=(1, 0))))
        calibration = MotorCalibration("cal", "0" * 64, .01, .01, (160, 120), ())
        with self.assertRaisesRegex(ValueError, "MULTIPLE_NOZZLE_COMPENSATIONS"):
            self.build(calibration=calibration)

    def test_real_mode_requires_calibration_binding_and_non_mock_offset(self):
        with self.assertRaises(ValueError):
            self.build(real=True)
        calibration = MotorCalibration("cal", "0" * 64, .01, .01, None, ())
        offset = replace(mock_offset(), mock_only=False, motor_calibration_sha256="1" * 64)
        with self.assertRaisesRegex(ValueError, "OFFSET_MOTOR_CALIBRATION_MISMATCH"):
            self.build(real=True, calibration=calibration, offset=offset)

    def test_budget_cannot_be_raised(self):
        with self.assertRaises(ValueError):
            self.build(budget=STAGE2_RUN_STEP_CAP + 1)
