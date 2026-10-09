import unittest
from microcleaning.control_system.orchestration.result_evidence import evaluate_target, final_quality, target_statistics


class ResultEvidenceTests(unittest.TestCase):
    def evaluate(self, **changes):
        fields = dict(source="algorithm", execution={"receipt": {"success": True, "controller_state": "DONE"}},
            verification={"match_status": "matched", "pre_area_px": 100, "post_area_px": 20, "removal_rate": .8},
            comparability={key: True for key in ("same_size", "same_source", "same_settings", "same_policy", "returned_to_overview", "quality_ok", "fresh_after_return", "pair_confirmed")}, mode="mock")
        fields.update(changes)
        return evaluate_target(**fields)

    def test_rule_pass_needs_all_evidence(self):
        self.assertEqual(self.evaluate().quality, "CLEANED")
        self.assertEqual(self.evaluate(mode="real").quality, "UNCERTAIN")
        self.assertEqual(self.evaluate(source="manual").quality, "UNCERTAIN")
        self.assertEqual(self.evaluate(execution={}).quality, "UNCERTAIN")
        self.assertEqual(self.evaluate(evidence_issues=("POST_IMAGE_MISSING",)).quality, "UNCERTAIN")

    def test_empty_foreground_and_unmatched_never_auto_pass(self):
        self.assertEqual(self.evaluate(verification={"match_status": "matched", "pre_area_px": 100, "post_area_px": 0, "removal_rate": 1}).quality, "UNCERTAIN")
        self.assertEqual(self.evaluate(verification={"match_status": "unmatched", "removal_rate": 1}).quality, "UNCERTAIN")

    def test_residue_and_pollution_growth_not_hidden(self):
        evidence = self.evaluate(verification={"match_status": "matched", "pre_area_px": 100, "post_area_px": 150, "removal_rate": 0})
        self.assertEqual(evidence.quality, "NOT_CLEANED")
        self.assertEqual(evidence.signed_area_change, -.5)

    def test_final_priority_and_zero_not_pass(self):
        rows = [{"decision": "APPROVED", "finished": True, "quality": "NOT_CLEANED"}, {"decision": "APPROVED", "finished": True, "quality": "UNCERTAIN"}]
        self.assertEqual(final_quality(rows, workflow_status="CANCELLED", mode="mock")[0], "INCOMPLETE")
        self.assertEqual(final_quality(rows, workflow_status="COMPLETED", mode="mock")[0], "FAIL")
        self.assertEqual(final_quality([], workflow_status="COMPLETED", mode="mock")[0], "REVIEW_REQUIRED")

    def test_manual_note_cannot_fabricate_missing_evidence(self):
        manual = {"conclusion": "CLEANED", "operator": "Tester", "reason": "review", "evidence_refs": ["post.png"]}
        evidence = self.evaluate(evidence_issues=("POST_IMAGE_MISSING",), manual=manual)
        self.assertEqual(evidence.quality, "UNCERTAIN")
        self.assertEqual(evidence.automatic_quality, "UNCERTAIN")
