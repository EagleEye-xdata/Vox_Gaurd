# Session Scoring Specification (NEW in v2)

> Fixes DR-008 (the largest single gap in v1), DR-027, DR-047, DR-048.

## 1. Why this document exists

v1's `DetectVoice(AudioChunk)` returned a **per-chunk** probability. v1's `RiskResult` was a
**per-session** score. Nothing anywhere described how you get from one to the other.

That gap silently determined everything: whether the score updates during a call, how many alerts
fire, what the latency actually is, and how noisy the system feels. Left unspecified, the default
implementation an engineer or coding agent will write is "alert whenever a chunk exceeds the
threshold" — which produces an alert storm on every long call and makes the product unusable.

## 2. Time model

| Term | Value | Notes |
|---|---|---|
| Frame | 20 ms | codec-native |
| **Analysis window** | 3.0 s of **voiced** audio | `[ASSUMPTION-4]` |
| **Hop** | 1.0 s | → a new verdict roughly every second of speech |
| Warm-up | first `min_voiced_seconds` (3.0 s) | band = `UNKNOWN` until met *(DR-048)* |
| Session | one call leg, one `session_id` | multi-speaker → one track per speaker |

Windows count **voiced** seconds only. Silence, hold music, IVR tones, and ringback contribute
nothing — a 40-second call with 4 seconds of speech has scored one window, not forty. *(DR-027)*

## 3. Aggregation

Per speaker track, maintain:

```
ewma_t   = α · window_score_t + (1 − α) · ewma_{t−1}        α = 0.35
peak_t   = max(peak_{t−1} · δ, window_score_t)               δ = 0.98   # slowly decaying peak
session_score_t = max(ewma_t, peak_t − peak_discount)        peak_discount = 8
```

**Rationale.** A pure mean lets a 30-second genuine conversation dilute a 4-second synthetic
insert — which is exactly the real attack (a cloned voice reads the account number, a human
handles the small talk). A pure max fires on a single noisy window. The decaying peak remembers
that something bad happened without letting one frame define the whole call forever.

`α`, `δ`, and `peak_discount` are policy-pack fields.

## 4. Hysteresis — the alert-storm fix

A band change requires **N-of-M** consecutive windows, and escalation and de-escalation are
deliberately asymmetric.

| Transition | Rule | Default |
|---|---|---|
| Escalate (band ↑) | N of the last M windows have `session_score` in the higher band | 2 of 3 |
| De-escalate (band ↓) | N_down of the last M_down windows below the lower band's ceiling **minus** `deescalate_margin` | 5 of 6, margin 5 |
| Floor-triggered escalation | **Immediate**, bypasses hysteresis | `adversarial_flag`, `replay_suspected` |

Escalate fast, de-escalate slowly. A call that touched HIGH does not quietly return to LOW because
the attacker went quiet for three seconds.

State machine per track:
```
WARMUP ──(voiced ≥ 3s)──► STEADY ──(N of M above)──► ESCALATING ──(confirmed)──► ESCALATED
   │                         ▲                                                      │
   └──────────(insufficient audio)                    (N_down of M_down below) ─────┘
```

## 5. Alert emission *(DR-008)*

- **One alert per session per band escalation.** Idempotency key
  `sha256(session_id + track + band + escalation_seq)`.
- Subsequent windows in the same band **update** the existing alert (score, evidence, factors).
  They do not create new alerts and do not re-notify.
- Re-notification happens only on a *further* escalation (MEDIUM → HIGH) or an SLA breach.
- De-escalation appends a status update; it never silently closes an alert. A human closes alerts.

Worst case notification volume for a 10-minute call is therefore **two** notifications (MEDIUM,
then HIGH), not six hundred.

## 6. Multi-speaker sessions *(DR-015)*

Diarisation produces tracks. Rules:

1. Each track is scored independently — its own EWMA, peak, and hysteresis state.
2. **Session band = max over tracks.** One synthetic participant compromises the call.
3. Speaker Verification runs only against the track asserting the claimed identity.
4. Tracks shorter than `min_voiced_seconds` are `UNKNOWN` and excluded from the max — they cannot
   raise the band, and they cannot lower it either.
5. Track churn (diarisation re-labelling mid-call) merges history by embedding similarity rather
   than starting a new track, otherwise hysteresis resets and the storm returns.

## 7. Session finalisation

On `close`, emit a `SessionSummary`:
```json
{ "session_id": "...", "final_band": "HIGH", "peak_score": 84, "final_score": 71,
  "windows_scored": 143, "voiced_seconds_total": 146.0,
  "band_timeline": [ {"t": 0.0, "band":"UNKNOWN"}, {"t": 3.0, "band":"LOW"},
                     {"t": 41.0, "band":"MEDIUM"}, {"t": 58.0, "band":"HIGH"} ],
  "tracks": [ {"track":"spk_1","peak":84}, {"track":"spk_2","peak":22} ],
  "degraded_window_fraction": 0.02 }
```

`band_timeline` drives the UI scrubber in `09-UX_SPEC.md` §4.3 and is the single most persuasive
artefact in a live demo — it shows *when* the system noticed, not just that it did.

## 8. Interaction with the latency budget

The `≤800 ms` target in `01-ARCHITECTURE.md` §4 is **window-close → verdict**, not call-start →
verdict. Total time to first meaningful verdict:

```
min_voiced_seconds (3.0 s of actual speech)  +  ≤800 ms pipeline  ≈ 3.8 s of speech
```

State this honestly *(DR-047)*. "Detects deepfakes in under a second" is false and a technical judge will
catch it. "Produces a first verdict after ~4 seconds of speech, then updates every second" is true
and still impressive.

## 9. Required tests

- **Storm test**: 10-minute synthetic call held at HIGH → assert exactly 2 notifications.
- **Flap test**: score oscillating across a band boundary every window → assert ≤1 band change.
- **Dilution test**: 4 s synthetic inside 60 s genuine → assert session reaches ≥ MEDIUM. This is
  the test a pure-mean implementation fails, and the attack it fails on is the realistic one.
- **Silence test**: 5-minute call, 3 s of speech → assert `UNKNOWN`, never `LOW`.
- **Warm-up test**: verdict requested at t=1 s → assert `UNKNOWN`, never `LOW`.
- **Multi-speaker test**: 2 tracks, one synthetic → assert session band = HIGH.
