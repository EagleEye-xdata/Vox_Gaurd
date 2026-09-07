# VoiceShield AI

Local voice-cloning detection **demonstration** for SIH26104 (AICTE), team vox_Guard. Implements the supplied architecture plan's pipeline, with the pasted build prompt's local-MVP boundaries. The reference PDF informs temporal consistency, feature coverage, secondary verification, and evaluation priorities.

## Services

The backend is split the way `docs/01-ARCHITECTURE.md` §2 specifies, in two processes:

| Process | Port | Owns |
|---|---|---|
| **Go gateway** (`gateway/`) | 8000 | API, schema validation, risk fusion, session aggregation, decision service, alerts, audit ledger, WebSocket |
| **Python ML sidecar** (`backend/app/sidecar.py`) | 8801 (loopback) | Preprocess, VAD, feature extraction, spoof detector, speaker verification |
| Vite dev server (`frontend/`) | 5173 | Dashboard; proxies `/api` and `/ws` to the gateway |

**Audio never leaves the Python process.** The gateway asks the sidecar for the *analysis* of the
next window and receives derived features and scores. The two endpoints that do carry caller audio
(`POST /api/v1/detect`, `POST /api/v1/enrolments`) are reverse-proxied straight through without
being decoded in Go.

## Run on Windows

Python 3.11+, Go 1.27+, and Node.js 20+ are required.

```powershell
winget install GoLang.Go
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe backend/generate_fixtures.py
cd frontend
npm ci
```

`backend/requirements-lock.txt` records the exact Python versions used for verification on Windows/Python 3.11; install it instead of `requirements.txt` to reproduce that environment.

`./start.ps1` from the project root builds the gateway, starts all three services in the
background, and stops them when you press Enter in the launcher terminal. It refuses to take over
occupied ports.

To run them by hand, in three terminals:

```powershell
# backend/
..\.venv\Scripts\python.exe -m app.sidecar

# repo root
go -C gateway run ./cmd/voxguard

# frontend/
npm run dev
```

Open http://localhost:5173. Gateway health: http://127.0.0.1:8000/api/v1/health — it reports
`status: degraded` when the sidecar is unreachable rather than pretending to be healthy with a
silently absent detector. Use one gateway process; active calls and alerts are in process memory.
Do not expose this unauthenticated prototype beyond loopback.

## Demo walkthrough

1. The generator supplies steady, variable-pitch, and silence WAV **test signals**. None is genuine speech or a cloned person. They demonstrate processing and alert mechanics only.
2. Click **Run simulation**, choose `fixture-steady.wav`, leave unknown caller and ₹250,000 context, and start. The regular test signal should produce sustained high risk under the heuristic.
3. Observe overlapping 3-second windows with a 1-second hop, the RMS energy envelope, active factors, session risk, and measured processing latency. Cold-start library compilation may make the first window much slower.
4. Two of three qualifying windows move a session upward through Elevated (40–69) and High (70–100). Inspect the verification queue and escalate to record a local review event. No call is auto-blocked and no message is sent.
5. Verify the audit chain. Run `fixture-variable.wav` with a known number and no transaction context for a different signal pattern. Run silence to verify skipped windows produce no risk score.
6. Add consented genuine and AI-cloned `.wav` recordings to `demo_audio/`. The source list refreshes automatically. Mono/stereo, ≤96 kHz, ≤5 minutes. Filenames never determine the detection score.

Optional: run `./backend/generate_speech_samples.ps1` from any directory to create three Windows TTS scenario recordings. Every generated filename begins with `tts-`; all are synthetic test material and must not be presented as genuine or cloned-human ground truth.

## Architecture and implementation

| Stage | Implementation |
|---|---|
| Ingestion | SoundFile reads overlapping 3-second windows with a 1-second hop; SciPy resamples to mono 16 kHz |
| Preprocessing | 80–3800 Hz band-pass, frame energy + in-band spectral flatness VAD, gentle noise gate |
| Features | librosa MFCCs, log-mel summary, YIN pitch contour, frame-based jitter/shimmer proxies, spectral peaks, RMS envelope |
| Detection | `SpoofClassifier.predict(chunk) -> float`; unvalidated `HeuristicClassifier` fallback |
| Fusion | Versioned active-signal weights (AI .60, speaker .20, context .20), renormalization, confidence shrinkage, and policy floors |
| Temporal risk | EWMA α=.35 plus decaying peak memory; 2-of-3 escalation and 5-of-6 recovery hysteresis |
| Response | One alert per band escalation, deterministic keys, callback/MFA recommendation, never auto-block |
| Ledger | SQLite WAL with FULL synchronous writes and a SHA-256 chain over canonical event JSON followed by the previous hash |
| Frontend | React, Tailwind/Vite, local fonts, WebSocket result replay, polling recovery |

`language` is **operator-asserted, not detected** — this build runs no language identification, and
a value outside the supported set (`en, hi, ta, te, bn, hi-en`) gates the detector signal off and
applies the 40-point floor rather than scoring out-of-scope audio. `simulate_adversarial_input` is
**simulated, not detected**; real adversarial input-sanity checking is Phase 2.

Risk is **0–100, higher means more verification is required**. LOW is 0–39, MEDIUM is 40–69, HIGH is 70–100, and UNKNOWN has no numeric score and appears as **Not assessed**. The REAL/SYNTHETIC window label is an uncalibrated heuristic indication only. Speaker match is `null` / not enrolled, never a fabricated measurement. Spectral peaks are explicitly not validated LPC formants; jitter/shimmer are frame proxies, not clinical measurements.

## API contract

All paths from the build prompt are implemented:

| Method | Path | Body / behavior |
|---|---|---|
| POST | `/api/v1/stream/start` | `{filename,label,context:{caller_attestation,attestation_source,transaction_value,beneficiary_is_new,request_urgency,urgency_source},interval:1,simulate_detector_failure:false,language:"en",simulate_adversarial_input:false}` → call_id |
| WS | `/ws/audio/{call_id}` | Replays derived chunk events, then streams live; ends with `complete` |
| POST | `/api/v1/detect` | `{samples:[...],sample_rate:16000}`; 4,000–64,000 finite normalized float samples |
| POST | `/api/v1/risk-score` | `{spectral_score,prosody_score,speaker_match_score:null,context:{...}}`; scores in [0,1] |
| GET | `/api/v1/risk-score/{call_id}` | Rolling risk, history, latest derived features |
| POST/GET | `/api/v1/alerts` | POST `{call_id}` requires sustained risk; GET lists alerts |
| POST | `/api/v1/ledger/log` | `{call_id,event_type,risk_score,band,policy_version,model_versions}`; strict allowlist rejects raw-audio fields |
| GET | `/api/v1/ledger/verify/{hash}` | Checks genesis through the supplied hash; use `all` to verify the full chain |

Additional local endpoints: health, audio list, call list, stop simulation, ledger list, and alert escalation. Stop means **stop the simulator**, not block a real call. Frontend requests go through Vite's local `/api` and `/ws` proxies. Bank webhooks, shared blacklists, and SMS are deliberately outside the MVP.

## Privacy and ledger limits

The application never writes incoming raw audio or reconstructable waveforms. It reads pre-existing source WAVs, processes each window in memory, clears owned input/processed NumPy buffers, and retains only derived features. The displayed waveform is a 96-bin RMS envelope. User-supplied source files remain on disk by design; they are not recordings made by this app. Python, native-library scratch memory, OS paging, and external OneDrive synchronization are not controlled secure-erasure boundaries.

Only event type, call ID, risk score, and timestamp enter the local SQLite ledger. Call labels, transaction context, audio, and feature vectors are excluded. An altered middle record is detectable. A local database owner can rewrite the entire chain or truncate its tail; an external trusted checkpoint or real permissioned network would be required to detect that. This is **tamper-evident, not tamper-proof or immutable**. Calls/alerts disappear on gateway restart; ledger entries persist. `LEDGER_PATH` overrides the database location.

## Verify

```powershell
# Go: fusion, session aggregation, decisions, alerts, audit, gateway API
go -C gateway test ./...

# Python: DSP, VAD, features, detector, speaker verification, consent, audio boundary
.\.venv\Scripts\python.exe -m pytest backend/tests -q

# One window at a time, printed as NDJSON
cd backend; ..\.venv\Scripts\python.exe -m app.cli ../demo_audio/fixture-variable.wav
```

The Go suite includes `gateway/internal/scoring/golden_test.go`, which replays scoring vectors
emitted from the Python implementation the fusion was ported from. That is the evidence the port
did not change the arithmetic — the arithmetic being exactly what the v2 spec exists to correct.
Regenerate the vectors with `python backend/tools/emit_golden.py` only when a policy change is
intended.

From `frontend`: `npm run build`.

## Limitations and next steps

- **No validated spoof detector or accuracy claims.** Pitch-regular genuine voices can be flagged and sophisticated clones can evade the heuristic. Codec noise and accents need evaluation. Energy/flatness VAD can accept tonal non-speech and reject unvoiced speech.
- Replace the classifier with an evaluated AASIST/RawNet2/wav2vec checkpoint, and train/evaluate with separate speaker/generator splits. Benchmark precision, recall, F1, false positives/negatives, and end-to-end latency on ASVspoof plus unseen sources.
- Add proper prosody modeling, validated formant tracks, speaker enrollment/ECAPA matching, and calibrated fusion before deployment. The Python sidecar uses librosa/NumPy/SciPy without PyTorch/torchaudio, avoiding unused heavyweight inference dependencies until a real checkpoint is supplied.
- Real telecom/VoIP integration, a permissioned blockchain network, bank webhooks, cross-institution sharing, and multilingual coverage remain stretch goals, matching the plan and deck.
- For production: authentication, authorization, request-size/rate limits, durable session storage, retention controls, backpressure, external ledger anchoring, and deployment hardening. This is a single-machine hackathon prototype.
