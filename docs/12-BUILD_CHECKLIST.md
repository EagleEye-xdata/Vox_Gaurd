# Build Checklist (v2)

> Fixes DR-003, DR-030, DR-039. Calibrated for a **hackathon/academic build**: Phase 0 is a
> day-by-day plan for 2–4 people over 3–5 days. Phases 1–3 are the production path — documented so
> the project has a credible "how would you ship this" answer, not so you build them now.

## 0. Before any code

- [ ] **Ethics gate cleared** — `06-DATA_PRIVACY_COMPLIANCE.md` §0. Consent forms signed for every
      voice you will record or clone. Institutional ethics process started if applicable.
- [ ] Pick one vertical `[ASSUMPTION-3]`. Recommended: banking.
- [ ] Confirm or override the assumption register in `00-README.md` §7.
- [ ] Datasets identified and licences checked. **This is the most common day-1 blocker** — a
      corpus with a non-commercial or no-redistribution clause discovered on day 3 costs you the
      project.
- [ ] Repo scaffolded with `.gitignore` covering `*.wav`, `*.flac`, `.env`, `models/`. Committing
      a training clip is a data breach in a public repo.

## 1. Phase 0 — Demo MVP

**Goal:** live audio → calibrated detection → session score → banded action → dashboard, with one
demonstrable fail-safe. Single tenant, single vertical, no distributed system.

**Architecture for Phase 0:** two processes. One Python service (preprocess + VAD + detector +
calibrator + fusion + session aggregator + decision) and one web app. Do not build six
microservices in five days — the contracts in `02-API_CONTRACTS.md` make the split cheap *later*,
and honouring them inside one process costs nothing today.

### Day-by-day `[ASSUMPTION-6]`

**Day 1 — data and detector skeleton**
- [ ] Assemble genuine + synthetic sets, ≥3 synthesis families, ≥2 channel conditions
- [ ] **Speaker-, generator-, and channel-disjoint splits** wired into the loader (`05 §2`). Doing
      this on day 1 costs an hour; retrofitting it on day 4 invalidates every number you have.
- [ ] Baseline detector training loop running end to end, however bad the accuracy

**Day 2 — make the numbers real**
- [ ] Train to a usable EER; record it
- [ ] **Fit the calibrator on a held-out split** and check ECE ≤ 0.05 (`05 §3`). Skipping this makes
      every score in `03` meaningless
- [ ] Implement `confidence` via K=5 MC-dropout, and verify it is not just `max(p, 1−p)` (T-6.2)
- [ ] `DetectOnce` working standalone on a file — first demo-able artefact

**Day 3 — scoring and session logic**
- [ ] Fusion with **active-signal renormalisation** (`03 §2`) — the fix that makes HIGH reachable
- [ ] Floors, applied last (`03 §5`)
- [ ] `contributing_factors`, with the sum assertion as a test (T-2.6)
- [ ] Session aggregator: EWMA + decaying peak + N-of-M hysteresis (`04 §3–4`)
- [ ] Tests T-2.2, T-2.3, T-2.6, T-2.8, T-3.1, T-3.3 green
- [ ] Hash-chained audit records (~20 lines; makes the audit story real)

**Day 4 — interface**
- [ ] Live monitor strip (`09 §4.1`) with sparkline and one recommended action
- [ ] Case detail with factor bars and applied-floors row (`09 §4.3`)
- [ ] Degraded state rendering (`09 §6`)
- [ ] Live streaming path: mic or file → verdicts at ~1 Hz

**Day 5 — make it survive the stage**
- [ ] Kill-a-service chaos path working (T-4.1) — **rehearse this, it is your best 20 seconds**
- [ ] Retention assertion test (T-4.7): prove no audio hit disk
- [ ] Model card with per-subgroup numbers and an honest out-of-scope section (`05 §10`)
- [ ] Seeded demo data + a scripted 4-minute demo run, executed twice without a restart
- [ ] Rehearse the three questions you *will* be asked (§4)

### Phase 0 exit gate
- [ ] A HIGH alert is reachable and reproducible from a recorded clip *(v1's design made this
      mathematically impossible — verify it explicitly)*
- [ ] Killing the detector produces a degraded MEDIUM, never a silent LOW
- [ ] `contributing_factors` sums to the base score on screen
- [ ] FAR/FRR measured on a held-out set and stated with the sample size
- [ ] No raw audio on disk after a run
- [ ] Demo runs twice consecutively without intervention

## 2. Phase 1 — Pilot-ready core (weeks)

- [ ] Split into the services in `01 §2`, behind the existing contracts
- [ ] Speaker Verification + consented enrolment flow (`06 §3`)
- [ ] **Diarisation** for multi-speaker sessions (`05 §5`) — required before any enterprise-collab claim
- [ ] Context Service with `caller_attestation` provenance (`02 §5.1`)
- [ ] Every degraded-mode row implemented **and chaos-tested** (`08 §4`)
- [ ] Decision Service with fsynced WAL before ack (`01 §7`)
- [ ] OAuth2/OIDC external, mTLS internal (`07 §1`)
- [ ] Tenant isolation: Postgres RLS, Redis namespacing, CI cross-tenant test (`07 §5`)
- [ ] Encryption at rest, per-tenant keys
- [ ] Async lane on Kafka — analytics and audit fan-out only, never audio
- [ ] Policy packs per vertical, versioned, with the simulation preview (`09 §4.5`)
- [ ] Observability: OTel traces (sampled — see `11 §3`), the model signals from `10 §2`
- [ ] Alert Service with SLA tracking + appeal path (`02 §10.1`, `09 §5`)
- [ ] Retention automation, verified by test
- [ ] Language gating (`05 §4`)

## 3. Phase 2 — Production hardening

- [ ] Adversarial input-sanity check + `adversarial_flag`, attack corpus in CI (`05 §6`)
- [ ] Bias report meeting the worst/best ratio gate with stated subgroup coverage (`05 §7`)
- [ ] Full `08-SECURITY_TESTING_PLAN.md` suite; pentest findings closed or risk-accepted
- [ ] HA: ≥2 replicas of Detection, Fusion, Decision
- [ ] Drift monitoring + retraining triggers (`05 §8`)
- [ ] Insider-access logging and anomaly alerting (`07 §6`)
- [ ] DR restore drill executed, actual RTO recorded (`10 §5`)
- [ ] Error-budget policy agreed and written down before it is needed (`10 §1`)
- [ ] Cost controls: per-tenant inference budget, trace sampling (`11 §5`)

## 4. Phase 3 — Optional ledger

- [ ] Second `AuditBackend` behind the unchanged interface (`13-BLOCKCHAIN_FUTURE_EXTENSION.md`)
- [ ] **Merkle batching from day one** — per-record anchoring does not survive call volume
- [ ] Signing-key management, HSM/KMS-backed, separate from app secrets
- [ ] Shadow mode → per-tenant opt-in cutover
- [ ] Verify: disabling the backend at runtime has zero downstream effect

## 5. Definition of done — every phase

- Contract tests green for every boundary touched
- No raw audio persisted (spot-check the filesystem, do not trust the code review)
- Every new dependency has a documented **and tested** fail-safe before merge
- Every new claim in a doc has a test ID in `08`
- `policy_version` and `model_versions` present on every decision produced

## 6. The three questions you will be asked

Rehearse these. They decide the score more than the code does.

1. **"How do I know it works?"** → Show the confusion matrix, the sample size, the held-out split
   design, and the calibration curve. Then say what it does *not* detect. Admitting a limit is the
   strongest available move; a judge who finds the limit themselves has found a flaw instead.
2. **"What if it's wrong about a real customer?"** → Show the appeal flow, the copy rules in
   `09 §5`, and the human-review requirement. Say that blocking is opt-in, not default.
3. **"Isn't this just a classifier with a dashboard?"** → No: show the degraded-mode demo. Kill the
   detector live and show the system refusing to say "safe." That single behaviour is the actual
   product, and almost no competing project will have it.
