# Security & Assurance Testing Plan (v2)

> Fixes DR-033, and adds executable tests for every claim asserted elsewhere.

**Governing principle: a claim that is not tested is a marketing statement.** Every "never", "always",
and "guaranteed" in this doc set maps to a test ID below. If it has no test, delete the claim.

## 1. Cadence

| Category | Scope | Cadence |
|---|---|---|
| Unit: scoring invariants | `03`, `04` maths | Every commit |
| Contract tests | Every boundary in `02` | Every commit |
| Dependency scanning | Both stacks + model artefacts | Every CI run |
| Retention/privacy assertions | `06` §2 table | Every CI run (§4.7) |
| Fuzzing | Gateway endpoints, audio parser, codec path | Every release |
| Adversarial audio regression | Detector + verifier vs full attack corpus | Every model release |
| Bias/fairness report | Per-subgroup rates with CIs | Every retrain |
| Chaos / fail-safe | Degraded-mode matrix | Before each launch; monthly in staging |
| Load test | Burst + sustained | Before each launch |
| Penetration test | Gateway, authZ, tenant isolation | Before production launch, then annually |
| Insider-access audit | Log review, RBAC drift | Quarterly |
| Compliance audit | Consent, retention, breach rehearsal | Before launch in each new jurisdiction |

## 2. Scoring correctness (new — these catch the v1 defects)

- [ ] **T-2.1** Unit consistency: no term mixes 0–1 and 0–100 scales. Property test over random
      inputs asserting `0 ≤ score ≤ 100` with no clamping ever engaged for in-range inputs. *(DR-001)*
- [ ] **T-2.2** Floor enforcement: `adversarial_flag = true` with all signals at 0 yields ≥ 55 and
      band ≥ MEDIUM. *(DR-002)*
- [ ] **T-2.3** Phase-0 reachability: detector-only, `p_cal = 0.95, conf = 0.95` yields band HIGH.
      *(DR-003)*
- [ ] **T-2.4** Telecom reachability: no enrolment, high detector output yields band HIGH. *(DR-004)*
- [ ] **T-2.5** Renormalisation: for any active subset, an all-max input yields exactly 100 and an
      all-min input yields exactly 0.
- [ ] **T-2.6** Explainability sum: `Σ contributing_factors.points == base` for 100% of a 10k
      random batch. *(DR-025)*
- [ ] **T-2.7** Monotonicity: increasing `p_synthetic`, `transaction_value`, or fraud flags never
      decreases the score. Property-based.
- [ ] **T-2.8** **Adversary monotonicity**: for every adversary-controlled field, no value reduces
      the score below its worst-case value. This is `03 §4` as code. *(DR-007)*
- [ ] **T-2.9** Null-safety: `match_score` accessed with `reference_available = false` raises;
      never coerces to a number. *(DR-012)*
- [ ] **T-2.10** Trust-discount gate: each of the six eligibility clauses individually blocks the
      discount when violated.

## 3. Session-scoring tests *(DR-008)*
- [ ] **T-3.1** Storm: 10-min call held at HIGH → exactly 2 notifications.
- [ ] **T-3.2** Flap: score oscillating across a boundary each window → ≤1 band change.
- [ ] **T-3.3** Dilution: 4 s synthetic within 60 s genuine → session ≥ MEDIUM.
- [ ] **T-3.4** Silence: 5-min call with 3 s speech → `UNKNOWN`, never `LOW`.
- [ ] **T-3.5** Warm-up: verdict at t=1 s → `UNKNOWN`.
- [ ] **T-3.6** Multi-speaker: 2 tracks, 1 synthetic → session band = HIGH.
- [ ] **T-3.7** Idempotency: replaying the alert-creation call yields one alert.

## 4. Fail-safe / chaos — one test per matrix row *(01 §5)*
- [ ] **T-4.1** Kill Detection mid-call → signal inactive, floor 40 applied, never silently LOW.
- [ ] **T-4.2** Kill Speaker Verification → renormalisation, floor 40; distinguish this from the
      no-enrolment case, which must apply **no** floor.
- [ ] **T-4.3** Kill Context Service → `high_min` lowered by delta; decision still produced.
- [ ] **T-4.4** Kill Fusion Engine → Decision Service circuit-breaks to `default_degraded_decision`.
- [ ] **T-4.5** Kill Audit backend → decision proceeds; WAL entry present and fsynced; drains on
      recovery with zero loss. *(DR-016)*
- [ ] **T-4.6** Kill Alert channel → DLQ populated, dashboard still live, no alert lost.
- [ ] **T-4.7** **Retention assertion** — the test that makes `06 §2` true: run a full session,
      then (a) inspect every mounted volume and broker log dir for audio-like payloads,
      (b) assert Redis `save`/`appendonly` are disabled, (c) assert the ring buffer is zeroised
      post-close, (d) assert no core dump or swap contains buffer contents. *(DR-005)*
- [ ] **T-4.8** Latency ceiling: inject 2 s detector delay → signal marked inactive at the 1200 ms
      ceiling, decision still returned within budget.
- [ ] **T-4.9** Burst load on ingestion → backpressure and shedding, no upstream telephony failure.

## 5. Adversarial audio red-team
- [ ] **T-5.1** White-box perturbation attack → measure accuracy drop; `adversarial_flag` fires.
- [ ] **T-5.2** Black-box / transfer attack from a surrogate model.
- [ ] **T-5.3** Replay attack against the verifier using a genuine recording.
- [ ] **T-5.4** Cross-generator: synthesis families absent from training.
- [ ] **T-5.5** **Codec laundering**: every attack re-run through G.711, Opus, and AMR-NB. An attack
      that dies under compression is not a threat; one that survives is the only kind that matters.
- [ ] **T-5.6** Score-oracle probing: simulate boundary probing; assert rate limiting and probing
      detection fire, and that no raw score was returned to the untrusted caller. *(DR-032)*
- [ ] **T-5.7** Splice attack: genuine speech with a synthetic clause inserted mid-sentence.
- [ ] **T-5.8** Liveness/challenge-response bypass, where enabled.

## 6. Model quality gates *(DR-018, DR-028)*
- [ ] **T-6.1** Calibration: ECE ≤ 0.05 on held-out; reliability diagram attached to the model card.
- [ ] **T-6.2** `confidence` is not a re-expression of `p_synthetic` — assert correlation |r| < 0.5
      on the held-out set. Catches the common `max(p, 1−p)` shortcut.
- [ ] **T-6.3** Speaker-, generator-, and channel-disjoint splits enforced by the data loader, with
      a test that leakage across splits raises.
- [ ] **T-6.4** Per-subgroup rates with Wilson CIs; `FAR_worst / FAR_best ≤ 2.0` on CI upper bounds;
      subgroups with n < 200 reported as *unmeasured*, never as passing.
- [ ] **T-6.5** Attack-corpus regression: no regression against any historical bypass.
- [ ] **T-6.6** Unsupported-language input → `language_supported = false` and floor 40. *(DR-023)*

## 7. Penetration test scope
- [ ] AuthN bypass: token forgery, replay, expiry, algorithm confusion (`alg: none`, HS/RS mixup).
- [ ] **Tenant isolation**: full API surface as tenant A against tenant B's IDs → 404, not 403.
- [ ] mTLS enforcement: no internal service reachable without a valid workload identity.
- [ ] Injection across all structured context fields.
- [ ] Secrets exposure in logs, traces, error bodies, and client responses.
- [ ] IDOR on alerts, sessions, decisions, enrolments.
- [ ] Rate-limit and DoS resilience on ingestion and detection.
- [ ] **Cost-based DoS**: can an authenticated tenant force unbounded GPU spend?
- [ ] Model artefact loading: attempt to load a malicious checkpoint; assert hash pinning blocks it.

## 8. Pre-launch sign-off gate

Every box below has a named owner and a date. "Risk accepted" is a valid outcome; "unknown" is not.

- [ ] Pentest findings closed or formally risk-accepted with owner + date
- [ ] Adversarial regression suite green against the shipping model version
- [ ] Bias report within the disparity threshold, with subgroup coverage stated
- [ ] Calibration gate met
- [ ] Chaos suite green for every row of the degraded-mode matrix
- [ ] Retention automation verified in staging (T-4.7 green)
- [ ] Consent flow reviewed against the target jurisdiction
- [ ] Appeal path live, with SLA instrumented and alerting on breach
- [ ] Insider-access logging confirmed capturing every decision/embedding read
- [ ] Breach plan rehearsed (tabletop) at least once
- [ ] Model card published, including the out-of-scope section

## 9. Hackathon-scale subset

You will not run all of the above in five days. Run **these eleven**, which cover every defect that
was actually load-bearing, and say plainly in your submission that the rest are specified but not
executed. That is a stronger position than implying full coverage.

`T-2.2` · `T-2.3` · `T-2.6` · `T-2.8` · `T-3.1` · `T-3.3` · `T-4.1` · `T-4.5` · `T-4.7` ·
`T-6.1` · `T-6.6`
