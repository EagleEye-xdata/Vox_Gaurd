"""Versioned Phase-0 implementation of v2 risk and session scoring."""
from dataclasses import dataclass, field
from hashlib import sha256
from math import log10

# NOT the 03 section 7 banking pack (ai .45 / speaker .30 / context .25, bands 35/65,
# mandatory_speaker_verification true). Named honestly: these weights are tuned for a
# detector-first Phase 0 with no speaker enrolment, where the banking pack's mandatory-verification
# floor of 50 would pin every window to Elevated. Deviation DEV-1 in docs/HANDOFF.md.
POLICY = {
    "version": "demo-detector-first@2.1.0",
    "weights": {"ai": 0.60, "speaker": 0.20, "context": 0.20},
    "context_weights": {"attestation": 0.25, "urgency": 0.15, "history": 0.20, "transaction": 0.40},
    "bands": {"medium_min": 40, "high_min": 70},
    "routine_transaction_threshold": 50_000,
    "mandatory_speaker_verification": False,
    "min_confidence": 0.35,
    "min_voiced_seconds": 3.0,
    "default_degraded_decision": "WARN",
    # 03 section 6: with context missing we cannot afford the usual HIGH threshold, so lower it.
    "degraded_threshold_delta": 10,
    # 05 section 4: the languages this build claims. Anything else gates the AI signal off.
    "supported_languages": ["en", "hi", "ta", "te", "bn", "hi-en"],
    "ewma_alpha": 0.35,
    "peak_decay": 0.98,
    "peak_discount": 8,
    "trust_discount": 10,
}
BAND_RANK = {"UNKNOWN": -1, "LOW": 0, "MEDIUM": 1, "HIGH": 2}


def band_for(score, policy=POLICY, context_degraded=False):
    if score is None:
        return "UNKNOWN"
    # 03 section 6: under Context-Service failure high_min drops by degraded_threshold_delta,
    # because a score built without context evidence deserves less benefit of the doubt.
    high_min = policy["bands"]["high_min"] - (policy["degraded_threshold_delta"] if context_degraded else 0)
    if score >= high_min:
        return "HIGH"
    if score >= policy["bands"]["medium_min"]:
        return "MEDIUM"
    return "LOW"


def context_term(context, policy=POLICY):
    values = {}
    attestation = context.get("caller_attestation")
    if attestation is not None:
        values["attestation"] = {"VERIFIED": 0.0, "KNOWN_UNVERIFIED": 0.5, "UNKNOWN": 1.0}[attestation]
    urgency = context.get("request_urgency")
    if urgency is not None:
        value = {"low": 0.0, "normal": 0.3, "high": 1.0}[urgency]
        if context.get("urgency_source") == "CALLER_CLAIMED":
            value = max(value, 0.3)
        values["urgency"] = value
    flags = context.get("confirmed_fraud_flags_90d")
    if flags is not None:
        values["history"] = min(flags / 3, 1.0)
    transaction = context.get("transaction_value")
    if transaction is not None:
        ratio = transaction / policy["routine_transaction_threshold"]
        value = min(log10(1 + ratio) / log10(11), 1.0)
        if context.get("beneficiary_is_new"):
            value = min(value + 0.2, 1.0)
        values["transaction"] = value
    if not values:
        return None, []
    weights = policy["context_weights"]
    denominator = sum(weights[key] for key in values)
    term = sum(weights[key] * value for key, value in values.items()) / denominator
    details = [{"factor": f"context_{key}", "value": round(value, 4)} for key, value in values.items()]
    return term, details


def trust_discount_eligible(context, verification, detection, degraded, policy=POLICY):
    if not context or not verification or not detection or degraded:
        return False
    return (
        context.get("caller_attestation") == "VERIFIED"
        and context.get("attestation_source") in {"STIR_SHAKEN_A", "AUTHENTICATED_APP_SESSION"}
        and verification.get("reference_available") is True
        and verification.get("voiced_seconds_used", 0) >= 2
        and verification.get("match_score", 0) >= 0.85
        and context.get("transaction_value") is not None
        and context["transaction_value"] <= policy["routine_transaction_threshold"]
        and not detection.get("adversarial_flag", False)
        and not verification.get("replay_suspected", False)
    )


def score_window(detection, context, verification=None, policy=POLICY):
    active, terms, inactive = {}, {}, {}
    floors, degraded_reasons = [], []
    context_details = []

    if detection is None:
        inactive["ai"] = "detector_unavailable"
        degraded_reasons.append("detector_unavailable")
        floors.append({"reason": "detector_unavailable", "value": 40})
    elif not detection.get("language_supported", True):
        inactive["ai"] = "unsupported_language"
        degraded_reasons.append("unsupported_language")
        floors.append({"reason": "unsupported_language", "value": 40})
    elif detection.get("voiced_seconds", 0) < 1.5:
        inactive["ai"] = "insufficient_voiced_audio"
    elif detection.get("confidence", 0) < policy["min_confidence"]:
        inactive["ai"] = "low_confidence"
    else:
        terms["ai"] = 0.5 + (detection["p_synthetic"] - 0.5) * detection["confidence"]
        active["ai"] = policy["weights"]["ai"]

    if verification is None:
        inactive["speaker"] = "not_enrolled"
    elif verification.get("failed"):
        inactive["speaker"] = "verifier_failed"
        degraded_reasons.append("verifier_failed")
        floors.append({"reason": "verifier_failed", "value": 40})
    elif verification.get("reference_available"):
        if verification.get("match_score") is None:
            raise ValueError("match_score is required when reference_available is true")
        if verification.get("voiced_seconds_used", 0) >= 2:
            terms["speaker"] = 1 - verification["match_score"]
            active["speaker"] = policy["weights"]["speaker"]
        else:
            inactive["speaker"] = "insufficient_voiced_audio"
    else:
        if verification.get("match_score") is not None:
            raise ValueError("match_score must be absent when reference_available is false")
        inactive["speaker"] = "not_enrolled"
        if policy["mandatory_speaker_verification"]:
            floors.append({"reason": "speaker_verification_required", "value": 50})

    if context is None:
        inactive["context"] = "context_unavailable"
        degraded_reasons.append("context_unavailable")
        floors.append({"reason": "context_unavailable", "value": 40})
    else:
        value, context_details = context_term(context, policy)
        if value is None:
            inactive["context"] = "no_context_signals"
        else:
            terms["context"] = value
            active["context"] = policy["weights"]["context"]

    if detection and detection.get("adversarial_flag"):
        floors.append({"reason": "adversarial_input", "value": 55})
    if verification and verification.get("replay_suspected"):
        floors.append({"reason": "replay_suspected", "value": 60})

    voiced_seconds = detection.get("voiced_seconds", 0) if detection else None
    insufficient_audio = detection is not None and voiced_seconds < policy["min_voiced_seconds"]
    if insufficient_audio or not active:
        reasons = degraded_reasons or ["insufficient_voiced_audio" if insufficient_audio else "no_active_signals"]
        return _result(None, None, 0, active, inactive, [] if insufficient_audio else floors, [], context_details, bool(degraded_reasons), reasons, policy)

    denominator = sum(active.values())
    factors = []
    for key, weight in active.items():
        effective = weight / denominator
        factors.append({
            "factor": {"ai": "ai_synthetic", "speaker": "speaker_mismatch", "context": "context_risk"}[key],
            "weight": round(effective, 6),
            "value": round(terms[key], 6),
            "points": round(100 * effective * terms[key], 6),
        })
    base = round(sum(factor["points"] for factor in factors), 6)
    discount = policy["trust_discount"] if trust_discount_eligible(context, verification, detection, bool(degraded_reasons), policy) else 0
    discounted_base = max(0, base - discount)
    score = round(min(100, max([discounted_base] + [floor["value"] for floor in floors])), 2)
    assert round(sum(factor["points"] for factor in factors), 6) == base
    return _result(score, base, discount, active, inactive, floors, factors, context_details, bool(degraded_reasons), degraded_reasons, policy)


def _result(score, base, trust_discount, active, inactive, floors, factors, context_details, degraded, reasons, policy):
    context_degraded = "context_unavailable" in reasons
    return {
        "window_score": score,
        "base_score": base,
        "trust_discount": trust_discount,
        "band": band_for(score, policy, context_degraded),
        "context_degraded": context_degraded,
        "active_signals": list(active),
        "inactive_signals": list(inactive),
        "inactive_reasons": inactive,
        "applied_floors": sorted(floors, key=lambda item: item["value"]),
        "contributing_factors": factors,
        "context_details": context_details,
        "degraded": degraded,
        "degraded_reasons": reasons,
        "policy_version": policy["version"],
    }


@dataclass
class SessionRisk:
    session_id: str = "test-session"
    ewma: float | None = None
    peak: float = 0
    score: float | None = None
    band: str = "UNKNOWN"
    history: list = field(default_factory=list)
    band_timeline: list = field(default_factory=lambda: [{"window": 0, "band": "UNKNOWN"}])
    escalation_seq: int = 0

    def update(self, window, policy=POLICY):
        value = window["window_score"]
        if value is None:
            return self.snapshot(False)
        # The window's threshold delta (03 section 6) must follow through to the session band,
        # otherwise the window says HIGH and the session quietly disagrees.
        context_degraded = window.get("context_degraded", False)
        band_of = lambda v: band_for(v, policy, context_degraded)
        alpha = policy["ewma_alpha"]
        self.ewma = value if self.ewma is None else alpha * value + (1 - alpha) * self.ewma
        self.peak = max(self.peak * policy["peak_decay"], value)
        self.score = round(max(self.ewma, self.peak - policy["peak_discount"]), 2)
        self.history.append(self.score)
        candidate, previous = band_of(self.score), self.band
        immediate = any(f["reason"] in {"adversarial_input", "replay_suspected"} for f in window["applied_floors"])
        if self.band == "UNKNOWN":
            if candidate == "LOW":
                self.band = "LOW"
            elif immediate or sum(BAND_RANK[band_of(v)] >= BAND_RANK["MEDIUM"] for v in self.history[-3:]) >= 2:
                self.band = "MEDIUM"
        elif BAND_RANK[candidate] > BAND_RANK[self.band]:
            next_band = "MEDIUM" if self.band == "LOW" else "HIGH"
            qualifying = sum(BAND_RANK[band_of(v)] >= BAND_RANK[next_band] for v in self.history[-3:])
            if immediate or qualifying >= 2:
                self.band = next_band
        elif BAND_RANK[candidate] < BAND_RANK[self.band] and len(self.history) >= 6:
            lower = "LOW" if self.band == "MEDIUM" else "MEDIUM"
            ceiling = policy["bands"]["medium_min"] if lower == "LOW" else (
                policy["bands"]["high_min"] - (policy["degraded_threshold_delta"] if context_degraded else 0))
            if sum(v < ceiling - 5 for v in self.history[-6:]) >= 5:
                self.band = lower
        changed = self.band != previous
        if changed:
            self.band_timeline.append({"window": len(self.history), "band": self.band})
        alert_key = None
        if changed and self.band in {"MEDIUM", "HIGH"}:
            self.escalation_seq += 1
            alert_key = sha256(f"{self.session_id}:main:{self.band}:{self.escalation_seq}".encode()).hexdigest()
        return self.snapshot(changed, alert_key)

    def snapshot(self, changed, alert_key=None):
        return {
            "session_score": self.score,
            "authenticity_score": None if self.score is None else round(100 - self.score, 2),
            "band": self.band,
            "band_changed": changed,
            "history": self.history.copy(),
            "peak_score": round(self.peak, 2),
            "ewma_score": None if self.ewma is None else round(self.ewma, 2),
            "band_timeline": self.band_timeline.copy(),
            "escalation_seq": self.escalation_seq,
            "alert_key": alert_key,
        }


def fuse(spectral, prosody, speaker=None, context=None):
    detection = {"p_synthetic": 0.55 * spectral + 0.45 * prosody, "confidence": 1.0, "language_supported": True, "voiced_seconds": 3.0}
    verification = None if speaker is None else {"reference_available": True, "match_score": speaker, "voiced_seconds_used": 3.0}
    return score_window(detection, context, verification)["window_score"]


RollingRisk = SessionRisk
