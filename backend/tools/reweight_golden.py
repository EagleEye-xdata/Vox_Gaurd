"""Re-derive golden_windows.json expectations for policy pack 2.2.0.

WHY THIS EXISTS ALONGSIDE emit_golden.py

emit_golden.py regenerates from backend/app/risk_scoring.py, the pre-port Python
fusion. That reference is frozen at policy 2.1.0 and has no intent signal, so it
cannot express 2.2.0. The fusion moved to Go permanently on 2026-09-07; there is
no Python 2.2.0 to regenerate from, and the golden file was left recording a
policy the gateway no longer implements. Every one of the 91 window vectors and
the policy test failed as a result.

WHAT THIS IS NOT

It is not a transcription of what the Go code prints. That would make the fixture
assert "Go equals Go" and destroy the property emit_golden.py's warning exists to
protect. This module is an independent Python implementation of the 2.2.0 fusion,
written from the specification in gateway/internal/scoring/window.go, computing
expectations from the recorded *inputs* (detection / context / verification) at
full precision. Running the Go test afterwards compares two independently written
implementations of the same arithmetic, which is what a differential test is.

WHAT CHANGED BETWEEN PACKS

Only the top-level weight vector: 0.60/0.20/0.20 -> 0.55/0.15/0.15/0.15, with
intent added as a fourth signal. ContextWeights, bands, floors, EWMA and the
trust discount are all unchanged, so the terms feeding the fusion are unchanged
and only their renormalised weights move. Windows whose sole active signal is ai
renormalise to 1.0 under both packs and keep byte-identical scores; windows with
context active shift.

    python backend/tools/reweight_golden.py
    cd gateway && go test ./internal/scoring/
"""
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "gateway" / "internal" / "scoring" / "testdata" / "golden_windows.json"

VERSION_NEW = "demo-detector-first@2.2.0"
W = {"ai": 0.55, "speaker": 0.15, "context": 0.15, "intent": 0.15}
CW = {"attestation": 0.25, "urgency": 0.15, "history": 0.20, "transaction": 0.40}
CONTEXT_ORDER = ["attestation", "urgency", "history", "transaction"]
SIGNAL_ORDER = ["ai", "speaker", "context", "intent"]
FACTOR = {"ai": "ai_synthetic", "speaker": "speaker_mismatch",
          "context": "context_risk", "intent": "intent_risk"}
ROUTINE_TXN = 50000.0
MIN_CONFIDENCE = 0.35
BANDS = {"medium_min": 40.0, "high_min": 70.0}


DEGRADED_DELTA = 10.0


def band_for(score: float, context_degraded: bool) -> str:
    """Port of scoring.BandFor.

    high_min drops by degraded_threshold_delta when context is unavailable: a
    score assembled without context evidence gets less benefit of the doubt. A
    band recomputation that ignores that flag turns every degraded HIGH window
    into a MEDIUM one, which is the wrong direction for a fraud system to be
    wrong in.
    """
    high_min = BANDS["high_min"] - (DEGRADED_DELTA if context_degraded else 0.0)
    if score >= high_min:
        return "HIGH"
    if score >= BANDS["medium_min"]:
        return "MEDIUM"
    return "LOW"


def rnd(x: float, n: int) -> float:
    """Go's numeric.Round — half away from zero."""
    return float(Decimal(repr(x)).quantize(Decimal(1).scaleb(-n), rounding=ROUND_HALF_UP))


def context_term(c: dict | None):
    """Port of scoring.ContextTerm. Returns (value|None, details)."""
    if not c:
        return None, []
    values: dict[str, float] = {}

    att = c.get("caller_attestation")
    if att is not None:
        values["attestation"] = {"VERIFIED": 0.0, "KNOWN_UNVERIFIED": 0.5,
                                 "UNKNOWN": 1.0}.get(att, 0.0)

    urg = c.get("request_urgency")
    if urg is not None:
        v = {"low": 0.0, "normal": 0.3, "high": 1.0}.get(urg, 0.0)
        # Invariant 5: a caller-claimed urgency may raise risk, never lower it.
        if c.get("urgency_source") == "CALLER_CLAIMED":
            v = max(v, 0.3)
        values["urgency"] = v

    hist = c.get("confirmed_fraud_flags_90d")
    if hist is not None:
        values["history"] = min(hist / 3, 1.0)

    txn = c.get("transaction_value")
    if txn is not None:
        ratio = txn / ROUTINE_TXN
        v = min(math.log10(1 + ratio) / math.log10(11), 1.0)
        if c.get("beneficiary_is_new"):
            v = min(v + 0.2, 1.0)
        values["transaction"] = v

    if not values:
        return None, []

    denom = weighted = 0.0
    details = []
    for k in CONTEXT_ORDER:
        if k not in values:
            continue
        denom += CW[k]
        weighted += CW[k] * values[k]
        details.append({"factor": "context_" + k, "value": rnd(values[k], 4)})
    return weighted / denom, details


def score_window(w: dict) -> dict | None:
    """Recompute one window's expectations. None => leave the recorded value alone."""
    d, c, v = w.get("detection"), w.get("context"), w.get("verification")
    exp = w["expected"]
    terms: dict[str, float] = {}

    # --- ai ---
    if d and d.get("language_supported", True) and d.get("voiced_seconds", 0) >= 1.5 \
            and d.get("confidence", 0) >= MIN_CONFIDENCE:
        terms["ai"] = 0.5 + (d["p_synthetic"] - 0.5) * d["confidence"]

    # --- speaker ---
    if v and not v.get("failed") and v.get("reference_available") \
            and v.get("voiced_seconds_used", 0) >= 2 and v.get("match_score") is not None:
        terms["speaker"] = 1 - v["match_score"]

    # --- context + intent ---
    ctx_value, _ = context_term(c)
    if ctx_value is not None:
        terms["context"] = ctx_value
    if c and c.get("intent_risk") is not None:
        terms["intent"] = c["intent_risk"]

    # Windows the fixture records as UNKNOWN carry no factors; nothing to recompute.
    if not terms or not exp.get("contributing_factors"):
        return None

    # Sanity: our reconstruction must agree with the recorded active set. If it does
    # not, the input decoding is wrong and silently rewriting would be worse than
    # failing loudly.
    if sorted(terms) != sorted(exp.get("active_signals", [])):
        return None

    denom = sum(W[k] for k in terms)
    factors, base = [], 0.0
    for k in SIGNAL_ORDER:
        if k not in terms:
            continue
        eff = W[k] / denom
        pts = rnd(100 * eff * terms[k], 6)
        factors.append({"factor": FACTOR[k], "weight": rnd(eff, 6),
                        "value": rnd(terms[k], 6), "points": pts})
        base += pts
    base = rnd(base, 6)

    score = max(0.0, base - exp.get("trust_discount", 0))
    for floor in exp.get("applied_floors") or []:
        score = max(score, floor["value"])
    final = rnd(min(100.0, score), 2)
    return {"contributing_factors": factors, "base_score": base,
            "window_score": final,
            "band": band_for(final, bool(exp.get("context_degraded")))}


def main() -> None:
    d = json.loads(GOLDEN.read_text(encoding="utf-8"))
    d["policy"]["version"] = VERSION_NEW
    d["policy"]["weights"] = dict(W)

    rederived = moved = skipped = 0
    rebanded: list[str] = []
    for w in d["windows"]:
        exp = w["expected"]
        exp["policy_version"] = VERSION_NEW

        if "intent" not in exp.get("active_signals", []):
            sig = exp.setdefault("inactive_signals", [])
            if "intent" not in sig:
                sig.append("intent")
                sig.sort(key=SIGNAL_ORDER.index)
            exp.setdefault("inactive_reasons", {})["intent"] = "intent_scorer_unavailable"

        new = score_window(w)
        if new is None:
            skipped += 1
            continue
        if new["window_score"] != exp.get("window_score"):
            moved += 1
        if new["band"] != exp.get("band"):
            rebanded.append(f"{w['name']}: {exp.get('band')} -> {new['band']} "
                            f"({exp.get('window_score')} -> {new['window_score']})")
        exp.update(new)
        rederived += 1

    for s in d.get("sessions", []):
        if isinstance(s.get("expected"), dict):
            s["expected"]["policy_version"] = VERSION_NEW

    GOLDEN.write_text(json.dumps(d, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{VERSION_NEW}: re-derived {rederived} windows "
          f"({moved} scores moved on the weight change), {skipped} left as recorded")
    for line in rebanded:
        print(f"  band change: {line}")


if __name__ == "__main__":
    main()
