# SLOs, Operations & Runbook (NEW in v2)

> Fixes DR-036, DR-037. v1 mentioned "alerting on SLA breach" with no SLA, no SLO, no error budget,
> and no disaster recovery anywhere.

## 1. Service level objectives

Measured over a rolling 28-day window, per tenant.

| SLO | Target | Error budget | Why this number |
|---|---|---|---|
| **Verdict latency** — window close → verdict | P95 ≤ 800 ms, P99 ≤ 1200 ms | 5% / 1% | The agent must react while the caller is still talking |
| **Availability** — session accepts and returns verdicts | 99.5% | 3.6 h / 28 d | Phase 1 target; 99.9% only once HA is real |
| **Assessment completeness** — sessions scored with all expected signals active | ≥ 97% | 3% | Degradation is allowed; silent degradation is not |
| **Decision durability** — decisions with a durable WAL entry | 100% | 0 | No budget. A lost decision record is unrecoverable evidence |
| **Alert delivery** — MEDIUM/HIGH notified within 30 s | 99% | 1% | An alert nobody sees is not an alert |
| **Erasure SLA** — embedding deleted after withdrawal | 100% within 24 h | 0 | Regulatory, not negotiable |
| **Appeal ack** | 99% within 1 business hour | 1% | `06 §7` |

**Error budget policy.** Budget exhausted → feature work stops, reliability work starts, and the
next model release is held. Two consecutive exhausted windows escalate to the project lead. Write
this down before you need it, because the argument during an incident is always about whether the
rule existed.

## 2. The golden signals, per service

Latency (P50/95/99 per §1 stage) · error rate by error code · saturation (GPU utilisation, queue
depth, inference batch wait) · traffic (sessions/s, voiced-seconds/s — **not** calls/s, which
under-counts load on long calls).

**Model-specific signals** that generic monitoring will not give you:
- `p_synthetic` distribution vs training baseline (drift → `05 §8`)
- `confidence` distribution — a collapse toward the gate threshold means the model has met
  something it has not seen
- Fraction of windows with `adversarial_flag`
- Fraction of sessions ending `UNKNOWN`
- Band mix vs 7-day baseline
- **Appeal-upheld rate** — the single best real-world signal that the model has drifted, and the
  only one grounded in ground truth rather than in the model's own output

## 3. Alerting — page vs ticket

| Condition | Action |
|---|---|
| Verdict latency P95 > 800 ms for 10 min | Page |
| Decision WAL write failing | **Page immediately** — this is evidence loss in progress |
| Availability SLO burn rate > 14× (1 h) | Page |
| Erasure job failed | Page — regulatory clock is running |
| Band mix shifts > 3σ from 7-day baseline | Ticket + model owner |
| `adversarial_flag` rate > 5× baseline | Ticket + security — this may be an active campaign |
| Alert DLQ non-empty > 15 min | Ticket |
| Any tenant-isolation assertion failing in CI | **Page** — treat as a security incident |
| Certificate expiry < 14 days | Ticket |

Alert on **symptoms and burn rate**, not on every component. A page for each of nine services
during one outage guarantees the real signal is missed.

## 4. Runbooks

### 4.1 Verdict latency breach
1. Check GPU saturation and inference queue depth first — it is this ~80% of the time.
2. If saturated: scale detection replicas; confirm admission control is shedding, not queueing
   unboundedly.
3. If not saturated: check the context-service call (the only synchronous external dependency in
   the path) and its cache hit rate.
4. If the breach persists past the 1200 ms ceiling, confirm the fail-safe engaged — sessions should
   be scoring `degraded` with floor 40, **not** returning LOW. If they are returning LOW, that is a
   correctness incident, not a performance one. Escalate.

### 4.2 Detector returning implausible outputs
1. Compare `p_synthetic` distribution against baseline; check `calibrator_version` matches the
   expected pairing for `model_version` — a mismatched calibrator is the most common cause and it
   is silent.
2. Check the channel mix: a new codec in the traffic is out-of-distribution input, not a bug.
3. Roll back to the previous model version. `model_versions` on audit records lets you enumerate
   exactly which decisions the bad version made — do that before anything else, because you will be
   asked.

### 4.3 Audit chain verification failure
Treat as a **security incident** until proven otherwise.
1. Do not repair the chain. Snapshot it first.
2. Identify the failing `seq` and the last good checkpoint.
3. Determine whether it is a canonicalisation bug (all records after a deploy fail) or tampering
   (one record fails in the middle). The first is a bug; the second is an investigation.
4. Preserve WAL and access logs before any remediation.

### 4.4 Suspected active attack campaign
Trigger: `adversarial_flag` spike, or probing detection firing across many credentials.
1. Do **not** raise thresholds to quiet the alerts. That is what the attacker is testing for.
2. Capture the samples into the attack corpus (`05 §6`), with consent/legal basis checked.
3. Tighten rate limits on the implicated credentials; notify affected tenants.
4. Post-incident: add the samples to the regression suite so this bypass can never silently return.

### 4.5 Erasure request failure
Regulatory clock is running. Escalate to the DPO within 1 hour. Execute the deletion manually if
the job cannot be fixed inside the window, then fix the job. Record the manual action.

## 5. Disaster recovery *(DR-037)*

| Data | RPO | RTO | Method |
|---|---|---|---|
| Decision records | 5 min | 1 h | Streaming replica + PITR |
| Audit chain + checkpoints | 0 | 1 h | Synchronous replication; checkpoints to write-once storage |
| Enrolment embeddings | 15 min | 4 h | Encrypted backup; **restore drill required** — a backup you have never restored is a hypothesis |
| Config / policy packs | 0 | 15 min | Git-backed, redeployable |
| Feature cache | n/a | n/a | Ephemeral by design; cold start is acceptable |
| Raw audio | n/a | n/a | Intentionally unrecoverable. This is a feature. |

Restore drill quarterly. Record the actual RTO achieved, not the target.

## 6. Deployment

- Model releases: shadow → canary 5% → 25% → 100%, with automatic rollback on band-mix deviation
  beyond threshold. Never deploy a model and a policy change in the same release — you will not be
  able to attribute the resulting shift.
- Service releases: rolling, with contract tests as the gate.
- Policy-pack changes go through the simulation preview in `09-UX_SPEC.md` §4.5 and are versioned
  independently of code.

## 7. Hackathon-scale operations

You are not running an SRE rotation. Do these four, which take under an hour total and cover the
failure modes that will actually bite you during a demo:

1. A `/healthz` per process and a one-line status strip in the dashboard.
2. Log the four model signals from §2 to stdout every 10 seconds. When the demo behaves oddly on
   stage, this is what tells you why.
3. `docker compose restart <service>` documented — this is also your chaos demo.
4. A known-good `.env` and a seeded database dump, so a total wipe costs three minutes rather than
   your slot.
