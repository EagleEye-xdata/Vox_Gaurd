"""VoxGuard Python SDK — integration-ready client for banks and telecom platforms.

PS requirement: "REST/gRPC APIs and SDKs" — this SDK wraps the VoxGuard REST gateway
so integration teams can detect AI-cloned voices with a single function call.

USAGE
-----
    from voxguard import VoxGuardClient, AnalysisMode

    client = VoxGuardClient(base_url="http://your-voxguard-host:8000")

    # Analyse a WAV file
    result = client.analyse_file("caller_audio.wav", identity_id="cust_rajesh_9012")
    print(result.risk_band)          # "LOW" / "MEDIUM" / "HIGH"
    print(result.p_synthetic)        # 0.0 – 1.0 (AI likelihood)
    print(result.prosody_risk)       # 0.0 – 1.0 (behavioral naturalness)
    print(result.i_risk)             # 0.0 – 1.0 (intent/content risk)
    print(result.should_block)       # True if HIGH band
    print(result.transcript)         # Whisper STT transcript

    # Enrol a speaker (requires consent)
    client.enrol_speaker(
        identity_id="cust_anita_7654",
        display_name="Anita Verma",
        audio_path="enrolment_anita.wav",
        consent_token="CONSENT_2024_ANITA_VERMA",
    )

    # Check compliance statement
    stmt = client.compliance_statement()
    print(stmt["inference_location"])   # "on_device"

INSTALL
-------
    pip install requests  # only dependency

LICENSE
-------
    Apache 2.0
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from enum import Enum

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False


class AnalysisMode(str, Enum):
    """Analysis depth tradeoff."""
    FAST = "fast"          # Single 3-second window, lowest latency
    STANDARD = "standard"  # Sliding windows, default
    THOROUGH = "thorough"  # Maximum coverage, slowest


@dataclass
class WindowResult:
    """Analysis result for a single 3-second audio window."""
    window_index: int = 0
    scored: bool = False
    reason: Optional[str] = None

    # Core risk signals
    p_synthetic: float = 0.0        # AI-clone likelihood [0,1]
    prosody_risk: float = 0.0       # Behavioural/prosody risk [0,1]
    i_risk: float = 0.0             # Intent/content risk [0,1]
    drift_score: Optional[float] = None  # Cross-session anomaly [0,1]

    # Speaker verification
    match_score: Optional[float] = None
    speaker_status: str = "unknown"

    # Intent details
    transcript: str = ""
    matched_phrases: list[str] = field(default_factory=list)
    language_detected: Optional[str] = None

    # Prosody details
    pitch_mean_hz: float = 0.0
    rhythm_regularity: float = 0.0
    pause_count: int = 0

    # Raw response
    raw: dict = field(default_factory=dict)


@dataclass
class AnalysisResult:
    """Aggregated result across all windows of a call."""
    call_id: Optional[str] = None
    identity_id: Optional[str] = None

    # Aggregated risk (max across windows for worst-case safety)
    p_synthetic: float = 0.0
    prosody_risk: float = 0.0
    i_risk: float = 0.0
    drift_score: Optional[float] = None

    # Band classification
    risk_band: str = "LOW"          # "LOW" / "MEDIUM" / "HIGH"
    policy_decision: str = "ALLOW"  # "ALLOW" / "WARN" / "STEP_UP" / "REJECT_AND_BLOCK"
    should_block: bool = False

    # Per-window results
    windows: list[WindowResult] = field(default_factory=list)

    # Transcript (concatenated across windows)
    transcript: str = ""
    language_detected: Optional[str] = None

    # Latency
    total_latency_ms: float = 0.0

    def summary(self) -> dict:
        """Return a compact, JSON-serialisable summary for logging or decision systems."""
        return {
            "risk_band": self.risk_band,
            "policy_decision": self.policy_decision,
            "should_block": self.should_block,
            "p_synthetic": round(self.p_synthetic, 4),
            "prosody_risk": round(self.prosody_risk, 4),
            "i_risk": round(self.i_risk, 4),
            "drift_score": round(self.drift_score, 4) if self.drift_score is not None else None,
            "transcript": self.transcript[:200] if self.transcript else "",
            "language_detected": self.language_detected,
            "window_count": len(self.windows),
            "total_latency_ms": self.total_latency_ms,
        }


class VoxGuardError(Exception):
    """Raised when the VoxGuard API returns an error response."""
    def __init__(self, status_code: int, message: str):
        super().__init__(f"VoxGuard API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class VoxGuardClient:
    """Integration-ready client for the VoxGuard AI-clone detection gateway.

    Parameters
    ----------
    base_url :
        Base URL of the VoxGuard Go gateway, e.g. "http://localhost:8000".
    api_key :
        Optional API key sent as the X-VoxGuard-Key header. Leave None for
        unauthenticated local deployments.
    timeout :
        HTTP timeout in seconds (default 30). Increase for longer audio files.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        timeout: float = 30.0,
    ):
        if not _REQUESTS_AVAILABLE:
            raise ImportError(
                "voxguard SDK requires the 'requests' library. "
                "Install it: pip install requests"
            )
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            h["X-VoxGuard-Key"] = self._api_key
        return h

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        r = requests.get(url, headers=self._headers(), params=params, timeout=self._timeout)
        if not r.ok:
            raise VoxGuardError(r.status_code, r.text[:300])
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        url = f"{self.base_url}{path}"
        r = requests.post(url, headers=self._headers(),
                          data=json.dumps(body), timeout=self._timeout)
        if not r.ok:
            raise VoxGuardError(r.status_code, r.text[:300])
        return r.json()

    # ------------------------------------------------------------------ #
    # Core analysis                                                        #
    # ------------------------------------------------------------------ #

    def analyse_file(
        self,
        audio_path: str | Path,
        identity_id: Optional[str] = None,
        window_seconds: float = 3.0,
        hop_seconds: float = 1.5,
    ) -> AnalysisResult:
        """Analyse a WAV/MP3/OGG audio file for AI voice cloning.

        The file is uploaded to the gateway for analysis. Only feature vectors
        and risk scores are returned — audio is never stored (on-device inference).

        Parameters
        ----------
        audio_path   : Path to the audio file on the local filesystem.
        identity_id  : Optional enrolled speaker ID to compare against.
        window_seconds : Duration of each analysis window (default 3.0 s).
        hop_seconds  : Hop between windows (default 1.5 s for 50% overlap).

        Returns
        -------
        AnalysisResult with aggregated risk scores and per-window details.
        """
        started = time.perf_counter()
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        # Upload via multipart to /api/v1/analyse
        url = f"{self.base_url}/api/v1/analyse"
        headers = {}
        if self._api_key:
            headers["X-VoxGuard-Key"] = self._api_key

        params: dict[str, Any] = {
            "window_seconds": window_seconds,
            "hop_seconds": hop_seconds,
        }
        if identity_id:
            params["identity_id"] = identity_id

        with open(audio_path, "rb") as f:
            r = requests.post(
                url,
                headers=headers,
                params=params,
                files={"file": (audio_path.name, f, "audio/wav")},
                timeout=self._timeout,
            )
        if not r.ok:
            raise VoxGuardError(r.status_code, r.text[:300])

        raw = r.json()
        return self._parse_analysis_response(raw, identity_id, time.perf_counter() - started)

    def analyse_samples(
        self,
        samples: list[float],
        sample_rate: int = 16000,
        identity_id: Optional[str] = None,
    ) -> AnalysisResult:
        """Analyse raw float32 PCM samples (e.g., from a microphone stream).

        Parameters
        ----------
        samples     : List of float32 values in [-1, 1], at sample_rate Hz.
        sample_rate : Sample rate in Hz (must be 16000 for best results).
        identity_id : Optional enrolled speaker ID.
        """
        started = time.perf_counter()
        body: dict[str, Any] = {
            "samples": samples,
            "sample_rate": sample_rate,
        }
        if identity_id:
            body["identity_id"] = identity_id

        raw = self._post("/api/v1/analyse/raw", body)
        return self._parse_analysis_response(raw, identity_id, time.perf_counter() - started)

    def _parse_analysis_response(
        self, raw: dict, identity_id: Optional[str], elapsed: float
    ) -> AnalysisResult:
        """Parse a gateway analysis response into a typed AnalysisResult."""
        windows_raw = raw.get("windows", [raw])  # single or list
        windows: list[WindowResult] = []
        transcripts: list[str] = []
        lang: Optional[str] = None

        for i, w in enumerate(windows_raw):
            intent = w.get("intent") or {}
            prosody = w.get("prosody") or {}
            verif = w.get("verification") or {}
            drift = w.get("session_drift") or {}

            transcript = intent.get("transcript", "")
            if transcript:
                transcripts.append(transcript)
            if intent.get("language_detected"):
                lang = intent["language_detected"]

            windows.append(WindowResult(
                window_index=i + 1,
                scored=w.get("scored", False),
                reason=w.get("reason"),
                p_synthetic=float(w.get("p_synthetic") or 0),
                prosody_risk=float(w.get("prosody_risk") or 0),
                i_risk=float(w.get("i_risk") or 0),
                drift_score=drift.get("drift_score"),
                match_score=verif.get("match_score"),
                speaker_status=w.get("speaker_status", ""),
                transcript=transcript,
                matched_phrases=intent.get("matched_phrases", []),
                language_detected=lang,
                pitch_mean_hz=float(prosody.get("pitch_mean_hz") or 0),
                rhythm_regularity=float(prosody.get("rhythm_regularity") or 0),
                pause_count=int(prosody.get("pause_count") or 0),
                raw=w,
            ))

        # Aggregate: worst-case across windows
        scored_windows = [w for w in windows if w.scored]
        p_syn = max((w.p_synthetic for w in scored_windows), default=0.0)
        pr = max((w.prosody_risk for w in scored_windows), default=0.0)
        ir = max((w.i_risk for w in scored_windows), default=0.0)
        drift_scores = [w.drift_score for w in scored_windows if w.drift_score is not None]
        drift = max(drift_scores) if drift_scores else None

        # Risk band from Go response or derive
        band = raw.get("risk_band") or raw.get("band", "LOW")
        decision = raw.get("policy_decision") or raw.get("decision", "ALLOW")

        return AnalysisResult(
            call_id=raw.get("call_id"),
            identity_id=identity_id,
            p_synthetic=round(p_syn, 4),
            prosody_risk=round(pr, 4),
            i_risk=round(ir, 4),
            drift_score=round(drift, 4) if drift is not None else None,
            risk_band=band,
            policy_decision=decision,
            should_block=decision in ("REJECT_AND_BLOCK",),
            windows=windows,
            transcript=" ".join(transcripts),
            language_detected=lang,
            total_latency_ms=round(elapsed * 1000, 1),
        )

    # ------------------------------------------------------------------ #
    # Speaker enrolment                                                   #
    # ------------------------------------------------------------------ #

    def enrol_speaker(
        self,
        identity_id: str,
        display_name: str,
        audio_path: str | Path,
        consent_token: str,
    ) -> dict:
        """Enrol a speaker voiceprint (requires explicit consent token).

        Parameters
        ----------
        identity_id   : Unique caller ID (e.g. "cust_9012").
        display_name  : Human-readable name for audit logs.
        audio_path    : Path to a clean 5–30 second speech recording.
        consent_token : Signed consent token from your consent management system.
        """
        if not _NUMPY_AVAILABLE:
            raise ImportError("Enrolment requires numpy. pip install numpy")

        import wave, struct
        audio_path = Path(audio_path)
        with wave.open(str(audio_path), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            sr = wf.getframerate()
            ch = wf.getnchannels()
            sw = wf.getsampwidth()

        n = len(frames) // (ch * sw)
        fmt = {1: "b", 2: "h", 4: "i"}[sw]
        raw_s = struct.unpack(f"{n * ch}{fmt}", frames)
        # Mono downmix
        if ch > 1:
            raw_s = [sum(raw_s[i::ch]) / ch for i in range(min(ch, len(raw_s)))]
        scale = {1: 128.0, 2: 32768.0, 4: 2147483648.0}[sw]
        samples = [float(s) / scale for s in raw_s]

        return self._post("/api/v1/speakers", {
            "identity_id": identity_id,
            "display_name": display_name,
            "samples": samples,
            "sample_rate": sr,
            "consent_token": consent_token,
        })

    def list_speakers(self) -> list[dict]:
        """List all enrolled speaker profiles."""
        return self._get("/api/v1/speakers").get("speakers", [])

    def revoke_speaker(self, identity_id: str) -> dict:
        """Revoke and erase a speaker profile (DPDPA erasure right)."""
        url = f"{self.base_url}/api/v1/speakers/{identity_id}"
        r = requests.delete(url, headers=self._headers(), timeout=self._timeout)
        if not r.ok:
            raise VoxGuardError(r.status_code, r.text[:300])
        return r.json()

    # ------------------------------------------------------------------ #
    # Compliance                                                          #
    # ------------------------------------------------------------------ #

    def compliance_statement(self) -> dict:
        """Return the on-device inference compliance statement.

        Use this to verify VoxGuard's data handling promises before integration.
        """
        return self._get("/api/v1/compliance")

    def health(self) -> dict:
        """Check gateway health and active model versions."""
        return self._get("/api/v1/health")

    # ------------------------------------------------------------------ #
    # Convenience                                                         #
    # ------------------------------------------------------------------ #

    def is_synthetic(self, audio_path: str | Path, threshold: float = 0.65) -> bool:
        """Quick boolean check: is this audio likely AI-generated?

        Parameters
        ----------
        threshold : p_synthetic threshold above which we return True (default 0.65).
        """
        result = self.analyse_file(audio_path)
        return result.p_synthetic >= threshold
