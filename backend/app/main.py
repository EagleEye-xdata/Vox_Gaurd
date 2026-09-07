"""VoiceShield / VoxGuard FastAPI Core Service (v2 Architecture).

Implements endpoints and streaming pipelines conforming to:
- docs/01-ARCHITECTURE.md
- docs/02-API_CONTRACTS.md
- docs/03-RISK_SCORING_SPEC.md
- docs/04-SESSION_SCORING_SPEC.md
- docs/09-UX_SPEC.md
"""
import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .ingestion import AUDIO_DIR, chunks, resolve_audio
from .preprocessing import preprocess
from .features import extract
from .detection import classifier
from .speaker_verification import verifier
from .decision import decision_service
from .risk_scoring import SessionRisk, POLICY, fuse, score_window
from .alerts import make_alert, assign_alert, resolve_alert, lodge_appeal
from .ledger import Ledger
from .schemas import (
    Start, AudioChunk, Scores, AlertRequest, AlertAssignRequest,
    AlertResolveRequest, AppealCreateRequest, DecisionOverrideRequest,
    EnrolmentCreateRequest, LedgerEvent
)

calls: dict[str, dict] = {}
alerts: list[dict] = []
tasks: set[asyncio.Task] = set()
ledger = Ledger(os.getenv("LEDGER_PATH", str(Path(__file__).resolve().parents[1] / "data/ledger.db")))


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="VoiceShield AI / VoxGuard", version="2.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


def analyze(audio: np.ndarray, language: str = "en", adversarial: bool = False):
    """
    Analyzes an audio buffer for synthetic speech markers.
    Invariant 1: Audio buffer is strictly zeroed in finally block; never written to disk.
    """
    started = time.perf_counter()
    processed = None
    try:
        prepared = preprocess(audio)
        if prepared is None:
            return {
                "scored": False,
                "reason": "Silence or non-speech noise",
                "latency_ms": round((time.perf_counter() - started) * 1000, 2)
            }
        processed, speech_ratio = prepared
        features = extract(processed)
        scores = classifier.score_features(features) if hasattr(classifier, "score_features") else {
            "spectral_score": classifier.predict(processed),
            "prosody_score": 0.5,
            "speaker_match_score": None,
            "model": classifier.name
        }
        pitch_coverage = min(1.0, len(features["pitch_hz"]) / 12)
        confidence = round(min(0.95, max(0.35, 0.35 + 0.35 * speech_ratio + 0.25 * pitch_coverage)), 4)
        return {
            "scored": True,
            **scores,
            "p_synthetic": scores["synthetic_score"],
            "p_synthetic_raw": scores["synthetic_score"],
            "confidence": confidence,
            "language": language,
            "language_supported": language in POLICY["supported_languages"],
            "adversarial_flag": adversarial,
            "adversarial_flag_source": "simulated" if adversarial else "none",
            "voiced_seconds": round(speech_ratio * 3.0, 3),
            "features": features,
            "speech_ratio": speech_ratio,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    finally:
        audio.fill(0)
        if processed is not None:
            processed.fill(0)


def public(call: dict) -> dict:
    return {k: v for k, v in call.items() if k not in {"rolling", "events"}}


def get_call(call_id: str) -> dict:
    if call_id not in calls:
        raise HTTPException(404, "Call not found")
    return calls[call_id]


def build_session_summary(call: dict) -> dict:
    """Creates a comprehensive SessionSummary payload per docs/04 §7."""
    return {
        "session_id": call["call_id"],
        "label": call.get("label", "Simulated Call"),
        "filename": call.get("filename"),
        "status": call.get("status", "completed"),
        "started_at": call.get("started_at"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "chunks_processed": call.get("chunks_processed", 0),
        "windows_scored": call.get("windows_scored", 0),
        "unassessed_windows": call.get("unassessed_windows", 0),
        "peak_score": call.get("peak_score"),
        "final_session_score": call.get("risk_score"),
        "final_band": call.get("band", "UNKNOWN"),
        "final_decision": call.get("decision", "ALLOW"),
        "decision_details": call.get("decision_details"),
        "escalation_count": call.get("escalation_seq", 0),
        "degraded": call.get("degraded", False),
        "degraded_reasons": call.get("degraded_reasons", []),
        "band_timeline": call.get("band_timeline", []),
        "enrolled_identity": call.get("identity_id"),
        "policy_version": call.get("policy_version", POLICY["version"]),
        "model_versions": call.get("model_versions", {}),
    }


def ensure_alert(call: dict, alert_key: str | None = None) -> dict:
    if alert_key is None:
        if call.get("band") not in {"MEDIUM", "HIGH"}:
            raise HTTPException(409, "The session has not reached an elevated risk band.")
        alert_key = f"manual:{call['call_id']}:{call['band']}:{call.get('escalation_seq', 0)}"
    existing = next((a for a in alerts if a["id"] == alert_key), None)
    if existing:
        existing["risk_score"] = call["risk_score"]
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
        return existing
    alert = make_alert(call["call_id"], call["risk_score"], call["band"], alert_key)
    alert["policy_version"] = POLICY["version"]
    alert["model_versions"] = call["model_versions"]
    alerts.append(alert)
    ledger.append({
        "call_id": call["call_id"],
        "event_type": "alert",
        "risk_score": call["risk_score"],
        "band": call["band"],
        "policy_version": POLICY["version"],
        "model_versions": call["model_versions"],
    })
    return alert


async def run_call(call: dict, path: Path, interval: float):
    source = chunks(path)
    try:
        for index, audio in enumerate(source, 1):
            if call["status"] == "stopped":
                break
            result = await asyncio.to_thread(analyze, audio, call["language"], call["simulate_adversarial_input"])
            call["chunks_processed"] = index
            call["latency_ms"] = result["latency_ms"]
            
            if result["scored"]:
                detector_output = None if call["simulate_detector_failure"] else result
                
                # Speaker verification against enrolled identity if provided
                verification = None
                if call.get("identity_id"):
                    verification = verifier.verify(result["features"], call["identity_id"])
                    call["speaker_verification"] = verification
                
                window = score_window(detector_output, call["context"], verification)
                verdict = call["rolling"].update(window)
                call.update(verdict)
                call["risk_score"] = verdict["session_score"]
                
                # Automated decision & actions via DecisionService
                decision_info = decision_service.decide(
                    {"session_id": call["call_id"], "band": verdict["band"], "session_score": verdict["session_score"], "degraded": window["degraded"]},
                    call["context"]
                )
                call["decision"] = decision_info["decision"]
                call["decision_details"] = decision_info
                
                public_result = result if detector_output is not None else {
                    "scored": True,
                    "latency_ms": result["latency_ms"],
                    "features": result["features"],
                    "speech_ratio": result["speech_ratio"],
                }
                call["latest"] = {**public_result, **window, "decision": decision_info}
                call["degraded"] = window["degraded"]
                call["degraded_reasons"] = window["degraded_reasons"]
                call["contributing_factors"] = window["contributing_factors"]
                call["applied_floors"] = window["applied_floors"]
                
                if window["window_score"] is None:
                    call["unassessed_windows"] += 1
                else:
                    call["windows_scored"] += 1
                    ledger.append({
                        "call_id": call["call_id"],
                        "event_type": "observation",
                        "risk_score": call["risk_score"],
                        "band": call["band"],
                        "policy_version": POLICY["version"],
                        "model_versions": call["model_versions"]
                    })
                
                if verdict["alert_key"]:
                    ensure_alert(call, verdict["alert_key"])
            else:
                call["dropped_chunks"] += 1
                
            call["events"].append({"type": "chunk", "chunk": index, **result, "call": public(call)})
            await asyncio.sleep(interval)
            
        if call["status"] != "stopped":
            call["status"] = "completed"
            
        if call["risk_score"] is not None:
            summary = build_session_summary(call)
            call["session_summary"] = summary
            ledger.append({
                "call_id": call["call_id"],
                "event_type": "call_completed",
                "risk_score": call["risk_score"],
                "band": call["band"],
                "policy_version": POLICY["version"],
                "model_versions": call["model_versions"]
            })
    except Exception as err:
        call["status"] = "error"
        call["error"] = f"Audio processing failed: {err}"
    finally:
        source.close()
        call["events"].append({"type": "complete", "call": public(call), "session_summary": call.get("session_summary")})


# --------------------------------------------------------------------------
# API Endpoints
# --------------------------------------------------------------------------

@app.get("/api/v1/health")
def health():
    return {
        "status": "ok",
        "model": classifier.name,
        "mode": "v2 full-pipeline simulation",
        "raw_audio_persistence": False,
        "policy_version": POLICY["version"],
    }


@app.get("/api/v1/audio")
def audio_files():
    return [
        {
            "filename": p.name,
            "fixture": p.name.startswith(("fixture-", "tts-")),
        }
        for p in sorted(AUDIO_DIR.glob("*.wav"))
    ]


@app.get("/api/v1/calls")
def list_calls():
    return [public(c) for c in reversed(list(calls.values()))]


@app.post("/api/v1/stream/start")
async def start(body: Start):
    if sum(c["status"] == "streaming" for c in calls.values()) >= 3:
        raise HTTPException(429, "Three simultaneous demo calls are supported.")
    try:
        path = resolve_audio(body.filename)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(400, str(error))
    if len(calls) >= 100:
        old = next((k for k, c in calls.items() if c["status"] != "streaming"), None)
        if old:
            del calls[old]
    call_id = str(uuid4())
    model_versions = {
        "detector": classifier.model_version,
        "calibrator": classifier.calibrator_version,
        "verifier": "verifier-v1.3.0" if body.identity_id else "not_enrolled",
    }
    call = {
        "call_id": call_id,
        "label": body.label,
        "filename": body.filename,
        "context": body.context.model_dump(),
        "identity_id": body.identity_id,
        "status": "streaming",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "risk_score": None,
        "authenticity_score": None,
        "band": "UNKNOWN",
        "decision": "WARN",
        "decision_details": None,
        "history": [],
        "rolling": SessionRisk(call_id),
        "events": [],
        "chunks_processed": 0,
        "windows_scored": 0,
        "unassessed_windows": 0,
        "dropped_chunks": 0,
        "latest": None,
        "latency_ms": None,
        "degraded": False,
        "degraded_reasons": [],
        "contributing_factors": [],
        "applied_floors": [],
        "policy_version": POLICY["version"],
        "model_versions": model_versions,
        "simulate_detector_failure": body.simulate_detector_failure,
        "language": body.language,
        "language_supported": body.language in POLICY["supported_languages"],
        "simulate_adversarial_input": body.simulate_adversarial_input,
        "band_timeline": [{"window": 0, "band": "UNKNOWN"}],
    }
    calls[call_id] = call
    task = asyncio.create_task(run_call(call, path, body.interval))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {"call_id": call_id}


@app.post("/api/v1/stream/{call_id}/stop")
def stop(call_id: str):
    call = get_call(call_id)
    if call["status"] == "streaming":
        call["status"] = "stopped"
        summary = build_session_summary(call)
        call["session_summary"] = summary
    return public(call)


@app.post("/api/v1/sessions/{call_id}/close")
def close_session(call_id: str):
    """Closes session and returns structured SessionSummary per docs/02 §2 and docs/04 §7."""
    call = get_call(call_id)
    if call["status"] == "streaming":
        call["status"] = "stopped"
    summary = build_session_summary(call)
    call["session_summary"] = summary
    ledger.append({
        "call_id": call["call_id"],
        "event_type": "call_completed",
        "risk_score": call.get("risk_score") or 0.0,
        "band": call.get("band", "UNKNOWN"),
        "policy_version": POLICY["version"],
        "model_versions": call.get("model_versions", {}),
    })
    return summary


@app.websocket("/ws/audio/{call_id}")
async def websocket(websocket: WebSocket, call_id: str):
    await websocket.accept()
    if call_id not in calls:
        await websocket.close(code=1008)
        return
    call, cursor = calls[call_id], 0
    try:
        while True:
            while cursor < len(call["events"]):
                event = call["events"][cursor]
                await websocket.send_json(event)
                cursor += 1
                if event["type"] == "complete":
                    await websocket.close()
                    return
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass


@app.post("/api/v1/detect")
async def detect(body: AudioChunk):
    audio = np.array(body.samples, dtype=np.float32)
    body.samples.clear()
    if np.max(np.abs(audio)) > 1:
        audio.fill(0)
        raise HTTPException(422, "Samples must be normalized to [-1, 1].")
    return await asyncio.to_thread(analyze, audio)


@app.post("/api/v1/risk-score")
def risk(body: Scores):
    score = fuse(body.spectral_score, body.prosody_score, body.speaker_match_score, body.context.model_dump())
    return {
        "risk_score": score,
        "authenticity_score": round(100 - score, 2),
        "policy_version": POLICY["version"],
        "aggregation": "single-window compatibility endpoint; sessions use EWMA, decaying peak, and hysteresis",
    }


@app.get("/api/v1/risk-score/{call_id}")
def current_risk(call_id: str):
    return public(get_call(call_id))


# --------------------------------------------------------------------------
# Decision & Override Endpoints (docs/02 §8)
# --------------------------------------------------------------------------

@app.post("/api/v1/decisions/{call_id}/override")
def override_decision(call_id: str, body: DecisionOverrideRequest):
    """Registers an authenticated supervisor override for a session decision."""
    call = get_call(call_id)
    try:
        override_record = decision_service.override(
            session_id=call_id,
            decision=body.decision,
            reason=body.reason,
            supervisor_id=body.supervisor_id,
            role=body.role,
        )
    except (ValueError, PermissionError) as err:
        raise HTTPException(400, str(err))

    call["decision"] = body.decision
    call["decision_details"] = override_record
    ledger.append({
        "call_id": call_id,
        "event_type": "override",
        "risk_score": call.get("risk_score") or 0.0,
        "band": call.get("band", "UNKNOWN"),
        "policy_version": POLICY["version"],
        "model_versions": call.get("model_versions", {}),
    })
    return override_record


# --------------------------------------------------------------------------
# Speaker Enrolment Endpoints (docs/02 §4)
# --------------------------------------------------------------------------

@app.post("/api/v1/enrolments")
def enroll_speaker(body: EnrolmentCreateRequest):
    """Registers a consented speaker profile voice embedding."""
    audio = np.array(body.samples, dtype=np.float32)
    try:
        res = verifier.enroll(
            identity_id=body.identity_id,
            display_name=body.display_name,
            audio=audio,
            consent_token=body.consent_token,
        )
        return res
    except PermissionError as perm_err:
        raise HTTPException(403, str(perm_err))
    except ValueError as val_err:
        raise HTTPException(422, str(val_err))
    finally:
        audio.fill(0)


@app.get("/api/v1/enrolments")
def list_enrolments():
    """Lists enrolled speaker identities."""
    return verifier.list_enrolments()


@app.delete("/api/v1/enrolments/{identity_id}")
def revoke_enrolment(identity_id: str):
    """Revokes and erases speaker embedding within privacy SLA."""
    success = verifier.revoke(identity_id)
    if not success:
        raise HTTPException(404, f"Speaker profile '{identity_id}' not found.")
    return {"status": "revoked", "identity_id": identity_id}


# --------------------------------------------------------------------------
# Alert Management & Appeal Endpoints (docs/02 §10, docs/09 §5)
# --------------------------------------------------------------------------

@app.post("/api/v1/alerts")
def create_alert(body: AlertRequest):
    return ensure_alert(get_call(body.call_id))


@app.get("/api/v1/alerts")
def list_alerts():
    return alerts[::-1]


@app.post("/api/v1/alerts/{alert_id}/assign")
def assign_alert_endpoint(alert_id: str, body: AlertAssignRequest):
    alert = next((a for a in alerts if a["id"] == alert_id), None)
    if alert is None:
        raise HTTPException(404, "Alert not found")
    return assign_alert(alert, body.assignee_id)


@app.post("/api/v1/alerts/{alert_id}/resolve")
def resolve_alert_endpoint(alert_id: str, body: AlertResolveRequest):
    alert = next((a for a in alerts if a["id"] == alert_id), None)
    if alert is None:
        raise HTTPException(404, "Alert not found")
    try:
        resolved = resolve_alert(alert, body.outcome, body.notes, body.resolver_id)
        ledger.append({
            "call_id": alert["call_id"],
            "event_type": "decision",
            "risk_score": alert["risk_score"],
            "band": alert["band"],
            "policy_version": POLICY["version"],
            "model_versions": alert["model_versions"],
        })
        return resolved
    except ValueError as err:
        raise HTTPException(422, str(err))


@app.post("/api/v1/alerts/{alert_id}/appeal")
def lodge_appeal_endpoint(alert_id: str, body: AppealCreateRequest):
    alert = next((a for a in alerts if a["id"] == alert_id), None)
    if alert is None:
        raise HTTPException(404, "Alert not found")
    try:
        ticket = lodge_appeal(alert, body.reason, body.appellant_type, body.contact_info)
        return ticket
    except ValueError as err:
        raise HTTPException(422, str(err))


@app.post("/api/v1/alerts/{alert_id}/escalate")
def escalate(alert_id: str):
    alert = next((a for a in alerts if a["id"] == alert_id), None)
    if alert is None:
        raise HTTPException(404, "Alert not found")
    if alert["status"] != "escalated":
        ledger.append({
            "call_id": alert["call_id"],
            "event_type": "escalation",
            "risk_score": alert["risk_score"],
            "band": alert["band"],
            "policy_version": POLICY["version"],
            "model_versions": alert["model_versions"],
        })
        alert["status"] = "escalated"
    return alert


# --------------------------------------------------------------------------
# Cryptographic Audit Ledger Endpoints (docs/02 §9)
# --------------------------------------------------------------------------

@app.post("/api/v1/ledger/log")
def log_event(body: LedgerEvent):
    return ledger.append(body.model_dump())


@app.get("/api/v1/ledger")
def ledger_entries():
    return ledger.entries()[::-1]


@app.get("/api/v1/ledger/verify/{hash}")
def verify(hash: str):
    return ledger.verify(None if hash == "all" else hash)
