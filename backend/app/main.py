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
from .risk_scoring import SessionRisk, POLICY, fuse, score_window
from .alerts import make_alert
from .ledger import Ledger
from .schemas import Start, AudioChunk, Scores, AlertRequest, LedgerEvent

calls, alerts, tasks = {}, [], set()
ledger = Ledger(os.getenv("LEDGER_PATH", str(Path(__file__).resolve().parents[1]/"data/ledger.db")))

@asynccontextmanager
async def lifespan(app):
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

app = FastAPI(title="VoiceShield AI", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

def analyze(audio):
    started = time.perf_counter()
    processed = None
    try:
        prepared = preprocess(audio)
        if prepared is None:
            return {"scored": False, "reason": "Silence or non-speech noise", "latency_ms": round((time.perf_counter()-started)*1000, 2)}
        processed, speech_ratio = prepared
        features = extract(processed)
        scores = classifier.score_features(features) if hasattr(classifier, "score_features") else {"spectral_score": classifier.predict(processed), "prosody_score": 0.5, "speaker_match_score": None, "model": classifier.name}
        pitch_coverage = min(1.0, len(features["pitch_hz"]) / 12)
        confidence = round(min(0.95, max(0.35, 0.35 + 0.35 * speech_ratio + 0.25 * pitch_coverage)), 4)
        return {"scored": True, **scores, "p_synthetic": scores["synthetic_score"],
                "p_synthetic_raw": scores["synthetic_score"], "confidence": confidence,
                "language": "und", "language_supported": True, "adversarial_flag": False,
                "voiced_seconds": round(speech_ratio * 3.0, 3), "features": features, "speech_ratio": speech_ratio,
                "latency_ms": round((time.perf_counter()-started)*1000, 2)}
    finally:
        audio.fill(0)
        if processed is not None:
            processed.fill(0)

def public(call):
    return {k:v for k,v in call.items() if k not in {"rolling", "events"}}

def get_call(call_id):
    if call_id not in calls:
        raise HTTPException(404, "Call not found")
    return calls[call_id]

def ensure_alert(call, alert_key=None):
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
    ledger.append({"call_id": call["call_id"], "event_type": "alert", "risk_score": call["risk_score"],
                   "band": call["band"], "policy_version": POLICY["version"], "model_versions": call["model_versions"]})
    return alert

async def run_call(call, path, interval):
    source = chunks(path)
    try:
        for index, audio in enumerate(source, 1):
            if call["status"] == "stopped":
                break
            result = await asyncio.to_thread(analyze, audio)
            call["chunks_processed"] = index
            call["latency_ms"] = result["latency_ms"]
            if result["scored"]:
                detector_output = None if call["simulate_detector_failure"] else result
                window = score_window(detector_output, call["context"])
                verdict = call["rolling"].update(window)
                call.update(verdict)
                call["risk_score"] = verdict["session_score"]
                call["decision"] = {"UNKNOWN": "WARN", "LOW": "ALLOW", "MEDIUM": "WARN", "HIGH": "STEP_UP"}[verdict["band"]]
                public_result = result if detector_output is not None else {
                    "scored": True,
                    "latency_ms": result["latency_ms"],
                    "features": result["features"],
                    "speech_ratio": result["speech_ratio"],
                }
                call["latest"] = {**public_result, **window}
                call["degraded"] = window["degraded"]
                call["degraded_reasons"] = window["degraded_reasons"]
                call["contributing_factors"] = window["contributing_factors"]
                call["applied_floors"] = window["applied_floors"]
                if window["window_score"] is None:
                    call["unassessed_windows"] += 1
                else:
                    call["windows_scored"] += 1
                    ledger.append({"call_id": call["call_id"], "event_type": "observation", "risk_score": call["risk_score"],
                                   "band": call["band"], "policy_version": POLICY["version"], "model_versions": call["model_versions"]})
                if verdict["alert_key"]:
                    ensure_alert(call, verdict["alert_key"])
            else:
                call["dropped_chunks"] += 1
            call["events"].append({"type": "chunk", "chunk": index, **result, "call": public(call)})
            await asyncio.sleep(interval)
        if call["status"] != "stopped":
            call["status"] = "completed"
        if call["risk_score"] is not None:
            ledger.append({"call_id": call["call_id"], "event_type": "call_completed", "risk_score": call["risk_score"],
                           "band": call["band"], "policy_version": POLICY["version"], "model_versions": call["model_versions"]})
    except Exception:
        call["status"] = "error"
        call["error"] = "Audio processing failed. Check that the WAV contains valid audio."
    finally:
        source.close()
        call["events"].append({"type": "complete", "call": public(call)})

@app.get("/api/v1/health")
def health():
    return {"status": "ok", "model": classifier.name, "mode": "local simulation", "raw_audio_persistence": False}

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
    if sum(c["status"]=="streaming" for c in calls.values()) >= 3:
        raise HTTPException(429, "Three simultaneous demo calls are supported.")
    try:
        path = resolve_audio(body.filename)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(400, str(error))
    if len(calls) >= 100:
        old = next((k for k,c in calls.items() if c["status"] != "streaming"), None)
        if old:
            del calls[old]
    call_id = str(uuid4())
    model_versions = {"detector": classifier.model_version, "calibrator": classifier.calibrator_version, "verifier": "not_enrolled"}
    call = {"call_id": call_id, "label": body.label, "filename": body.filename, "context": body.context.model_dump(),
            "status": "streaming", "started_at": datetime.now(timezone.utc).isoformat(), "risk_score": None,
            "authenticity_score": None, "band": "UNKNOWN", "decision": "WARN", "history": [],
            "rolling": SessionRisk(call_id), "events": [], "chunks_processed": 0, "windows_scored": 0,
            "unassessed_windows": 0, "dropped_chunks": 0, "latest": None, "latency_ms": None,
            "degraded": False, "degraded_reasons": [], "contributing_factors": [], "applied_floors": [],
            "policy_version": POLICY["version"], "model_versions": model_versions,
            "simulate_detector_failure": body.simulate_detector_failure,
            "band_timeline": [{"window": 0, "band": "UNKNOWN"}]}
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
    return public(call)

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
    return {"risk_score": score, "authenticity_score": round(100-score, 2), "policy_version": POLICY["version"],
            "aggregation": "single-window compatibility endpoint; sessions use EWMA, decaying peak, and hysteresis"}

@app.get("/api/v1/risk-score/{call_id}")
def current_risk(call_id: str):
    return public(get_call(call_id))

@app.post("/api/v1/alerts")
def create_alert(body: AlertRequest):
    return ensure_alert(get_call(body.call_id))

@app.get("/api/v1/alerts")
def list_alerts():
    return alerts[::-1]

@app.post("/api/v1/alerts/{alert_id}/escalate")
def escalate(alert_id: str):
    alert = next((a for a in alerts if a["id"] == alert_id), None)
    if alert is None:
        raise HTTPException(404, "Alert not found")
    if alert["status"] != "escalated":
        ledger.append({"call_id": alert["call_id"], "event_type": "escalation", "risk_score": alert["risk_score"],
                       "band": alert["band"], "policy_version": POLICY["version"],
                       "model_versions": alert["model_versions"]})
        alert["status"] = "escalated"
    return alert

@app.post("/api/v1/ledger/log")
def log_event(body: LedgerEvent):
    return ledger.append(body.model_dump())

@app.get("/api/v1/ledger")
def ledger_entries():
    return ledger.entries()[::-1]

@app.get("/api/v1/ledger/verify/{hash}")
def verify(hash: str):
    return ledger.verify(None if hash == "all" else hash)
