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

## Not yet verified

- `npm run build` — not re-run this session (`node_modules` not installed).
- Browser walkthrough — not re-run this session.
- **No genuine-vs-cloned speech benchmark exists yet.** These checks establish pipeline behaviour
  only, **not spoof-detection performance**. There is no measured FAR/FRR, no EER, and no
  calibration. Any accuracy claim at this point would be fabricated.

## History

| Date | Result |
|---|---|
| 2026-09-07 | 23 passed. DEF-1…DEF-6 fixed; consent gate added and proven to bite. |
| 2026-09-06 | 7 passed. Superseded — the suite has since grown to 23 and one test was failing when re-run on 09-07 (see DEF-1). |
