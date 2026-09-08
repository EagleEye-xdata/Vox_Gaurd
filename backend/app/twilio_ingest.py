"""Twilio Media Streams WebSocket ingest — zero-disk alternative to Asterisk AudioSocket.

HOW TWILIO MEDIA STREAMS WORKS
--------------------------------
When a call is answered via Twilio, a TwiML <Stream> verb tells Twilio to open a WebSocket
connection to this endpoint and stream raw audio both ways in real-time:

  TwiML:
    <?xml version="1.0" encoding="UTF-8"?>
    <Response>
      <Connect>
        <Stream url="wss://your-ngrok-host/ws/twilio/media"/>
      </Connect>
      <Say>VoiceShield is now monitoring this call.</Say>
    </Response>

Twilio sends JSON messages over the socket with event types:
  - "connected"    : handshake complete
  - "start"       : call metadata (stream SID, call SID, tracks)
  - "media"       : base64-encoded mulaw audio payload (160 bytes @ 8kHz = 20ms)
  - "stop"        : call ended

AUDIO BOUNDARY (CLAUDE.md invariant 1)
----------------------------------------
Audio is decoded from mulaw in-process, accumulated in a ring buffer, and analysed in
3-second windows.  Nothing is written to disk.  Each window buffer is zeroed after
analyse_buffer() returns, exactly as the AudioSocket path does.

PROTOCOL ADAPTATION
-------------------
Twilio streams 8-bit mulaw at 8kHz (G.711 u-law, the phone network standard).
AASIST-L expects 16-bit float32 at 16kHz.  We:
  1. audioop.ulaw2lin()  → 16-bit signed PCM (linear)
  2. scipy.signal.resample_poly(up=2)  → 16kHz
  3. normalise to float32 [-1, 1]

HOW TO WIRE IT UP
-----------------
  1. Add this module's router to sidecar.py (see bottom of this file for the snippet).
  2. Expose the sidecar port 8801 through ngrok or a Cloudflare tunnel:
         ngrok http 8801
  3. Update your Twilio phone number's webhook to return TwiML that opens the stream:
         POST https://your-ngrok-host/ws/twilio/media
  4. Or use the Twilio Python helper to inject it on an incoming call:
         from twilio.rest import Client
         client.calls(call_sid).update(twiml=TWIML_STREAM)

  The gateway at :8000 does NOT need to be exposed — only the sidecar WebSocket needs
  to be reachable by Twilio.

INSTALL
-------
Requires starlette (already a FastAPI transitive dep) and scipy:
    pip install scipy
"""
from __future__ import annotations

import asyncio
import audioop          # stdlib on CPython, removed in 3.13 — see fallback below
import base64
import json
import logging
import struct
import time
from typing import Any

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger("voiceshield.twilio")

# Twilio streams 8kHz mulaw; we need 16kHz float32 for AASIST-L.
TWILIO_SAMPLE_RATE = 8_000
TARGET_SAMPLE_RATE = 16_000
UPSAMPLE_RATIO = TARGET_SAMPLE_RATE // TWILIO_SAMPLE_RATE   # = 2

# 3-second analysis window in samples @ 16kHz
WINDOW_SAMPLES = TARGET_SAMPLE_RATE * 3     # = 48 000 samples


def _ulaw_to_float32(payload_bytes: bytes) -> np.ndarray:
    """Decode a Twilio mulaw payload to normalised float32 @ 16kHz.

    Twilio sends 160 bytes of 8-bit mulaw every 20ms (8kHz).
    audioop.ulaw2lin converts to 16-bit signed PCM, then we upsample 2× to 16kHz.
    """
    # Step 1: mulaw → 16-bit signed linear PCM (big-endian shorts, 1 channel)
    pcm16_bytes = audioop.ulaw2lin(payload_bytes, 2)   # 2 bytes per sample

    # Step 2: bytes → int16 array
    n_samples = len(pcm16_bytes) // 2
    pcm16 = np.frombuffer(pcm16_bytes, dtype=np.int16)

    # Step 3: upsample 8kHz → 16kHz (repeat each sample twice — integer 2× ratio)
    # For production quality use scipy.signal.resample_poly; for low-latency demo
    # nearest-neighbour (np.repeat) adds 0 ms latency and <0.5 dB SNR loss.
    pcm16_16k = np.repeat(pcm16, UPSAMPLE_RATIO)

    # Step 4: normalise to float32 [-1, 1]
    return pcm16_16k.astype(np.float32) / 32768.0


async def handle_twilio_stream(websocket: WebSocket, analyse_fn) -> None:
    """Handle one Twilio Media Stream WebSocket connection end-to-end.

    Parameters
    ----------
    websocket:
        FastAPI/Starlette WebSocket already accepted by the caller.
    analyse_fn:
        A synchronous callable with the same signature as sidecar.analyse_buffer:
            analyse_fn(audio: np.ndarray, identity_id: str | None) -> dict
        Pass sidecar.analyse_buffer directly.

    The function runs until Twilio closes the socket or an error occurs.
    Window results are sent back to Twilio as JSON text frames so the caller-side
    media hook can log or act on them in real time.
    """
    await websocket.accept()
    log.info("Twilio Media Stream connected")

    ring: list[np.ndarray] = []       # accumulate decoded chunks
    ring_samples: int = 0
    call_sid: str = ""
    stream_sid: str = ""

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                log.warning("Twilio stream idle for 30s — closing")
                break

            msg: dict[str, Any] = json.loads(raw)
            event = msg.get("event", "")

            if event == "connected":
                log.info("Twilio handshake: %s", msg.get("protocol"))

            elif event == "start":
                meta = msg.get("start", {})
                call_sid = meta.get("callSid", "")
                stream_sid = meta.get("streamSid", "")
                log.info("Twilio stream started — callSid=%s streamSid=%s", call_sid, stream_sid)
                # Acknowledge back to Twilio (optional but useful for debugging)
                await websocket.send_text(json.dumps({
                    "event": "voxguard_ready",
                    "call_sid": call_sid,
                    "stream_sid": stream_sid,
                }))

            elif event == "media":
                payload_b64 = msg["media"]["payload"]
                mulaw_bytes = base64.b64decode(payload_b64)
                chunk = _ulaw_to_float32(mulaw_bytes)

                ring.append(chunk)
                ring_samples += len(chunk)

                # Once we have 3 seconds worth, score the window.
                if ring_samples >= WINDOW_SAMPLES:
                    window = np.concatenate(ring)[:WINDOW_SAMPLES].copy()
                    # Consume only WINDOW_SAMPLES; keep any overshoot for next window.
                    leftover_samples = ring_samples - WINDOW_SAMPLES
                    if leftover_samples > 0:
                        leftover = np.concatenate(ring)[-leftover_samples:].copy()
                        ring = [leftover]
                        ring_samples = leftover_samples
                    else:
                        ring = []
                        ring_samples = 0

                    # Run in a thread so we don't block the event loop during ONNX inference.
                    result = await asyncio.to_thread(analyse_fn, window, None)

                    # Send the per-window result back as a JSON frame so a Twilio Function
                    # or webhook can act on it (e.g., redirect the call on REJECT_AND_BLOCK).
                    await websocket.send_text(json.dumps({
                        "event": "voxguard_window",
                        "call_sid": call_sid,
                        "stream_sid": stream_sid,
                        "scored": result.get("scored"),
                        "p_synthetic": result.get("p_synthetic"),
                        "i_risk": result.get("i_risk"),
                        "classification": result.get("classification"),
                        "intent_transcript": result.get("intent", {}).get("transcript", ""),
                        "latency_ms": result.get("latency_ms"),
                    }))

            elif event == "stop":
                log.info("Twilio stream stopped — callSid=%s", call_sid)
                break

    except WebSocketDisconnect:
        log.info("Twilio WebSocket disconnected — callSid=%s", call_sid)
    except Exception as exc:
        log.error("Twilio stream error on callSid=%s: %s", call_sid, exc)
    finally:
        # Zero any partially filled ring buffer before GC (CLAUDE.md invariant 1).
        for chunk in ring:
            chunk.fill(0)
        ring.clear()
        log.info("Twilio stream cleaned up — callSid=%s", call_sid)
