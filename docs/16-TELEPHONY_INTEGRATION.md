# Telephony Integration — Live Call Detection

> **Version 1.0** | September 2026 | VoxGuard / VoiceShield AI

## Overview

This document describes how to connect live SIP/WebRTC phone calls to the existing VoiceShield AI voice-cloning detection pipeline. The key insight: your detector already works on streaming audio windows — the missing piece is a **telephony-to-audio-stream adapter** that feeds live call audio into the system you have already built.

### Architecture

```
Browser/SIP.js ──── SIP + WebRTC ────┐
Linphone ─────────── SIP ────────────┤
Optional SIP trunk ── SIP ───────────┤
                                     ▼
                              Asterisk PBX
                                     │
              ARI events/control ──→ Go ARI Controller
              snoop caller audio     (cmd/controller)
                                     │
                                     ▼
                        ExternalMedia WebSocket
                                     │
                              16 kHz slin16 PCM
                                     │
                                     ▼ AudioSocket (TCP :9019, 8kHz PCM)
                     Python ML Sidecar (audiosocket.py)
                     - resamples 8kHz to 16kHz
                     - extract features & run classifier
                     - clears raw audio from memory (Invariant 1)
                                     │
                                     ▼ POST /internal/live-sessions/{id}/windows
                     Go Gateway (gateway/internal/session)
                     - SessionRisk fusion & hysteresis
                     - automated policy decision
                     - audit ledger signing
                                     │
                          ┌──────────┼──────────┐
                          ▼          ▼          ▼
                     Dashboard    Webhooks    Ledger
```

**Key principle:** Only the caller's audio leg is analyzed. The callee, IVR prompts, and hold music are excluded using Asterisk's channel-level snoop with `spy=in`. Raw audio never touches disk or the Go gateway.

---

## Prerequisites

| Component | Version | Notes |
|-----------|---------|-------|
| Asterisk | 22.6+ LTS (22.11.0 recommended) | `chan_websocket` requires 22.6+; JSON control requires 22.8+ |
| Go | 1.21+ | For building the ARI controller and media adapter |
| Node.js | 18+ | Already installed for the frontend |
| Python | 3.11+ | Already installed for the backend |
| SIP Client | SIP.js (browser) or Linphone (desktop/mobile) | |

## Quick Start (LAN Demo)

### 1. Asterisk Setup (Linux/WSL2)

```bash
# Install Asterisk 22 LTS
sudo apt update && sudo apt install asterisk

# Verify version and modules
asterisk -rx 'core show version'
asterisk -rx 'module show like chan_websocket'
asterisk -rx 'module show like res_ari'

# Copy reference configs
sudo cp asterisk/*.conf /etc/asterisk/
# IMPORTANT: Edit passwords in pjsip.conf and ari.conf!

# Generate self-signed cert for WSS
sudo mkdir -p /etc/asterisk/keys
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/asterisk/keys/asterisk.key \
  -out /etc/asterisk/keys/asterisk.crt \
  -subj "/CN=$(hostname)"

# Restart
sudo systemctl restart asterisk
sudo asterisk -rx 'http show status'
sudo asterisk -rx 'pjsip show endpoints'
```

### 2. Python Backend (Windows — already running)

The new `/api/v1/sessions` endpoints are automatically loaded when the backend starts. No additional setup needed.

```bash
# Verify sessions endpoint
curl http://127.0.0.1:8000/api/v1/sessions
# Expected: []
```

### 3. Go Services (Windows)

```bash
cd telephony

# Download dependencies
go mod tidy

# Build the media adapter
go build -o adapter.exe ./cmd/adapter

# Build the ARI controller
go build -o controller.exe ./cmd/controller

# Run the media adapter
set DETECTOR_HTTP=http://127.0.0.1:8000
set DETECTOR_WS=ws://127.0.0.1:8000
adapter.exe

# In another terminal, run the ARI controller
set ARI_URL=http://<asterisk-ip>:8088
set ARI_USER=voxguard
set ARI_SECRET=<your-ari-secret>
controller.exe
```

### 4. SIP.js WebPhone (Browser)

```bash
cd frontend
npm install sip.js
npm run dev
```

Open the dashboard. The WebPhone component appears in the sidebar. Enter:
- **PBX Host:** IP of your Asterisk server
- **Extension:** 1001
- **Password:** Your configured SIP secret

Click **Connect**, then dial **7000** to place a monitored call.

---

## Component Details

### Python AudioSocket Ingest (`backend/app/audiosocket.py`)

Asterisk connects directly via TCP to the AudioSocket server (`:9019`).
- Receives raw 8 kHz signed-linear PCM from Asterisk AudioSocket channel.
- Resamples each 3-second window to 16 kHz for the ML classifier.
- Zeroes raw audio buffer in memory immediately after feature extraction (Invariant 1).
- Posts derived features/scores to Go Gateway's loopback endpoint:
  `POST /internal/live-sessions/{call_id}/windows`

### Go Gateway Live Sessions (`gateway/internal/session` & `httpapi`)

The Go Gateway (`:8000`) manages live session lifecycle:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/internal/live-sessions/{call_id}` | POST | Initialize live session tracking |
| `/internal/live-sessions/{call_id}/windows` | POST | Ingest derived ML window from Python sidecar |
| `/internal/live-sessions/{call_id}/close` | POST | Finalize session, build summary, emit ledger event |
| `/api/v1/calls` | GET | List calls (including live sessions) for frontend |
| `/api/v1/stream/{call_id}` | WS | Push live updates to browser dashboard |

### Go ARI Controller (`telephony/cmd/controller`)

Orchestrates Asterisk call lifecycle:

| Env Var | Default | Purpose |
|---------|---------|---------|
| `ARI_URL` | `http://127.0.0.1:8088` | Asterisk ARI URL |
| `ARI_USER` | `voxguard` | ARI username |
| `ARI_SECRET` | `voxguard-ari-secret` | ARI password |
| `ARI_APP` | `voxguard` | Stasis application name |
| `WEBHOOK_URL` | (empty) | Webhook delivery URL |

---

## Call Flows

### Monitored Incoming Call

```
1. Caller (SIP.js/Linphone) dials 7000
2. Asterisk routes to Stasis(voxguard,target=1002)
3. ARI Controller receives StasisStart
4. Controller creates mixing bridge, originates PJSIP/1002
5. Controller adds caller + callee to bridge
6. Controller snoops caller channel (spy=in)
7. Controller creates analysis bridge + ExternalMedia
8. Asterisk sends decoded slin16 PCM to media adapter
9. Adapter creates detector session, streams PCM
10. Detector sends rolling verdicts back
11. On hangup: session closes, summary logged
```

### IVR → Monitored Call

Caller dials 6000 → IVR prompt → press 1 → enters Stasis(voxguard) → same flow as above.

---

## Verification Checklist

- [ ] SIP.js or Linphone registers to Asterisk reliably
- [ ] Two extensions can complete a normal voice call
- [ ] Calling 7000 enters the ARI application
- [ ] Caller and callee remain connected through the normal bridge
- [ ] Only the intended suspicious leg is snooped
- [ ] Asterisk sends live slin16 media over WebSocket
- [ ] Detector receives the stream without intermediate WAV-file recording
- [ ] Rolling authenticity verdicts appear while the conversation is still active
- [ ] Events share a common call_id
- [ ] IVR/DTMF works
- [ ] Unanswered calls/voicemail behave predictably
- [ ] Killing the detector produces degraded state, call stays alive
- [ ] Webhook signatures work
- [ ] No passwords or raw audio in logs
- [ ] Real and synthetic test calls evaluated through telephony codec
- [ ] Demo runs twice in succession without manual repair

---

## Security Boundaries

```
Internet/LAN
    │
    ├── WSS :8089 ──────────→ Asterisk browser SIP (TLS + DTLS-SRTP)
    │
    └── HTTPS ──────────────→ Dashboard + webhook endpoint
                                │
localhost/private
    ├── Asterisk ARI :8088
    ├── Media adapter :8787
    ├── VoiceShield backend :8000
    └── SQLite ledger
```

**Critical rules:**
- SIP passwords → long random, never committed
- ARI → bind to localhost
- Audio → memory only, zeroed after analysis
- Webhooks → HMAC-SHA256 + timestamp + event-ID dedup
- Logs → structured JSON, never log passwords or raw audio
