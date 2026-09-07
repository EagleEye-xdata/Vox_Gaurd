# Verification record

> Every row states the command, the date it was run, and the observed result. Numbers here are
> **observations on one machine**, never guarantees. If you change code, re-run and update this
> file — a stale verification record is worse than none.

## Run — 7 September 2026

Machine: Windows 11, Python 3.11.9, i7-13620H, RTX 4060 Laptop (unused by these tests).

| Check | Command | Result |
|---|---|---|
| Backend test suite | `python -m pytest tests -q` (from `backend/`) | **23 passed**, 1 third-party deprecation warning (librosa `resources.path`). No application failures. |
| Consent gate bites | Dropped an unreferenced `.wav` into `demo_audio/`, re-ran `-k consent` | **Failed as designed**: `Unconsented voice audio present: someones-voice.wav: no consent-log reference in the filename`. File removed; suite green again. |
| Detector range | `test_detector_output_range_is_attainable` | `p_synthetic` spans **0.05 – 0.95**; both ends reachable. |
| Phase 0 exit gate | `test_phase0_high_is_reachable_from_the_detector_alone` | `fixture-steady.wav` → **90.95, band HIGH from the detector alone** (21-point margin over the 70 threshold). |

### Measured pipeline values (`fixture-steady.wav`, first 3 s window)

| Quantity | Before DEF-1 fix | After |
|---|---|---|
| `spectral_flatness` | 1.4e-06 | 1.4e-06 (unchanged — the input, not the bug) |
| `spectral_score` | 0.48 (constant for **every** input) | **0.9196** |
| `pitch_cv` | 0.1355 | **0.0001** |
| `prosody_score` | 0.5979 | **0.945** |
| `p_synthetic` | 0.5331 | **0.9311** |
| Detector-only window score | 68.19 ceiling → MEDIUM | **90.95 → HIGH** |

`fixture-variable.wav` → 69.21 MEDIUM detector-only, 71.38 HIGH with transaction context. The
steady-vs-variable contrast the demo narrative relies on is preserved.

## 2026-09-07 (3) — Go/Python split

Backend split into a Go gateway and a Python ML sidecar per `01` §2. Commands and results:

```
$ go -C gateway test ./...
ok  internal/alerts · internal/decision · internal/httpapi · internal/ledger
ok  internal/numeric · internal/schema · internal/scoring
162 passing

$ python -m pytest backend/tests -q
15 passed
```

**Differential evidence for the fusion port.** `backend/tools/emit_golden.py` was run against the
pre-port `backend/app/risk_scoring.py` and emitted 91 window cases and 8 session replays into
`gateway/internal/scoring/testdata/golden_windows.json`. The Go implementation reproduces every
one exactly, field for field, including `contributing_factors` weights and points to 6 decimal
places. Cases cover each invariant in `CLAUDE.md` §2–7, every context sub-signal subset, the
transaction log curve across ten values, and a 48-point dense sweep of `p_synthetic × confidence`
that exists to catch rounding drift the named cases would miss.

`internal/numeric.Round` reproduces CPython's round-half-to-even rather than using `math.Round`,
which rounds half away from zero. On `round(2.675, 2)` the two differ (2.67 vs 2.68); on a window
sitting on a band threshold that difference changes the band.

**Live end-to-end run**, both services up, real fixture audio:

| Check | Result |
|---|---|
| `fixture-steady.wav`, 22 windows scored | **80.51 HIGH**, decision `STEP_UP` |
| Active-signal renormalisation | `ai_synthetic` weight **0.75**, `context_risk` **0.25** — speaker inactive and its weight removed from the denominator |
| `contributing_factors` sum vs `base_score` | 80.507542 vs 80.507542 — exact |
| Band timeline | UNKNOWN → MEDIUM at window 2 → HIGH at window 3 |
| Alerts raised over 22 HIGH windows | **2** — one per escalation |
| Audit chain | 25 records, `{"valid": true, "reason": "Chain verified"}`, every record carrying `policy_version`, `model_versions` and an HMAC origin signature |
| Audio-shaped keys anywhere in the ledger | none |
| `simulate_detector_failure` | **49.17 MEDIUM**, `degraded_reasons: [detector_unavailable]`, floor 40 applied — never a silent LOW; no detector score shown in the UI payload |
| `language: fr` | `language_supported: false`, **49.17 MEDIUM**, `degraded_reasons: [unsupported_language]` |
| WebSocket `/ws/audio/{id}` | 23 events, terminated with `complete`, session summary attached, no audio-shaped keys on the wire |

## 2026-09-08 — scripted attacker over the live AudioSocket path

The demo caller from `implementation_plan.md` Phases 1–2: `backend/app/elevenlabs_agent.py`
(script + voice, with the invariant-14 gate) and `backend/app/ai_caller.py` (AudioSocket client).

```
$ go -C gateway test ./...        all packages ok, 166 PASS lines incl. subtests (+3 AI-call tests)
$ python -m pytest backend/tests -q    26 passed   (was 15; +10 new, 1 fake updated)
$ go -C telephony build ./...     ok
```

**Live end-to-end run**, real sidecar + real gateway + real AudioSocket frames, persona
`bank_verification_hinglish` at 4× real time:

| Check | Result |
|---|---|
| Session created from the AudioSocket UUID frame | `969fa58f-…`, label `AI attacker demo - Bank verification (Hinglish)`, language `hi-en` |
| Windows scored | 11, band timeline UNKNOWN → … → **HIGH** |
| Final session score | **74.88 HIGH**, EWMA 74.88, peak 74.49 |
| Decision | **BLOCK**, reason `SYNTHETIC_HIGH_VALUE_OR_NEW_BENEFICIARY_RISK`, WAL seq 1022, HMAC origin signature present |
| Alerts | **2** — one per band escalation (MEDIUM, then HIGH), not one per window |
| Ledger | 14 records including `call_completed`, hash-chained |
| Audio-shaped keys in the forwarded windows | none |

> **The voice in that run was the local fallback, not ElevenLabs.** `ELEVENLABS_API_KEY` is not
> set on this machine, so the attacker was the deterministic DSP signal
> `local-synthetic@1.0.0`. **74.88 is therefore a measurement of the pipeline, not of the
> detector against real synthesised speech.** How this detector scores actual ElevenLabs audio is
> **unmeasured**, and `implementation_plan.md` §5's expected "85–95" is an expectation, not a
> result. The ElevenLabs request path is covered by tests against a fake HTTP client only; it has
> never run against the live API.

| Invariant-14 gate | Result |
|---|---|
| Voice with category `cloned` | refused — `VoiceNotPermitted`, message cites `docs/CONSENT_LOG.md` |
| Voice with category `professional` | refused |
| Voice whose category cannot be fetched (network error) | **refused** — an unverifiable voice is not a permitted one |
| Refusal path | falls back to the local synthesiser **and reports the refusal** as `fallback_reason` in the API and on the dashboard |

## Not yet verified

- `npm run build` — **run 2026-09-08, built in 22.3 s with no errors** (the AI-caller panel compiles).
- The AI-caller panel has **not been clicked through in a browser** this session; only the build and the API behind it were exercised.
- **`go test -race` has never been run.** No 64-bit C toolchain exists on this machine; see
  `CAPABILITY_MATRIX.md` Gaps. The gateway's concurrency (one goroutine per call, the shared
  alert store and event log) is guarded by review and by the `httptest` suite only. One race was
  found and fixed by review — `call.cancel` was written after the call became reachable — but
  review is not a substitute for the detector, and the gateway must not be described as
  race-free until it has been run.
- Browser walkthrough — not re-run this session.
- **No genuine-vs-cloned speech benchmark exists yet.** These checks establish pipeline behaviour
  only, **not spoof-detection performance**. There is no measured FAR/FRR, no EER, and no
  calibration. Any accuracy claim at this point would be fabricated.

## History

| Date | Result |
|---|---|
| 2026-09-08 | **All Go packages ok (166 PASS lines incl. subtests) + 26 Python passed.** Scripted attacker reaches HIGH/BLOCK over the live AudioSocket path. Detector performance against real TTS still unmeasured. |
| 2026-09-07 (3) | **162 Go + 15 Python passed.** Backend split per `01` §2; fusion port verified against 99 golden vectors from the Python reference. |
| 2026-09-07 | 23 passed. DEF-1…DEF-6 fixed; consent gate added and proven to bite. |
| 2026-09-06 | 7 passed. Superseded — the suite has since grown to 23 and one test was failing when re-run on 09-07 (see DEF-1). |
