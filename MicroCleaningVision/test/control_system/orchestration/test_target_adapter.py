import unittest

from microcleaning.control_system.orchestration.target_adapter import TargetIdentityError, TargetLedger
from microcleaning.vision.target_instance import extract_target_instances


class TargetLedgerTests(unittest.TestCase):
    def test_completed_target_reappearing_is_not_registered_as_new(self):
        mask, ledger = self.initial()
        post = mask.copy()
        post[5:15, 5:15] = 0
        ledger.advance("post", post, extract_target_instances(post), completed_id="S0001")
        with self.assertRaisesRegex(TargetIdentityError, "COMPLETED_TARGET_REAPPEARED"):
            ledger.advance("fresh", mask, extract_target_instances(mask))
        self.assertEqual(2, len(ledger.entries))
    def initial(self):
        import numpy as np
        mask = np.zeros((60, 100), dtype=np.uint8)
        mask[5:15, 5:15] = 255
        mask[5:15, 60:70] = 255
        return mask, TargetLedger("pre", mask, extract_target_instances(mask))

    def test_frame_t1_relabel_does_not_complete_original_t2(self):
        mask, ledger = self.initial()
        post = mask.copy()
        post[5:15, 5:15] = 0
        ledger.advance("post", post, extract_target_instances(post), completed_id="S0001")
        self.assertEqual(["S0002"], [target.target_id for target in ledger.pending()])
        self.assertEqual("T1", ledger.entries["S0002"].instance.target_id)
        self.assertTrue(ledger.entries["S0001"].completed)

    def test_retry_count_survives_fresh_observation(self):
        mask, ledger = self.initial()
        ledger.entries["S0002"].retry_count = 1
        ledger.advance("fresh", mask.copy(), extract_target_instances(mask))
        self.assertEqual(1, ledger.entries["S0002"].as_sequence_target().retry_count)
        self.assertEqual("fresh", ledger.entries["S0002"].observation_id)
        self.assertEqual(0.0, ledger.entries["S0002"].as_sequence_target().risk)

    def test_unattempted_disappearance_is_not_cleaning_success(self):
        mask, ledger = self.initial()
        post = mask.copy()
        post[5:15, 60:70] = 0
        with self.assertRaisesRegex(TargetIdentityError, "UNATTEMPTED_TARGET_DISAPPEARED"):
            ledger.advance("post", post, extract_target_instances(post))
        self.assertFalse(ledger.entries["S0002"].completed)

    def test_completed_residual_is_not_a_new_target(self):
        mask, ledger = self.initial()
        post = mask.copy()
        post[7:15, 5:15] = 0
        ledger.advance("post", post, extract_target_instances(post), completed_id="S0001")
        ledger.advance("fresh", post.copy(), extract_target_instances(post))
        self.assertEqual(2, len(ledger.entries))
        self.assertEqual(["S0002"], [target.target_id for target in ledger.pending()])

    def test_merged_components_are_not_assigned_to_two_task_targets(self):
        import numpy as np
        mask = np.zeros((40, 50), dtype=np.uint8)
        mask[5:15, 5:15] = 255
        mask[5:15, 20:30] = 255
        ledger = TargetLedger("pre", mask, extract_target_instances(mask))
        post = mask.copy()
        post[9, 15:20] = 255
        with self.assertRaisesRegex(TargetIdentityError, "MULTIPLE_TARGETS_MATCH_ONE_COMPONENT"):
            ledger.advance("post", post, extract_target_instances(post))

    def test_split_component_requires_human(self):
        mask, ledger = self.initial()
        post = mask.copy()
        post[5:15, 9:11] = 0
        with self.assertRaisesRegex(TargetIdentityError, "AMBIGUOUS"):
            ledger.advance("post", post, extract_target_instances(post))

    def test_default_advance_still_registers_an_unclaimed_component(self):
        mask, ledger = self.initial()
        post = mask.copy()
        post[40:50, 40:50] = 255
        ledger.advance("fresh", post, extract_target_instances(post))
        self.assertEqual(3, len(ledger.entries))

    def test_closed_roster_does_not_register_a_new_component(self):
        mask, ledger = self.initial()
        post = mask.copy()
        post[40:50, 40:50] = 255
        ledger.advance("fresh", post, extract_target_instances(post), allow_new=False)
        self.assertEqual(2, len(ledger.entries))
        self.assertEqual(1, ledger.ignored_new)
        self.assertEqual(["S0001", "S0002"], [target.target_id for target in ledger.pending()])
