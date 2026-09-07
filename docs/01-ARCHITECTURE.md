# System Architecture (v2)

> Fixes DR-002, DR-005, DR-009, DR-010, DR-011, DR-013, DR-016, DR-020, DR-023.

## 1. Design principles

1. **Modular by contract, monolithic by default.** Every boundary in `02-API_CONTRACTS.md` is a
   real seam, but Phase 0 runs them in as few processes as possible. Contracts make the split
   cheap later; premature splitting makes the demo late.
2. **Fail predictably, never fail silently.** Every dependency has a defined degraded behaviour
   and a *score floor*, never a silent pass-through.
3. **Absence of evidence is never evidence of safety.** A check that could not run produces
   `UNKNOWN`, not `LOW`.
4. **Nothing an adversary controls may lower a risk score.** Attacker-influenced inputs may only
   raise risk or be ignored. See `03-RISK_SCORING_SPEC.md` §4. *(DR-007)*
5. **Audit is an interface, not a dependency.** But the evidence must survive the outage —
   see §7.
6. **Least data, shortest retention** — and the architecture must make that claim *testable*,
   not merely stated.
7. **Config over code for verticals.**

## 2. Corrected end-to-end topology

v1 contained two contradictory topologies (§2 sequential vs §7 parallel) and put Kafka in the
sub-second path. Both are fixed. **Detection and Speaker Verification run in parallel** — they
consume the same feature frames and have no data dependency on each other.

```
 CALL SOURCES  (SIP/RTP trunk · WebRTC · SDK · uploaded file in Phase 0)
        │  audio frames
        ▼
┌──────────────────────────────────────────────────────────────────┐
│ EDGE / API GATEWAY [Go]                                          │
│  mTLS term · OIDC · tenant claim · rate limit · schema validate  │
└───────────────┬──────────────────────────────────────────────────┘
                │ gRPC bidi stream (audio in, verdicts out)
                ▼
┌──────────────────────────────────────────────────────────────────┐
│ SESSION ORCHESTRATOR [Go]        ← owns one session's lifecycle  │
│  · session_id issue · codec normalise · jitter buffer            │
│  · in-memory ring buffer ONLY (never to disk)                    │
│  · backpressure: sheds oldest windows, never blocks telephony    │
└───────────────┬──────────────────────────────────────────────────┘
                ▼
      PREPROCESS + VAD + DIARISATION  [Python]
      · resample 16 kHz mono · denoise · voice-activity detect
      · speaker diarisation → per-speaker streams   (DR-015)
      · emits 3.0 s voiced windows, 1.0 s hop       [ASSUMPTION-4]
                │
        ┌───────┴────────┐   ← PARALLEL, not sequential (DR-010)
        ▼                ▼
  AI DETECTION      SPEAKER VERIFICATION
   [Python]              [Python]
  p_synthetic         match_score vs enrolment
  + calibration       + reference_available
  + confidence        + liveness / anti-replay
  + adversarial_flag  + cross-session consistency
        └───────┬────────┘
                ▼
    ┌──────────────────────────────────────────┐
    │ RISK FUSION ENGINE [Go]                  │◄── CONTEXT SERVICE [Go]
    │  active-signal renormalisation            │    caller attestation,
    │  → window_score 0-100                     │    txn context, history
    └───────────────┬──────────────────────────┘
                    ▼
    ┌──────────────────────────────────────────┐
    │ SESSION AGGREGATOR [Go]                  │
    │  EWMA + N-of-M hysteresis + asymmetric    │
    │  de-escalation → session_score, band      │
    │  see 04-SESSION_SCORING_SPEC.md           │
    └───────────────┬──────────────────────────┘
                    ▼
    ┌──────────────────────────────────────────┐
    │ DECISION SERVICE [Go]   ← NEW in v2       │   (DR-013)
    │  band + tenant policy pack → Decision:     │
    │  ALLOW · WARN · STEP_UP · ESCALATE · BLOCK │
    │  owns circuit-breaker default policy       │
    │  writes durable WAL entry BEFORE acking    │
    └────┬──────────────────────┬───────────────┘
         ▼                      ▼
   ALERT SERVICE [Go]     AUDIT SERVICE [Go] ─► AuditBackend
   dashboard · webhook    hash-chained records    ├─ Postgres (P0–P2)
   step-up trigger        signed at origin        └─ Ledger (P3, optional)
```

**Async lane (Kafka, off the hot path):** decision events, alert delivery retries, analytics,
consented training-data capture, audit fan-out. Topics are `<domain>.<tenant_id>`, partitioned,
**keyed by `session_id`** — never a topic per session, which would create millions of topics and
kill the broker. *(DR-011)*

## 3. Why Kafka left the hot path *(DR-005, DR-009)*

v1 published raw audio to `audio.raw.<tenant>` and cleaned audio to `audio.clean.<tenant>`, then
claimed in `05` that raw audio is "never written to disk." **Kafka is a disk-backed log.** The two
statements could not both be true, and the claim was the one that mattered legally.

Two Kafka round-trips also added unbudgeted queue latency to an 800 ms path.

**v2:** audio never enters a broker. It exists in the Session Orchestrator's in-memory ring buffer
and in gRPC stream frames, and nowhere else. This makes the privacy claim *testable* — see
`08-SECURITY_TESTING_PLAN.md` §4.7.

## 4. Latency budget — corrected arithmetic *(DR-009)*

v1's budget summed the components to exactly 800 ms, leaving nothing for network, serialisation,
queueing, or ingestion, and never explained the 1.5 s ceiling.

**Scope:** measured from **voiced-window close → decision available**, not call start. A live call
produces a rolling verdict every hop (1.0 s), so "end-to-end call latency" is not the right unit.

| Stage | P95 budget | Notes |
|---|---|---|
| Ingest + jitter + ring-buffer write | 30 ms | |
| Preprocess + VAD + diarisation | 60 ms | |
| Feature extraction | 40 ms | shared by both branches |
| **max**(AI Detection 220, Speaker Verification 120) | **220 ms** | parallel — take the max, not the sum |
| Context fetch (cached, Redis) | 15 ms | cache miss → signal marked inactive, not blocking |
| Risk fusion | 15 ms | |
| Session aggregation + decision | 25 ms | |
| Network + gRPC serialisation, 5 hops × 12 ms | 60 ms | |
| Scheduler / queue wait allowance | 100 ms | |
| **Total P95** | **565 ms** | |
| **Target** | **≤800 ms** | 235 ms headroom — deliberate |
| **Hard ceiling** | **1200 ms** | breach → fail-safe below |

The ceiling is 1.5× the target, and every millisecond over it is accounted for by the headroom
plus one retry attempt. Ceiling breach never means "pass the call" — it means the affected signal
is marked **inactive** and the degraded floor applies.

**Non-live lanes** (post-call review, batch analytics, model backfill) run on separate worker
pools with their own resource quota so batch load can never contend with live-call SLOs.

## 5. Degraded-mode matrix — with score floors *(DR-002, DR-004, DR-013)*

v1 described behaviours but gave no numeric floors and no owner for the circuit breaker. Both now
specified. **Floors are applied after all other scoring, so nothing can defeat them.**

| Failure | Behaviour | Score floor | Owner |
|---|---|---|---|
| AI Detection times out or confidence < `min_confidence` | Signal marked **inactive**; remaining signals renormalise; `degraded_reasons += "detector_unavailable"` | **40** (MEDIUM min) | Fusion Engine |
| Speaker Verification unavailable **or** no enrolment | Signal inactive, weight renormalised across active signals (**not** zeroed — see DR-004) | 0 if `reference_available=false` by design; **40** if the service *failed* | Fusion Engine |
| Context Service unavailable | Signal inactive; tenant's `high_min` threshold lowered by `degraded_threshold_delta` (default −10) | — | Fusion Engine |
| Fusion Engine down | Decision Service circuit-breaks to the tenant's `default_degraded_decision` (default `WARN`) | n/a — decision bypasses scoring | **Decision Service** |
| Session Aggregator down | Fall back to last-known band; if none, `UNKNOWN` → `WARN` | — | Decision Service |
| Alert channel down | Durable queue + retry + DLQ; dashboard still live; alert marked `delivery_pending` | — | Alert Service |
| Audit backend down | Decision proceeds. **WAL entry is fsynced before the decision is acked**, then drained async | — | Decision Service |
| Insufficient voiced audio (< `min_voiced_seconds`) | Band = `UNKNOWN`, never `LOW` | policy-dependent | Session Aggregator |
| Unsupported language detected | Detector inactive; explicit `unsupported_language` reason | **40** | Fusion Engine |

**Rule:** `UNKNOWN` is a first-class band. It is never rendered to a user as "safe" and never maps
to `ALLOW` without an explicit tenant opt-in.

## 6. Adversarial robustness

- A lightweight **input-sanity check** runs *before* the main detector: statistical anomaly
  detection over the input signal (spectral flatness outliers, unnatural phase coherence, energy
  discontinuities at splice points, ultrasonic residue).
- Anomalous input sets `adversarial_flag = true`, which applies a **score floor of 55** — a floor,
  not a `+15` addend. v1's addend could not deliver the "at least MEDIUM" behaviour it promised.
  *(DR-002)*
- Replay defence lives in Speaker Verification: channel/device fingerprint consistency plus
  optional challenge-response for high-stakes step-up.
- Adaptive-attacker assumption: attackers have black-box query access. Rate limiting and probing
  detection at the gateway — `07-SECURITY_ARCHITECTURE.md` §3.
- Ongoing process: `05-ML_MODEL_LIFECYCLE.md` §5.

## 7. Audit durability *(DR-016, DR-020)*

v1 said audit failures "queue and retry," which loses exactly the evidence covering the incident
you will later be asked to explain.

**v2:** the Decision Service appends every decision to a local, fsynced **write-ahead log** before
returning a verdict. The WAL entry carries the full signed record. Async drainers move WAL entries
to the Audit Service. A decision is never returned unless its evidence is already durable.

Records are **hash-chained and signed at origin** (Decision Service key), not hashed by the Audit
Service — otherwise a compromised Audit Service could fabricate history. See `02` §9.

## 8. Speaker Verification enrolment

1. **Enrolment**: explicit opt-in consent, guided prompts, liveness-checked, via app or IVR. Never
   silently captured. Consent record is itself audited.
2. **Storage**: derived embedding only, encrypted with a tenant-scoped key. No raw audio.
3. **Re-enrolment**: triggered when rolling verification confidence trends down over N sessions
   (voices change with age, illness, device). Prompt, don't silently degrade.
4. **No enrolment available**: the signal is `reference_available = false` — explicitly *not
   applicable*, distinct from *failed*. The Fusion Engine renormalises. It is not a risk signal on
   its own; being un-enrolled is not suspicious. *(DR-004)*
5. **Multi-speaker sessions**: diarisation assigns each speaker a track; verification runs against
   the track claiming the identity. Non-claimant speakers are scored for synthesis only.
   *(DR-015)*

## 9. Multi-tenancy

Tenant identity is derived from **both** the workload's SPIFFE ID and the request JWT claim, and
the two must agree. Enforcement points: Postgres row-level security, Redis key prefix `t:{id}:`
on a dedicated instance, Kafka prefix ACLs, per-tenant KMS data keys. Design detail in
`07-SECURITY_ARCHITECTURE.md` §5.

## 10. Data flow and retention — enforceable version

```
Raw audio            in-process ring buffer only. No broker, no disk, no swap-backed page.
                     Zeroised on session close or after `raw_buffer_seconds` (default 8).
Feature frames       Redis, persistence DISABLED (appendonly no / save ""), TTL 120 s.
Embeddings (enrol)   Postgres, encrypted at rest, tenant key, deletable on request.
Decision records     Postgres, durable, hash-chained. No biometric content.
Consent records      Postgres, durable, immutable-append.
```

Every line above has a corresponding automated test in `08-SECURITY_TESTING_PLAN.md` §4.7. A
retention claim that is not tested is a marketing claim.

## 11. Cross-cutting

- **Observability**: OpenTelemetry traces spanning gateway → detector → decision, carrying
  `trace_id`, `session_id`, `tenant_id`. Metrics per §4 stage. See `10-SLO_OPS_RUNBOOK.md`.
- **Scalability**: detector and verifier are stateless and horizontally scalable. Session
  Orchestrator is sticky per session; use consistent hashing, not shared state.
- **HA**: ≥2 replicas of Fusion, Decision, and Detection from Phase 2. Phase 0 single-node is
  acceptable and should be stated as such.
- **Reproducibility**: every decision records `model_versions{detector, verifier, calibrator}`,
  `policy_version`, and `code_version`. Without policy version a decision cannot be reproduced.
  *(DR-014)*
