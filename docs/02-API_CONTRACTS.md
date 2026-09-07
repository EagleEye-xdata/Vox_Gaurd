# API & Service Contracts (v2)

> Fixes DR-011, DR-012, DR-013, DR-014, DR-016, DR-017, DR-019, DR-021, DR-024, DR-041, DR-043.

## 1. Conventions

- **External** (client → gateway): REST + JSON, or WebSocket for live audio.
- **Internal hot path**: gRPC, **bidirectional streaming** for anything carrying audio. v1
  defined unary RPCs over a streaming architecture — a structural mismatch. *(DR-019)*
- **Internal cold path**: REST/JSON (enrolment, config, audit reads).
- Every call carries `trace_id`, `tenant_id`, `session_id` where applicable.
- Every mutating REST endpoint accepts `Idempotency-Key`; replays return the original response.
  *(DR-021)*
- Timestamps: RFC 3339 UTC with milliseconds.
- Versioning: URL-versioned (`/v1/`). Breaking changes bump the version; additive fields do not.
- Vertical-specific context is an opaque, schema-validated JSON object so verticals stay pluggable.

### 1.1 Standard error envelope
```json
{ "error": { "code": "DETECTOR_TIMEOUT", "message": "...", "retryable": true,
             "trace_id": "...", "degraded": true } }
```
Codes are a closed enum. Clients branch on `code`, never on `message`.

## 2. Session Orchestrator

```
POST   /v1/sessions                  → { session_id, expires_at }
WS     /v1/sessions/{id}/stream      ← audio frames in, verdict events out
POST   /v1/sessions/{id}/close       → finalise, returns SessionSummary
GET    /v1/sessions/{id}             → current state (idempotent, safe to poll)
```

**Verdict event** (server → client, emitted per hop and on band change):
```json
{ "type": "verdict", "session_id": "...", "seq": 14,
  "window_score": 62, "session_score": 58, "band": "MEDIUM",
  "band_changed": false, "degraded": false, "degraded_reasons": [],
  "contributing_factors": [ ... ], "as_of": "..." }
```

## 3. AI Detection Service

```
gRPC DetectStream(stream FeatureWindow) returns (stream DetectionResult)
gRPC DetectOnce(FeatureWindow) returns (DetectionResult)   // standalone-demo contract
```

```protobuf
message DetectionResult {
  string session_id      = 1;
  string speaker_track   = 2;   // from diarisation; "main" if single-speaker  (DR-015)
  uint32 window_seq      = 3;
  float  p_synthetic_raw = 4;   // uncalibrated model output, 0-1, for debugging only
  float  p_synthetic     = 5;   // CALIBRATED probability, 0-1 — this is what fusion uses (DR-018)
  float  confidence      = 6;   // 0-1; see §3.1 — NOT a duplicate of p_synthetic
  bool   adversarial_flag= 7;
  string language        = 8;   // detected; "und" if undetermined
  bool   language_supported = 9;                                          // (DR-023)
  float  voiced_seconds  = 10;  // voiced audio in this window
  string model_version   = 11;
  string calibrator_version = 12;
  uint32 latency_ms      = 13;
}
```

**`DetectOnce` must run with zero dependency on any other service.** That is the standalone-demo
contract and the first thing to build.

### 3.1 What `confidence` means and how it is used *(DR-018)*
v1 emitted `confidence`, described it in one place as a confidence *interval*, and then never used
it in the risk formula. v2 defines it precisely:

> `confidence` = 1 − normalised predictive uncertainty, estimated by ensemble/MC-dropout variance
> across K forward passes (K=5 default), rescaled to [0,1].

It is used in exactly two ways, both specified in `03-RISK_SCORING_SPEC.md` §3.1:
1. **Gating** — if `confidence < min_confidence` (default 0.35) the AI signal is marked *inactive*.
2. **Shrinkage** — the calibrated probability is pulled toward the neutral prior in proportion to
   uncertainty.

## 4. Speaker Verification Service

```
POST   /v1/enrolments                → create (explicit consent token required)
DELETE /v1/enrolments/{identity_id}  → revoke; hard-deletes embedding within SLA (24 h)
gRPC   VerifyStream(stream FeatureWindow) returns (stream VerificationResult)
```

```protobuf
message VerificationResult {
  string session_id           = 1;
  string speaker_track        = 2;
  bool   reference_available  = 3;   // THE gate. Check this first, always.
  optional float match_score  = 4;   // 0-1, ABSENT when reference_available=false  (DR-012)
  optional float cross_session_consistency = 5;
  bool   replay_suspected     = 6;
  float  voiced_seconds_used  = 7;
  string model_version        = 8;
}
```

v1 declared `match_score: float [0-1]` and then reserved `-1` for "no reference" — a sentinel
outside the declared range that makes `1 - match_score` evaluate to `2.0` in any naive
implementation. v2 makes the field **optional/nullable** and `reference_available` the sole gate.
Consumers that read `match_score` without checking the gate must fail loudly, not silently.

## 5. Context Service

```
POST /v1/context                     → attach context to a session
GET  /v1/context/{session_id}        → current context snapshot
```

```json
{
  "session_id": "...",
  "caller_attestation": "VERIFIED | KNOWN_UNVERIFIED | UNKNOWN",
  "attestation_source": "STIR_SHAKEN_A | AUTHENTICATED_APP_SESSION | CALLER_ID_ONLY | NONE",
  "transaction_value": 250000,
  "transaction_currency": "INR",
  "transaction_type": "wire_transfer",
  "beneficiary_is_new": true,
  "request_urgency": "low | normal | high",
  "urgency_source": "AGENT_ASSERTED | POLICY_DERIVED | CALLER_CLAIMED",
  "confirmed_fraud_flags_90d": 1,
  "vertical_payload": { }
}
```

### 5.1 Trust provenance is mandatory *(DR-007)*
v1 used a boolean `caller_known` derived from `caller_number` — the single most spoofable field in
telephony — and let it *reduce* the risk score. v2 replaces it with a three-valued
`caller_attestation` plus a mandatory `attestation_source`.

**Only `VERIFIED` attestation may reduce risk.** `CALLER_ID_ONLY` can never produce `VERIFIED`.
Likewise `urgency_source: CALLER_CLAIMED` is recorded but is never allowed to *lower* the urgency
contribution below the `normal` baseline. Enforced in `03-RISK_SCORING_SPEC.md` §4.

## 6. Risk Fusion Engine

```
gRPC ScoreWindow(WindowInputs) returns (WindowScore)
```
```json
{ "session_id": "...", "window_seq": 14, "window_score": 62,
  "active_signals": ["ai", "context"], "inactive_signals": ["speaker"],
  "inactive_reasons": { "speaker": "no_reference" },
  "applied_floors": [], "contributing_factors": [
     {"factor":"ai_synthetic","weight":0.60,"value":0.71,"points":42.6},
     {"factor":"context_risk","weight":0.40,"value":0.48,"points":19.2} ],
  "policy_version": "banking@1.4.0", "degraded": false }
```

`contributing_factors` is **mandatory** and must sum to `window_score` before floors. If it does
not, the response is invalid. This is testable and should be a unit test. *(DR-025)*

## 7. Session Aggregator

```
gRPC AggregateStream(stream WindowScore) returns (stream SessionVerdict)
```
```json
{ "session_id":"...", "session_score": 58, "band": "MEDIUM",
  "band_changed": true, "escalation_seq": 2,
  "windows_scored": 14, "voiced_seconds_total": 21.0,
  "hysteresis_state": "ESCALATED_CONFIRMED" }
```
Semantics in `04-SESSION_SCORING_SPEC.md`.

## 8. Decision Service *(new in v2 — DR-013)*

v1's diagram had an "ACTION LAYER" with no service, no contract, and no owner, while `RiskResult`
carried a `band` and `AuditRecord` carried a `decision` with nothing in between.

```
gRPC Decide(SessionVerdict) returns (Decision)
POST /v1/decisions/{session_id}/override   → human override, requires role + reason
```
```json
{ "session_id":"...", "decision":"STEP_UP",
  "required_actions":[{"type":"otp_challenge","channel":"registered_device"}],
  "reason_code":"SYNTHETIC_LIKELY_HIGH_VALUE_TXN",
  "explanation_for_human":"...", "reversible": true,
  "policy_version":"banking@1.4.0", "wal_seq": 90124 }
```

Decision Service also owns:
- the circuit-breaker default when Fusion is unavailable (`01-ARCHITECTURE.md` §5),
- the WAL write that must be fsynced **before** this response is returned,
- signing the audit record at origin.

## 9. Audit Service

```
POST /v1/audit-records              → append (idempotent on wal_seq)
GET  /v1/audit-records/{id}
POST /v1/audit-records/verify       → chain + anchor verification
```
```json
{ "seq": 90124, "tenant_id": "...", "session_id": "...", "ts": "...",
  "session_score": 58, "band": "MEDIUM", "decision": "STEP_UP",
  "model_versions": {"detector":"d-2.1.0","verifier":"v-1.3.0","calibrator":"c-1.0.2"},
  "policy_version": "banking@1.4.0", "code_version": "git:9f3a1c",
  "degraded": false, "degraded_reasons": [],
  "prev_hash": "sha256:...",
  "record_hash": "sha256:...",
  "origin_signature": "ed25519:..." }
```

### 9.1 Hash chain, not content hash *(DR-006, DR-017)*
v1 defined `record_hash` as SHA-256 **of the record's own fields**. That detects nothing: alter a
record, recompute its hash, and it validates. v1's `07` nonetheless listed "hash chain" as the
tamper mitigation. v2 makes it real:

```
record_hash = sha256( canonical_json(record_without_hash_fields) || prev_hash )
```
`prev_hash` is the previous record's `record_hash` for that tenant; `seq` is monotonic per tenant.
`origin_signature` is produced by the **Decision Service**, not the Audit Service, so a compromised
Audit Service cannot fabricate history.

Canonicalisation (RFC 8785 JCS) must be specified and tested — two implementations that serialise
differently produce different hashes and break the chain silently.

## 10. Alert Service

```
POST /v1/alerts                     → created by Decision Service (idempotent on escalation key)
GET  /v1/alerts?tenant=&status=&band=&since=
POST /v1/alerts/{id}/assign
POST /v1/alerts/{id}/resolve        → { outcome: CONFIRMED_FRAUD | FALSE_POSITIVE | INCONCLUSIVE,
                                        notes, resolver_id }
POST /v1/alerts/{id}/appeal         → subject-initiated; see 06 §7
```

**Idempotency key** = `sha256(session_id + band + escalation_seq)`. One alert per escalation, not
one per window — without this the system emits an alert every hop. *(DR-008)*

### 10.1 Human-in-the-loop SLA *(DR-019 — v1's dangling cross-reference)*
v1's `02` pointed to an SLA "in `08`" that did not exist. Defined here:

| Band | Ack SLA | Resolve SLA | Breach action |
|---|---|---|---|
| HIGH | 2 min | 30 min | Page on-call; auto-escalate to supervisor queue |
| MEDIUM | 15 min | 4 h | Dashboard escalation banner |
| Appeal (any band) | 1 business hour | 24 h | Regulatory-reportable if breached |

## 11. Notification channel contract *(DR-020)*
v1 had a degraded mode for "alert channel down" but never defined the channel.
```
NotificationChannel { send(alert) -> {delivered: bool, provider_ref: string} }
```
Implementations: `DashboardChannel` (Phase 0), `WebhookChannel`, `EmailChannel`, `SmsChannel`.
All are retried with exponential backoff into a DLQ; delivery state is visible in the UI.

## 12. Contract testing requirement

Every service ships golden-fixture contract tests. Required assertions:
- `contributing_factors` points sum to `window_score` before floors (§6).
- Reading `match_score` when `reference_available=false` raises, never coerces (§4).
- Replaying a request with the same `Idempotency-Key` returns the original response byte-for-byte.
- An audit chain of N records verifies; mutating record k breaks verification at k and only k.
