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
    model_version = "heuristic-acoustic@1.1.0"
    calibrator_version = "identity-demo@0.0.0-unvalidated"

    def score_features(self, f):
        spectral = float(np.clip(0.3 + (0.015-f["spectral_flatness"])*12, 0.1, 0.9))
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
