"""UI 与报告共用的质量解释；原算法结果保留，证据不足不升级为合格。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite


QUALITY_POLICY = {"version": "workbench-evidence-v1", "threshold": 0.8,
                  "source": "existing replay area rule", "physical_acceptance_validated": False,
                  "priority": ["INCOMPLETE", "FAIL", "REVIEW_REQUIRED", "PASS"]}


@dataclass(frozen=True)
class QualityEvidence:
    quality: str
    reasons: tuple[str, ...]
    automatic_quality: str
    effective_source: str = "automatic_evidence"
    signed_area_change: float | None = None
    manual_review: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_target(*, source: str, execution: dict, verification: dict | None,
                    comparability: dict | None, mode: str, evidence_issues: tuple[str, ...] = (),
                    manual: dict | None = None) -> QualityEvidence:
    reasons = list(evidence_issues)
    receipt = ((execution.get("session") or {}).get("receipt") or execution.get("receipt") or {})
    done = receipt.get("success") is True and receipt.get("controller_state") == "DONE"
    raw = verification or {}
    result = raw.get("result") or raw
    rate = raw.get("removal_rate", result.get("removal_rate"))
    pre, post = raw.get("pre_area_px"), raw.get("post_area_px")
    signed = None
    if isinstance(pre, (int, float)) and pre > 0 and isinstance(post, (int, float)):
        signed = (pre - post) / pre
    if not done:
        reasons.append("PUMP_DONE_NOT_RECORDED")
    if not verification:
        reasons.append("VERIFICATION_NOT_RECORDED")
    if source == "manual":
        reasons.append("MANUAL_REGION_IS_NOT_STAIN_BASELINE")
        signed = None
    if verification and raw.get("match_status") != "matched":
        reasons.append("TARGET_IDENTITY_NOT_MATCHED")
    if post == 0:
        reasons.append("NO_FOREGROUND_IS_NOT_VERIFIED_DISAPPEARANCE")
    if not comparability or not all(comparability.get(key) is True for key in
            ("same_size", "same_source", "same_settings", "same_policy", "returned_to_overview", "quality_ok", "fresh_after_return", "pair_confirmed")):
        reasons.append("COMPARABILITY_NOT_ESTABLISHED")
    if mode != "mock":
        reasons.append("PHYSICAL_ACCEPTANCE_CRITERION_NOT_VALIDATED")
    numeric = isinstance(rate, (int, float)) and not isinstance(rate, bool) and isfinite(rate)
    if not numeric:
        reasons.append("REMOVAL_RATE_NOT_RECORDED")
    automatic = "UNCERTAIN"
    if not reasons and numeric:
        automatic = "CLEANED" if rate >= QUALITY_POLICY["threshold"] else "NOT_CLEANED"
    # 可靠的可比较残留可记录规则不合格；实物阈值仍显示尚待科学验收。
    if reasons == ["PHYSICAL_ACCEPTANCE_CRITERION_NOT_VALIDATED"] and numeric and rate < QUALITY_POLICY["threshold"]:
        automatic = "NOT_CLEANED"
    effective, origin = automatic, "automatic_evidence"
    if manual and manual.get("conclusion") in {"CLEANED", "NOT_CLEANED", "UNCERTAIN"}:
        review_complete = bool(manual.get("operator") and manual.get("reason") and manual.get("evidence_refs"))
        # 人工复核可解释失配/框选/未验收判据，但不能凭备注补造缺图/回执/未完成动作。
        hard_missing = bool(evidence_issues) or not done or not verification or not comparability or not comparability.get("returned_to_overview")
        if review_complete and not hard_missing:
            effective, origin = manual["conclusion"], "operator_review"
        else:
            reasons.append("MANUAL_REVIEW_INSUFFICIENT_EVIDENCE")
    return QualityEvidence(effective, tuple(dict.fromkeys(reasons)), automatic, origin, signed, manual)


def final_quality(targets: list[dict], *, workflow_status: str, mode: str) -> tuple[str, tuple[str, ...]]:
    approved = [target for target in targets if target.get("decision") == "APPROVED"]
    if workflow_status != "COMPLETED" or any(not target.get("finished") for target in approved):
        return "INCOMPLETE", ("WORKFLOW_OR_REQUIRED_TARGET_NOT_COMPLETED",)
    if any(target.get("quality") == "NOT_CLEANED" for target in approved):
        return "FAIL", ("REQUIRED_TARGET_HAS_RESIDUE",)
    if not approved or any(target.get("decision") == "PENDING_REVIEW" for target in targets) or any(target.get("quality") != "CLEANED" for target in approved):
        return "REVIEW_REQUIRED", ("REVIEW_OR_RELIABLE_QUALITY_EVIDENCE_REQUIRED",)
    return "PASS", ("SYNTHETIC_RULE_PASS" if mode == "mock" else "ALL_REQUIRED_TARGETS_HAVE_REVIEWED_EVIDENCE",)


def target_statistics(targets: list[dict], attempts: list[dict]) -> dict:
    return {"algorithm_candidates": sum(t.get("source") == "algorithm" for t in targets),
            "manual_candidates": sum(t.get("source") == "manual" for t in targets),
            "excluded": sum(t.get("decision") == "EXCLUDED" for t in targets),
            "approved": sum(t.get("decision") == "APPROVED" for t in targets),
            "pending_review": sum(t.get("decision") == "PENDING_REVIEW" for t in targets),
            "executed_targets": sum(t.get("execution") == "EXECUTED" for t in targets),
            "cleaned": sum(t.get("decision") == "APPROVED" and t.get("quality") == "CLEANED" for t in targets),
            "not_cleaned": sum(t.get("decision") == "APPROVED" and t.get("quality") == "NOT_CLEANED" for t in targets),
            "uncertain": sum(t.get("decision") != "EXCLUDED" and t.get("quality") in {"UNCERTAIN", "NOT_ASSESSED"} for t in targets),
            "failed_targets": sum(t.get("execution") == "FAILED" for t in targets),
            "attempts": len(attempts), "pump_tx_attempts": sum(t.get("pump_tx") is True for t in attempts),
            "pump_done_attempts": sum(t.get("pump_done") is True for t in attempts)}
