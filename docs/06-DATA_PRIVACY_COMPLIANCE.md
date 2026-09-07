# Data Privacy & Compliance (v2)

> Fixes DR-005, DR-030, DR-031, DR-035.

## 0. Hackathon ethics gate — read before recording anything *(DR-030)*

v1 said Phase 0 needs "no real auth" and "local storage acceptable." Both are fine for
infrastructure. Neither is fine for **voice biometrics**, which are sensitive personal data from
the first recording — regardless of project phase, grade weighting, or deadline.

**Non-negotiable rules for the demo build:**

1. **Consent before capture.** Every person whose voice is recorded or cloned signs a one-page
   consent: purpose, retention, deletion route, and the right to withdraw. Keep the forms.
2. **Never clone a non-consenting person.** Not a judge, not a professor, not a celebrity, not a
   bank CEO, not a politician. It is the obvious demo idea; it is also a defamation and
   impersonation exposure and a disqualification risk.
3. **Team voices and open corpora only.** Anything else needs a documented licence.
4. **The demo runs on pre-recorded, consented clips**, not on a live audience member's voice,
   unless that person consented in advance and in writing.
5. **Delete on request, immediately, including from the demo box.** Test that you can.
6. **Publish the limits.** If you show the system on stage, say what it does not do. An
   overclaimed deepfake detector is a safety problem, not just a marketing one.
7. **No cloning tool as a public service.** The synthesis tooling you use to build the training
   set runs offline, locally, and is never exposed as an endpoint.

If your institution has an ethics or IRB process for human-subject data, this project needs it.
Start that paperwork on day one — it is slower than the code.

## 1. Regulatory baseline `[ASSUMPTION-1]`

Primary: **India's DPDP Act 2023.** Voice biometric data is personal data and, under most
frameworks, sensitive. Secondary reference: GDPR (Art. 9 special-category biometric data where
used for unique identification) and CCPA/CPRA. Confirm before any real deployment; if your pilot
customer is elsewhere, revise this section first because everything downstream follows from it.

## 2. Data classification and truthful persistence *(DR-005)*

v1 claimed raw audio is "never written to disk" while routing it through Kafka, which is a
disk-backed log, and cached features in Redis, which persists by default. The claim was false
against v1's own architecture — and it is the one claim with legal weight.

| Data | Class | Persisted? | Enforcement |
|---|---|---|---|
| Raw call audio | Sensitive biometric | **No** | In-process ring buffer only. No broker, no disk, no swap. Zeroised on close or after `raw_buffer_seconds` (8 s). Verified by test `08 §4.7`. |
| Feature frames | Derived, short-lived | Memory only | Dedicated Redis instance with `save ""` and `appendonly no`. TTL 120 s. |
| Enrolment embeddings | Sensitive biometric, irreversible | Yes | Postgres, envelope-encrypted, tenant-scoped key, hard-deletable |
| Decision records | Business record, no biometric content | Yes | Postgres, hash-chained |
| Consent records | Legal record | Yes | Append-only, never deleted (deleting consent proof helps no one) |
| Call metadata | Personal data | Yes | Per tenant retention policy |
| Training clips | Sensitive biometric | Yes, offline | Encrypted, access-logged, separate environment from production |

**"Irreversible" needs a caveat.** Voice embeddings are not reversible to audio, but they are
linkable and can support re-identification across sessions and services. Treat them as biometric
identifiers, not as anonymised data. Several regulators take this view explicitly. Do not describe
embeddings as "anonymous" in any customer-facing material. *(DR-031)*

## 3. Consent

- **Enrolment**: explicit, informed, opt-in, granular. Must state purpose (fraud detection),
  retention period, and the withdrawal route. Consent is recorded with a timestamp, version of the
  notice shown, and the actor. Bundled consent (consent-to-service implies consent-to-biometrics)
  is not valid consent.
- **Live-call screening**: in two-party-consent jurisdictions the call must be flagged before
  detection runs — IVR notice, in-app banner, or contractual notice. This is a **per-tenant,
  per-jurisdiction toggle** the platform must support, not a default it may assume away.
- **Withdrawal**: one action, no dark patterns. Embedding hard-deleted within **24 h**; a
  tombstone (identity, timestamp, no biometric content) is retained to prove the deletion occurred.
- **Employees and enterprise deployments**: consent given to an employer is under a power
  imbalance and is weak in several jurisdictions. Prefer a legitimate-interest or contractual basis
  with a documented balancing test, plus opt-out — not a consent checkbox in an onboarding flow.

## 4. Retention defaults (tenant-configurable)

| Data | Default | Notes |
|---|---|---|
| Raw audio | 0 s beyond in-memory processing | Tenant opt-in recording is a *separate* pipeline with its own clock and its own consent |
| Feature frames | 120 s TTL | Auto-expire; no manual delete path needed |
| Embeddings | Until withdrawn or offboarding | Reviewed annually; re-consent prompt at 24 months |
| Decision records | Per tenant regulation, commonly 5–7 y in banking | No biometric content, so long retention is defensible |
| Alerts + resolutions | 2 y | Feeds model feedback loop |
| Access logs | 1 y minimum | Insider-threat investigations need history |

Retention is enforced by scheduled jobs **and verified by test**, not by policy prose. An untested
retention policy is a statement of intent.

## 5. Cross-border transfer

Default to per-tenant data residency. If shared inference infrastructure crosses a border, document
the mechanism (SCCs, adequacy, or DPDP's notified-country list) per jurisdiction pair *before*
launching in that market. Model weights crossing a border is not a personal-data transfer; training
clips crossing one is.

## 6. Breach response

- Severity tiers defined in advance. **Biometric exposure is always top tier.**
- Pre-drafted notification templates and a per-jurisdiction legal contact list, with the mandated
  window recorded (72 h under GDPR; DPDP requires notification to the Board and affected
  principals). Rehearse once, as a tabletop, before launch.
- Embedding exposure is materially worse than password exposure: a person cannot rotate their
  voice. Say so in the incident plan so the response is scaled correctly.

## 7. False positives are a harm, not just a metric *(DR-035)*

A HIGH alert against a legitimate person is a reputational and, in banking, financial harm. This is
a compliance control, not a UX nicety.

- **Every** MEDIUM/HIGH outcome has a low-friction appeal or step-up path — see
  `09-UX_SPEC.md` §5. Appeal SLA: acknowledge within 1 business hour, resolve within 24 h.
- **Never state a conclusion the system did not reach.** Customer-facing text says "we need to
  verify your identity," never "we detected fraud" or "we believe you are not who you claim."
  The system produces a probability, not an accusation. *(This is also the difference between an
  awkward call and a defamation claim.)*
- **Automated blocking requires explicit tenant opt-in** plus a documented human-review route.
  Under GDPR Art. 22 (and analogously elsewhere), a fully automated decision with legal or
  similarly significant effect gives the subject a right to human review. Build the route.
- Track and publish internally: appeal volume, appeal upheld rate, and time-to-resolution. A rising
  upheld rate is your earliest real-world signal that the model has drifted.

## 8. Subject rights

Access · correction · erasure · withdrawal of consent · human review of an automated decision ·
grievance route with a named contact. Each needs an implemented endpoint and an SLA, not a mailbox.
Erasure of an embedding must also invalidate any cached derivative — enumerate the caches.
