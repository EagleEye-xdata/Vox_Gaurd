"""Emit golden scoring vectors from the pre-port Python reference implementation.

The emitted file is the differential-test asset proving the Go fusion in
`gateway/internal/scoring` reproduces the Python fusion exactly, including the DEF-1 fix (HIGH
reachable from the detector alone) and the invariants in CLAUDE.md 2-7.

    python backend/tools/emit_golden.py
    -> gateway/internal/scoring/testdata/golden_windows.json

`app/risk_scoring.py` no longer exists in the working tree; the fusion moved to Go on 2026-09-07.
This script is kept so the golden file's provenance stays reproducible rather than being an
unexplained blob. To run it, restore the reference first:

    git show 077e300:backend/app/risk_scoring.py > backend/app/risk_scoring.py
    python backend/tools/emit_golden.py
    git checkout -- . && rm backend/app/risk_scoring.py

Regenerating is only correct when a policy or spec change is intended. Regenerating to make a
failing Go test pass would destroy the only evidence that the port preserved the arithmetic --
which is the one thing this file exists to prove.
"""
import json
import sys
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.risk_scoring import POLICY, SessionRisk, score_window  # noqa: E402

OUT = ROOT / "gateway" / "internal" / "scoring" / "testdata" / "golden_windows.json"


def detection(p=0.5, confidence=1.0, voiced=3.0, **extra):
    return {"p_synthetic": p, "confidence": confidence, "language_supported": True,
            "voiced_seconds": voiced, **extra}


def window_cases():
    """Named cases. Each exercises a specific spec clause or invariant."""
    cases = []

    def add(name, detection_in, context, verification=None):
        cases.append({"name": name, "detection": detection_in,
                      "context": context, "verification": verification})

    # Invariant 4 / DEF-1: renormalisation over active signals; HIGH must be reachable.
    add("ai_only_high", detection(0.95, 0.95), None)
    add("ai_only_mid", detection(0.5, 1.0), None)
    add("ai_only_low_p", detection(0.02, 1.0), None)
    add("detector_ceiling_from_heuristic", detection(0.9483, 0.95), None)

    # Invariants 2 and 3: absence of evidence takes a floor via max(), never a LOW.
    add("detector_unavailable", None, {"caller_attestation": "UNKNOWN"})
    add("detector_unavailable_no_context", None, None)
    add("unsupported_language", detection(0.1, language_supported=False),
        {"caller_attestation": "UNKNOWN", "transaction_value": 250000,
         "beneficiary_is_new": True, "request_urgency": "high"})
    add("insufficient_voiced", detection(0.9, 1.0, voiced=1.0), None)
    add("low_confidence", detection(0.9, 0.2), None)
    add("verifier_failed", detection(0.4), None, {"failed": True})

    # Invariant 5: adversary-controlled context may only raise risk.
    adversary = {"caller_attestation": "KNOWN_UNVERIFIED", "attestation_source": "CALLER_ID_ONLY",
                 "transaction_value": 800000, "beneficiary_is_new": True,
                 "confirmed_fraud_flags_90d": 0, "urgency_source": "CALLER_CLAIMED"}
    add("claimed_urgency_low", detection(0.5), {**adversary, "request_urgency": "low"})
    add("claimed_urgency_normal", detection(0.5), {**adversary, "request_urgency": "normal"})
    add("claimed_urgency_high", detection(0.5), {**adversary, "request_urgency": "high"})

    # 03 section 5: floors applied last, sorted ascending, combined with max().
    add("adversarial_floor", detection(0.0, 1.0, adversarial_flag=True), None)
    add("replay_floor", detection(0.0, 1.0), None,
        {"reference_available": True, "match_score": 0.99,
         "voiced_seconds_used": 3.0, "replay_suspected": True})

    # 03 section 4: every trust-discount gate is independent.
    trusted = {"caller_attestation": "VERIFIED", "attestation_source": "STIR_SHAKEN_A",
               "transaction_value": 1000, "request_urgency": "normal"}
    good_match = {"reference_available": True, "match_score": 0.9, "voiced_seconds_used": 3.0}
    add("trust_discount_eligible", detection(0.5), trusted, good_match)
    add("trust_discount_weak_attestation", detection(0.5),
        {**trusted, "caller_attestation": "KNOWN_UNVERIFIED",
         "attestation_source": "CALLER_ID_ONLY"}, good_match)
    add("trust_discount_adversarial", detection(0.5, adversarial_flag=True), trusted, good_match)
    add("trust_discount_low_match", detection(0.5), trusted, {**good_match, "match_score": 0.84})
    add("trust_discount_short_audio", detection(0.5), trusted,
        {**good_match, "voiced_seconds_used": 1.0})
    add("trust_discount_large_txn", detection(0.5),
        {**trusted, "transaction_value": 50001}, good_match)

    # Invariant 7: match_score is only read behind reference_available.
    add("no_reference", detection(0.5), None,
        {"reference_available": False, "match_score": None})
    add("speaker_active", detection(0.5), None,
        {"reference_available": True, "match_score": 0.3, "voiced_seconds_used": 3.0})
    add("speaker_short_audio", detection(0.5), None,
        {"reference_available": True, "match_score": 0.3, "voiced_seconds_used": 1.0})

    # 03 section 2: context sub-signal renormalisation over each active subset.
    add("context_empty", detection(0.5), {})
    add("context_attestation_only", detection(0.5), {"caller_attestation": "UNKNOWN"})
    add("context_txn_only", detection(0.5), {"transaction_value": 500000})
    add("context_txn_new_beneficiary", detection(0.5),
        {"transaction_value": 500000, "beneficiary_is_new": True})
    add("context_history_saturates", detection(0.5), {"confirmed_fraud_flags_90d": 9})
    add("context_history_partial", detection(0.5), {"confirmed_fraud_flags_90d": 1})
    add("context_full", detection(0.72, 0.88),
        {"caller_attestation": "KNOWN_UNVERIFIED", "attestation_source": "CALLER_ID_ONLY",
         "transaction_value": 275000, "beneficiary_is_new": True,
         "request_urgency": "high", "urgency_source": "AGENT_ASSERTED",
         "confirmed_fraud_flags_90d": 2},
        {"reference_available": True, "match_score": 0.41, "voiced_seconds_used": 3.0})

    # 03 section 6: the degraded delta moves the band threshold, never the score.
    add("context_unavailable_near_high", detection(0.68, 0.95), None)
    add("context_unavailable_at_delta", detection(0.6421, 1.0), None)

    # Transaction log curve, sampled across the domain including the threshold boundary.
    for value in (0, 1, 5000, 50000, 50001, 100000, 250000, 550000, 1000000, 10000000):
        add("txn_%d" % value, detection(0.5), {"transaction_value": float(value)})

    # Dense sweep: catches rounding drift the named cases would miss.
    for p, conf in product((0.0, 0.13, 0.37, 0.5, 0.63, 0.81, 0.95, 1.0),
                           (0.35, 0.47, 0.62, 0.79, 0.95, 1.0)):
        add("sweep_p%s_c%s" % (p, conf), detection(p, conf), None)

    return cases


def session_cases():
    """04 sections 3-5: EWMA, decaying peak, N-of-M hysteresis, one alert per escalation."""
    def w(value, floors=None, context_degraded=False):
        return {"window_score": value, "applied_floors": floors or [],
                "context_degraded": context_degraded}

    return [
        {"name": "two_stage_escalation", "session_id": "session",
         "windows": [w(90), w(90), w(90), w(5)]},
        {"name": "alert_storm_bounded", "session_id": "storm", "windows": [w(95)] * 40},
        {"name": "peak_resists_dilution", "session_id": "dilution",
         "windows": [w(10)] * 30 + [w(90)] * 4},
        {"name": "immediate_on_adversarial_floor", "session_id": "immediate",
         "windows": [w(55, [{"reason": "adversarial_input", "value": 55}])] * 3},
        {"name": "asymmetric_deescalation", "session_id": "deesc",
         "windows": [w(90)] * 3 + [w(10)] * 12},
        {"name": "degraded_threshold_followthrough", "session_id": "degraded",
         "windows": [w(62, context_degraded=True)] * 4},
        {"name": "unassessed_windows_ignored", "session_id": "unassessed",
         "windows": [w(None), w(None), w(85), w(85), w(85)]},
        {"name": "slow_climb", "session_id": "climb",
         "windows": [w(v) for v in (10, 20, 30, 41, 45, 52, 60, 68, 72, 75, 80, 88)]},
    ]


def main():
    windows = []
    for case in window_cases():
        result = score_window(case["detection"], case["context"], case["verification"])
        windows.append({**case, "expected": result})

    sessions = []
    for case in session_cases():
        risk = SessionRisk(case["session_id"])
        steps = [{"window": w, "expected": risk.update(w)} for w in case["windows"]]
        sessions.append({"name": case["name"], "session_id": case["session_id"], "steps": steps})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "_generated_by": "backend/tools/emit_golden.py against app/risk_scoring.py",
        "_purpose": "Differential test asset: the Go port must reproduce these exactly.",
        "policy": POLICY,
        "windows": windows,
        "sessions": sessions,
    }, indent=1, sort_keys=True, allow_nan=False), encoding="utf-8")
    print("wrote %s :: %d window cases, %d session cases" % (OUT, len(windows), len(sessions)))


if __name__ == "__main__":
    main()
