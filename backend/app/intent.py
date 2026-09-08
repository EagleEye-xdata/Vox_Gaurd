"""Intent & Content Risk Scorer (I_risk) — Step 5 of the full detection pipeline.

WHAT THIS DOES
--------------
Every 3-second audio window that reaches the detector also flows through this module.
It transcribes the speech locally with OpenAI Whisper (tiny model, CPU-only, no API
calls) and then runs a two-pass classifier over the transcript:

  Pass 1 — Keyword matching: a curated lexicon of high-risk phrases typical of vishing
            and social-engineering calls (OTP demands, urgency language, account-freeze
            threats, wire-transfer pressure, PIN/CVV solicitation, identity spoofing).

  Pass 2 — Regex heuristics: monetary amounts, 6-digit OTP patterns, bank account
            numbers, UPI handle patterns.

The combined output is I_risk ∈ [0, 1].  It is one term in the Go fusion formula:

    R = 0.60·P_synth + 0.25·(1 - S_bio) + 0.15·I_risk

AUDIO BOUNDARY (CLAUDE.md invariant 1)
---------------------------------------
Whisper receives an in-memory numpy float32 array that is zeroed by the caller's
finally-block immediately after this function returns.  Nothing is written to disk.

MODEL CHOICE
------------
Whisper "tiny" loads in < 2 s on CPU and transcribes a 3-second window in ~500 ms,
keeping the full pipeline under 1 s per window.  The "base" model is ~2× slower but
noticeably more accurate for Indian-accented English and Hindi-Romanised Hinglish;
set WHISPER_MODEL=base in .env for production.  Both are CC-BY-NC-4.0-free weights.

GRACEFUL DEGRADATION
--------------------
If Whisper is not installed (openai-whisper package absent) or fails on a window, this
module returns I_risk = 0.0 and logs a warning rather than crashing the pipeline.
The absence of intent evidence is not the same as confirmed-safe intent (CLAUDE.md
invariant 2); a window without a transcript simply contributes 0 to the intent term.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

log = logging.getLogger("voiceshield.intent")

# ---------------------------------------------------------------------------
# Whisper model — loaded lazily so the sidecar still starts if the package is
# absent.  The flag lets callers check availability without try/except.
# ---------------------------------------------------------------------------
_whisper_model = None
_whisper_available: bool | None = None   # None = not yet attempted

def _load_whisper() -> bool:
    global _whisper_model, _whisper_available
    if _whisper_available is not None:
        return _whisper_available
    try:
        import whisper  # type: ignore[import]
        import os
        model_name = os.environ.get("WHISPER_MODEL", "tiny")
        log.info("Loading Whisper model '%s' for intent scoring …", model_name)
        _whisper_model = whisper.load_model(model_name)
        _whisper_available = True
        log.info("Whisper '%s' loaded — intent scoring is active.", model_name)
    except Exception as exc:
        log.warning(
            "Whisper not available (%s). Intent scoring disabled — I_risk will be 0.0. "
            "Install openai-whisper to enable: pip install openai-whisper",
            exc,
        )
        _whisper_available = False
    return _whisper_available


# ---------------------------------------------------------------------------
# High-risk phrase lexicon (banking / financial-fraud / vishing domain)
# ---------------------------------------------------------------------------
# Each entry is (phrase, weight).  Overlapping phrases are fine — the classifier
# accumulates weights and clips to 1.0, so a window with multiple signals just
# saturates the score rather than double-counting.
_HIGH_RISK_PHRASES: list[tuple[str, float]] = [
    # OTP / authentication pressure
    ("otp",                  0.60),
    ("one.time.password",    0.60),
    ("verification code",    0.55),
    ("authentication code",  0.55),
    ("confirm.*code",        0.50),
    ("share.*code",          0.50),
    ("enter.*code",          0.45),
    # PIN / credentials solicitation
    ("pin",                  0.55),
    ("cvv",                  0.60),
    ("card number",          0.55),
    ("account number",       0.50),
    ("account.*password",    0.60),
    ("net.?banking.*password", 0.65),
    # Wire / transfer urgency
    ("wire transfer",        0.65),
    ("urgent.*transfer",     0.70),
    ("immediate.*transfer",  0.70),
    ("fund.*transfer",       0.60),
    ("transfer.*immediately", 0.70),
    # Account-freeze / threat language
    ("account.*freeze",      0.65),
    ("account.*suspend",     0.65),
    ("account.*block",       0.60),
    ("card.*block",          0.55),
    ("legal action",         0.55),
    ("arrest",               0.60),
    ("police",               0.40),
    ("cyber crime",          0.50),
    # Identity / authority spoofing
    ("rbi",                  0.35),
    ("reserve bank",         0.35),
    ("fraud department",     0.45),
    ("security department",  0.45),
    ("bank.*officer",        0.40),
    ("bank.*official",       0.40),
    # Urgency / pressure language
    ("immediately",          0.30),
    ("right now",            0.25),
    ("do not hang up",       0.55),
    ("do not disconnect",    0.55),
    ("stay on the line",     0.45),
    ("emergency",            0.30),
    # Hindi-Romanised equivalents
    ("otp.*bataye",          0.70),
    ("otp.*share",           0.70),
    ("otp.*confirm",         0.65),
    ("turant",               0.30),
    ("abhi",                 0.20),
    ("account.*freeze.*ho",  0.65),
    ("transfer.*karo",       0.55),
    ("transfer.*kijiye",     0.55),
]

# Compile once — re.search is used, so partial matches work.
_COMPILED: list[tuple[re.Pattern[str], float]] = [
    (re.compile(phrase, re.IGNORECASE), weight)
    for phrase, weight in _HIGH_RISK_PHRASES
]

# ---------------------------------------------------------------------------
# Regex heuristics
# ---------------------------------------------------------------------------
_RE_OTP_PATTERN   = re.compile(r'\b\d{4,8}\b')          # 4-8 digit number → OTP / code
_RE_ACCOUNT_NO    = re.compile(r'\b\d{9,18}\b')          # 9-18 digit → bank account
_RE_UPI           = re.compile(r'\b[\w.+-]+@[a-z]+\b', re.IGNORECASE)  # UPI handle
_RE_AMOUNT_INR    = re.compile(r'(?:₹|rs\.?|inr)\s*[\d,]+', re.IGNORECASE)


@dataclass
class IntentResult:
    i_risk: float = 0.0
    transcript: str = ""
    matched_phrases: list[str] = field(default_factory=list)
    heuristic_hits: list[str] = field(default_factory=list)
    whisper_available: bool = False
    language_detected: Optional[str] = None
    latency_ms: float = 0.0
    error: Optional[str] = None


def score(audio: np.ndarray, sample_rate: int = 16_000) -> IntentResult:
    """Transcribe one audio window and compute I_risk ∈ [0, 1].

    Parameters
    ----------
    audio:
        Float32 numpy array normalised to [-1, 1].  The caller is responsible for
        zeroing this buffer after the call returns.
    sample_rate:
        Must be 16 000 Hz (Whisper's native rate); resampling is the caller's job.

    Returns
    -------
    IntentResult with i_risk, the raw transcript, matched phrases, and diagnostics.
    """
    started = time.perf_counter()
    result = IntentResult()

    available = _load_whisper()
    result.whisper_available = available

    transcript = ""
    if available and _whisper_model is not None:
        try:
            # Whisper expects float32 at 16kHz — that's exactly what we have.
            # task="translate" would romanise Hindi; task="transcribe" keeps original script.
            import whisper  # type: ignore[import]
            audio_copy = audio.astype(np.float32)
            out = _whisper_model.transcribe(
                audio_copy,
                language=None,          # auto-detect
                task="transcribe",
                fp16=False,             # CPU path
                no_speech_threshold=0.6,
                condition_on_previous_text=False,
            )
            transcript = out.get("text", "").strip()
            result.language_detected = out.get("language")
            audio_copy.fill(0)         # zero the copy immediately
        except Exception as exc:
            result.error = str(exc)
            log.warning("Whisper transcription failed on window: %s", exc)

    result.transcript = transcript

    if not transcript:
        result.latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return result

    # --- Pass 1: keyword/phrase matching ---
    accumulated = 0.0
    matched: list[str] = []
    for pattern, weight in _COMPILED:
        if pattern.search(transcript):
            accumulated += weight
            matched.append(pattern.pattern)
    result.matched_phrases = matched

    # --- Pass 2: regex heuristics ---
    hits: list[str] = []
    if _RE_OTP_PATTERN.search(transcript):
        accumulated += 0.20
        hits.append("digit_code_pattern")
    if _RE_ACCOUNT_NO.search(transcript):
        accumulated += 0.15
        hits.append("account_number_pattern")
    if _RE_UPI.search(transcript):
        accumulated += 0.25
        hits.append("upi_handle_pattern")
    if _RE_AMOUNT_INR.search(transcript):
        accumulated += 0.10
        hits.append("inr_amount_mentioned")
    result.heuristic_hits = hits

    result.i_risk = round(min(1.0, accumulated), 4)
    result.latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return result
