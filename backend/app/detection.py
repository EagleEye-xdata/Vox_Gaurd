from abc import ABC, abstractmethod
import numpy as np
from .features import extract

class SpoofClassifier(ABC):
    name = "abstract"

    @abstractmethod
    def predict(self, chunk: np.ndarray) -> float:
        """Return uncalibrated synthetic-likelihood indicator in [0, 1]."""

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

classifier = HeuristicClassifier()
