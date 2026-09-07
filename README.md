# VoiceShield AI

Local voice-cloning detection **demonstration** for SIH26104 (AICTE), team vox_Guard. Implements the supplied architecture plan's pipeline, with the pasted build prompt's local-MVP boundaries. The reference PDF informs temporal consistency, feature coverage, secondary verification, and evaluation priorities.

## Run on Windows

Python 3.11+ and Node.js 20+ are required. From this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe backend/generate_fixtures.py
cd frontend
npm ci
```

`backend/requirements-lock.txt` records the exact Python versions used for verification on Windows/Python 3.11; install it instead of `requirements.txt` to reproduce that environment.

In one terminal, from `voiceshield-ai/backend`:

```powershell
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal, from `voiceshield-ai/frontend`:

```powershell
npm run dev
```

Open http://localhost:5173. API documentation: http://127.0.0.1:8000/docs. Use one backend worker; active calls and alerts are in process memory. Do not expose this unauthenticated prototype beyond loopback.

After installation, `./start.ps1` from the project root starts both services in the background and stops them when you press Enter in the launcher terminal. It refuses to take over occupied ports.

## Demo walkthrough

1. The generator supplies steady, variable-pitch, and silence WAV **test signals**. None is genuine speech or a cloned person. They demonstrate processing and alert mechanics only.
2. Click **Run simulation**, choose `fixture-steady.wav`, leave unknown caller and ₹250,000 context, and start. The regular test signal should produce sustained high risk under the heuristic.
3. Observe 3-second chunks, RMS energy envelope, acoustic sub-scores, rolling risk, and measured processing latency. Cold-start library compilation may make the first window much slower.
4. After at least three scored windows and two consecutive rolling scores ≥65, inspect the verification queue. Escalate to record a local review event. No call is auto-blocked and no message is sent.
5. Verify the audit chain. Run `fixture-variable.wav` with a known number and no transaction context for a different signal pattern. Run silence to verify skipped windows produce no risk score.
6. Add consented genuine and AI-cloned `.wav` recordings to `demo_audio/`. The source list refreshes automatically. Mono/stereo, ≤96 kHz, ≤5 minutes. Filenames never determine the detection score.

Optional: run `./backend/generate_speech_samples.ps1` from any directory to create three Windows TTS scenario recordings. Every generated filename begins with `tts-`; all are synthetic test material and must not be presented as genuine or cloned-human ground truth.

## Architecture and implementation

| Stage | Implementation |
|---|---|
| Ingestion | SoundFile reads one 3-second window; SciPy resamples to mono 16 kHz |
| Preprocessing | 80–3800 Hz band-pass, frame energy + in-band spectral flatness VAD, gentle noise gate |
| Features | librosa MFCCs, log-mel summary, YIN pitch contour, frame-based jitter/shimmer proxies, spectral peaks, RMS envelope |
| Detection | `SpoofClassifier.predict(chunk) -> float`; unvalidated `HeuristicClassifier` fallback |
| Fusion | 55% spectral + 45% prosody; optional enrolled speaker match supported in fusion API; context adds at most 20 points |
| Temporal risk | EMA α=.30; minimum 3 observations + 2 consecutive scores ≥65 for an alert |
| Response | Callback/MFA/escalation recommendation, deduplicated per call, never auto-block |
| Ledger | SQLite transaction-protected SHA-256 chain over canonical event JSON and previous hash |
| Frontend | React, Tailwind/Vite, local fonts, WebSocket result replay, polling recovery |

Risk is **0–100, higher = more suspicious**. Voice Authenticity is exactly **100 − risk**, including context; neither is calibrated confidence. The REAL/SYNTHETIC chunk label is a heuristic indication only. Speaker match is `null` / not enrolled, never a fabricated measurement. Spectral peaks are explicitly not validated LPC formants; jitter/shimmer are frame proxies, not clinical measurements.

## API contract

All paths from the build prompt are implemented:

| Method | Path | Body / behavior |
|---|---|---|
| POST | `/api/v1/stream/start` | `{filename,label,context:{known_number,transaction_size,hour},interval:3}` → call_id |
| WS | `/ws/audio/{call_id}` | Replays derived chunk events, then streams live; ends with `complete` |
| POST | `/api/v1/detect` | `{samples:[...],sample_rate:16000}`; 4,000–64,000 finite normalized float samples |
| POST | `/api/v1/risk-score` | `{spectral_score,prosody_score,speaker_match_score:null,context:{...}}`; scores in [0,1] |
| GET | `/api/v1/risk-score/{call_id}` | Rolling risk, history, latest derived features |
| POST/GET | `/api/v1/alerts` | POST `{call_id}` requires sustained risk; GET lists alerts |
| POST | `/api/v1/ledger/log` | `{call_id,event_type,risk_score}`; strict allowlist rejects raw-audio fields |
| GET | `/api/v1/ledger/verify/{hash}` | Checks genesis through the supplied hash; use `all` to verify the full chain |

Additional local endpoints: health, audio list, call list, stop simulation, ledger list, and alert escalation. Stop means **stop the simulator**, not block a real call. Frontend requests go through Vite's local `/api` and `/ws` proxies. Bank webhooks, shared blacklists, and SMS are deliberately outside the MVP.

## Privacy and ledger limits

The application never writes incoming raw audio or reconstructable waveforms. It reads pre-existing source WAVs, processes each window in memory, clears owned input/processed NumPy buffers, and retains only derived features. The displayed waveform is a 96-bin RMS envelope. User-supplied source files remain on disk by design; they are not recordings made by this app. Python, native-library scratch memory, OS paging, and external OneDrive synchronization are not controlled secure-erasure boundaries.

Only event type, call ID, risk score, and timestamp enter the local SQLite ledger. Call labels, transaction context, audio, and feature vectors are excluded. An altered middle record is detectable. A local database owner can rewrite the entire chain or truncate its tail; an external trusted checkpoint or real permissioned network would be required to detect that. This is **tamper-evident, not tamper-proof or immutable**. Calls/alerts disappear on backend restart; ledger entries persist. `LEDGER_PATH` overrides the database location.

## Verify

From `backend`:

```powershell
..\.venv\Scripts\python.exe -m pytest tests -q
..\.venv\Scripts\python.exe -m app.cli ../demo_audio/fixture-variable.wav
```

The CLI prints measured feature vectors and scores for every window. Tests cover silence/noise rejection, feature sanity, smoothing and alert persistence, speaker absence, context bounds, tamper detection, buffer clearing, strict schemas, complete WebSocket streaming, escalation, and ledger integrity.

From `frontend`: `npm run build`.

## Limitations and next steps

- **No validated spoof detector or accuracy claims.** Pitch-regular genuine voices can be flagged and sophisticated clones can evade the heuristic. Codec noise and accents need evaluation. Energy/flatness VAD can accept tonal non-speech and reject unvoiced speech.
- Replace the classifier with an evaluated AASIST/RawNet2/wav2vec checkpoint, and train/evaluate with separate speaker/generator splits. Benchmark precision, recall, F1, false positives/negatives, and end-to-end latency on ASVspoof plus unseen sources.
- Add proper prosody modeling, validated formant tracks, speaker enrollment/ECAPA matching, and calibrated fusion before deployment. The default architecture uses librosa/NumPy/SciPy without PyTorch/torchaudio, avoiding unused heavyweight inference dependencies until a real checkpoint is supplied.
- Real telecom/VoIP integration, a permissioned blockchain network, bank webhooks, cross-institution sharing, and multilingual coverage remain stretch goals, matching the plan and deck.
- For production: authentication, authorization, request-size/rate limits, durable session storage, retention controls, backpressure, external ledger anchoring, and deployment hardening. This is a single-machine hackathon prototype.
