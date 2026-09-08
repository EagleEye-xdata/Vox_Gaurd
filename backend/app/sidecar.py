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
import base64
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .aasist import AASISTLClassifier
from .detection import HEURISTIC_FALLBACK, classifier
from .audiosocket import AudioSocketIngest
from .features import extract
from .ingestion import AUDIO_DIR, chunks, resolve_audio
from .preprocessing import SAMPLE_RATE, preprocess
from .speaker_verification import MODEL_VERSION as VERIFIER_VERSION, verifier
from . import tts
from . import intent as intent_scorer
from . import prosody as prosody_analyser
from .session_drift import drift_checker
from . import privacy_logger

# A demo box streams multiple calls at once. The cap exists so a client that forgets to close
# streams cannot pin an unbounded number of open file handles and generators.
MAX_OPEN_STREAMS = 32

log = logging.getLogger("voiceshield.sidecar")


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




class SpeakRequest(StrictModel):
    text: str = Field(min_length=1, max_length=400)
    voice: str | None = Field(default=None, max_length=8)
    identity_id: str | None = Field(default=None, max_length=80)


class EnrolRequest(StrictModel):
    identity_id: str = Field(min_length=3, max_length=80, pattern="^[a-zA-Z0-9_-]+$")
    display_name: str = Field(min_length=2, max_length=100)
    consent_token: str = Field(default="CONSENT_RECORD_VERIFIED", min_length=5, max_length=200)
    samples: list[float] = Field(min_length=8000, max_length=96000)


streams: dict[str, dict] = {}

# Strong references to in-flight demo calls. asyncio only weakly references running tasks, so
# dropping these would let the garbage collector hang up a call mid-sentence.
ai_calls: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    live_ingest = AudioSocketIngest(analyse_buffer)
    await live_ingest.start()
    app.state.live_ingest = live_ingest
    try:
        yield
    finally:
        for call in list(ai_calls):
            call.cancel()
        if ai_calls:
            await asyncio.gather(*ai_calls, return_exceptions=True)
        ai_calls.clear()
        await live_ingest.close()
        for state in list(streams.values()):
            state["generator"].close()
        streams.clear()


app = FastAPI(title="VoxGuard ML sidecar", version="1.0.0", lifespan=lifespan)


def analyse_buffer(audio: np.ndarray, identity_id: str | None = None) -> dict:
    """Preprocess, extract features, detect, verify, and score intent for one window.

    Three parallel branches run over the same window:
      • AI detection   (AASIST-L or heuristic fallback)  → P_synth
      • Speaker verify (ECAPA-TDNN cosine match)          → S_bio
      • Intent scoring (Whisper STT + phrase classifier)  → I_risk

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
        try:
            # AASIST-L (when active) scores the raw, unfiltered `audio` window; the heuristic
            # scores the hand-crafted `features` built from the filtered/gated `processed` signal.
            # `analyze()` picks the right input per detector -- see SpoofClassifier.analyze.
            scores = classifier.analyze(audio, features)
        except Exception as error:
            # A detector that throws mid-call must degrade this one window, not the request
            # (CLAUDE.md invariant 2: never a silently-passing score, but also never a crash).
            log.error("Detector %s failed on this window, falling back to %s: %s",
                      classifier.name, HEURISTIC_FALLBACK.name, error)
            scores = HEURISTIC_FALLBACK.score_features(features)

        # Confidence is an honest statement of how much voiced evidence this window carried, not
        # a model output. It shrinks the detector's contribution in the Go fusion, so a window
        # built from a fragment of speech moves the score less than a full one.
        pitch_coverage = min(1.0, len(features["pitch_hz"]) / 12)
        confidence = round(min(0.95, max(0.35, 0.35 + 0.35 * speech_ratio + 0.25 * pitch_coverage)), 4)

        # Detection and speaker verification are parallel branches over the same feature frames
        # (docs/01 section 2, DR-010) — verification never consumes the detector's output.
        verification = verifier.verify(features, identity_id) if identity_id else None

        # --- Intent / Content Risk (Step 5) ----------------------------------------------
        intent_audio = np.array(audio, dtype=np.float32)
        try:
            intent_result = intent_scorer.score(intent_audio, SAMPLE_RATE)
        except Exception as intent_err:
            log.warning("Intent scorer failed on window; marking I_risk unavailable: %s", intent_err)
            intent_result = intent_scorer.IntentResult()
        finally:
            # score() promises not to retain the buffer; erase this sidecar-owned
            # copy even when a scorer implementation raises unexpectedly.
            intent_audio.fill(0)

        # A missing or failed transcription is not evidence of benign intent.
        # Send null so Go renormalises this signal out instead of accepting a
        # fabricated 0.0 as a low-risk score.
        i_risk = (
            intent_result.i_risk
            if intent_result.whisper_available and intent_result.error is None
            else None
        )

        # --- Prosody & Behavioural Analysis (PS requirement — separate layer) --------------
        # Runs on the pre-filtered `processed` signal (VAD-gated, bandpass-filtered).
        # prosody_risk is exposed to the Go fusion as an additional explainability signal
        # and surfaced in the dashboard as a dedicated prosody score.
        try:
            prosody_result = prosody_analyser.analyse(np.array(processed, dtype=np.float32), SAMPLE_RATE)
        except Exception as pr_err:
            log.warning("Prosody analyser failed: %s", pr_err)
            prosody_result = prosody_analyser.ProsodyResult()

        # --- Cross-session drift check (PS requirement: historical sample comparison) -----
        drift_result = None
        if identity_id and verification and verification.get("reference_available"):
            try:
                from .speaker_verification import _embedding_from_feats
                cur_emb = _embedding_from_feats(features, speech_ratio)
                drift_result = drift_checker.check_drift(identity_id, cur_emb)
                # Record this window's embedding in history ONLY if verification passed
                if (verification.get("match_score") or 0) > 0.60:
                    drift_checker.record_embedding(
                        identity_id, cur_emb.copy(),
                        match_score=verification.get("match_score")
                    )
            except Exception as dr_err:
                log.warning("Session drift check failed: %s", dr_err)

        result_dict = {
            "scored": True,
            **scores,
            "p_synthetic": scores["synthetic_score"],
            "p_synthetic_raw": scores["synthetic_score"],
            "confidence": confidence,
            "voiced_seconds": round(speech_ratio * 3.0, 3),
            "speech_ratio": speech_ratio,
            "features": features,
            "verification": verification,
            # Intent branch — I_risk for Go fusion
            "i_risk": i_risk,
            "intent": {
                "transcript": intent_result.transcript,
                "matched_phrases": intent_result.matched_phrases,
                "heuristic_hits": intent_result.heuristic_hits,
                "whisper_available": intent_result.whisper_available,
                "language_detected": intent_result.language_detected,
                "latency_ms": intent_result.latency_ms,
                "error": intent_result.error,
                "model_diagnostics": intent_result.model_diagnostics,
            },
            # Prosody branch — dedicated behavioral analysis (PS explicit requirement)
            "prosody_risk": prosody_result.prosody_risk,
            "prosody": {
                "pitch_mean_hz": prosody_result.pitch_mean_hz,
                "pitch_std_hz": prosody_result.pitch_std_hz,
                "pitch_cv": prosody_result.pitch_cv,
                "pitch_contour_smoothness": prosody_result.pitch_contour_smoothness,
                "pause_count": prosody_result.pause_count,
                "mean_pause_duration_ms": prosody_result.mean_pause_duration_ms,
                "rhythm_regularity": prosody_result.rhythm_regularity,
                "energy_cv": prosody_result.energy_cv,
                "local_rate_cv": prosody_result.local_rate_cv,
                "subscores": prosody_result.subscores,
                "analysis_available": prosody_result.analysis_available,
                "error": prosody_result.error,
            },
            # Cross-session drift (PS: compare against historical genuine samples)
            "drift_score": drift_result.drift_score if drift_result else None,
            "session_drift": {
                "drift_score": drift_result.drift_score if drift_result else None,
                "history_size": drift_result.history_size if drift_result else 0,
                "mean_historical_similarity": drift_result.mean_historical_similarity if drift_result else None,
                "drift_available": drift_result.drift_available if drift_result else False,
                "reason": drift_result.reason if drift_result else "identity_id_not_provided",
            },
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }

        # --- Anonymised compliance log (PS: edge inference + feature-only logging) ---------
        try:
            privacy_logger.log_window(
                call_id="live",
                window_index=0,
                identity_id=identity_id,
                p_synthetic=result_dict["p_synthetic"],
                prosody_risk=prosody_result.prosody_risk,
                i_risk=i_risk,
                drift_score=drift_result.drift_score if drift_result else None,
                match_score=(verification or {}).get("match_score"),
                features=features,
            )
        except Exception as pl_err:
            log.debug("Privacy log write failed (non-critical): %s", pl_err)

        return result_dict
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


@app.get("/internal/compliance")
def compliance():
    """Return machine-readable compliance statement evidencing on-device edge inference.

    PS requirement: privacy/compliance + on-device/edge inference support.
    This endpoint exists so integration partners (banks, telecom) can verify the system's
    data-handling claims without auditing source code.
    """
    return privacy_logger.compliance_statement()


@app.get("/internal/compliance/audit")
def audit_log(call_id: str | None = None, limit: int = 100):
    """Export anonymised audit records (scores + fingerprints, never audio).

    PS requirement: anonymized feature-only logging for compliance.
    """
    records = privacy_logger.export_report(call_id=call_id, max_records=limit)
    return {"records": records, "count": len(records)}


@app.get("/internal/drift/{identity_id}")
def drift_history(identity_id: str):
    """Return cross-session embedding history summary for an identity.

    PS requirement: compare ongoing call against historical genuine samples.
    Shows stored call count, per-call match scores, and embedding hashes (not vectors).
    """
    return drift_checker.history_summary(identity_id)


@app.get("/internal/health")
def health():
    live_ingest = getattr(app.state, "live_ingest", None)
    is_aasist = isinstance(classifier, AASISTLClassifier)
    return {
        "status": "ok",
        "model": classifier.name,
        "model_version": classifier.model_version,
        "calibrator_version": classifier.calibrator_version,
        "verifier_version": VERIFIER_VERSION,
        "sample_rate": SAMPLE_RATE,
        "raw_audio_persistence": False,
        # Additive fields: makes it possible to tell "AASIST-L loaded" apart from "running on the
        # heuristic fallback" without parsing model_version strings.
        "detector_mode": "aasist-l" if is_aasist else "heuristic-fallback",
        "detector_provider": classifier.provider if is_aasist else None,
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




def _synthesize_and_score(text: str, voice: str | None, identity_id: str | None) -> dict:
    """Generate one TTS turn and score that exact waveform with the live detector.

    The point of this endpoint is that the number the operator sees is the number the model
    returned. So the audio handed back for playback and the audio handed to `analyse_buffer` are
    the same samples -- there is no separate "demo score" path, and no hardcoded constant anywhere
    between the vocoder and the UI.

    Windows are the same 3.0 s the streaming pipeline uses, scored independently and reported
    individually, because one aggregate number would hide a detector that only fired on part of
    the turn. Aggregation is over *scored* windows only: a window `preprocess()` rejected as
    silence carries no evidence, and CLAUDE.md invariant 2 forbids letting it read as a pass.
    """
    audio, chosen_voice, model_id = tts.SYNTHESIZER.synthesize(text, voice)
    duration = len(audio) / tts.SAMPLE_RATE
    # Encode for playback first: `analyse_buffer` zeroes every buffer it is given.
    wav_bytes = tts.to_wav_bytes(audio, tts.SAMPLE_RATE)

    window_samples = int(3.0 * SAMPLE_RATE)
    windows = []
    try:
        for start in range(0, max(1, len(audio)), window_samples):
            segment = audio[start:start + window_samples]
            if len(segment) < 4000:  # too short to carry a pitch track; not evidence either way
                continue
            result = analyse_buffer(np.array(segment, dtype=np.float32), identity_id)
            windows.append({
                "window_index": len(windows) + 1,
                "scored": result["scored"],
                "p_synthetic": result.get("p_synthetic"),
                "classification": result.get("classification"),
                "confidence": result.get("confidence"),
                "reason": result.get("reason"),
                "latency_ms": result.get("latency_ms"),
            })
    finally:
        audio.fill(0)

    scored = [w for w in windows if w["scored"] and w["p_synthetic"] is not None]
    if scored:
        values = [float(w["p_synthetic"]) for w in scored]
        detection = {
            "status": "SCORED",
            "p_synthetic_max": round(max(values), 4),
            "p_synthetic_mean": round(sum(values) / len(values), 4),
            "windows_scored": len(scored),
        }
    else:
        # No voiced window survived preprocessing. Absence of evidence is UNKNOWN, never LOW.
        detection = {
            "status": "UNKNOWN",
            "p_synthetic_max": None,
            "p_synthetic_mean": None,
            "windows_scored": 0,
            "reason": "No voiced window in the generated turn could be scored.",
        }

    return {
        "audio_wav_base64": base64.b64encode(wav_bytes).decode("ascii"),
        "sample_rate": tts.SAMPLE_RATE,
        "duration_seconds": round(duration, 3),
        "voice": chosen_voice,
        "tts_model_id": model_id,
        # Every decision carries the versions that produced it (CLAUDE.md invariant 8). The TTS
        # model is part of that provenance here: it is what generated the audio being scored.
        "tts_model_version": f"{model_id}@mms-tts-vits",
        "tts_licence": "CC-BY-NC-4.0 (research/demo only)",
        "detector": classifier.name,
        "model_versions": {
            "detector": classifier.model_version,
            "calibrator": classifier.calibrator_version,
            "tts": f"{model_id}@mms-tts-vits",
        },
        "windows": windows,
        "detection": detection,
    }


@app.post("/internal/tts/speak")
async def tts_speak(body: SpeakRequest):
    """Synthesise the bot's turn locally and return it with its real detector score.

    Failures are reported, never papered over with silence: a caller that got a 503 here knows the
    turn did not happen, whereas an empty WAV plus a score would be a fabricated result.
    """
    try:
        return await asyncio.to_thread(
            _synthesize_and_score, body.text, body.voice, body.identity_id)
    except tts.MMSTTSUnavailable as error:
        raise HTTPException(503, f"Neural TTS unavailable: {error}")
    except ValueError as error:
        raise HTTPException(422, str(error))


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


# ---------------------------------------------------------------------------
# Twilio Media Streams WebSocket ingest
# ---------------------------------------------------------------------------
# Register lazily so missing `audioop` (Python 3.13+) does not break the sidecar
# when Twilio ingest is not needed.
try:
    from . import twilio_ingest as _twilio

    @app.websocket("/ws/twilio/media")
    async def twilio_media_stream(websocket):
        """Accept a Twilio Media Stream and pipe audio through the full detection pipeline.

        TwiML to activate:
            <Connect><Stream url="wss://YOUR_HOST/ws/twilio/media"/></Connect>
        """
        await _twilio.handle_twilio_stream(websocket, analyse_buffer)

except ImportError as _twilio_import_err:
    log.warning(
        "Twilio ingest not available (%s). "
        "Install scipy and ensure Python <= 3.12 (audioop removed in 3.13).",
        _twilio_import_err,
    )


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
