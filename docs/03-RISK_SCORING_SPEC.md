# Risk Scoring Specification (v2)

> Fixes DR-001, DR-002, DR-003, DR-004, DR-007, DR-012, DR-018, DR-022, DR-025, DR-026.
> This is the core IP. Treat it as a versioned, unit-tested component with its own release notes.

## 0. What was broken in v1, in one place

| v1 statement | Problem |
|---|---|
| `raw_score = W·signals + 15 − 10`, then `× 100` | Mixes a 0–1 weighted sum with 0–100 flat terms. `+15` became `+1500`. |
| "Adversarial penalty forces at least Medium" | `0 + 15 = 15`, which is LOW. An addend cannot enforce a floor. |
| "Zero the speaker weight and redistribute" | Redistribution rule never defined. Without it, max score with no enrolment = 70 — so telecom could never reach HIGH. |
| Phase 0 = "AI only, other weights 0" | Max score = 45. **The demo could never fire a HIGH alert.** |
| `TRUST_DISCOUNT` on `caller_known` | Derived from caller ID. An attacker spoofs a number and *lowers* their own fraud score. |
| `transaction_value` in `ContextInput` | Collected and then never used anywhere in the formula. |
| `confidence` in `DetectionResult` | Emitted and then never used anywhere in the formula. |

## 1. Evaluation order (normative)

The order matters and v1 never stated one. Implementations **must** follow exactly this sequence:

```
1. Determine the ACTIVE signal set
2. Compute each active signal's term in [0,1]
3. Weighted sum, renormalised over ACTIVE weights only  → base ∈ [0,100]
4. Apply trust discount        (may only ever DECREASE; strictly gated)
5. Apply floors                (max() — floors are applied LAST so nothing defeats them)
6. Clamp to [0,100], round
7. Map to band via tenant thresholds
```

Floors after discount is the whole point: in v1 a discount could cancel a safety penalty.

## 2. Active-signal renormalisation *(DR-003, DR-004)*

```
base = 100 × ( Σ_{i ∈ ACTIVE} w_i · term_i ) / ( Σ_{i ∈ ACTIVE} w_i )
```

A signal is **inactive** when it could not be computed or does not apply. Inactive signals are
removed from *both* numerator and denominator. This is the single fix that makes HIGH reachable in
Phase 0 and in telecom.

| Signal | Active when |
|---|---|
| `ai` | detector responded ∧ `confidence ≥ min_confidence` (0.35) ∧ `language_supported` ∧ `voiced_seconds ≥ 1.5` |
| `speaker` | `reference_available = true` ∧ `voiced_seconds_used ≥ 2.0` |
| `context` | context snapshot retrieved (even partially — sub-components renormalise, see §3.3) |

If **no** signal is active → band = `UNKNOWN`, no numeric score, Decision Service applies the
tenant's degraded default. `UNKNOWN` never renders as safe.

> **Not-applicable ≠ failed.** `reference_available = false` (an unenrolled caller) is normal and
> attracts no floor. A *timeout* on Speaker Verification is a failure and does attract the
> degraded floor of 40. v1 conflated the two.

## 3. Signal terms

### 3.1 AI term — now uses `confidence` *(DR-018)*
```
ai_term = 0.5 + ( p_synthetic_calibrated − 0.5 ) × confidence
```
Uncertainty shrinks the claim toward neutral. Note the deliberate asymmetry: low confidence pulls
a "definitely genuine" reading *up* toward 0.5, and a "definitely synthetic" reading *down*. That
is the conservative direction for a fraud system — an uncertain model is not permitted to vouch
for anyone. Below `min_confidence` the signal is gated off entirely rather than shrunk to mush.

`p_synthetic_calibrated` must come from a fitted calibrator (temperature scaling or isotonic), not
a raw softmax. Multiplying an uncalibrated network output by a weight produces a number with no
probabilistic meaning. See `05-ML_MODEL_LIFECYCLE.md` §3.

### 3.2 Speaker term
```
speaker_term = 1 − match_score          # only when reference_available = true
```
`replay_suspected = true` does not enter this term; it applies a floor (§5).

### 3.3 Context term — with transaction risk *(DR-026)*
```
context_term = Σ_{j ∈ ACTIVE_SUB} c_j · sub_j  /  Σ_{j ∈ ACTIVE_SUB} c_j
```
Same renormalisation rule, applied recursively.

| Sub-component `j` | Default `c_j` | Value |
|---|---|---|
| `attestation_risk` | 0.30 | `VERIFIED` → 0 · `KNOWN_UNVERIFIED` → 0.5 · `UNKNOWN` → 1.0 |
| `urgency_risk` | 0.20 | low → 0 · normal → 0.3 · high → 1.0 |
| `history_risk` | 0.20 | `min(confirmed_fraud_flags_90d / K, 1)`, K = 3 |
| `transaction_risk` | 0.30 | see below; **inactive** when `transaction_value` is null (telecom) |

```
v          = transaction_value / routine_transaction_threshold
txn_base   = min( log10(1 + v) / log10(11), 1 )      # v=1 → 0.29 ;  v=10 → 1.00
transaction_risk = min( txn_base + (0.2 if beneficiary_is_new else 0), 1 )
```

`history_risk` semantics, previously undefined *(DR-022)*: **distinct confirmed-fraud incidents
linked to this caller identity in the trailing 90 days.** Not suspicions, not alerts, not
unresolved flags — confirmed. K is tenant-configurable.

## 4. Adversary-controlled inputs may never lower risk *(DR-007)*

**Normative rule.** An input is adversary-controlled if an attacker can set it without defeating an
independent cryptographic or out-of-band check. Such inputs may raise risk or be ignored. They may
never reduce it.

| Input | v1 treatment | v2 treatment |
|---|---|---|
| Caller ID number | `caller_known → −10` | Yields at most `KNOWN_UNVERIFIED` (0.5 risk). Cannot produce `VERIFIED`. |
| Claimed urgency | lowered risk when "low" | If `urgency_source = CALLER_CLAIMED`, then `urgency_risk = max(computed, 0.3)`. A caller saying "no rush" buys nothing. |
| Claimed identity | implicit | Only a signal to verify against, never evidence of itself. |
| Transaction type string | opaque | Schema-validated enum; unknown values map to the *highest* risk in their family, not the lowest. |

### 4.1 Trust discount — narrowly gated
```
TRUST_DISCOUNT = 10 points, applied only if ALL of:
  caller_attestation == VERIFIED
    (attestation_source ∈ {STIR_SHAKEN_A, AUTHENTICATED_APP_SESSION})
  AND speaker signal ACTIVE AND match_score ≥ 0.85
  AND transaction_value ≤ routine_transaction_threshold
  AND adversarial_flag == false AND replay_suspected == false
  AND degraded == false
```
Every clause is independently verifiable and none is set by the caller's voice or claims.

## 5. Floors — replacing v1's addends *(DR-002)*

```
score = max(score, floor)   for each triggered floor
```

| Condition | Floor | Effect |
|---|---|---|
| `adversarial_flag` | **55** | guarantees ≥ MEDIUM under default bands |
| `replay_suspected` | **60** | guarantees ≥ MEDIUM, near HIGH |
| Any signal *failed* (timeout/error), i.e. `degraded = true` | **40** | guarantees ≥ MEDIUM |
| `mandatory_speaker_verification = true` but `reference_available = false` | **50** | policy violation is itself a risk |
| `language_supported = false` | **40** | model out of scope, cannot vouch |
| Voiced audio < `min_voiced_seconds` (default 3.0) | band = `UNKNOWN` | no numeric floor; never `LOW` |

Floors are recorded in `applied_floors[]` and shown in the explainability panel. A user seeing a
MEDIUM must be able to see it came from a floor, not from evidence.

## 6. Bands

| Band | Default range | Default decision |
|---|---|---|
| `LOW` | 0–39 | ALLOW |
| `MEDIUM` | 40–69 | WARN, optional STEP_UP |
| `HIGH` | 70–100 | STEP_UP or ESCALATE; BLOCK only per explicit tenant policy |
| `UNKNOWN` | n/a | tenant `default_degraded_decision`, default WARN |

Thresholds are per-tenant. Under Context-Service failure, `high_min` drops by
`degraded_threshold_delta` (default 10) — conservative when context is missing.

**Blocking is never the automatic default.** A false block on a legitimate customer is a harm with
legal and reputational weight; see `06-DATA_PRIVACY_COMPLIANCE.md` §7 and the appeal path.

## 7. Vertical policy packs

```json
{
  "policy_version": "banking@1.4.0",
  "tenant_type": "banking",
  "weights": { "ai": 0.45, "speaker": 0.30, "context": 0.25 },
  "context_weights": { "attestation": 0.25, "urgency": 0.15, "history": 0.20, "transaction": 0.40 },
  "bands": { "medium_min": 35, "high_min": 65 },
  "routine_transaction_threshold": 50000,
  "mandatory_speaker_verification": true,
  "min_confidence": 0.35,
  "min_voiced_seconds": 3.0,
  "default_degraded_decision": "WARN",
  "degraded_threshold_delta": 10,
  "high_band_decision": "STEP_UP"
}
```

| Vertical | ai | speaker | context | Rationale |
|---|---|---|---|---|
| Banking | 0.45 | 0.30 | 0.25 | Transaction context carries real signal; enrolment is realistic |
| Telecom | 0.55 | 0.15 | 0.30 | Usually no enrolment; the detector carries the load |
| Enterprise collab | 0.50 | 0.30 | 0.20 | Enrolment easy via SSO; tune hard against false positives in meetings |
| General anti-phishing | 0.60 | 0.10 | 0.30 | Minimal context, minimal enrolment |

`policy_version` is recorded on **every decision and audit record**. Without it a decision cannot
be reproduced or defended. *(DR-014)*

## 8. Reference implementation (normative pseudocode)

```python
def score_window(det, ver, ctx, policy) -> WindowScore:
    active, terms, floors, reasons = {}, {}, [], []

    # --- AI ---
    if det and det.language_supported and det.confidence >= policy.min_confidence \
       and det.voiced_seconds >= 1.5:
        terms["ai"] = 0.5 + (det.p_synthetic - 0.5) * det.confidence
        active["ai"] = policy.weights["ai"]
    else:
        reasons.append("detector_inactive")
        if det is None or det.timed_out:      floors.append(40)
        if det and not det.language_supported: floors.append(40)

    # --- Speaker ---
    if ver and ver.reference_available and ver.voiced_seconds_used >= 2.0:
        terms["speaker"] = 1.0 - ver.match_score     # match_score is None unless gate passed
        active["speaker"] = policy.weights["speaker"]
    else:
        if ver is None or ver.failed:
            reasons.append("verifier_failed"); floors.append(40)
        else:
            reasons.append("no_reference")           # NOT a failure, no floor
        if policy.mandatory_speaker_verification:
            floors.append(50)

    # --- Context (recursive renormalisation) ---
    if ctx:
        terms["context"] = context_term(ctx, policy)
        active["context"] = policy.weights["context"]
    else:
        reasons.append("context_unavailable")

    if not active:
        return WindowScore(band="UNKNOWN", degraded=True, degraded_reasons=reasons)

    base = 100.0 * sum(active[k] * terms[k] for k in active) / sum(active.values())

    # --- Trust discount (gated; may only decrease) ---
    if trust_discount_eligible(ctx, ver, det, policy, degraded=bool(reasons)):
        base -= policy.trust_discount            # default 10

    # --- Floors LAST ---
    if det and det.adversarial_flag: floors.append(55)
    if ver and ver.replay_suspected: floors.append(60)
    score = max([base] + floors)

    return WindowScore(score=int(round(clamp(score, 0, 100))),
                       active_signals=list(active), applied_floors=sorted(set(floors)),
                       degraded=bool(reasons), degraded_reasons=reasons,
                       contributing_factors=explain(active, terms, base),
                       policy_version=policy.version)
```

## 9. Worked examples

**E1 — Phase 0, detector only.** Proves HIGH is now reachable *(DR-003)*.
`p_cal = 0.92, confidence = 0.90`; active = {ai}.
`ai_term = 0.5 + 0.42·0.90 = 0.878` → `base = 100 · (0.45·0.878)/0.45 = 87.8` → **88 = HIGH.**
Under v1's rule this same input scored 41 and displayed as LOW.

**E2 — Telecom, no enrolment.**
`p_cal = 0.80, conf = 0.80` → `ai_term = 0.74`. Context: unknown caller 1.0, urgency normal 0.3,
history 0, transaction inactive → renormalised over {0.25,0.15,0.20} → `context_term = 0.593`.
Active = {ai 0.55, context 0.30}.
`base = 100 · (0.55·0.74 + 0.30·0.593)/0.85 = 100 · (0.407 + 0.178)/0.85 = 68.8` → **69 = MEDIUM.**
Raise `p_cal` to 0.95 and this reaches 78 → HIGH. Under v1 it was capped at 70 and effectively
never escalated.

**E3 — Spoofed caller ID, the v1 loophole.**
Attacker spoofs a known number, claims low urgency, ₹8,00,000 transfer to a new beneficiary.
v1: `caller_known = true` → `−10`. v2: `attestation_source = CALLER_ID_ONLY` →
`KNOWN_UNVERIFIED = 0.5`; `urgency_source = CALLER_CLAIMED` → floored at 0.3;
`transaction_risk = min(log10(1+16)/log10(11), 1) + 0.2 = 1.0`. Trust discount **not eligible**.
Context term ≈ 0.72. The attack now *raises* the score instead of lowering it.

**E4 — Adversarially perturbed audio.**
`p_cal = 0.12` (attack succeeded in fooling the classifier), `adversarial_flag = true`.
`base ≈ 21`, floor 55 → **55 = MEDIUM.** v1's `+15` addend gave 36 = LOW: the attack worked.

## 10. Validation gates before any pilot

- Confusion matrix on a held-out labelled set, **per policy pack**.
- FAR / FRR targets agreed and documented *before* go-live. Use the disambiguated names in
  `14-GLOSSARY.md` — "false accept" means three different things in this system. *(DR-024)*
- **Calibration check**: reliability diagram + Expected Calibration Error ≤ 0.05. An uncalibrated
  detector invalidates every number in this document.
- **Sum check**: `contributing_factors` points sum to `base` for 100% of a test batch.
- **Monotonicity check**: increasing `p_synthetic`, `transaction_value`, or
  `confirmed_fraud_flags_90d` must never decrease the score. Property-based test.
- **Adversary-monotonicity check**: for every adversary-controlled field, no value of that field
  reduces the score relative to its worst-case value. This is the §4 rule as an executable test.
