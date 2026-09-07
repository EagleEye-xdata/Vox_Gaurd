# Security Architecture (v2)

> Fixes DR-006, DR-016, DR-017, DR-032, DR-042, DR-044, DR-045.

## 1. AuthN / AuthZ

- **External**: OAuth2/OIDC, short-lived JWTs (≤15 min), tenant-scoped claims, refresh rotation.
- **Internal**: mTLS everywhere with per-workload identity (SPIFFE/SPIRE or cloud workload
  identity). **No shared static API keys between services** — a shared key means any compromised
  service can impersonate any other.
- **Dual-check tenancy**: `tenant_id` is derived from the workload's SPIFFE ID *and* the request
  JWT claim, and the two must match. A mismatch is a security event, not a 400.

| Role | Can | Cannot |
|---|---|---|
| `tenant_admin` | Edit policy packs, thresholds, retention config | Read embeddings; alter audit records |
| `analyst` | View and resolve alerts, view decision metadata and explanations | Read raw audio or embeddings; change weights |
| `auditor` | Read decision + audit records, verify chains | Write anything |
| `dpo` | Process subject-rights requests, trigger erasure | View alert queues |
| `service` | Only the RPCs its contract declares | Anything outside its contract |

Separation of duty: the role that can change risk weights cannot resolve the alerts those weights
produce. Otherwise "tune the threshold until the alerts stop" is a one-person operation.

## 2. Encryption

- **In transit**: TLS 1.3 preferred, 1.2 floor. mTLS internally.
- **At rest**: envelope encryption via KMS. **Per-tenant data encryption keys** so one tenant's key
  compromise does not cascade. Embeddings additionally get a distinct key class from business data.
- **In use**: raw audio buffers are locked pages where the platform supports it (`mlock`) so
  biometric data is never written to swap. This is what makes "never touches disk" literally true.
- **Secrets**: dedicated manager (Vault/KMS). Never in code, env files, images, or CI logs.
- **Key rotation**: documented cadence and a tested re-wrap procedure. An unrotatable key is an
  incident waiting for a date.

## 3. Gateway hardening

- Per-tenant and per-credential rate limits.
- **Model-probing detection**: alert on request patterns consistent with black-box boundary
  probing — high volume of near-duplicate audio, systematic parameter sweeps, abnormal ratio of
  short sessions, unusual score-distribution flatness per credential. This is the enforcement point
  for the adaptive-attacker assumption in `05-ML_MODEL_LIFECYCLE.md` §6.
- Schema validation on every endpoint. Reject unknown fields rather than ignoring them.
- **Do not return raw scores to untrusted clients.** Returning `p_synthetic` to an unauthenticated
  or attacker-controlled caller turns your API into a free oracle for tuning attacks. External
  responses carry the band and required action; numeric scores are for authenticated dashboards
  only. *(DR-032)*
- WAF on public endpoints; strict CORS; CSP on the dashboard.
- Idempotency keys on all mutating endpoints, with replay windows and stored responses.

## 4. Tamper-evidence *(DR-006, DR-017)*

v1 listed "append-only store + hash chain" as the Tampering mitigation, but the contract defined
`record_hash` as a hash of the record's own fields — which detects nothing, since an attacker
recomputes it. And the Audit Service computed the hash itself, so a compromised Audit Service could
rewrite history freely.

**v2:**
```
record_hash = sha256( JCS(record_without_hash_fields) || prev_hash )
origin_signature = Ed25519_sign(decision_service_key, record_hash)
```
- `prev_hash` links the chain; `seq` is monotonic per tenant. Gaps are detectable.
- The record is **signed by the Decision Service**, the party that made the decision, not by the
  store that keeps it. Compromising the store no longer lets you forge decisions.
- The signing key lives in KMS/HSM and is separate from application secrets.
- Canonicalisation is RFC 8785 JCS and is itself unit-tested — two serialisers that disagree break
  the chain silently, which is the worst failure mode available.
- Chain heads are periodically checkpointed (daily) to a write-once store. Phase 3 optionally
  anchors those checkpoints to a ledger (`13-BLOCKCHAIN_FUTURE_EXTENSION.md`).

## 5. Multi-tenant isolation — designed, not just tested *(DR-042)*

v1's pentest plan asserted "tenant A cannot read tenant B's records" but no document described any
mechanism to make that true.

| Layer | Mechanism |
|---|---|
| Postgres | Row-level security on `tenant_id`, enforced by policy, with the application role unable to `SET ROLE` past it. Every table carries `tenant_id`; a table without one fails CI. |
| Redis | Dedicated instance for feature cache; keys namespaced `t:{tenant}:…`; ACL per tenant credential |
| Kafka | Prefix ACLs per tenant; consumer groups scoped; no wildcard subscriptions in production |
| Object storage | Per-tenant prefix + bucket policy + distinct KMS key |
| Models | Shared weights are fine. **Per-tenant thresholds and policy packs must never be cached across tenant boundaries** — a cache keyed only on `session_id` leaks policy |
| Logs/traces | `tenant_id` is an indexed attribute; log access is itself tenant-scoped |

CI gate: an automated test that runs the full API surface as tenant A against tenant B's IDs and
asserts 404 (not 403 — 403 confirms existence).

## 6. Insider threat

- Every read of a decision record, alert, or embedding is logged with actor, timestamp, and reason
  code where the workflow supports one. This log is append-only and **separate** from the business
  audit trail, so someone covering their tracks has to defeat two systems.
- Alerting on anomalous access: bulk export, off-hours embedding reads, one analyst reading many
  unassigned alerts, any `SELECT` touching more than N embeddings.
- Least privilege: analysts never see raw audio or embeddings. Per the retention policy, raw audio
  should not exist to be seen — but the RBAC must not depend on that being true.

## 7. Threat model (STRIDE, expand at Phase 1 design review)

| Threat | Concrete instance | Mitigation |
|---|---|---|
| Spoofing | Attacker impersonates the Detection Service and returns `p_synthetic = 0.01` | mTLS + SPIFFE identity; fusion rejects results without a valid workload identity |
| Spoofing | Caller ID spoofing to gain "known caller" trust | `03 §4` — caller ID can never produce `VERIFIED` |
| Tampering | Decision record altered post-hoc to justify a block | Hash chain + origin signature + checkpointing (§4) |
| Tampering | Policy weights quietly edited to suppress alerts | `policy_version` on every record; weight changes are audited, dual-control at Phase 2 |
| Repudiation | Tenant disputes a block with no proof of what was known | `contributing_factors` + `applied_floors` + model/policy versions on the audit record |
| Info disclosure | Embedding exfiltration | Encrypted, tenant-keyed, access-logged, bulk-read alerting |
| Info disclosure | **Score oracle** — attacker uses the API to tune an evasive clone | No raw scores to untrusted callers; probing detection (§3) |
| DoS | Call flood exhausts GPU capacity | Admission control, per-tenant quotas, backpressure, degraded-mode floors rather than collapse |
| DoS | **Cost-based DoS** — attacker forces expensive inference at your expense | Per-tenant inference budget with hard cap; see `11-COST_MODEL.md` §5 |
| Elevation | Analyst alters thresholds | RBAC separation + separation of duty (§1) |
| **Model evasion** | Adversarial audio flips the classifier | Input-sanity check, `adversarial_flag` floor of 55, attack-corpus regression |
| **Model inversion** | Embeddings reconstructed toward audio | Embeddings never leave the service boundary; no embedding-returning endpoint exists |

## 8. Supply chain

- Dependency scanning in CI for both stacks: `pip-audit` for Python, `govulncheck` for Go.
- **Pin ML model artefacts by hash**, not by tag. A model registry tag is mutable; a checkpoint is
  the most privileged code you run and it is loaded by a deserialiser. Prefer `safetensors` over
  pickle-backed formats — loading an untrusted pickle is arbitrary code execution.
- The TTS/voice-cloning tooling used for dataset generation runs in an isolated, offline
  environment, is version-pinned, and is never exposed as a network service.
- SBOM generated per release; builds reproducible where feasible.

## 9. Phase-0 minimum (what you actually ship in the hackathon)

Not everything above. This much, and no less:
- [ ] No secrets in the repo. A `.env.example` and a real `.env` in `.gitignore`.
- [ ] No raw audio written to disk — grep the code, then verify by inspecting the filesystem after
      a run (`08 §4.7`).
- [ ] Dashboard behind *any* login, even basic auth. An open dashboard showing voice-fraud alerts
      is a live demo of the wrong thing.
- [ ] Consent forms collected and stored outside the repo.
- [ ] `record_hash` chain implemented — it is ~20 lines and it is the thing that makes the audit
      story real rather than aspirational.
