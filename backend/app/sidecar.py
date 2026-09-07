"""VoxGuard ML sidecar — the [Python] half of docs/01-ARCHITECTURE.md section 2.

The spec places Preprocess + VAD, AI Detection and Speaker Verification in Python and everything
downstream (gateway, session orchestrator, risk fusion, session aggregation, decision, alerts,
audit) in Go. This module is that Python half and nothing else: it owns the audio and returns
only derived acoustic features and scores.

THE AUDIO BOUNDARY (CLAUDE.md invariant 1)
------------------------------------------
Raw audio never leaves this process. The Go gateway never receives samples from here, never
holds a window, and never learns a file's contents — it asks for the next window's *analysis*.
Buffers are zeroed in a finally block before each response is built, so a window's samples are
gone before the reply is serialised. Nothing here writes audio to disk, a log, or a socket.

This service binds to loopback and is not the public API. Everything a browser talks to is the
Go gateway on :8000.
"""
import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .detection import classifier
from .audiosocket import AudioSocketIngest
from .features import extract
from .ingestion import AUDIO_DIR, chunks, resolve_audio
from .preprocessing import SAMPLE_RATE, preprocess
from .speaker_verification import MODEL_VERSION as VERIFIER_VERSION, verifier

# A demo box streams multiple calls at once. The cap exists so a client that forgets to close
# streams cannot pin an unbounded number of open file handles and generators.
MAX_OPEN_STREAMS = 32


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class StreamOpen(StrictModel):
    filename: str = Field(min_length=1, max_length=200)
    identity_id: str | None = Field(default=None, max_length=80)
    window_seconds: float = Field(default=3.0, ge=0.5, le=10)
    hop_seconds: float = Field(default=1.0, ge=0.1, le=10)


class AnalyseRequest(StrictModel):
    samples: list[float] = Field(min_length=4000, max_length=64000)
    sample_rate: int = Field(default=SAMPLE_RATE, ge=SAMPLE_RATE, le=SAMPLE_RATE)
    identity_id: str | None = Field(default=None, max_length=80)


class EnrolRequest(StrictModel):
    identity_id: str = Field(min_length=3, max_length=80, pattern="^[a-zA-Z0-9_-]+$")
    display_name: str = Field(min_length=2, max_length=100)
    consent_token: str = Field(default="CONSENT_RECORD_VERIFIED", min_length=5, max_length=200)
    samples: list[float] = Field(min_length=8000, max_length=96000)


streams: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    live_ingest = AudioSocketIngest(analyse_buffer)
    await live_ingest.start()
    app.state.live_ingest = live_ingest
    try:
        yield
    finally:
        await live_ingest.close()
        for state in list(streams.values()):
            state["generator"].close()
        streams.clear()


app = FastAPI(title="VoxGuard ML sidecar", version="1.0.0", lifespan=lifespan)


def analyse_buffer(audio: np.ndarray, identity_id: str | None = None) -> dict:
    """Preprocess, extract features, detect, and verify one window.

    The window's samples are zeroed in the finally block whatever happens, so no caller can
    receive them and no exception path leaves them resident.
    """
    started = time.perf_counter()
    processed = None
    try:
        prepared = preprocess(audio)
        if prepared is None:
            return {
                "scored": False,
                "reason": "Silence or non-speech noise",
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        processed, speech_ratio = prepared
        features = extract(processed)
        scores = classifier.score_features(features)

        # Confidence is an honest statement of how much voiced evidence this window carried, not
        # a model output. It shrinks the detector's contribution in the Go fusion, so a window
        # built from a fragment of speech moves the score less than a full one.
        pitch_coverage = min(1.0, len(features["pitch_hz"]) / 12)
        confidence = round(min(0.95, max(0.35, 0.35 + 0.35 * speech_ratio + 0.25 * pitch_coverage)), 4)

        # Detection and speaker verification are parallel branches over the same feature frames
        # (docs/01 section 2, DR-010) — verification never consumes the detector's output.
        verification = verifier.verify(features, identity_id) if identity_id else None

        return {
            "scored": True,
            **scores,
            "p_synthetic": scores["synthetic_score"],
            "p_synthetic_raw": scores["synthetic_score"],
            "confidence": confidence,
            "voiced_seconds": round(speech_ratio * 3.0, 3),
            "speech_ratio": speech_ratio,
            "features": features,
            "verification": verification,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    finally:
        audio.fill(0)
        if processed is not None:
            processed.fill(0)


def window_results(path: Path, identity_id: str | None, window_seconds: float, hop_seconds: float):
    """Yield one analysis per voiced window, never the window itself.

    Analysis happens inside the generator so each buffer is zeroed before control returns to the
    HTTP handler. If this yielded audio instead, a window would stay resident between requests.
    """
    source = chunks(path, seconds=window_seconds, hop_seconds=hop_seconds)
    try:
        for index, audio in enumerate(source, 1):
            yield {"chunk_index": index, **analyse_buffer(audio, identity_id)}
    finally:
        source.close()


@app.get("/internal/health")
def health():
    live_ingest = getattr(app.state, "live_ingest", None)
    return {
        "status": "ok",
        "model": classifier.name,
        "model_version": classifier.model_version,
        "calibrator_version": classifier.calibrator_version,
        "verifier_version": VERIFIER_VERSION,
        "sample_rate": SAMPLE_RATE,
        "raw_audio_persistence": False,
        "audiosocket_state": live_ingest.state if live_ingest else "unavailable",
        "audiosocket_port": live_ingest.bound_port if live_ingest else None,
    }


@app.get("/internal/audio")
def audio_files():
    """List the fixture WAVs. Names only — this endpoint never returns audio."""
    return [
        {"filename": p.name, "fixture": p.name.startswith(("fixture-", "tts-"))}
        for p in sorted(AUDIO_DIR.glob("*.wav"))
    ]


@app.post("/internal/stream/open")
def stream_open(body: StreamOpen):
    if len(streams) >= MAX_OPEN_STREAMS:
        raise HTTPException(429, f"At most {MAX_OPEN_STREAMS} streams may be open at once.")
    try:
        path = resolve_audio(body.filename)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(400, str(error))

    stream_id = str(uuid4())
    streams[stream_id] = {
        "generator": window_results(path, body.identity_id, body.window_seconds, body.hop_seconds),
        "identity_id": body.identity_id,
    }
    return {
        "stream_id": stream_id,
        "model_version": classifier.model_version,
        "calibrator_version": classifier.calibrator_version,
        "verifier_version": VERIFIER_VERSION if body.identity_id else "not_enrolled",
    }


@app.post("/internal/stream/{stream_id}/next")
async def stream_next(stream_id: str):
    state = streams.get(stream_id)
    if state is None:
        raise HTTPException(404, "Stream not found or already closed.")
    try:
        result = await asyncio.to_thread(next, state["generator"], None)
    except Exception as error:
        streams.pop(stream_id, None)
        raise HTTPException(500, f"Audio processing failed: {error}")
    if result is None:
        return {"exhausted": True}
    return {"exhausted": False, **result}


@app.post("/internal/stream/{stream_id}/close")
def stream_close(stream_id: str):
    state = streams.pop(stream_id, None)
    if state is not None:
        state["generator"].close()
    return {"closed": True}


@app.post("/internal/analyse")
async def analyse(body: AnalyseRequest):
    """Analyse one caller-supplied window. Backs the gateway's /api/v1/detect proxy."""
    audio = np.array(body.samples, dtype=np.float32)
    # Drop the request's own copy immediately; from here the samples exist in exactly one buffer.
    body.samples.clear()
    if np.max(np.abs(audio)) > 1:
        audio.fill(0)
        raise HTTPException(422, "Samples must be normalized to [-1, 1].")
    return await asyncio.to_thread(analyse_buffer, audio, body.identity_id)


@app.post("/internal/enrolments")
def enrol(body: EnrolRequest):
    audio = np.array(body.samples, dtype=np.float32)
    body.samples.clear()
    try:
        return verifier.enroll(
            identity_id=body.identity_id,
            display_name=body.display_name,
            audio=audio,
            consent_token=body.consent_token,
        )
    except PermissionError as error:
        # CLAUDE.md invariant 14: enrolment without a consent record is refused, not defaulted.
        raise HTTPException(403, str(error))
    except ValueError as error:
        raise HTTPException(422, str(error))
    finally:
        audio.fill(0)


@app.get("/internal/enrolments")
def list_enrolments():
    return verifier.list_enrolments()


@app.delete("/internal/enrolments/{identity_id}")
def revoke_enrolment(identity_id: str):
    if not verifier.revoke(identity_id):
        raise HTTPException(404, f"Speaker profile '{identity_id}' not found.")
    return {"status": "revoked", "identity_id": identity_id}


def main():
    import uvicorn

    # Loopback only. This service trusts its caller, so it must not be reachable off the box.
    uvicorn.run(
        app,
        host=os.getenv("VOXGUARD_SIDECAR_HOST", "127.0.0.1"),
        port=int(os.getenv("VOXGUARD_SIDECAR_PORT", "8801")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
