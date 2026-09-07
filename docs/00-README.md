# Voice Impersonation / AI-Voice-Fraud Detection Platform — v2 Spec Set

> **This is v2.** It supersedes the previous 10-document set. Every change traces to a numbered
> finding in `15-DEFECT_REGISTER.md`. If you are looking for "what changed and why", start there.

---

## 1. What this project is

A real-time system that listens to live calls (mobile / VoIP / enterprise collaboration),
detects AI-synthesised or cloned speech, cross-checks the claimed speaker's identity, fuses that
with call and transaction context into a single explainable risk score, and drives a bounded
action (allow, warn, step-up, escalate, block).

Blockchain is an optional Phase 3 bolt-on. The system is complete and demonstrable without it.

## 2. Build context (this drives every trade-off below)

**Target: hackathon / academic project.** That is a real constraint, not a disclaimer. It means:

| Priority | Why |
|---|---|
| A demo that provably works end-to-end | Judges score what they see run, not what the doc promises |
| Measured numbers, however small the test set | "We measured EER = 0.14 on 400 clips" beats "high accuracy" every time |
| Visible fail-safe behaviour | Killing a service on stage and showing it degrade correctly is the single most memorable demo moment |
| Explainability on screen | Turns a black box into a defensible system in front of a technical judge |
| An honest ethics/privacy answer | The first question every panel asks about voice biometrics |
| Production path documented, not built | Shows engineering maturity without burning your timeline |

**Phase 0 must be buildable by 2–4 people in 3–5 days.** Everything in Phase 1+ exists so the
project has a credible answer to "how would you actually ship this?" — not so you build it now.

### 2.1 Hard ethics gate (read before you record anything)
- Never clone, synthesise, or enrol the voice of a real person without their **written, informed,
  revocable** consent. This includes teammates, professors, and public figures.
- Use only openly licensed speech corpora plus voices your team recorded of itself.
- Do not demo by cloning a judge, a celebrity, or a bank executive. It is the obvious idea and it
  is the one that gets a project disqualified.
- Full rules: `06-DATA_PRIVACY_COMPLIANCE.md` §0.

## 3. Target verticals (config, not forks)

Banking/fintech, telecom screening, enterprise collaboration, general anti-phishing. These differ
in **policy pack** (weights, thresholds, required actions) and **integration surface** only. Core
architecture is identical. See `03-RISK_SCORING_SPEC.md` §5.

**Pick exactly one vertical for Phase 0.** Recommended: **banking**, because it has the richest
context signals, the clearest demo narrative, and the strongest reason for a live intervention.

## 4. Document index

| Doc | Purpose | Read it when |
|---|---|---|
| `01-ARCHITECTURE.md` | Services, topology, transport, latency budget, degraded-mode matrix | Before writing any code |
| `02-API_CONTRACTS.md` | Every service boundary, schema, versioning, idempotency | Before implementing a service |
| `03-RISK_SCORING_SPEC.md` | The fusion maths — corrected, with worked examples | Building the Risk Engine |
| `04-SESSION_SCORING_SPEC.md` | How per-window scores become one live session verdict | **New in v2. The biggest previous gap.** |
| `05-ML_MODEL_LIFECYCLE.md` | Data, training, calibration, adversarial, bias, drift | Building or evaluating the model |
| `06-DATA_PRIVACY_COMPLIANCE.md` | Ethics gate, consent, retention, jurisdiction | Before touching audio |
| `07-SECURITY_ARCHITECTURE.md` | AuthN/Z, tenant isolation, tamper-evidence, threat model | Phase 1 |
| `08-SECURITY_TESTING_PLAN.md` | Pentest, chaos, adversarial audio, launch gates | Phase 1–2 |
| `09-UX_SPEC.md` | Screens, states, live-alert UX, accessibility, appeal flow | **New in v2.** Building the dashboard |
| `10-SLO_OPS_RUNBOOK.md` | SLOs, error budgets, alerts, runbooks, DR | **New in v2.** Phase 1–2 |
| `11-COST_MODEL.md` | Unit economics per call-minute, break-even, scaling cliff | **New in v2.** The slide judges ask about |
| `12-BUILD_CHECKLIST.md` | Phased plan with explicit gates and a day-by-day Phase 0 | Every day of the build |
| `13-BLOCKCHAIN_FUTURE_EXTENSION.md` | The plug-in point, with a Phase-3-proof interface | Only at Phase 3 |
| `14-GLOSSARY.md` | Precise terms, incl. the three different meanings of "false accept" | Whenever a metric name is ambiguous |
| `15-DEFECT_REGISTER.md` | All v1 defects, severity, and where each is fixed | Reviewing this rewrite |
| `AGENT_PROMPT.md` | Master prompt for an AI coding agent, incl. capability discovery | Handing this to Claude Code |

## 5. Roadmap

| Phase | Goal | Exit gate |
|---|---|---|
| **0 — Demo MVP** (3–5 days) | Live audio → detection → session score → banded action → dashboard, single tenant | Demo runs twice in a row without a restart; a HIGH alert is reachable and reproducible; FAR/FRR measured on a held-out set |
| **1 — Pilot-ready core** (weeks) | Speaker verification, full fusion, real auth, degraded modes, observability | Every row of the degraded-mode matrix demonstrated by killing the service |
| **2 — Production hardening** | Adversarial suite, bias gate, pentest, HA, compliance automation | `08-SECURITY_TESTING_PLAN.md` §6 sign-off complete |
| **3 — Optional ledger** | Second `AuditBackend`, zero upstream change | Backend can be disabled at runtime with no downstream effect |

## 6. Stack decisions (v2 — changed from v1)

| Concern | Choice | Change from v1 |
|---|---|---|
| Model training + inference | Python (PyTorch, ONNX Runtime) | unchanged |
| Services | Go | unchanged |
| **Hot-path transport** | **gRPC bidirectional streaming, in-process fan-out** | **Changed.** Kafka removed from the sub-second path — it was silently breaking both the latency budget and the "audio never touches disk" claim (DR-005, DR-009) |
| Async lane | Kafka/Redpanda — analytics, audit fan-out, alert retries, replay | Kafka retained here only |
| State | Postgres (durable, RLS-isolated) + Redis (in-memory-only instance, persistence disabled) | Redis persistence explicitly disabled (DR-005) |
| Deploy | Docker Compose for Phase 0; K8s-ready manifests deferred to Phase 1 | Made explicit |

**Phase 0 simplification:** run Detection, Fusion, and Decision as one Python process plus one Go
gateway. Do not build six microservices in five days. The contracts in `02` are what make the
split trivial later; they are not an instruction to split now.

## 7. Assumption register

Every assumption is tagged `[ASSUMPTION-n]` in-line where it appears. Confirm or override:

| ID | Assumption | Default | Override if |
|---|---|---|---|
| A-1 | Primary jurisdiction | India DPDP Act, GDPR/CCPA secondary | Your institution or pilot customer is elsewhere |
| A-2 | Live-intervention latency target | ≤800 ms P95 window-to-decision | Post-call-only use case → budget does not apply |
| A-3 | Phase 0 vertical | Banking | Your dataset or demo story fits telecom better |
| A-4 | Analysis window | 3.0 s voiced audio, 1.0 s hop | Model architecture requires a different receptive field |
| A-5 | Detector base model | Open self-supervised speech encoder + lightweight classifier head | You have a licensed anti-spoofing model available |
| A-6 | Team size / time | 2–4 people, 3–5 days for Phase 0 | Adjust `12-BUILD_CHECKLIST.md` §2 day plan |
