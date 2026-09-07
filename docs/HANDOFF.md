# VoxGuard — Handoff

> **Living document.** Update it as part of every work unit, not at the end. If you are a new
> session: read `CLAUDE.md` first, then this file, then `docs/12-BUILD_CHECKLIST.md`.

**Last updated:** 2026-09-07 · **Branch:** `codex/voiceshield-mvp` · **Phase:** 0 (Demo MVP)

---

## 1. What this project is

Real-time AI-voice-fraud detection for financial call centres. Live call audio → voiced-window
detection of synthesised speech → explainable fusion into a 0–100 risk score → banded action
(ALLOW / WARN / STEP_UP) → agent dashboard + tamper-evident audit chain.

Risk is **0–100, higher means more verification required**. `LOW` 0–39 · `MEDIUM` 40–69 ·
`HIGH` 70–100 · `UNKNOWN` has no numeric score and renders as **"Not assessed"**.

The `docs/` spec set is **v2**. Its entire purpose is fixing 48 numbered defects in a v1 spec —
chiefly that v1's arithmetic made a HIGH alert *mathematically unreachable*, and that
adversary-controlled inputs (a spoofed caller ID) could *lower* risk. See `15-DEFECT_REGISTER.md`.

---

## 2. Current state — honest assessment

The repo predates the v2 spec set (built from an earlier plan) but independently lands most of
v2's core corrections. **Phase 0 is roughly two-thirds complete.**

| Build-checklist day | State | Notes |
|---|---|---|
| Day 1 — data, disjoint splits, detector skeleton | ❌ **Not started** | No corpora, no splits, no training loop. Only synthetic sine fixtures + Windows TTS. |
| Day 2 — EER, calibration, MC-dropout confidence | ❌ **Not started** | `calibrator_version = "identity-demo@0.0.0-unvalidated"`. No measured FAR/FRR anywhere. |
| Day 3 — fusion + session logic + hash chain | ✅ **Strong** | See §3. |
| Day 4 — interface | ✅ **Mostly done** | See §3. |
| Day 5 — survive the stage | ⚠️ **Partial** | Chaos toggle exists; no model card, no consent log, no scripted demo, no measured numbers. |

### Phase 0 exit gate

| Gate | Status |
|---|---|
| A HIGH alert is reachable and reproducible | ✅ **GREEN** (2026-09-07) — `fixture-steady.wav` → 90.95 HIGH from the detector alone, 21-pt margin. Regression-tested. |
| Killing the detector produces a degraded MEDIUM, never a silent LOW | ✅ Implemented + unit tested |
| `contributing_factors` sums to the base score on screen | ✅ Asserted in code and rendered |
| FAR/FRR measured on a held-out set, stated with sample size | ❌ Not measured |
| No raw audio on disk after a run | ⚠️ Buffer-zeroing tested; no filesystem sweep yet (T-4.7) |
| Demo runs twice consecutively without intervention | ❓ Not rehearsed |

---

## 3. What is actually built

**Backend** (`backend/app/`, FastAPI + NumPy/SciPy/librosa, no PyTorch in the default path):

| Module | State |
|---|---|
| `ingestion.py` | 3.0 s windows, 1.0 s hop, stereo→mono, `resample_poly` to 16 kHz ✅ |
| `preprocessing.py` | DC removal, 80–3800 Hz Butterworth, energy + in-band flatness VAD, noise gate ✅ |
| `features.py` | MFCC, log-mel, YIN pitch, jitter/shimmer proxies, spectral flatness, RMS envelope ✅ |
| `detection.py` | `SpoofClassifier` ABC + `HeuristicClassifier` — **unvalidated**, and see DEF-1 |
| `risk_scoring.py` | `03` §1 evaluation order, active-signal renormalisation, recursive context renormalisation, gated trust discount, floors-last via `max()`, in-code sum assertion ✅ **Faithful to spec** |
| `risk_scoring.SessionRisk` | `04` §3–4 EWMA α=.35 + decaying peak δ=.98 + 2-of-3 / 5-of-6 hysteresis + one-alert-per-escalation w/ SHA-256 idempotency key ✅ |
| `ledger.py` | Real `sha256(payload ‖ prev)` chain per `02` §9.1, SQLite WAL + `synchronous=FULL` ✅ |
| `schemas.py` | Pydantic v2 strict models, `extra="forbid"`, attestation-provenance validator ✅ |

**Frontend** (`frontend/src/`, React 19 + Vite): band glyph vocabulary (● ◆ ▲ ◌), "Not assessed",
persistent degraded strip, factor bars, **applied floors rendered separately from evidence**,
versions always visible, `aria-live` split correctly (assertive for band, off for score ticks),
`prefers-reduced-motion`, "Live assessment paused — reconnecting". Conforms well to `09`.

**Tests** (`backend/tests/test_pipeline.py`): **23 tests, all passing** as of 2026-09-07. See
`docs/verification.md` for the run record.

---

## 4. Defects found in this codebase

**All six are fixed as of 2026-09-07**, each with a regression test. Kept here because the *reason*
each existed is the useful part — these are the mistakes this design invites, and a future session
(or a reviewer asking "did you check X?") needs to see they were found deliberately.

### DEF-1 — HIGH band was mathematically unreachable ✅ FIXED *(was blocking the Phase 0 exit gate)*

**Fix:** log-axis remap of the spectral term (`detection.py`, `FLATNESS_TONAL`/`FLATNESS_NOISY`)
so the declared `[0.05, 0.95]` range is attainable, plus YIN octave-error rejection in
`features.extract`. `heuristic-acoustic@1.1.0` → `@1.2.0`.
**Guarded by:** `test_detector_output_range_is_attainable`,
`test_phase0_high_is_reachable_from_the_detector_alone`, `test_pitch_outliers_do_not_dominate_prosody`.

`HeuristicClassifier.score_features` computes:

```
spectral = clip(0.3 + (0.015 - flatness) * 12, 0.1, 0.9)
```

Spectral flatness is **≥ 0 by definition**, so this term maxes at **0.48** — the entire upper half
of the clip range is unreachable. With `prosody` capped at 0.95:

```
p_synthetic ceiling  = 0.55*0.48 + 0.45*0.95 = 0.6915
detector-only base   = 100 * (0.5 + (0.6915-0.5)*0.95) = 68.19    # HIGH needs >= 70
```

HIGH is therefore only reachable when context signals drag it over the line, and even then the
maximum is **70.6** — a 0.6-point margin. It has already silently fallen to MEDIUM, failing
`test_api_and_stream`.

**This is DR-003 reintroduced one layer down.** v2 fixed unreachable-HIGH in the fusion; the
detector quietly put it back. The fusion is not at fault — the renormalisation unit test passes
and correctly yields 92.75 for `p_cal=0.95, conf=0.95`.

**Compounding bug:** a single YIN outlier (first frame reads 457 Hz on a constant 170 Hz tone)
inflated `pitch_cv` from ~0 to 0.1355 and cratered the prosody score.

**Worse than first described:** measured `spectral_flatness` is ~1e-6 on *every* fixture, so the
linear `0.015` threshold pinned `spectral_score` at a constant **0.48 regardless of input** — the
term carried no discriminative information at all. Measured before/after in `docs/verification.md`.

### DEF-2 — `language_supported` is hardcoded `true` while `language` is `"und"`

`main.analyze` emits `{"language": "und", "language_supported": True}`. `05` §4 is explicit that
`und` must be treated as **unsupported** until enough audio arrives, sending the AI signal inactive
with floor 40. This was a live DR-023 violation.

✅ **FIXED.** `language` is now an operator-asserted field on `POST /stream/start`, validated
against `POLICY["supported_languages"]` (`en, hi, ta, te, bn, hi-en` — the D-4 set). Out-of-set
values gate the AI signal off and apply floor 40. We assert rather than detect because this build
runs no language ID, and claiming detection would be a false capability claim.
**Guarded by:** `test_unsupported_language_gates_the_detector_off` (T-6.6).

### DEF-3 — `adversarial_flag` is always `false`

Nothing ever set it, so the floor-55 path in `03` §5 was dead code outside unit tests.

✅ **FIXED.** `simulate_adversarial_input` on `POST /stream/start` drives the floor end to end, and
the response carries `adversarial_flag_source: "simulated"` so a viewer can never mistake it for a
detection. Real input-sanity checking remains Phase 2 (`05` §6).
**Guarded by:** `test_simulated_adversarial_input_reaches_the_floor` (T-2.2 end to end).

### DEF-4 — `degraded_threshold_delta` (`03` §6) is unimplemented

`high_min` was never lowered when context is unavailable.

✅ **FIXED.** `band_for(score, policy, context_degraded)` lowers `high_min` by
`degraded_threshold_delta` (10), `score_window` emits `context_degraded`, and `SessionRisk` carries
the same delta through the hysteresis and de-escalation ceiling — otherwise the window would say
HIGH while the session quietly disagreed.
**Guarded by:** `test_context_failure_lowers_the_high_threshold`,
`test_session_band_honours_the_degraded_threshold`.

### DEF-5 — Policy pack misnames itself

`POLICY["version"] = "banking-demo@2.0.0"` but the weights are ai .60 / speaker .20 / context .20
with bands 40/70. The `03` §7 banking pack is ai .45 / speaker .30 / context .25, bands 35/65,
`mandatory_speaker_verification: true`. Per **D-5** we keep the current weights (they suit a
detector-only Phase 0 with no enrolment) but must rename and document the deviation.

✅ **FIXED.** Renamed to **`demo-detector-first@2.1.0`** with the rationale in a comment above
`POLICY`. Deviation recorded as DEV-1.
**Guarded by:** `test_policy_version_no_longer_claims_to_be_the_banking_pack`.

### DEF-6 — `docs/verification.md` is stale

Claimed 7 tests passing; there were 14, one of which failed.

✅ **FIXED.** Rewritten as a dated verification record with the exact commands, the before/after
measurements for DEF-1, and an explicit "Not yet verified" section stating that **no
spoof-detection performance has been measured at all**.

---

## 5. Not built at all

| Subsystem | Spec | Notes |
|---|---|---|
| Speaker Verification + enrolment | `02` §4, `09` §4.4 | Fusion fully supports it; nothing produces a `VerificationResult`. |
| Decision Service | `02` §8 (DR-013) | Currently a 4-entry dict. No `required_actions`, `reason_code`, override endpoint, or WAL. |
| Alert resolve outcomes | `02` §10 | No CONFIRMED_FRAUD / FALSE_POSITIVE / INCONCLUSIVE → no human-in-the-loop feedback loop (`05` §9). |
| Appeal flow | `09` §5 | This is a **compliance control**, not a nice-to-have. |
| Diarisation / multi-track | `04` §6, `05` §5 | Single track only. |
| `SessionSummary` on close | `04` §7 | Only a `call_completed` ledger row. |
| Model card | `05` §10 | Absent. |
| Live call ingest | `01` §2 | File simulation only. **D-2** targets Asterisk/AudioSocket. |

---

## 6. Environment (verified 2026-09-07)

| Resource | Status | Implication |
|---|---|---|
| GPU | **RTX 4060 Laptop, 8 GB VRAM, CUDA 13.1** | Encoder+head fine-tuning comfortable. 7B audio LLM needs 4-bit QLoRA and will be tight. |
| PyTorch | **2.11.0+cu128, `cuda.is_available() == True`** | Ready. |
| RAM | 15.6 GB total, **1.9 GB free at check time** | Close apps before training. |
| Disk | C: **22.7 GB free (tight)** · D: 205 · E: 243 · F: 237 | **Datasets go on E:, never C:.** Repo is on F:. |
| Docker / WSL2 | Docker 29.5.3 · WSL2 Ubuntu (stopped) | Asterisk-in-Docker path viable. |
| ffmpeg | 8.1.1 | G.711 / Opus / AMR-NB codec laundering (T-5.5). |
| git-lfs | 3.7.1 | HF dataset pulls. |
| Missing | `datasets`, `peft`, `bitsandbytes`, `accelerate` | Install before the training track. |

---

## 7. Datasets — researched, licences checked

**Genuine speech**

| Dataset | Licence | Coverage |
|---|---|---|
| IndicVoices (AI4Bharat) | **CC BY 4.0** — commercial OK | 22 Indian languages, 23.7k hrs, conversational |
| Mozilla Common Voice | **CC0** | Broad multilingual incl. Indian languages |

**Spoof / deepfake**

| Dataset | Licence | Coverage |
|---|---|---|
| ASVspoof 2019 LA | **ODC-By** — commercial OK w/ attribution | English, the standard benchmark |
| In-The-Wild | **Apache 2.0** | 37.9 hrs (20.8 real / 17.2 fake), 58 English public figures |
| MLAAD v10 | ⚠️ **CC BY-NC 4.0 — research only** | 175 TTS models, 1002.9 hrs, 54 languages, **183 GB** |
| ASVspoof 5 (2024) | Verify `LICENSE.txt` at download | ~2000 crowdsourced speakers, incl. adversarial attacks |

**Code-switching:** MUCS 2021 (~600 hrs), IIT-Guwahati Hindi-English CS corpus, SPRING-INX.

### The multilingual gap — and the plan for it

MLAAD does not clearly cover Hindi/Tamil/Telugu/Bengali, and **no off-the-shelf Indian-language
spoof corpus exists**. So the synthetic side for the D-4 target languages must be **generated
in-house** with:

- **Indic Parler-TTS** — Apache 2.0, 20 languages incl. Hindi, Tamil, Telugu, Bengali, English
- **IndicF5** — 11 Indian languages, reference-prompt voice cloning

This is real day-1 work but it is also the project's genuinely novel contribution, and it yields
**generator-disjoint splits by construction**.

> **Ethics binding (invariant 14):** IndicF5 voice cloning may only ever be pointed at
> open-corpus / consented speakers logged in `docs/CONSENT_LOG.md`. Never a real person otherwise.

### Known results, to calibrate expectations

Published numbers: wav2vec2 **XLS-R + AASIST** reaches **0.82% EER in-domain** on ASVspoof 2019 LA
but **42.6% EER on a multilingual evaluation set** — near coin-flip out of domain. Off-the-shelf
audio LLMs are worse than guessing (Qwen-audio-base 11.34% accuracy; Qwen-audio-chat labels
everything real; GPT-4o ~0% and appears post-trained to refuse the task). **Fine-tuned**,
Qwen-Audio reaches 99.40% on ASVspoof2019 LA — which is why **D-1** chose that route.
Expect the multilingual number to be far worse than the English one. **Report both.**

---

## 8. Deviations from spec — declared, not hidden

| ID | Deviation | Reason | Spec ref |
|---|---|---|---|
| DEV-1 | Policy weights ai .60 / .20 / .20, bands 40/70 | Suits detector-only Phase 0 with no enrolment; `mandatory_speaker_verification: true` would floor every window at 50 | `03` §7 |
| DEV-2 | Detector latency will exceed 800 ms | D-1 chose a 7B audio LLM on an 8 GB laptop GPU | `01` §4, `[ASSUMPTION-2]` |
| DEV-3 | Single Python process + React app, own `/api/v1` surface | Phase 0 simplification the spec explicitly endorses | `00` §6, `12` §1 |
| DEV-4 | Model weights research-only | MLAAD CC BY-NC (D-3) | `05` §1 |

---

## 9. Next actions (ordered)

1. ~~Fix DEF-1~~ ✅ done 2026-09-07 — exit gate green.
2. ~~Fix DEF-2…DEF-6~~ ✅ done 2026-09-07.
3. Label existing tests with `08` T-IDs; add the missing hackathon-eleven plus T-2.1, T-2.4,
   T-2.5, T-2.7, T-3.2, T-3.4, T-3.5, T-3.7, T-4.2, T-4.7 (filesystem sweep for audio).
4. Install the training stack (`datasets`, `transformers`, `peft`, `bitsandbytes`, `accelerate`).
5. Dataset acquisition to **E:** — ASVspoof 2019 LA, In-The-Wild, MLAAD (language subset),
   IndicVoices / Common Voice for the six D-4 languages.
6. In-house synthetic generation for Hindi/Tamil/Telugu/Bengali/Hinglish via Indic Parler-TTS.
7. Speaker-, generator-, channel-, and language-disjoint split loader with a leakage test (T-6.3).
8. Qwen2-Audio QLoRA training run; calibrator on a held-out split; ECE ≤ 0.05 (T-6.1).
9. Asterisk-in-Docker + AudioSocket live call ingest.
10. Model card with per-subgroup numbers and the out-of-scope section.

---

## 10. Changelog

| Date | Change |
|---|---|
| 2026-09-07 (2) | **DEF-1…DEF-6 all fixed**, each with a regression test. Detector range made attainable (log-axis flatness map + YIN outlier rejection) → HIGH reachable from the detector alone at 90.95, 21-pt margin. Language gating, simulated adversarial floor, degraded threshold delta, policy rename to `demo-detector-first@2.1.0`. Consent-log gate added and **proven to bite**. Suite 14 → **23 passing**. `verification.md` rewritten. |
| 2026-09-07 | Handoff created. v2 spec set imported to `docs/`. `CLAUDE.md`, `CONSENT_LOG.md`, `CAPABILITY_MATRIX.md` written. Codebase audited against spec; DEF-1…DEF-6 recorded. Environment and dataset licences verified. Decisions D-1…D-6 confirmed with the human. |
