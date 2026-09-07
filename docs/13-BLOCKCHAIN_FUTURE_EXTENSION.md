# Ledger / Blockchain Audit Layer — Phase 3, Optional (v2)

> Fixes DR-040. v1 promised that adding this would require "zero changes to any other service", and
> then defined an interface that could not survive the batching it admitted would be necessary.

## 0. Read this first

The system is complete, demonstrable, and production-viable **without** this layer. If you are
building a hackathon demo, skip this document entirely and spend the time on
`04-SESSION_SCORING_SPEC.md` and `09-UX_SPEC.md`, which affect whether the product works.

Mentioning blockchain in a pitch without a specific reason it is needed is a negative signal to a
technical audience. The honest position — *"the audit chain is cryptographically verifiable today;
anchoring it externally is a Phase 3 option for consortium deployments"* — is stronger than a
half-built ledger.

## 1. The plug-in point

Everything upstream talks only to `POST /v1/audit-records` (`02-API_CONTRACTS.md` §9). Internally
the Audit Service depends on one interface.

### The v1 interface would have broken *(DR-040)*
```
// v1
AuditBackend {
  write(record) -> hash
  verify(hash) -> bool
}
```
Two problems, both fatal to the doc's own promise:
1. `verify(hash) -> bool` only confirms a hash exists. It cannot confirm a *record* matches its
   hash, which is the actual question. You can verify a fabricated hash successfully.
2. v1 correctly noted that Merkle batching would be necessary at call volume — but a batched anchor
   requires an **inclusion proof**, which a `bool` cannot carry. Adopting batching would have forced
   a breaking change to the very interface that existed to prevent one.

### The v2 interface, designed to survive Phase 3
```go
type AuditBackend interface {
    // Append. Returns the chained hash and any anchoring reference.
    Write(ctx, record AuditRecord) (WriteReceipt, error)

    // Verify a RECORD (not a bare hash) against the chain and any external anchor.
    Verify(ctx, record AuditRecord) (VerificationResult, error)

    // Range verification for auditors.
    VerifyRange(ctx, tenantID string, fromSeq, toSeq uint64) (RangeResult, error)
}

type WriteReceipt struct {
    Seq        uint64
    RecordHash string          // sha256( JCS(record) || prev_hash )
    PrevHash   string
    Anchor     *AnchorRef      // nil until anchored; anchoring is ASYNC by design
}

type AnchorRef struct {
    Type       string          // "postgres_checkpoint" | "merkle_ledger" | "tsa_rfc3161"
    BatchRoot  string
    Proof      []string        // Merkle inclusion path — empty for non-batched backends
    ExternalID string          // tx hash / checkpoint id
    AnchoredAt time.Time
}

type VerificationResult struct {
    ChainIntact  bool
    HashMatches  bool
    SignatureOK  bool
    AnchorStatus string        // "none" | "pending" | "confirmed" | "mismatch"
    FailedAtSeq  *uint64
}
```

`Anchor` is nullable and anchoring is **asynchronous by contract**. This is what lets a batched
ledger backend drop in without changing a single caller — the field was always there, always
optional. That is the difference between a stable interface and a stated intention.

## 2. Implementations

| Phase | Backend | Behaviour |
|---|---|---|
| 0–2 | `PostgresAuditBackend` | Hash-chained append + Ed25519 origin signature + daily checkpoint to write-once storage. `Anchor.Type = "postgres_checkpoint"` |
| 2.5 | `TimestampAuthorityBackend` | RFC 3161 timestamps on daily Merkle roots. **Achieves most of the third-party-attestation benefit at near-zero cost and complexity** — evaluate this before reaching for a ledger |
| 3 | `MerkleLedgerBackend` | Same Postgres write, plus periodic Merkle-root anchoring to a permissioned ledger. Runs alongside the others during migration |

`TimestampAuthorityBackend` deserves emphasis: for most threat models the actual requirement is
"prove this record existed at this time and has not changed since," which an RFC 3161 timestamp
authority satisfies for a few dollars a month. Reach for a ledger only when the requirement is
specifically *multi-party* trust — several banks who do not trust each other or you.

## 3. What goes on-chain — and what never does

**On-chain:** a Merkle root, a batch time range, and a tenant identifier. That is all.

**Never on-chain:** raw audio, embeddings, caller PII, transaction details, risk scores, decisions,
or session identifiers.

Two reasons, and the second is the one people miss:
1. A public ledger is a permanent, global, unerasable publication. Any personal data placed there
   cannot be erased, which is directly incompatible with `06-DATA_PRIVACY_COMPLIANCE.md` §3 and with
   the erasure right under DPDP and GDPR.
2. Even a hash of personal data can be a personal-data processing operation where the input space
   is small enough to brute-force. A hash of a phone number is not anonymous — a phone number has
   ~10^10 possibilities, which is minutes of compute. **Anchor Merkle roots over batches, never
   hashes of individual identifiers.**

## 4. Decisions deferred to Phase 3 start

- **Permissioned vs public.** Permissioned (a consortium of partner banks/telcos) fits cost,
  throughput, and regulatory comfort. Decide with real pilot-partner input, not speculatively.
- **Node operation.** Who runs nodes determines the trust model. If you run all of them, the ledger
  proves nothing a signed checkpoint did not already prove more cheaply — and that is worth being
  honest about before building it.
- **Cost.** Batch size vs anchoring frequency vs proof latency. At carrier volume, one anchor per
  record is impossible; a 10-minute batch at 5M calls/day is trivial.
- **Key management.** HSM/KMS-backed, separate from application secrets (`07 §2`).
- **Legal weight.** An anchored hash proves integrity, not truth. It proves the record has not
  changed; it does not prove the decision was correct. Do not let anyone market it as the latter.

## 5. Rollout

1. Implement against the existing interface in isolation.
2. **Shadow mode**: write to both backends, compare receipts, rely on neither.
3. Per-tenant opt-in cutover, with instant fallback to Postgres-only.
4. Verify the disable path: turning the backend off at runtime must have zero downstream effect.
   If it does not, the abstraction failed and the whole premise of this document is void — test it
   before you need it.
