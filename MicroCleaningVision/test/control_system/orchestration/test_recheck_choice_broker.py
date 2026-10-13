"""复检多选与物理动作布尔许可必须分离；所有测试不访问设备。"""
import threading
import unittest
from microcleaning.control_system.orchestration.workbench_events import (
    Cancellation, ConfirmationBroker, EventBus, OperationCancelled,
)


class RecheckChoiceBrokerTests(unittest.TestCase):
    def start_request(self, broker, bus, callback):
        result, errors = [], []
        def work():
            try:
                result.append(callback())
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=work)
        thread.start()
        request = bus.events.get(timeout=1).payload["request_id"]
        return thread, request, result, errors

    def test_strings_never_approve_physical_boolean_request(self):
        bus, cancel = EventBus(), Cancellation()
        broker = ConfirmationBroker(bus, cancel, timeout=1)
        self.assertFalse(broker.reply(None, True))
        thread, request, result, errors = self.start_request(broker, bus, lambda: broker.ask("移动许可"))
        for invalid in ("rewash", "True", 1, None):
            self.assertFalse(broker.reply(request, invalid))
        self.assertTrue(broker.reply(request, False))
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [False])
        self.assertEqual(errors, [])

    def test_choice_keeps_reason_and_rejects_duplicate_and_stale(self):
        bus, cancel = EventBus(), Cancellation()
        broker = ConfirmationBroker(bus, cancel, timeout=1)
        thread, request, result, errors = self.start_request(broker, bus,
            lambda: broker.ask_choice("复检选择", choices=("rewash", "pause")))
        for identity, value in (("old", "rewash"), (request, True), (request, "next")):
            self.assertFalse(broker.reply(identity, value))
        self.assertTrue(broker.reply(request, "rewash", reason="  仍有残留  "))
        self.assertFalse(broker.reply(request, "pause"))
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [{"choice": "rewash", "reason": "仍有残留"}])
        self.assertEqual(errors, [])

    def test_timeout_is_pause_and_never_boolean_authorization(self):
        bus, cancel = EventBus(), Cancellation()
        broker = ConfirmationBroker(bus, cancel, timeout=.03)
        answer = broker.ask_choice("复检选择", choices=("rewash", "pause"))
        self.assertEqual(answer["choice"], "pause")
        self.assertFalse(broker.reply(None, "rewash"))
        self.assertTrue(any(event.kind == "confirmation_closed" for event in bus.drain()))

    def test_cancelled_request_never_accepts_late_reply(self):
        bus, cancel = EventBus(), Cancellation()
        broker = ConfirmationBroker(bus, cancel, timeout=1)
        thread, request, result, errors = self.start_request(broker, bus,
            lambda: broker.ask_choice("复检选择", choices=("next", "pause")))
        cancel.cancel()
        self.assertFalse(broker.reply(request, "next"))
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], OperationCancelled)

    def test_illegal_choice_sets_are_rejected_before_request(self):
        bus, cancel = EventBus(), Cancellation()
        broker = ConfirmationBroker(bus, cancel)
        for choices in ((), ("rewash",), ("pause", "spray")):
            with self.assertRaises(ValueError):
                broker.ask_choice("复检选择", choices=choices)
        self.assertEqual(bus.drain(), [])
