import logging
import os
import numpy as np
from .features import extract
from .spoof_classifier import SpoofClassifier

log = logging.getLogger("voiceshield.sidecar")

class HeuristicClassifier(SpoofClassifier):
    name = "acoustic-heuristic-v1 (unvalidated)"
    model_version = "heuristic-acoustic@1.2.0"
    calibrator_version = "identity-demo@0.0.0-unvalidated"

    # Wiener entropy spans orders of magnitude (measured 1e-6 on tonal fixtures, ~1e-2 on noisy
    # speech), so a linear threshold pinned this term at a constant 0.48 and made the detector's
    # upper range unreachable -- DR-003 reintroduced below the fusion. Map it on a log axis so the
    # declared [0.05, 0.95] range is actually attainable. See docs/HANDOFF.md DEF-1.
    FLATNESS_TONAL = 1e-6      # fully tonal / harmonic -> maximal synthetic indication
    FLATNESS_NOISY = 3e-2      # broadband -> minimal synthetic indication

    def score_features(self, f):
        flatness = max(float(f["spectral_flatness"]), self.FLATNESS_TONAL)
        tonality = (np.log10(self.FLATNESS_NOISY) - np.log10(flatness)) / (
            np.log10(self.FLATNESS_NOISY) - np.log10(self.FLATNESS_TONAL))
        spectral = float(np.clip(0.05 + 0.90*tonality, 0.05, 0.95))
        prosody = float(np.clip(0.95 - 2.4*f["pitch_cv"] - 2*f["jitter"] - 0.35*f["shimmer"], 0.05, 0.95))
        return {"spectral_score": round(spectral, 4), "prosody_score": round(prosody, 4),
                "speaker_match_score": None, "speaker_status": "not_enrolled",
                "synthetic_score": round(0.55*spectral+0.45*prosody, 4),
                "classification": "SYNTHETIC" if 0.55*spectral+0.45*prosody >= 0.5 else "REAL",
                "classification_note": "Heuristic label only, not a validated finding", "model": self.name,
                "model_version": self.model_version, "calibrator_version": self.calibrator_version}

    def predict(self, chunk):
        return self.score_features(extract(chunk))["synthetic_score"]

# Always-available fallback instance. This is what `sidecar.analyse_buffer` scores with when the
# primary detector is disabled, fails to load, or throws on a given window (CLAUDE.md invariant 2:
# a degraded detector must never look like a silently-passing one).
HEURISTIC_FALLBACK = HeuristicClassifier()


def _select_active_classifier() -> SpoofClassifier:
    """Pick the detector `sidecar.py` scores every window with, once, at process start.

    CLAUDE.md decision D-1: a real detector runs behind this interface with the heuristic as a
    fast fallback. `AASIST_ENABLED=false` opts back out to heuristic-only (e.g. no ONNX Runtime
    available, or a deliberate demo of the pre-AASIST behaviour); otherwise AASIST-L is loaded
    once here, and a load failure (missing dependency, missing/corrupt model file, no network for
    the one-time download) degrades to the heuristic rather than raising -- the sidecar must still
    start and serve requests.
    """
    if os.getenv("AASIST_ENABLED", "true").strip().lower() in ("0", "false", "no"):
        log.info("AASIST_ENABLED is false; using %s", HEURISTIC_FALLBACK.name)
        return HEURISTIC_FALLBACK
    from .aasist import AASISTLClassifier  # local import: keeps onnxruntime optional at import time
    aasist = AASISTLClassifier()
    if aasist.load():
        log.info("Active detector: %s (provider=%s)", aasist.name, aasist.provider)
        return aasist
    log.warning("AASIST-L unavailable (%s); using %s", aasist.load_error, HEURISTIC_FALLBACK.name)
    return HEURISTIC_FALLBACK


# The module-level singleton every other module imports. Computed once at process start so a
# session never pays ONNX Runtime session-creation cost per request (CLAUDE.md D-1, and the
# integration's own "load once per process" requirement).
classifier = _select_active_classifier()
