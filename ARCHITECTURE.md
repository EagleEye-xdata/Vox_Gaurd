# VoiceShield AI — Architecture

> Real-time deepfake / AI-voice fraud detection for financial call centres.

---

## Table of Contents

1. [High-Level Overview](#1-high-level-overview)
2. [Languages & Runtimes](#2-languages--runtimes)
3. [Technology Stack](#3-technology-stack)
4. [Directory Structure](#4-directory-structure)
5. [Backend Architecture](#5-backend-architecture)
   - [Module Map](#51-module-map)
   - [Audio Processing Pipeline](#52-audio-processing-pipeline)
   - [REST API Endpoints](#53-rest-api-endpoints)
   - [WebSocket Streaming](#54-websocket-streaming)
   - [Tamper-Evident Ledger](#55-tamper-evident-ledger)
6. [Frontend Architecture](#6-frontend-architecture)
   - [Component Tree](#61-component-tree)
   - [API & WebSocket Client](#62-api--websocket-client)
7. [Data Flow Diagram](#7-data-flow-diagram)
8. [Risk Scoring Model](#8-risk-scoring-model)
9. [Scripts & Tooling](#9-scripts--tooling)
10. [Dependency Summary](#10-dependency-summary)

---

## 1. High-Level Overview

```
┌─────────────────────────────────────────────┐
│                FRONTEND (React)              │
│  Browser @ http://127.0.0.1:5173            │
│  Vite dev server / dist bundle               │
└──────────────┬──────────────────────────────┘
               │  HTTP REST  /api/v1/*
               │  WebSocket  /ws/audio/{call_id}
               ▼
┌─────────────────────────────────────────────┐
│           GATEWAY (Go) @ 127.0.0.1:8000     │
│  net/http · coder/websocket · modernc sqlite│
│                                             │
│  schema validate → session orchestrator     │
│         ┌───────────────┐                   │
│         │ Risk Fusion   │  active-signal    │
│         │ (03 §1)       │  renormalisation  │
│         └──────┬────────┘                   │
│                ▼                            │
│         ┌───────────────┐                   │
│         │ Session Aggr. │  EWMA + peak      │
│         │ (04 §3-5)     │  + hysteresis     │
│         └──────┬────────┘                   │
│                ▼                            │
│         ┌───────────────┐                   │
│         │   Decision    │                   │
│         └──┬─────────┬──┘                   │
│            ▼         ▼                      │
│      ┌────────┐  ┌────────────┐             │
│      │ Alerts │  │   Ledger   │             │
│      └────────┘  │ (SQLite)   │             │
│                  └────────────┘             │
└──────────────┬──────────────────────────────┘
               │  HTTP/JSON, loopback only
               │  analyses out — never audio in
               ▼
┌─────────────────────────────────────────────┐
│      ML SIDECAR (Python) @ 127.0.0.1:8801   │
│  Uvicorn ASGI — internal, not public         │
│                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │Ingestion │→ │ Preproc  │→ │ Features │  │
│  └──────────┘  └──────────┘  └────┬─────┘  │
│                          ┌────────┴─────┐  │
│                          ▼              ▼  │
│                   ┌───────────┐ ┌──────────┐│
│                   │ Detection │ │ Speaker  ││
│                   │(Heuristic)│ │  Verif.  ││
│                   └───────────┘ └──────────┘│
│                                             │
│  Raw audio lives here and nowhere else.     │
│  Buffers are zeroed before each response.   │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│             demo_audio/  (WAV files)         │
│             backend/data/ledger.db (SQLite)  │
└─────────────────────────────────────────────┘
```

This split is the one specified in `docs/01-ARCHITECTURE.md` §2, which places the gateway, session
orchestrator, risk fusion, session aggregator, decision service, alert service and audit service
in **[Go]**, and preprocess/VAD, detection and speaker verification in **[Python]**. The spec's
transport between them is gRPC; Phase 0 uses loopback HTTP/JSON, which is deviation DEV-4 in
`docs/HANDOFF.md`.

---

## 2. Languages & Runtimes

| # | Language | Version / Notes | Used For |
|---|----------|-----------------|----------|
| 1 | **Go** | >= 1.27 | Gateway — API, schema validation, risk fusion, session aggregation, decisions, alerts, hash-chained ledger |
| 1b | **Python** | >= 3.10 (type union syntax `X \| Y`) | ML sidecar — DSP pipeline, VAD, feature extraction, spoof detector, speaker verification |
| 2 | **JavaScript (JSX)** | ES2022 modules | React frontend — components, state, routing |
| 3 | **JavaScript (JS)** | ES Modules | API client (`api.js`), Vite config (`vite.config.js`) |
| 4 | **CSS** | Vanilla / custom properties | All visual styles (`styles.css`, 20 KB) |
| 5 | **HTML** | HTML5 | SPA shell (`index.html`) |
| 6 | **PowerShell** | Windows PowerShell 5.1+ | Dev launcher (`start.ps1`), sample generator (`generate_speech_samples.ps1`) |
| 7 | **SQL** | SQLite dialect | Ledger DDL / DML inside `gateway/internal/ledger` |
| 8 | **JSON** | — | `package.json`, `package-lock.json`, API request/response bodies, ledger payloads |
| 9 | **Markdown** | GitHub Flavored | `README.md`, `ARCHITECTURE.md`, `docs/verification.md`, `models/README.md` |

---

## 3. Technology Stack

### Gateway (Go)

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Web framework | **net/http** (stdlib, Go 1.22+ pattern routing) | REST endpoints, no router dependency |
| WebSocket | **github.com/coder/websocket** `v1.8` | Live per-window stream to the dashboard |
| Persistence | **modernc.org/sqlite** `v1.58` | Pure-Go SQLite; no cgo, so the gateway cross-compiles and needs no C toolchain |
| Identifiers | **github.com/google/uuid** `v1.6` | Call and appeal identifiers |
| Data validation | stdlib `encoding/json` with `DisallowUnknownFields` | Reproduces Pydantic's `extra="forbid"` |
| Testing | stdlib `testing` | Golden replay, invariant tests, `httptest` API tests |

### ML sidecar (Python)

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Web framework | **FastAPI** `>=0.115` | Internal loopback endpoints |
| ASGI server | **Uvicorn** `[standard]` `>=0.30` | Async HTTP server |
| Data validation | **Pydantic v2** (bundled with FastAPI) | Internal request models, strict |
| Numerical computing | **NumPy** `>=1.26` | Array ops, FFT, RMS, MFCC, resampling |
| Audio I/O | **SoundFile** `>=0.12` | WAV file reading (libsndfile binding) |
| Audio features | **Librosa** `>=0.10.2` | Mel-spectrogram, MFCC, pitch (YIN), spectral flatness, RMS |
| Signal processing | **SciPy** `>=1.12` | Butterworth bandpass filter (`butter/sosfilt`), `resample_poly`, `find_peaks` |
| HTTP testing | **HTTPX** `>=0.27` | Async test client for pytest |
| Testing | **Pytest** `>=8` | Unit & integration tests |
| Hashing | **SHA-256** (stdlib `hashlib`) | Ledger chain integrity |
| Concurrency | **asyncio** + `asyncio.to_thread` | Non-blocking audio processing |

### Frontend

| Layer | Technology | Purpose |
|-------|-----------|---------|
| UI library | **React** `^19.0.0` | Component model, state management (hooks) |
| Build tool | **Vite** `^6.0.0` | Dev server, HMR, production bundler |
| React plugin | **@vitejs/plugin-react** `^4.3.4` | JSX/Babel transform |
| CSS pipeline | **TailwindCSS** `^4.0.0` | Vite CSS integration; the current interface primarily uses authored semantic classes and custom properties |
| Icon library | **Lucide React** `^0.468.0` | SVG icon set |
| Typography | **IBM Plex Sans** & **IBM Plex Mono** (`@fontsource`) | Custom web fonts |
| Formatter | **Prettier** `^3.9.6` | Code formatting |
| Transport | Native **WebSocket** API | Real-time call event streaming |
| Transport | Native **Fetch** API | REST calls |

---

## 4. Directory Structure

```
voiceshield-ai/
│
├── gateway/                        ← Go module: everything downstream of the detector
│   ├── go.mod
│   ├── cmd/voxguard/main.go        [Go]  Entrypoint, flags, graceful shutdown
│   └── internal/
│       ├── numeric/                [Go]  CPython-identical rounding (round-half-to-even)
│       ├── policy/                 [Go]  Versioned scoring policy pack
│       ├── scoring/                [Go]  Risk fusion (03) + session aggregator (04)
│       │   └── testdata/golden_windows.json   Vectors emitted from the Python reference
│       ├── decision/               [Go]  Decision service, WAL seq, supervisor overrides
│       ├── alerts/                 [Go]  Idempotent alerts, SLA, resolutions, appeals
│       ├── ledger/                 [Go + SQL]  Hash-chained, origin-signed audit store
│       ├── schema/                 [Go]  Request validation, attestation provenance rule
│       ├── sidecar/                [Go]  Client + reverse proxy for the Python service
│       ├── session/                [Go]  Session orchestrator, call state, event log
│       └── httpapi/                [Go]  Routes, CORS, WebSocket, error shape
│
├── backend/                        ← Python ML sidecar
│   ├── app/
│   │   ├── __init__.py             [Python]  Package marker
│   │   ├── sidecar.py              [Python]  Internal FastAPI service; owns the audio boundary
│   │   ├── ingestion.py            [Python]  WAV reader, chunking, resampling
│   │   ├── preprocessing.py        [Python]  VAD (energy + periodicity), bandpass filter
│   │   ├── features.py             [Python]  MFCCs, mel-spec, pitch, jitter, shimmer, flatness
│   │   ├── spoof_classifier.py     [Python]  Abstract SpoofClassifier interface
│   │   ├── detection.py            [Python]  HeuristicClassifier, active-detector selection
│   │   ├── aasist.py               [Python]  AASISTLClassifier — ONNX Runtime AASIST-L detector
│   │   ├── speaker_verification.py [Python]  Enrolment, embeddings, consent gate
│   │   └── cli.py                  [Python]  Per-window NDJSON debugging tool
│   │
│   ├── tests/
│   │   └── test_pipeline.py        [Python]  DSP, detector, consent, audio-boundary tests
│   │
│   ├── tools/
│   │   └── emit_golden.py          [Python]  Emits the Go golden vectors from the reference
│   │
│   ├── data/                       ← Runtime data
│   │   └── ledger.db               [SQLite]  Auto-created at runtime, written by the gateway
│   │
│   ├── conftest.py                 [Python]  Puts backend/ on sys.path for pytest
│   ├── generate_fixtures.py        [Python]     Creates synthetic WAV fixtures for demo
│   ├── generate_speech_samples.ps1 [PowerShell] Windows TTS → WAV sample generator
│   ├── requirements.txt            [Text]    Direct Python dependencies
│   └── requirements-lock.txt       [Text]    Pinned dependency lockfile
│
├── frontend/                       ← React SPA
│   ├── index.html                  [HTML]    SPA entry, theme-color meta
│   ├── vite.config.js              [JavaScript] Vite config, React plugin, proxy
│   ├── package.json                [JSON]    Node.js manifest, scripts
│   │
│   └── src/
│       ├── main.jsx                [JSX]     React DOM root render
│       ├── App.jsx                 [JSX]     Root component, routing, global state
│       ├── api.js                  [JavaScript] fetch() wrapper, WebSocket factory
│       ├── styles.css              [CSS]     Design tokens, responsive layout, and interaction states
│       └── components/
│           ├── Dashboard.jsx       [JSX]     Call list overview, status badges
│           ├── CallDetail.jsx      [JSX]     Per-call risk gauge, feature charts
│           ├── AlertPopup.jsx      [JSX]     Persistent secondary-verification queue
│           └── LedgerPanel.jsx     [JSX]     Chain-of-custody ledger viewer
│
├── demo_audio/                     ← WAV sample files (git-ignored)
│
├── docs/
│   └── verification.md             [Markdown] Ledger verification guide
│
├── start.ps1                       [PowerShell] One-command dev launcher (sidecar + gateway + frontend)
├── .gitignore
└── README.md                       [Markdown]
```

---

## 5. Backend Architecture

### 5.1 Module Map

```
gateway/cmd/voxguard
  └── internal/httpapi     (Routes, CORS, WebSocket, FastAPI-shaped error bodies)
        ├── internal/schema    (Start, Scores, AlertRequest, LedgerEvent, Context provenance)
        ├── internal/session   (Manager, Call, event log, streaming loop)
        │     ├── internal/sidecar   (OpenStream / NextWindow / CloseStream / Proxy)
        │     ├── internal/scoring   (ScoreWindow(), SessionRisk, ContextTerm())
        │     │     ├── internal/policy    (Pack, Default)
        │     │     └── internal/numeric   (Round())
        │     ├── internal/decision  (Service.Decide(), Service.Override())
        │     ├── internal/alerts    (Store.Ensure(), Resolve(), LodgeAppeal())
        │     └── internal/ledger    (Open(), Append(), Verify())
        └── ...

backend/app/sidecar.py     (analyse_buffer(), window_results(), internal endpoints)
  ├── ingestion.py            (AUDIO_DIR, chunks(), resolve_audio())
  ├── preprocessing.py        (preprocess(), SAMPLE_RATE=16000)
  ├── features.py             (extract())
  ├── detection.py            (classifier = active detector, AASIST-L or heuristic fallback)
  ├── aasist.py               (AASISTLClassifier, ONNX Runtime)
  └── speaker_verification.py (verifier.enroll(), verify(), revoke())
```

The arrow between them runs one way and carries no audio: `internal/sidecar` asks for the next
window's *analysis*, and `sidecar.py` zeroes the buffer before the response is built.

### 5.2 Audio Processing Pipeline

```
WAV File (demo_audio/)
      │
      ▼ soundfile.SoundFile  —  3-second windows, 1-second hop
      │  • Reads float32
      │  • Downmix stereo → mono (mean)
      │  • Resample to 16 000 Hz via resample_poly (GCD-safe)
      │
      ▼ preprocessing.preprocess()   [Python / NumPy / SciPy]
      │  • DC removal (mean subtraction)
      │  • Butterworth bandpass 80–3800 Hz (order 3)
      │  • Frame-level RMS + spectral flatness VAD
      │  • Reject silence / broadband noise (< 5 voiced frames or < 15% voiced)
      │  • Noise gate (x0.1) on unvoiced frames
      │
      ▼ features.extract()           [Python / NumPy / Librosa / SciPy]
      │  • 40-band log-mel spectrogram (FFT 512, hop 160)
      │  • 13-coefficient MFCCs
      │  • YIN pitch estimator (fmin=65 Hz, fmax=450 Hz), octave-error outliers
      │    beyond ±0.5 octave of the median rejected
      │  • RMS envelope (96 segments)
      │  • Jitter  = mean(|Δpitch|) / mean(pitch)
      │  • Shimmer = mean(|ΔRMS|) / mean(RMS)
      │  • Spectral flatness (Wiener entropy)
      │  • Top-3 spectral peaks (200–3500 Hz)
      │
      ▼ detection.classifier.analyze(audio, features)   [Python]
      │  Primary: aasist.AASISTLClassifier — ONNX Runtime, CPU, the raw (pre-filter) 16 kHz
      │  mono window padded/cropped to 64,600 samples. synthetic_score = sigmoid(spoof_logit −
      │  bonafide_logit); uncalibrated (calibrator_version = identity-sigmoid@0.0.0-unvalidated).
      │  Fallback (disabled / load or inference failure): HeuristicClassifier.score_features()
      │  on the filtered/gated `features` —
      │    tonality       = log-axis map of Wiener entropy over [3e-2, 1e-6]
      │    spectral_score  = clip(0.05 + 0.90×tonality, 0.05, 0.95)
      │    prosody_score   = clip(0.95 − 2.4×pitch_cv − 2×jitter − 0.35×shimmer, 0.05, 0.95)
      │    synthetic_score = 0.55×spectral + 0.45×prosody
      │  classification → "SYNTHETIC" if synthetic_score >= threshold else "REAL"
      │
      ═══ process boundary: the analysis crosses, the audio does not ═══

      ▼ scoring.ScoreWindow()  [Go]
      │  • AI .60, speaker .20, context .20; active weights renormalize
      │  • AI probability shrinks toward .5 according to confidence
      │  • Context recursively renormalizes over available, sourced fields
      │  • Adversarial/replay/degraded policy floors are applied last
      │  • LOW 0–39 · MEDIUM 40–69 · HIGH 70–100 · UNKNOWN has no score
      │
      ▼ scoring.SessionRisk.Update()  [Go]
         • EWMA α=.35 plus decaying peak: max(EWMA, peak−8), peak decay=.98
         • 2-of-3 escalation and 5-of-6 de-escalation with a 5-point margin
         • One deterministic alert per MEDIUM/HIGH band escalation
```

### 5.3 REST API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/health` | Service health & model name |
| `GET` | `/api/v1/audio` | List available WAV files |
| `GET` | `/api/v1/calls` | All call records (latest first) |
| `POST` | `/api/v1/stream/start` | Start a streaming call analysis |
| `POST` | `/api/v1/stream/{call_id}/stop` | Stop a streaming call |
| `POST` | `/api/v1/detect` | Analyse a raw audio chunk (float32 samples) |
| `POST` | `/api/v1/risk-score` | Single-shot score fusion |
| `GET` | `/api/v1/risk-score/{call_id}` | Current risk for a call |
| `POST` | `/api/v1/alerts` | Manually create an alert |
| `GET` | `/api/v1/alerts` | List all alerts |
| `POST` | `/api/v1/alerts/{alert_id}/escalate` | Escalate an alert |
| `POST` | `/api/v1/ledger/log` | Append a custom ledger event |
| `GET` | `/api/v1/ledger` | Read all ledger entries |
| `GET` | `/api/v1/ledger/verify/{hash}` | Verify chain integrity |

### 5.4 WebSocket Streaming

```
Client  ──── WS connect ──►  /ws/audio/{call_id}
        ◄── JSON event ──────  { type: "chunk", chunk: N, scored: bool, risk_score, ... }
        ◄── JSON event ──────  { type: "complete", call: { ... } }
        ──── disconnect ──►  (or server close on complete)
```

The server sends buffered events via a cursor loop (polling every 100 ms). Max 3 simultaneous streaming calls.

### 5.5 Tamper-Evident Ledger

- **Storage**: SQLite (`backend/data/ledger.db`) — single table `ledger(id, previous, payload, hash)`.
- **Language**: Python + `sqlite3` stdlib + `hashlib.sha256`.
- **Chain**: Version 2 rows store `SHA-256(canonical_JSON_payload + previous_hash)`. Genesis previous = `"0"×64`; verification remains compatible with earlier local demo rows.
- **Verification**: `GET /api/v1/ledger/verify/all` walks the chain linearly; `verify/{hash}` finds a specific entry.
- **Caveats**: Local only, not distributed, not cryptographically signed — for audit-trail demo purposes.

---

## 6. Frontend Architecture

### 6.1 Component Tree

```
main.jsx
└── App.jsx                     (global state, page routing, WebSocket lifecycle)
    ├── Dashboard.jsx           (call list, risk badges, start-call form)
    ├── CallDetail.jsx          (real-time risk gauge, feature table, history chart)
    ├── AlertPopup.jsx          (secondary-verification queue panel)
    └── LedgerPanel.jsx         (chain-of-custody table, verify button)
```

**State managed in `App.jsx`** via React `useState`:

| State | Type | Purpose |
|-------|------|---------|
| `calls` | `Array` | All call records from `/api/v1/calls` |
| `alerts` | `Array` | All alerts from `/api/v1/alerts` |
| `ledger` | `Array` | Ledger entries |
| `audio` | `Array` | Available WAV filenames |
| `selected` | `string \| null` | Currently viewed call ID |
| `online` | `bool` | Backend connectivity status |
| `page` | `string` | Active nav page (`"monitor"`, `"pipeline"`, `"ledger"`, `"guide"`) |
| `notification` | `object \| null` | Alert popup content |

### 6.2 API & WebSocket Client

**`api.js`** — Two exported functions:

```javascript
// REST helper (GET or POST)
export async function api(path, body?) → Promise<JSON>

// WebSocket factory  (ws:// or wss:// based on page protocol)
export function socket(callId) → WebSocket
```

---

## 7. Data Flow Diagram

```
User clicks "Run simulation"
        │
        ▼  POST /api/v1/stream/start
   Go gateway (internal/httpapi)
        │  schema validate → session.Manager.Start()
        ▼  POST /internal/stream/open
   Python sidecar opens a windowed read over the WAV
        │
        ▼  go m.run(...)   — one goroutine per call
   Loop, one tick per hop ──► POST /internal/stream/{id}/next
        │                            │
        │        [Python]  preprocess → extract → detect ‖ verify → zero the buffer
        │                            │
        │        ◄───────────────────┘  analysis only: features and scores, never samples
        │
        │  [Go]  scoring.ScoreWindow() → SessionRisk.Update() → decisions.Decide()
        │
        │  ledger.Append("observation")
        │
        ├──► verdict.AlertKey != nil? → alerts.Ensure() → ledger.Append("alert")
        │
        ▼  call.emit(Event{...})
   WebSocket /ws/audio/{call_id}
        │
        ▼  JSON event stream
   React App.jsx (ws.onmessage)
        │
        └──► setCalls() → CallDetail.jsx renders the latest derived results

   REST refresh (every 2 seconds)
        ├──► setAlerts() → verification queue + alert toast in App.jsx
        └──► setLedger() → LedgerPanel.jsx
```

---

## 8. Risk Scoring Model

> **Disclaimer**: The primary detector (AASIST-L) is a real published model, but it is not
> validated for this project's languages, telephony codecs, or demo fixtures — see the README's
> "Spoof detector: AASIST-L" section and `docs/05-ML_MODEL_LIFECYCLE.md`. Its heuristic fallback
> is not a validated ML model at all.

### Window Score

```
ai_term = 0.5 + (p_synthetic − 0.5) × confidence
speaker_term = 1 − match_score                  # only with a valid enrollment
context_term = weighted mean of available attestation, urgency, history, transaction

base_score = 100 × weighted_mean(active signal terms)
discounted = base_score − 10 only when attestation, speaker match, transaction, and health gates all pass
window_score = clamp(max(discounted, applicable policy floors), 0, 100)
```

Signals that are unavailable are excluded from the denominator. No speaker enrollment is a normal inactive state. Fewer than three voiced seconds yields UNKNOWN; detector failure and unsupported language enter degraded mode with a 40-point floor when another signal is active.

### Session Scoring and Hysteresis

```
ewma_t = 0.35 × window_score_t + 0.65 × ewma_(t−1)
peak_t = max(0.98 × peak_(t−1), window_score_t)
session_score_t = max(ewma_t, peak_t − 8)
```

The band escalates after two of the last three windows qualify. It recovers after five of six windows sit at least five points below the lower boundary. Adversarial and replay floors can escalate immediately.

---

## 9. Scripts & Tooling

| Script | Language | Purpose |
|--------|----------|---------|
| `start.ps1` | PowerShell | One-command launcher: checks ports 8000/8801/5173, generates fixtures, builds the gateway, starts sidecar + gateway + Vite in hidden windows |
| `backend/generate_fixtures.py` | Python | Generates synthetic sine-wave WAV fixtures (`fixture-*.wav`) for offline demo |
| `backend/generate_speech_samples.ps1` | PowerShell | Uses an installed Windows `System.Speech` voice to generate clearly labelled synthetic TTS scenario WAVs |

---

## 10. Dependency Summary

### Go (`gateway/go.mod`)

| Module | Version | Role |
|--------|---------|------|
| `github.com/coder/websocket` | `v1.8.15` | WebSocket server for the live dashboard stream |
| `modernc.org/sqlite` | `v1.58.0` | Pure-Go SQLite driver for the audit ledger — no cgo, so no C toolchain is needed to build |
| `github.com/google/uuid` | `v1.6.0` | Call and appeal identifiers |

Everything else — routing, JSON, HMAC, SHA-256, the reverse proxy — is the standard library.

### Python (`backend/requirements.txt`)

| Package | Version Constraint | Role |
|---------|-------------------|------|
| `fastapi` | `>=0.115, <1` | ASGI framework for the internal sidecar |
| `uvicorn[standard]` | `>=0.30, <1` | ASGI server |
| `numpy` | `>=1.26, <3` | Numerical arrays, FFT |
| `scipy` | `>=1.12, <2` | DSP filters, resampling, peak finding |
| `librosa` | `>=0.10.2, <1` | Audio feature extraction |
| `soundfile` | `>=0.12, <1` | WAV I/O |
| `onnxruntime` | `>=1.18, <2` | CPU inference for the AASIST-L spoof detector (`app/aasist.py`) |
| `httpx` | `>=0.27, <1` | Async HTTP test client |
| `pytest` | `>=8, <10` | Test framework |

### Node.js (`frontend/package.json`)

| Package | Version | Role |
|---------|---------|------|
| `react` | `^19.0.0` | UI component library |
| `react-dom` | `^19.0.0` | DOM renderer |
| `lucide-react` | `^0.468.0` | SVG icon set |
| `@fontsource/ibm-plex-sans` | `^5.3.0` | IBM Plex Sans web font |
| `@fontsource/ibm-plex-mono` | `^5.3.0` | IBM Plex Mono web font |
| `vite` | `^6.0.0` | Build tool & dev server |
| `@vitejs/plugin-react` | `^4.3.4` | React JSX transform |
| `tailwindcss` | `^4.0.0` | CSS utility framework |
| `@tailwindcss/vite` | `^4.0.0` | Vite integration for Tailwind |
| `prettier` | `^3.9.6` | Code formatter |

---

The generated speech scenarios are all synthetic TTS samples. They are useful for exercising the interface and narrative, but they do not constitute genuine-vs-cloned evaluation data.

*Generated: 2026-09-07 · VoiceShield AI · SIH Submission*
