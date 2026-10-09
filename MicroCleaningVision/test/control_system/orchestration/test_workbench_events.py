import tempfile
import threading
import time
import unittest
from pathlib import Path

from demo.closed_loop_fixture import MockF103Serial
from integration.test_workbench_flow import run_reviewed
from microcleaning.control_system.orchestration.workbench_events import Cancellation, ConfirmationBroker, EventBus, OperationCancelled
from microcleaning.control_system.planning.stage2_position import load_position


class WorkbenchEventsTests(unittest.TestCase):
    def test_confirmation_is_bound_once_and_can_cancel_wait(self):
        bus, cancel = EventBus(), Cancellation()
        broker = ConfirmationBroker(bus, cancel, timeout=1)
        result = []
        worker = threading.Thread(target=lambda: result.append(broker.ask("test")))
        worker.start()
        request = bus.events.get(timeout=1).payload["request_id"]
        self.assertFalse(broker.reply("old-request", True))
        self.assertTrue(broker.reply(request, True))
        self.assertFalse(broker.reply(request, True))
        worker.join(timeout=1)
        self.assertEqual(result, [True])
        cancel.cancel()
        with self.assertRaises(OperationCancelled):
            broker.ask("cancelled")

    def test_latest_frame_is_bounded_and_snapshot_not_shared(self):
        import numpy as np
        bus = EventBus()
        frame = np.zeros((2, 2), np.uint8)
        bus.frame(frame)
        frame[:] = 255
        self.assertEqual(int(bus.latest_frame().sum()), 0)
        self.assertIsNone(bus.latest_frame())
        payload = {"targets": ["S0001"]}
        bus.emit("task", snapshot=payload)
        payload["targets"].append("S0002")
        self.assertEqual(bus.drain()[0].payload["snapshot"]["targets"], ["S0001"])

    def test_cancel_during_serial_wait_stops_owner_in_motion_pump_return(self):
        for scenario in ("motion-timeout", "pump-timeout", "return-timeout"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temp:
                waiting = threading.Event()
                cancellation = Cancellation()
                instances = []
                class SlowSerial(MockF103Serial):
                    def __init__(self, *args):
                        super().__init__(*args)
                        self.timeout = 1.0
                        instances.append(self)
                    def readline(self):
                        if self._queue:
                            return super().readline()
                        waiting.set()
                        time.sleep(self.timeout)
                        return b""
                results = []
                worker = threading.Thread(target=lambda: results.append(run_reviewed(Path(temp), scenario=scenario,
                    raw_type=SlowSerial, cancellation=cancellation)))
                worker.start()
                self.assertTrue(waiting.wait(2))
                started = time.monotonic()
                cancellation.cancel()
                worker.join(timeout=1)
                self.assertFalse(worker.is_alive(), "取消等待不能等完整原超时")
                self.assertLess(time.monotonic()-started, .8)
                result, raw, _ = results[0]
                self.assertEqual(result["status"], "CANCELLED")
                self.assertEqual(raw.opens, 1)
                self.assertIn(b"MCV1|STOP\n", raw.writes)
                if scenario != "pump-timeout":
                    self.assertIn(b"STOP\r\n", raw.writes)
                    self.assertIsNone(load_position(Path(temp)/"mock_position.json").xy())
                self.assertFalse((Path(temp)/"cycle_001/post.png").exists())

