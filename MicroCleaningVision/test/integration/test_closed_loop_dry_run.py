"""识别、排序、先步进后喷水的软件干跑。

合成 Mask 和脚本串口只证明这条软件链的失败边界。
步进成功后的 DONE 只表示喷水输出流程结束，不是洗净，也不是标定。
"""

import ast
import sys
import types
import unittest
from pathlib import Path

from microcleaning.contracts import ExecutionReceipt, NextRoute, SafetyOutcome
from microcleaning.control_system.planning.sequence_planner import (
    STRATEGY_NEAREST_NEIGHBOR,
    STRATEGY_WEIGHTED_SCORE,
    SequenceTarget,
    plan_sequence,
)
from microcleaning.control_system.planning.stage2_axes import (
    DEFAULT_TRANSMIT_BUDGET,
    single_move_dispatch,
)
from microcleaning.control_system.safety.fixed_rule import propose_pump_in_place
from microcleaning.control_system.safety.governor import approve_human_gate, evaluate_action
from microcleaning.control_system.safety.motion_gate import (
    STAGE2_LEG_STEP_CAP,
    STAGE2_RUN_STEP_CAP,
    MotionLimits,
    MotionRequest,
    approve_motion_gate,
    evaluate_motion,
    new_motion_request_id,
)
from microcleaning.control_system.serial.f103_session import (
    F103SerialSession,
    F103SessionBusy,
)
from microcleaning.control_system.serial.stage2_link import Stage2SerialLink
from microcleaning.control_system.serial.stage2_protocol import Stage2ProtocolError, parse_stage2_reply
from microcleaning.control_system.serial.stm32_protocol import STM32ProtocolError, parse_response
from microcleaning.control_system.serial.stm32_serial import STM32SerialController
from microcleaning.data_learning.image_quality import ImageQuality, build_observation
from microcleaning.vision.contamination import ContaminationMeasurement
from microcleaning.vision.state_estimator import estimate_state
from microcleaning.vision.target_instance import TargetInstance, extract_target_instances, verify_single_target


_SERIAL_MODULE = None
_SERIAL_ORIGINAL = None
_SERIAL_INSERTED = False
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def setUpModule() -> None:
    """挡住 serial.Serial，避免干跑打开真实 COM。"""

    global _SERIAL_MODULE, _SERIAL_ORIGINAL, _SERIAL_INSERTED
    serial_mod = sys.modules.get("serial")
    if serial_mod is None:
        serial_mod = types.ModuleType("serial")
        sys.modules["serial"] = serial_mod
        _SERIAL_INSERTED = True
        _SERIAL_ORIGINAL = None
    else:
        _SERIAL_ORIGINAL = getattr(serial_mod, "Serial", None)
        _SERIAL_INSERTED = False

    def _boom(*args, **kwargs):
        raise AssertionError("集成干跑不应打开真实串口")

    serial_mod.Serial = _boom
    _SERIAL_MODULE = serial_mod


def tearDownModule() -> None:
    if _SERIAL_INSERTED:
        sys.modules.pop("serial", None)
        return
    if _SERIAL_MODULE is not None and _SERIAL_ORIGINAL is not None:
        _SERIAL_MODULE.Serial = _SERIAL_ORIGINAL


def _zeros(height: int, width: int):
    import numpy as np

    return np.zeros((height, width), dtype=np.uint8)


def _fill(mask, x: int, y: int, width: int, height: int) -> None:
    mask[y : y + height, x : x + width] = 255


def _three_stains():
    """T1 在左上，T2、T3 离得够远。面积分别是 100、500、500。"""

    mask = _zeros(80, 100)
    _fill(mask, 4, 4, 10, 10)
    _fill(mask, 60, 4, 20, 25)
    _fill(mask, 4, 50, 25, 20)
    return mask


def _sequence_target(instance: TargetInstance) -> SequenceTarget:
    """只在集成测试里抄字段。视觉和规划模块不互相引用。"""

    return SequenceTarget(
        target_id=instance.target_id,
        centroid_px=(float(instance.centroid_px[0]), float(instance.centroid_px[1])),
        area_px=float(instance.area_px),
    )


class ScriptSerial:
    """按写入回答 STEP 或 MCV1。不对应任何真实端口。"""

    def __init__(self, *, readxy: str = "idle", pump: str = "done", ack_after_stop: bool = False) -> None:
        if readxy not in {"idle", "timeout", "ack"}:
            raise ValueError(readxy)
        if pump not in {"done", "ack"}:
            raise ValueError(pump)
        self.readxy = readxy
        self.pump = pump
        self.ack_after_stop = ack_after_stop
        self.writes: list[bytes] = []
        self._queue: list[bytes] = []
        self.close_count = 0

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        text = data.decode("ascii").strip()
        if text == "HELLO":
            self._queue.append(b"STEP_OK v0.3\r\n")
        elif text.startswith("MOVEXY "):
            _command, nx, dx, ny, dy = text.split()
            self._queue.append(f"STEP2_START X={nx} {dx} Y={ny} {dy}\r\n".encode("ascii"))
        elif text == "READXY":
            if self.readxy == "ack":
                self._queue.append(b"MCV1|ACK|step-ack\n")
            elif self.readxy == "idle":
                self._queue.append(b"STEP2 X=1 BX=0 Y=0 BY=0\r\n")
        elif text == "STOP":
            self._queue.append(b"STEP_STOPPED\r\n")
            if self.ack_after_stop:
                self._queue.append(b"MCV1|ACK|after-stop\n")
        elif text.startswith("MCV1|"):
            parts = text.split("|")
            kind = parts[1] if len(parts) > 1 else ""
            if kind == "STATUS":
                self._queue.append(b"MCV1|STATUS|ESTOP=0|PUMP=0\n")
            elif kind == "STOP":
                self._queue.append(b"MCV1|ACK|STOP\n")
                self._queue.append(b"MCV1|DONE|STOP\n")
            elif kind == "PUMP":
                action_id = parts[2]
                self._queue.append(f"MCV1|ACK|{action_id}\n".encode("ascii"))
                if self.pump == "done":
                    self._queue.append(f"MCV1|DONE|{action_id}\n".encode("ascii"))
                else:
                    self._queue.append(f"MCV1|ACK|{action_id}\n".encode("ascii"))
        return len(data)

    def flush(self) -> None:
        return None

    def reset_input_buffer(self) -> None:
        self._queue.clear()

    def readline(self) -> bytes:
        if not self._queue:
            return b""
        return self._queue.pop(0)

    def close(self) -> None:
        self.close_count += 1


class ClosedLoopDryRunTests(unittest.TestCase):
    def setUp(self) -> None:
        import cv2

        self._video_capture = cv2.VideoCapture

        def _refuse_camera(*args, **kwargs):
            raise AssertionError("集成干跑不应打开相机")

        cv2.VideoCapture = _refuse_camera
        quality = ImageQuality(0.95, 0.95, 0.95)
        self.pre = build_observation(
            task_id="closed-loop",
            frame_id="pre",
            raw_image_ref="replay://closed-loop-pre.png",
            quality=quality,
        )
        self.post = build_observation(
            task_id="closed-loop",
            frame_id="post",
            raw_image_ref="replay://closed-loop-post.png",
            quality=quality,
        )

    def tearDown(self) -> None:
        import cv2

        cv2.VideoCapture = self._video_capture

    def test_chain_steps_before_pump_and_done_is_output_not_cleaning(self) -> None:
        pre_mask = _three_stains()
        found = extract_target_instances(pre_mask)
        selected = self._first_target(found)
        port = ScriptSerial()
        result = self._run(port, "DRY-CHAIN", selected, pre_mask)
        pump_writes = [item for item in port.writes if item.startswith(b"MCV1|PUMP|")]
        blob = b"".join(port.writes)

        self.assertEqual(selected.target_id, self._plan(found).selected_target_id)
        self.assertEqual(1, blob.count(b"MOVEXY "))
        self.assertLess(blob.index(b"HELLO\r\n"), blob.index(b"MOVEXY "))
        self.assertLess(blob.index(b"READXY\r\n"), blob.index(b"MCV1|PUMP|"))
        self.assertEqual(1, len(pump_writes))
        self.assertEqual(len(pump_writes[0]), result.pump_bytes)
        self.assertGreater(result.pump_bytes, 0)
        self.assertTrue(result.pump_called)
        self.assertTrue(result.output_finished)
        self.assertIsNotNone(result.receipt)
        assert result.receipt is not None
        self.assertEqual("DONE", result.receipt.controller_state)
        self.assertTrue(result.receipt.success)
        self.assertNotIn(b"TO_NEEDLE", blob)
        self.assertNotIn(b"TO_SCOPE", blob)

        unchanged = verify_single_target(
            task_id="closed-loop",
            pre=self.pre,
            post=self.post,
            pre_target=selected,
            pre_mask=pre_mask,
            post_mask=pre_mask,
            receipt=result.receipt,
        )
        self.assertEqual("matched", unchanged.match_status)
        self.assertEqual(NextRoute.RETRY, unchanged.result.next_route)
        self.assertNotEqual(NextRoute.STOP, unchanged.result.next_route)
        self.assertAlmostEqual(0.0, unchanged.removal_rate)

    def test_motion_failure_unarmed_or_not_allow_sends_zero_pump_bytes(self) -> None:
        selected = self._first_target(extract_target_instances(_three_stains()))
        failed = self._run(ScriptSerial(readxy="timeout"), "DRY-FAIL", selected, _three_stains())
        self.assertEqual(0, failed.pump_bytes)
        self.assertFalse(failed.pump_called)
        self.assertFalse(failed.output_finished)
        self.assertEqual("TIMEOUT", failed.motion_reason)

        cases = (
            {"motion_armed": False},
            {"pump_armed": False},
            {"motion_allow": False},
            {"pump_allow": False},
        )
        for index, options in enumerate(cases):
            with self.subTest(**options):
                port = ScriptSerial()
                opened = {"n": 0}

                def factory(port=port, opened=opened):
                    opened["n"] += 1
                    return port

                result = self._run(
                    port,
                    f"DRY-GATE-{index}",
                    selected,
                    _three_stains(),
                    factory=factory,
                    **options,
                )
                self.assertEqual(0, opened["n"])
                self.assertEqual(0, result.pump_bytes)
                self.assertFalse(result.pump_called)
                self.assertFalse(result.serial_opened)
                self.assertEqual([], port.writes)

    def test_step_failure_ack_does_not_continue_to_pump(self) -> None:
        selected = self._first_target(extract_target_instances(_three_stains()))
        port = ScriptSerial(readxy="ack", ack_after_stop=True)
        session_box: dict[str, F103SerialSession] = {}
        result = self._run(port, "DRY-ACK-STEP", selected, _three_stains(), session_box=session_box)

        self.assertEqual("WRONG_PROTOCOL", result.motion_reason)
        self.assertEqual(0, result.pump_bytes)
        self.assertFalse(result.pump_called)
        self.assertFalse(result.output_finished)
        self.assertFalse(any(item.startswith(b"MCV1|PUMP|") for item in port.writes))
        self.assertIn(b"MCV1|ACK|after-stop\n", session_box["session"].preserved_replies)
        self.assertIsNone(result.receipt)

    def test_parsers_do_not_treat_the_other_protocol_as_success(self) -> None:
        step_success = {"STEP_OK", "STEP2_START", "STEP2", "STEP_START", "STEP_SENT"}
        pump_success = {"PONG", "ACK", "DONE"}
        foreign_to_step = (
            b"MCV1|DONE|A001\n",
            b"MCV1|ACK|A001\n",
            b"MCV1|PONG\n",
        )
        foreign_to_pump = (
            b"STEP_OK v0.3\r\n",
            b"STEP2_START X=1 FWD Y=0 FWD\r\n",
            b"STEP2 X=1 BX=0 Y=0 BY=0\r\n",
            b"STEP_STOPPED\r\n",
        )
        for raw in foreign_to_step:
            with self.subTest(parser="stage2", raw=raw):
                with self.assertRaises(Stage2ProtocolError) as caught:
                    parse_stage2_reply(raw)
                self.assertEqual("WRONG_PROTOCOL", caught.exception.reason_code)
                self.assertFalse(any(kind in str(caught.exception) for kind in step_success if kind == "STEP_OK"))
        for raw in foreign_to_pump:
            with self.subTest(parser="mcv1", raw=raw):
                with self.assertRaises(STM32ProtocolError) as caught:
                    parsed = parse_response(raw)
                    self.assertNotIn(parsed.kind, pump_success)
                self.assertNotEqual("ACK", getattr(caught.exception, "reason_code", ""))

        step_ok = parse_stage2_reply(b"STEP_OK v0.3\r\n")
        self.assertEqual("STEP_OK", step_ok.kind)
        done = parse_response(b"MCV1|DONE|A001\n")
        self.assertEqual("DONE", done.kind)
        self.assertNotEqual(step_ok.kind, done.kind)

    def test_one_session_rejects_two_logical_controllers(self) -> None:
        port = ScriptSerial()
        session = F103SerialSession(port="DRY-BUSY", connection=port)
        self.addCleanup(session.close)
        motion = Stage2SerialLink(armed=True, session=session, speed_hz=20000)
        pump = STM32SerialController(arm_pump=True, session=session)
        lease = object()
        session.acquire("stage2", lease)
        self.assertTrue(session.is_open)

        with self.assertRaises(F103SessionBusy):
            session.acquire("mcv1", object())

        selected = self._first_target(extract_target_instances(_three_stains()))
        pump_request, pump_decision = self._pump(selected)
        with self.assertRaises(F103SessionBusy):
            pump.execute(pump_request, pump_decision)
        self.assertEqual(0, session.pump_bytes)
        self.assertFalse(any(item.startswith(b"MCV1|PUMP|") for item in port.writes))

        other = Stage2SerialLink(port="DRY-BUSY", armed=True, serial_factory=ScriptSerial, speed_hz=20000)
        dispatch, motion_request, motion_decision = self._motion(selected.target_id)
        with self.assertRaises(Exception) as caught:
            other.transmit(dispatch, request=motion_request, decision=motion_decision)
        self.assertIsInstance(caught.exception.__cause__, F103SessionBusy)
        self.assertEqual(0, session.pump_bytes)
        session.release(lease)
        self.assertIs(session, motion.bound_session)
        self.assertIs(session, pump.bound_session)

    def test_shrinking_other_stains_does_not_mark_t1_clean(self) -> None:
        import numpy as np

        pre_mask = _three_stains()
        post_mask = pre_mask.copy()
        post_mask[4:29, 60:80] = 0
        post_mask[50:70, 4:29] = 0
        found = extract_target_instances(pre_mask)
        self.assertEqual(["T1", "T2", "T3"], [item.target_id for item in found])
        self.assertEqual([100.0, 500.0, 500.0], [item.area_px for item in found])
        post_found = extract_target_instances(post_mask)
        self.assertEqual(["T1"], [item.target_id for item in post_found])
        self.assertNotEqual(found[1].area_px, 0.0)
        self.assertNotEqual(found[2].area_px, 0.0)
        self.assertLess(int(np.count_nonzero(post_mask)), int(np.count_nonzero(pre_mask)))

        outcome = self._verify(pre_mask, post_mask, found[0], success=True)
        self.assertEqual("T1", outcome.target_id)
        self.assertEqual("matched", outcome.match_status)
        self.assertEqual(100.0, outcome.pre_area_px)
        self.assertEqual(100.0, outcome.post_area_px)
        self.assertAlmostEqual(0.0, outcome.removal_rate)
        self.assertEqual(NextRoute.RETRY, outcome.result.next_route)
        self.assertNotEqual(NextRoute.STOP, outcome.result.next_route)
        self.assertNotIn("REPLAY_THRESHOLD_MET", outcome.result.reason_codes)

    def test_bad_registration_is_human_not_clean(self) -> None:
        cases = {
            "split": self._split_masks(),
            "centroid_walk": self._centroid_walk_masks(),
            "nearby_blob": self._nearby_blob_masks(),
        }
        expected_status = {
            "split": "ambiguous",
            "centroid_walk": "unmatched",
            "nearby_blob": "unmatched",
        }
        for name, (pre_mask, post_mask, target) in cases.items():
            with self.subTest(name=name):
                outcome = self._verify(pre_mask, post_mask, target, success=True)
                self.assertIn(outcome.match_status, {"unmatched", "ambiguous"})
                self.assertEqual(expected_status[name], outcome.match_status)
                self.assertEqual(NextRoute.HUMAN, outcome.result.next_route)
                self.assertNotEqual(NextRoute.STOP, outcome.result.next_route)
                self.assertIsNone(outcome.removal_rate)
                self.assertIsNone(outcome.post_area_px)
                self.assertNotIn("REPLAY_THRESHOLD_MET", outcome.result.reason_codes)

    def test_sequence_is_stable_and_ties_break_on_target_id(self) -> None:
        found = extract_target_instances(_three_stains())
        copied = [_sequence_target(item) for item in found]
        first = plan_sequence(copied, strategy=STRATEGY_NEAREST_NEIGHBOR, start_px=(0.0, 0.0))
        second = plan_sequence(copied, strategy=STRATEGY_NEAREST_NEIGHBOR, start_px=(0.0, 0.0))
        self.assertEqual(first.ordered_target_ids, second.ordered_target_ids)
        self.assertEqual(first, second)
        self.assertEqual(first.selected_target_id, first.ordered_target_ids[0])

        tied = [
            SequenceTarget("T2", (0.0, 0.0), 10.0, retry_count=0),
            SequenceTarget("T1", (3.0, 0.0), 10.0, retry_count=3),
        ]
        forward = plan_sequence(tied, strategy=STRATEGY_WEIGHTED_SCORE, start_px=(0.0, 0.0))
        backward = plan_sequence(list(reversed(tied)), strategy=STRATEGY_WEIGHTED_SCORE, start_px=(0.0, 0.0))
        again = plan_sequence(tied, strategy=STRATEGY_WEIGHTED_SCORE, start_px=(0.0, 0.0))
        self.assertEqual(forward, again)
        self.assertEqual(forward.ordered_target_ids, backward.ordered_target_ids)
        self.assertEqual(("T1", "T2"), forward.ordered_target_ids)
        self.assertEqual("T1", forward.selected_target_id)
        self.assertAlmostEqual(forward.score_breakdown[0].score, forward.score_breakdown[1].score)
        self.assertIn("target_id", forward.reason)

    def test_sequence_planner_does_not_import_target_instance(self) -> None:
        import microcleaning.control_system.planning.sequence_planner as planner

        source = Path(planner.__file__).read_text(encoding="utf-8")
        self.assertNotIn("TargetInstance", source)
        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
                imported.extend(alias.name for alias in node.names)
        for name in imported:
            self.assertNotIn("vision", name)
            self.assertNotEqual("TargetInstance", name)
        self.assertFalse(hasattr(planner, "TargetInstance"))

    def test_ack_alone_is_neither_success_nor_cleaning(self) -> None:
        pre_mask = _three_stains()
        selected = self._first_target(extract_target_instances(pre_mask))
        result = self._run(ScriptSerial(pump="ack"), "DRY-ACK-ONLY", selected, pre_mask)

        self.assertTrue(result.pump_called)
        self.assertGreater(result.pump_bytes, 0)
        self.assertFalse(result.output_finished)
        self.assertIsNotNone(result.receipt)
        assert result.receipt is not None
        self.assertEqual("ACK", result.receipt.controller_state)
        self.assertFalse(result.receipt.success)
        outcome = verify_single_target(
            task_id="closed-loop",
            pre=self.pre,
            post=self.post,
            pre_target=selected,
            pre_mask=pre_mask,
            post_mask=_zeros(*pre_mask.shape),
            receipt=result.receipt,
        )
        self.assertNotEqual(NextRoute.STOP, outcome.result.next_route)
        self.assertFalse(result.receipt.success)
        self.assertIsNone(outcome.removal_rate)

    def test_empty_mask_sends_no_motion_and_no_pump(self) -> None:
        blank = _zeros(40, 40)
        found = extract_target_instances(blank)
        plan = plan_sequence([], strategy=STRATEGY_NEAREST_NEIGHBOR, start_px=(0.0, 0.0))
        opened = {"n": 0}

        def factory():
            opened["n"] += 1
            raise AssertionError("没有目标时不应创建串口")

        self.assertEqual([], found)
        self.assertIsNone(plan.selected_target_id)
        self.assertEqual((), plan.ordered_target_ids)
        self.assertEqual(0, opened["n"])
        session = F103SerialSession(port="DRY-EMPTY", serial_factory=factory)
        self.addCleanup(session.close)
        self.assertEqual(0, session.pump_bytes)
        self.assertFalse(session.is_open)
        self.assertEqual(0, opened["n"])

    def test_step_budget_stays_at_code_cap_and_over_cap_offset_is_not_travel(self) -> None:
        nominal_offset_steps = 24 * 320
        self.assertEqual(10000, STAGE2_RUN_STEP_CAP)
        self.assertEqual(10000, STAGE2_LEG_STEP_CAP)
        self.assertEqual(10000, DEFAULT_TRANSMIT_BUDGET)
        self.assertLess(nominal_offset_steps, STAGE2_RUN_STEP_CAP)
        self.assertGreater(nominal_offset_steps * 2, STAGE2_RUN_STEP_CAP)
        with self.assertRaises(ValueError):
            MotionLimits(max_abs_steps_per_axis=STAGE2_RUN_STEP_CAP + 1).validate()
        refused = single_move_dispatch(STAGE2_RUN_STEP_CAP + 1, "FWD", 0, "FWD")
        self.assertTrue(refused.truncated)
        self.assertEqual((), refused.lines)
        self.assertEqual(0, refused.transmit_abs_steps_x)

        selected = self._first_target(extract_target_instances(_three_stains()))
        dispatch, _request, _decision = self._motion(selected.target_id)
        self.assertEqual(STAGE2_RUN_STEP_CAP, dispatch.budget)
        self.assertLessEqual(dispatch.transmit_abs_steps_x, STAGE2_RUN_STEP_CAP)
        self.assertLessEqual(dispatch.transmit_abs_steps_y, STAGE2_RUN_STEP_CAP)
        self.assertFalse(dispatch.truncated)
        self.assertTrue(dispatch.lines)

        needle = str(nominal_offset_steps)
        for path in _python_sources():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(needle, text, str(path))
        for line in dispatch.lines:
            self.assertTrue(line.startswith("MOVEXY "))
            self.assertNotIn("PUMP", line)

    def _first_target(self, found: list[TargetInstance]) -> TargetInstance:
        plan = self._plan(found)
        self.assertIsNotNone(plan.selected_target_id)
        selected_id = plan.ordered_target_ids[0]
        self.assertEqual(selected_id, plan.selected_target_id)
        return next(item for item in found if item.target_id == selected_id)

    def _plan(self, found: list[TargetInstance]):
        return plan_sequence(
            [_sequence_target(item) for item in found],
            strategy=STRATEGY_NEAREST_NEIGHBOR,
            start_px=(0.0, 0.0),
        )

    def _motion(self, target_id: str):
        dispatch = single_move_dispatch(1, "FWD", 0, "FWD", budget=STAGE2_RUN_STEP_CAP)
        request = MotionRequest(
            request_id=new_motion_request_id(),
            task_id=f"closed-loop-{target_id}",
            lines=dispatch.lines,
            position_before_steps=(0, 0),
            start_reference="image_center",
            calibration_ref="synthetic/not-a-measured-calibration.json",
            calibration_sha256="0" * 64,
        )
        allowed = approve_motion_gate(request, evaluate_motion(request), confirmed=True)
        self.assertEqual(SafetyOutcome.ALLOW, allowed.outcome)
        return dispatch, request, allowed

    def _pump(self, target: TargetInstance, *, confirmed: bool = True):
        state = estimate_state(
            self.pre,
            ContaminationMeasurement(target.area_px, target.centroid_px, 0.2, target.confidence),
            device_state={"controller_connected": True, "interlock_ok": True},
        )
        request = propose_pump_in_place(state)
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual((0.0, 0.0), request.target_centroid_mm)
        self.assertEqual("nozzle_fixed", request.coordinate_frame)
        human = evaluate_action(state, request)
        if not confirmed:
            self.assertEqual(SafetyOutcome.HUMAN, human.outcome)
            return request, human
        allowed = approve_human_gate(state, request, human, confirmed=True)
        self.assertEqual(SafetyOutcome.ALLOW, allowed.outcome)
        return request, allowed

    def _run(
        self,
        port: ScriptSerial,
        port_name: str,
        target: TargetInstance,
        pre_mask,
        *,
        motion_armed: bool = True,
        pump_armed: bool = True,
        motion_allow: bool = True,
        pump_allow: bool = True,
        factory=None,
        session_box: dict | None = None,
    ):
        del pre_mask
        dispatch, motion_request, motion_decision = self._motion(target.target_id)
        if not motion_allow:
            motion_decision = evaluate_motion(motion_request)
            self.assertNotEqual(SafetyOutcome.ALLOW, motion_decision.outcome)
        pump_request, pump_decision = self._pump(target, confirmed=pump_allow)
        opens = {"n": 0}

        def default_factory():
            opens["n"] += 1
            return port

        opener = factory or default_factory
        session = F103SerialSession(port=port_name, serial_factory=opener)
        self.addCleanup(session.close)
        if session_box is not None:
            session_box["session"] = session
        link = Stage2SerialLink(armed=motion_armed, session=session, speed_hz=20000)
        controller = STM32SerialController(arm_pump=pump_armed, session=session)
        return session.run_step_then_pump(
            link,
            controller,
            dispatch=dispatch,
            motion_request=motion_request,
            motion_decision=motion_decision,
            pump_request=pump_request,
            pump_decision=pump_decision,
        )

    def _verify(self, pre_mask, post_mask, target: TargetInstance, *, success: bool):
        receipt = ExecutionReceipt(
            "closed-loop-receipt",
            "script_serial",
            "t0",
            "t1",
            (0.0, 0.0),
            200,
            0.0,
            "DONE" if success else "ACK",
            "SIMULATED",
            success,
        )
        return verify_single_target(
            task_id="closed-loop",
            pre=self.pre,
            post=self.post,
            pre_target=target,
            pre_mask=pre_mask,
            post_mask=post_mask,
            receipt=receipt,
        )

    def _split_masks(self):
        pre_mask = _zeros(80, 80)
        _fill(pre_mask, 20, 20, 12, 10)
        post_mask = _zeros(80, 80)
        _fill(post_mask, 20, 20, 5, 6)
        _fill(post_mask, 26, 20, 5, 6)
        target = extract_target_instances(pre_mask)[0]
        return pre_mask, post_mask, target

    def _centroid_walk_masks(self):
        pre_mask = _zeros(80, 90)
        _fill(pre_mask, 10, 40, 10, 10)
        _fill(pre_mask, 24, 40, 10, 10)
        _fill(pre_mask, 4, 4, 40, 10)
        post_mask = _zeros(80, 90)
        _fill(post_mask, 22, 40, 10, 10)
        target = next(item for item in extract_target_instances(pre_mask) if item.bbox == (10, 40, 10, 10))
        return pre_mask, post_mask, target

    def _nearby_blob_masks(self):
        pre_mask = _zeros(80, 90)
        _fill(pre_mask, 10, 40, 10, 10)
        post_mask = _zeros(80, 90)
        _fill(post_mask, 22, 40, 8, 8)
        target = extract_target_instances(pre_mask)[0]
        return pre_mask, post_mask, target


def _python_sources():
    for folder in ("microcleaning", "test", "demo", "scripts"):
        base = _PROJECT_ROOT / folder
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path
    main_py = _PROJECT_ROOT / "main.py"
    if main_py.exists():
        yield main_py


if __name__ == "__main__":
    unittest.main()
