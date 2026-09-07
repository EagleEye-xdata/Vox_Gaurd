# Defect Register — v1 → v2

Every defect found in the v1 doc set, its severity, and where v2 fixes it.

**Severity:** **S1** the system does not work as described · **S2** a load-bearing claim is false or
a serious loophole exists · **S3** a real gap that will cause rework or a bad outcome · **S4**
correctness/clarity issue.

## Category A — Scoring maths

| ID | Sev | v1 location | Defect | Fixed in |
|---|---|---|---|---|
| DR-001 | **S1** | `03` §1 | Formula mixes a 0–1 weighted sum with 0–100 flat terms, then multiplies by 100. `+15` becomes `+1500`. | `03` §1–2, `08` T-2.1 |
| DR-002 | **S1** | `03` §1, `01` §5 | "Adversarial penalty forces at least Medium" — but `0 + 15 = 15` = LOW. An addend cannot enforce a minimum. | `03` §5 floors, `08` T-2.2 |
| DR-003 | **S1** | `08` Phase 0 | AI-only scoring with `W_ai = 0.45` caps the score at 45. **The Phase 0 demo could never fire a HIGH alert.** | `03` §2 renormalisation, `08` T-2.3 |
| DR-004 | **S1** | `01` §3, `03` | "Zero the speaker weight and redistribute" — redistribution never defined. Without enrolment the max score is 70, so the telecom vertical could never escalate. | `03` §2, `08` T-2.4 |
| DR-007 | **S2** | `03` §1 | `TRUST_DISCOUNT` triggered by `caller_known`, derived from caller ID — trivially spoofable. **An attacker lowers their own fraud score.** | `03` §4, `02` §5.1, `08` T-2.8 |
| DR-018 | **S2** | `02` §3, `03` | `confidence` emitted and never used; no calibration requirement anywhere. Weighting an uncalibrated softmax output produces a meaningless number. | `03` §3.1, `05` §3, `08` T-6.1/T-6.2 |
| DR-022 | S3 | `03` §1 | `historical_fraud_flags / 3` — magic constant, undefined semantics (flags against whom, over what window, confirmed or suspected). | `03` §3.3 |
| DR-025 | S3 | `02` §6 | `contributing_factors` declared mandatory but never required to reconcile with the score. | `02` §6, `08` T-2.6 |
| DR-026 | S3 | `02` §5, `03` §1 | `transaction_value` and `transaction_type` collected and then used nowhere in the formula. | `03` §3.3 |
| DR-048 | S3 | `01` §2 | No minimum voiced audio before scoring — the detector runs on silence, hold music, and ringback. | `04` §2, `03` §5 |

## Category B — Session and streaming semantics

| ID | Sev | v1 location | Defect | Fixed in |
|---|---|---|---|---|
| DR-008 | **S1** | absent | **The largest gap.** Per-chunk detection results, per-session risk score, and nothing describing the aggregation. Left unspecified, the default implementation alerts on every chunk. | `04` (new doc), `08` §3 |
| DR-027 | S3 | absent | Silence and non-speech audio counted as scoreable, inflating both cost and noise. | `04` §2 |
| DR-041 | S3 | `02` §1, §3 | Unary RPCs specified over a fundamentally streaming architecture. | `02` §1, §3 |
| DR-015 | **S2** | absent | No diarisation anywhere, while "Zoom/Teams meeting protection" is a named v1 vertical. Speaker verification assumes one speaker per session. | `01` §8.5, `04` §6, `05` §5 |

## Category C — Architecture and contradictions

| ID | Sev | v1 location | Defect | Fixed in |
|---|---|---|---|---|
| DR-005 | **S2** | `01` §2/§8 vs `05` §2 | Audio routed through Kafka (a disk-backed log) and features cached in Redis (persists by default) while `05` claims raw audio is "never written to disk." The false claim is the legally significant one. | `01` §3, `06` §2, `08` T-4.7 |
| DR-009 | S3 | `01` §4 | Latency budget components sum to exactly the 800 ms target — zero allowance for network, serialisation, queueing, or ingestion. The 1.5 s ceiling is unexplained. | `01` §4 |
| DR-010 | S3 | `01` §2 vs §7 | Two contradictory topologies: Detection → Verification sequential in one diagram, parallel in the other. | `01` §2 |
| DR-011 | S3 | `01` §2 vs `02` §2 | Topic naming conflict; `02`'s topic-per-session would create millions of Kafka topics. | `01` §2 |
| DR-013 | **S2** | `01` §2, `02` | An "ACTION LAYER" box with no service, contract, or owner. `RiskResult` has a band; `AuditRecord` has a decision; nothing bridges them. Nobody owns the circuit breaker. | `01` §2, `02` §8 (new Decision Service) |
| DR-014 | **S2** | `02` §8 | Audit records `model_version` but not policy/weights/threshold version — **decisions cannot be reproduced**, despite `03` calling fusion "a versioned component." | `02` §9, `03` §7 |
| DR-019 | S4 | `02` §7 | Dangling cross-reference to an SLA "in `08`" that does not exist; `01` §5 cites a section title absent from `04`. | `02` §10.1 |
| DR-020 | S3 | `01` §3 | A degraded mode defined for "alert channel down" with no notification channel contract anywhere. | `02` §11 |
| DR-012 | **S2** | `02` §4 | `match_score` declared `float [0-1]` with `-1` reserved for "no reference" — a sentinel outside the declared range. Any naive `1 - match_score` evaluates to `2.0` and silently corrupts the score. | `02` §4, `08` T-2.9 |
| DR-021 | S3 | `02` | No idempotency on any mutating endpoint. | `02` §1 |
| DR-043 | S4 | `02` | No error envelope or error-code enum; clients would branch on message strings. | `02` §1.1 |

## Category D — Security

| ID | Sev | v1 location | Defect | Fixed in |
|---|---|---|---|---|
| DR-006 | **S2** | `02` §8 vs `06` §5 | `record_hash` is a hash of the record's own fields — a content hash, not a chain. Alter and recompute; undetectable. Yet `06` lists "hash chain" as the tampering mitigation. | `02` §9.1, `07` §4 |
| DR-016 | **S2** | `01` §3 | "Audit down → queue and retry" loses exactly the evidence covering the incident under investigation. | `01` §7, `08` T-4.5 |
| DR-017 | **S2** | `02` §8 | Hash computed **by the Audit Service** — a compromised store can fabricate history. | `02` §9.1, `07` §4 |
| DR-032 | **S2** | absent | No control on returning raw scores externally — the API becomes a free oracle for tuning an evasive clone. | `07` §3, §7, `08` T-5.6 |
| DR-042 | **S2** | `07` §3 vs absent | Tenant isolation pentested but no mechanism designed anywhere. | `07` §5 |
| DR-044 | S3 | `06` §6 | Dependency scanning specified, but model artefacts unpinned and pickle-format loading unaddressed — loading an untrusted checkpoint is arbitrary code execution. | `07` §8 |
| DR-045 | S3 | `06` §1 | Roles defined without separation of duty; one person could tune thresholds and resolve the resulting alerts. | `07` §1 |

## Category E — ML lifecycle

| ID | Sev | v1 location | Defect | Fixed in |
|---|---|---|---|---|
| DR-023 | **S2** | absent | No language detection or gating. An India-first deployment will meet Telugu, Tamil, Bengali, and heavy code-switching; the model would guess silently outside its evaluated scope. | `05` §4, `08` T-6.6 |
| DR-024 | S3 | `03` §4, `04` §2, `05` §7 | "FAR", "FRR", and "false positive" used interchangeably across three subsystems with three different meanings. | `14` §1 |
| DR-028 | S3 | `04` §4 | Bias gate of "1.5× overall FAR" is statistically weak — dominated by the majority subgroup, unstable for small n, no CIs, no minimum sample size. | `05` §7, `08` T-6.4 |
| DR-029 | S3 | `04` §6 | Human-in-the-loop labels fed back without addressing the confirmation loop — reviewers see the model's score before labelling. | `05` §9 |

## Category F — Privacy, ethics, and product

| ID | Sev | v1 location | Defect | Fixed in |
|---|---|---|---|---|
| DR-030 | **S2** | `08` Phase 0 | "No real auth required, local storage acceptable" for a phase that processes voice biometrics. No ethics gate for a project that generates cloned voices. | `06` §0, `12` §0 |
| DR-031 | S3 | `05` §2 | Embeddings described as "irreversible derived data", implying anonymity. They are linkable biometric identifiers. | `06` §2 |
| DR-035 | S3 | `05` §7 | False-positive harm acknowledged in one paragraph with no flow, no copy rules, no SLA, and no human-review requirement for automated decisions. | `06` §7, `09` §5, `02` §10.1 |
| DR-034 | **S2** | absent | **No UX specification** for a product whose appeal path is a compliance control and whose output is an accusation-adjacent judgement. One bullet said "basic dashboard." | `09` (new doc) |
| DR-047 | S4 | throughout | Latency framing implies instant detection; time-to-first-verdict is ~4 s of speech. Overclaiming is a safety problem, not just marketing. | `04` §8, `14` §6 |

## Category G — Operations and economics

| ID | Sev | v1 location | Defect | Fixed in |
|---|---|---|---|---|
| DR-036 | S3 | `01` §9 | "Alerting on SLA breach" with no SLA, no SLO, and no error budget defined anywhere. | `10` §1–3 |
| DR-037 | S3 | absent | HA mentioned; **no disaster recovery, RPO, RTO, or restore drill**. | `10` §5 |
| DR-038 | S3 | absent | No cost model for a product whose core operation is GPU inference on every second of every call. | `11` (new doc) |
| DR-046 | S4 | `01` §9 | Full distributed tracing specified at a per-second verdict cadence — observability cost can exceed inference cost. | `11` §3, `10` §2 |
| DR-033 | S3 | `07` | Test plan did not tie tests to the specific claims made elsewhere; several "never" claims had no test. | `08` (every claim now has a test ID) |
| DR-039 | S3 | `08` | Build checklist not calibrated to the actual build context; Phase 0 as written was unachievable and internally contradictory (DR-003). | `12` §1 |

## Category H — Blockchain

| ID | Sev | v1 location | Defect | Fixed in |
|---|---|---|---|---|
| DR-040 | **S2** | `09` §1 vs §3 | `verify(hash) -> bool` cannot carry a Merkle inclusion proof, yet the same doc says batching is necessary. Adopting batching would break the very interface that existed to prevent breakage. Also `verify(hash)` cannot confirm a *record* matches. | `13` §1 |

## Summary

| Severity | Count |
|---|---|
| S1 — system does not work as described | 5 |
| S2 — false load-bearing claim or serious loophole | 16 |
| S3 — real gap causing rework or bad outcome | 23 |
| S4 — correctness/clarity | 4 |
| **Total** | **48** |

## Document mapping v1 → v2

| v1 | v2 |
|---|---|
| `00-README` | `00-README` (rewritten) |
| `01-ARCHITECTURE` | `01-ARCHITECTURE` (rewritten) |
| `02-API_CONTRACTS` | `02-API_CONTRACTS` (rewritten) |
| `03-RISK_SCORING_SPEC` | `03-RISK_SCORING_SPEC` (rewritten) |
| — | **`04-SESSION_SCORING_SPEC` (new)** |
| `04-ML_MODEL_LIFECYCLE` | `05-ML_MODEL_LIFECYCLE` |
| `05-DATA_PRIVACY_COMPLIANCE` | `06-DATA_PRIVACY_COMPLIANCE` |
| `06-SECURITY_ARCHITECTURE` | `07-SECURITY_ARCHITECTURE` |
| `07-SECURITY_TESTING_PLAN` | `08-SECURITY_TESTING_PLAN` |
| — | **`09-UX_SPEC` (new)** |
| — | **`10-SLO_OPS_RUNBOOK` (new)** |
| — | **`11-COST_MODEL` (new)** |
| `08-BUILD_CHECKLIST` | `12-BUILD_CHECKLIST` |
| `09-BLOCKCHAIN_FUTURE_EXTENSION` | `13-BLOCKCHAIN_FUTURE_EXTENSION` |
| — | **`14-GLOSSARY` (new)** |
| — | **`15-DEFECT_REGISTER` (new — this file)** |
| — | **`AGENT_PROMPT.md` (new)** |
