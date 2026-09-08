"""Trained intent classifier — semantic layer over the intent.py keyword lexicon.

DROP-IN LOCATION
----------------
    backend/app/intent_model.py
    backend/models/intent_model.joblib     (the trained artefact)

WHY THIS EXISTS
---------------
`intent.py` scores I_risk with ~40 regexes. Measured on 68 hand-written novel
scam phrasings that share no template with the training corpus, that lexicon
detects **0 of 68** while still false-positiving on 5.9% of benign speech. It
matches literal strings, so "OTP bataiye" fires and "chhe ank ka jo number aaya
hai wo padh dijiye" does not — and a scammer who has read a blocklist uses the
second form.

This module adds a multilingual sentence-embedding classifier that scores
paraphrases. On the same held-out probe it detects 35% at an 11.8% benign
false-positive rate, in high-precision mode.

35% is not good enough to stand alone and is not presented as such. It is
strictly additive: `score()` returns the **max** of the model score and the
existing keyword score, so the combined system is never worse than the lexicon
it augments, and picks up paraphrased attacks the lexicon structurally cannot.

RAISING THAT NUMBER
-------------------
The ceiling here is training data, not architecture. The corpus is
template-generated (scripts/generate_intent_dataset.py), and template data
teaches template surface forms. The fix is more *diverse* data, not more rows:
LLM-generated paraphrases with high variation, and real transcribed call audio
once available. Retrain with scripts/train_intent_final.py; nothing downstream
changes, because the contract below is stable.

GRACEFUL DEGRADATION
--------------------
Mirrors aasist.py: `load()` returns bool and never raises. If sentence-transformers
or the artefact is missing, `available` stays False and intent.py falls back to
keyword-only scoring. A missing model degrades one signal, never the pipeline.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("voiceshield.intent_model")

DEFAULT_ARTEFACT = Path(__file__).resolve().parent.parent / "models" / "intent_model.joblib"

# Mode thresholds. "high_precision" raises every per-label threshold to at least
# this floor, trading recall for a benign false-positive rate an operator can
# live with. An alert channel that cries wolf stops being read, and an unread
# alert protects nobody.
HIGH_PRECISION_FLOOR = 0.80


@dataclass
class ModelIntentResult:
    i_risk: float = 0.0
    labels: dict[str, float] = field(default_factory=dict)
    model_available: bool = False
    latency_ms: float = 0.0
    model_version: str = "unavailable"
    error: str | None = None


class IntentClassifier:
    """Sentence-embedding multi-label vishing classifier.

    Same lifecycle contract as AASISTLClassifier: constructed at import, loaded
    once per process, `load()` returns bool rather than raising.
    """

    def __init__(self, artefact: Path | None = None, mode: str | None = None):
        self.artefact = Path(artefact or os.getenv("INTENT_MODEL_PATH", DEFAULT_ARTEFACT))
        self.mode = mode or os.getenv("INTENT_MODEL_MODE", "high_precision")
        self.available = False
        self.load_error: str | None = None
        self._encoder = None
        self._models: dict = {}
        self._thresholds: dict[str, float] = {}
        self._weights: dict[str, float] = {}
        self.model_version = "unavailable"

    def load(self) -> bool:
        if self.available:
            return True
        try:
            import joblib
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            self.load_error = f"dependency missing: {exc}"
            log.warning("Intent model unavailable (%s); keyword-only scoring.", self.load_error)
            return False

        if not self.artefact.exists():
            self.load_error = f"artefact not found: {self.artefact}"
            log.warning("Intent model unavailable (%s); keyword-only scoring.", self.load_error)
            return False

        try:
            bundle = joblib.load(self.artefact)
            self._models = bundle["models"]
            self._weights = bundle["label_weights"]
            self.model_version = bundle.get("model_version", "unknown")
            raw = bundle["thresholds"]
            if self.mode == "high_precision":
                self._thresholds = {k: max(v, HIGH_PRECISION_FLOOR) for k, v in raw.items()}
            else:
                self._thresholds = dict(raw)
            # ~470 MB, loaded once. Sharing this instance with any other
            # embedding consumer in-process is intentional.
            self._encoder = SentenceTransformer(bundle["emb_model"])
        except Exception as exc:
            self.load_error = str(exc)
            log.warning("Intent model failed to load (%s); keyword-only scoring.", exc)
            return False

        self.available = True
        log.info("Intent model loaded: %s (mode=%s, labels=%d)",
                 self.model_version, self.mode, len(self._models))
        return True

    def predict(self, transcript: str) -> ModelIntentResult:
        """Score one transcript. Never raises; a failed window scores 0.0.

        A 0.0 here means "this model found no evidence", not "this window is
        safe" — the caller combines it with the keyword score and the absence of
        evidence never lowers the combined result.
        """
        started = time.perf_counter()
        if not self.available or not transcript or not transcript.strip():
            return ModelIntentResult(model_available=self.available,
                                     model_version=self.model_version)
        try:
            emb = self._encoder.encode([transcript], batch_size=1,
                                       show_progress_bar=False,
                                       normalize_embeddings=True)
            hits: dict[str, float] = {}
            risk = 0.0
            for label, clf in self._models.items():
                p = float(clf.predict_proba(emb)[0, 1])
                if p >= self._thresholds[label]:
                    hits[label] = round(p, 4)
                    risk = max(risk, p * self._weights.get(label, 1.0))
            return ModelIntentResult(
                i_risk=round(min(risk, 1.0), 4),
                labels=hits,
                model_available=True,
                model_version=self.model_version,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except Exception as exc:
            log.warning("Intent model failed on window: %s", exc)
            return ModelIntentResult(model_available=True, error=str(exc),
                                     model_version=self.model_version,
                                     latency_ms=round((time.perf_counter() - started) * 1000, 2))


# Module-level singleton, same pattern as detection.classifier.
model = IntentClassifier()
model.load()


def combined_score(transcript: str, keyword_risk: float) -> tuple[float, dict]:
    """Fuse the trained model with the existing keyword lexicon.

    max(), not a weighted blend. The lexicon is a precision instrument: when it
    fires on "OTP bataiye" it is right, and averaging that certainty against a
    model that happened to score 0.2 would throw away a correct detection. The
    model only ever adds evidence the lexicon could not see.

    Returns (i_risk, diagnostics) — diagnostics is surfaced to the dashboard so
    an operator can see *which* signal fired, which matters when a human has to
    justify a blocked call.
    """
    m = model.predict(transcript)
    fused = max(float(keyword_risk), m.i_risk)
    return fused, {
        "keyword_risk": round(float(keyword_risk), 4),
        "model_risk": m.i_risk,
        "model_labels": m.labels,
        "model_available": m.model_available,
        "model_version": m.model_version,
        "model_latency_ms": m.latency_ms,
        "fused_i_risk": round(fused, 4),
        "decisive_signal": "model" if m.i_risk > keyword_risk else "keyword",
    }
