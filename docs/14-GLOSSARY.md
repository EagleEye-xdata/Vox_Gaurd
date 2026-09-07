# Glossary (NEW in v2)

> Fixes DR-024. v1 used "FAR", "FRR", and "false positive" across three different subsystems with
> three different meanings, interchangeably. That is not pedantry: it means a stated accuracy number
> cannot be interpreted, and two engineers can implement the same metric differently and both be
> "right".

## 1. Error rates — disambiguated

Always use the qualified name. Bare "FAR" is banned in this doc set.

| Term | Subsystem | Means | Cost of the error |
|---|---|---|---|
| **FAR-D** (detector false accept) | AI Detection | Synthetic speech classified as genuine | A deepfake gets through |
| **FRR-D** (detector false reject) | AI Detection | Genuine speech classified as synthetic | A real customer is challenged |
| **FAR-SV** | Speaker Verification | An impostor's voice matches the enrolled reference | Wrong person authenticated |
| **FRR-SV** | Speaker Verification | The genuine enrolled speaker fails to match | Real customer locked out; commonly caused by illness, ageing, or a new handset |
| **System FPR** | End-to-end | A session banded MEDIUM/HIGH where no fraud existed | Analyst time + customer friction — see `11-COST_MODEL.md` §4 |
| **System FNR** | End-to-end | A fraudulent session banded LOW | The loss event |
| **EER** | Either model | Operating point where FAR = FRR | A model comparison figure, **not** an operating target |

`FAR-D` and `System FPR` are unrelated quantities that v1 conflated. A detector with excellent
`FAR-D` can still produce an unusable `System FPR` if fusion, calibration, or hysteresis are wrong —
which is precisely what the v1 scoring bugs would have caused.

**When quoting any of these**, state: the sample size, the split design, the channel condition, and
the threshold. A rate without those four is not a measurement.

## 2. Scoring terms

| Term | Definition |
|---|---|
| **Frame** | 20 ms of audio |
| **Window** | 3.0 s of *voiced* audio; the unit of model inference |
| **Hop** | 1.0 s; the interval between windows, and the verdict cadence |
| **Voiced second** | A second of audio containing speech per VAD. The billing and cost unit. Silence, hold music, and ringback are not voiced seconds |
| **`window_score`** | 0–100 risk for one window |
| **`session_score`** | 0–100 aggregated across windows per `04 §3` |
| **Band** | `LOW` · `MEDIUM` · `HIGH` · `UNKNOWN` |
| **`UNKNOWN`** | Insufficient evidence to assess. **Never rendered as safe.** Displayed as "Not assessed" |
| **Active signal** | A signal that was computed and applies. Only active signals enter the weighted sum and the renormalisation denominator |
| **Inactive — not applicable** | The signal does not apply (e.g. caller has no enrolment). Attracts **no** floor |
| **Inactive — failed** | The signal should have applied but errored or timed out. Attracts the degraded floor of 40 |
| **Floor** | A minimum score enforced by `max()` after all other computation. Replaces v1's additive penalties, which could not enforce a minimum |
| **Renormalisation** | Dividing by the sum of *active* weights so the score spans the full 0–100 range regardless of which signals ran |
| **Hysteresis** | The N-of-M rule preventing band flapping and alert storms (`04 §4`) |
| **Degraded** | At least one signal failed. Surfaced in the API, the audit record, and every UI showing the score |
| **`contributing_factors`** | Per-signal points that must sum to the base score. Mandatory; testable |
| **Policy pack** | The versioned per-tenant config: weights, thresholds, floors, hysteresis constants |
| **`policy_version`** | Identifier recorded on every decision. Without it a decision cannot be reproduced |

## 3. Trust and identity

| Term | Definition |
|---|---|
| **Attestation** | Cryptographic or out-of-band evidence about the caller's origin. `VERIFIED` · `KNOWN_UNVERIFIED` · `UNKNOWN` |
| **STIR/SHAKEN attestation A** | Carrier-signed assertion that the originating number is legitimately the caller's. The only telephony-native source that can yield `VERIFIED` |
| **Adversary-controlled input** | Any field an attacker can set without defeating an independent check. Caller ID, claimed identity, claimed urgency. **May raise risk, never lower it** (`03 §4`) |
| **Enrolment** | Consented capture of a reference voice sample, stored as an embedding |
| **Embedding** | Fixed-length vector derived from voice. Not reversible to audio, but **linkable and re-identifying** — treat as a biometric identifier, never describe as anonymous |
| **Liveness / anti-replay** | Evidence that audio is being spoken now, not played back |
| **Diarisation** | Segmenting multi-speaker audio into per-speaker tracks |
| **Track** | One speaker's stream within a session. Scored independently; session band = max over tracks |

## 4. Security and audit

| Term | Definition |
|---|---|
| **Content hash** | `sha256(record)`. Detects accidental corruption. **Detects no tampering** — an attacker recomputes it. This is what v1 shipped while claiming tamper-evidence |
| **Hash chain** | `sha256(JCS(record) ‖ prev_hash)`. Altering record *k* invalidates every record after it |
| **Origin signature** | Signature applied by the Decision Service — the party that made the decision — not by the store that holds it |
| **JCS** | RFC 8785 JSON Canonicalisation Scheme. Two serialisers that disagree break a chain silently, so canonicalisation is itself unit-tested |
| **Checkpoint** | Periodic write-once snapshot of a chain head |
| **Anchor** | External attestation of a checkpoint — a timestamp authority or a ledger. Proves *integrity*, never *correctness* |
| **WAL** | Write-ahead log. Fsynced by the Decision Service before a verdict is returned, so evidence survives an audit-backend outage |
| **Score oracle** | An API that returns numeric scores to an untrusted caller, letting an attacker tune an evasive clone. Prevented by returning bands, not scores, externally |

## 5. Operations

| Term | Definition |
|---|---|
| **SLO** | Internal reliability objective (`10 §1`) |
| **SLA** | External commitment, incl. the alert-response times in `02 §10.1` |
| **Error budget** | Permitted SLO shortfall. Exhaustion halts feature work |
| **Verdict latency** | Window close → verdict available. **Not** call start → verdict |
| **Time to first verdict** | `min_voiced_seconds` + verdict latency ≈ 3.8 s of speech. The number to quote publicly — "sub-second deepfake detection" is false and will be caught |
| **Burn rate** | Rate of error-budget consumption; the basis for paging |
| **RPO / RTO** | Max tolerable data loss / max tolerable downtime (`10 §5`) |

## 6. Banned phrases

| Do not say | Say |
|---|---|
| "Fraud detected" | "Verification required" |
| "This voice is fake" | "Synthetic speech likely (p = 0.74)" |
| "99% accurate" | "EER 0.06 on a speaker- and generator-disjoint held-out set, n = 4,120, 8 kHz narrowband" |
| "Anonymous voice data" | "Pseudonymous biometric embedding" |
| "Blockchain-secured" | "Hash-chained and signed; externally anchored in consortium deployments" |
| "Real-time / instant detection" | "First verdict after ~4 s of speech, updated each second" |
| "Safe" (for an unassessed call) | "Not assessed" |
