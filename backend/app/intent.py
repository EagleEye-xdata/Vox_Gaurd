"""Intent & Content Risk Scorer (I_risk) — Step 5 of the full detection pipeline.

WHAT THIS DOES
--------------
Every 3-second audio window that reaches the detector also flows through this module.
It transcribes speech locally with faster-whisper (CTranslate2 CPU backend, no API
calls) and then runs three complementary checks over the transcript:

  Pass 1 — Keyword matching: a curated lexicon of high-risk phrases typical of vishing
            and social-engineering calls (OTP demands, urgency language, account-freeze
            threats, wire-transfer pressure, PIN/CVV solicitation, identity spoofing).

  Pass 2 — Regex heuristics: monetary amounts, credential-framed digit patterns, bank account
            numbers, UPI handle patterns.

  Pass 3 — A multilingual sentence-embedding classifier for paraphrased vishing language.

The combined output is I_risk ∈ [0, 1].  It is one term in the Go fusion formula:

    R = weighted fusion of AI, speaker, context, and I_risk signals; active signals
        are renormalised by the Go policy engine.

AUDIO BOUNDARY (CLAUDE.md invariant 1)
---------------------------------------
The transcription backend receives an in-memory numpy float32 copy which is zeroed
in this module on every path. Nothing is written to disk.

MODEL CHOICE
------------
The default faster-whisper model is "small" in int8 CPU mode. Set WHISPER_MODEL to
another supported model only after measuring latency and accuracy on the deployment
hardware.

GRACEFUL DEGRADATION
--------------------
If faster-whisper is unavailable or transcription fails, this module logs a warning
without crashing the pipeline. The sidecar reports I_risk as unavailable in that
case, so Go renormalises it out rather than treating absent evidence as safe intent.
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
    """Load faster-whisper (CTranslate2 backend).

    Swapped from openai-whisper, which was never in requirements.txt -- so this
    function failed on every call and I_risk was silently 0.0 for the life of
    the process. Same OpenAI weights, int8 quantised: `small` on this backend
    runs faster than `tiny` did on the original and is markedly better on
    Indian-accented English and Romanised Hindi, which is most of what a vishing
    call in this market actually sounds like.
    """
    global _whisper_model, _whisper_available
    if _whisper_available is not None:
        return _whisper_available
    try:
        from faster_whisper import WhisperModel  # type: ignore[import]
        import os
        model_name = os.environ.get("WHISPER_MODEL", "small")
        log.info("Loading faster-whisper '%s' for intent scoring ...", model_name)
        _whisper_model = WhisperModel(model_name, device="cpu", compute_type="int8")
        _whisper_available = True
        log.info("faster-whisper '%s' loaded -- intent scoring is active.", model_name)
    except Exception as exc:
        log.warning(
            "faster-whisper not available (%s). Intent scoring disabled -- I_risk will be 0.0. "
            "Install with: pip install faster-whisper",
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
    (r"\b(atm|card|upi|m|debit|credit|banking)\s*pin\b", 0.55),
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
    (r"\b(police|cyber\s*cell)\b.{0,40}\b(case|fir|complaint|action|arrest|station bula)", 0.40),
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
    model_diagnostics: dict = field(default_factory=dict)
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
        audio_copy = None
        try:
            # faster-whisper expects float32 at 16kHz — that's exactly what we have.
            # task="translate" would romanise Hindi; task="transcribe" keeps original script.
            # faster-whisper returns a generator of segments plus an info object.
            audio_copy = audio.astype(np.float32)
            segments, info = _whisper_model.transcribe(
                audio_copy,
                language=None,             # auto-detect
                task="transcribe",
                vad_filter=False,          # our own VAD already gated this window
                condition_on_previous_text=False,
            )
            transcript = " ".join(seg.text for seg in segments).strip()
            result.language_detected = getattr(info, "language", None)
        except Exception as exc:
            result.error = str(exc)
            log.warning("Whisper transcription failed on window: %s", exc)
        finally:
            # The transcription backend may raise after allocating its input
            # view. Clear our explicit copy on both the success and error path.
            if audio_copy is not None:
                audio_copy.fill(0)

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
    # A bare 4-8 digit run is not evidence of anything -- it matches order numbers,
    # PNRs, years, PIN codes, amounts and roll numbers. The digits only carry signal
    # when something nearby frames them as a secret to be read out.
    if _RE_OTP_PATTERN.search(transcript) and re.search(
            r"(otp|code|password|pin|verify|verification|bhej|bata|share|confirm|read|padh|sunao|dictate)",
            transcript, re.IGNORECASE):
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

    keyword_risk = round(min(1.0, accumulated), 4)

    # --- Pass 3: trained semantic classifier ------------------------------------
    # The two passes above match literal strings. On 50 novel scam phrasings they
    # detect zero, because a scammer who has read a blocklist describes the secret
    # instead of naming it ("jo ankde handset pe blink kar rahe hain"). The model
    # scores paraphrases. Fused with max(): the lexicon is a precision instrument,
    # and averaging its correct 0.6 against a model's 0.2 would discard a true
    # detection. The model only ever adds evidence.
    try:
        from . import intent_model as _im
        fused, diag = _im.combined_score(transcript, keyword_risk)
        result.i_risk = round(fused, 4)
        result.model_diagnostics = diag
    except Exception as _model_err:                       # never break the pipeline
        log.warning("Intent model unavailable on window (%s); keyword-only.", _model_err)
        result.i_risk = keyword_risk
        result.model_diagnostics = {"model_available": False, "error": str(_model_err)}

    result.latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return result
