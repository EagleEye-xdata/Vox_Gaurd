"""The SpoofClassifier interface, in its own module.

Split out from detection.py so both detection.py (HeuristicClassifier) and aasist.py
(AASISTLClassifier) can depend on the interface without depending on each other -- detection.py
selects between the two implementations at process start (see `detection._select_active_classifier`),
so a shared base in either of those two files would create a circular import depending on which one
happened to be imported first.
"""
from abc import ABC, abstractmethod

import numpy as np


class SpoofClassifier(ABC):
    name = "abstract"

    @abstractmethod
    def predict(self, chunk: np.ndarray) -> float:
        """Return uncalibrated synthetic-likelihood indicator in [0, 1]."""

    def analyze(self, audio: np.ndarray, features: dict) -> dict:
        """Full detector-result contract used by sidecar.analyse_buffer.

        Default implementation scores from the hand-crafted DSP features only, ignoring the raw
        waveform -- this is what every classifier before AASIST-L did. A raw-waveform detector
        (see app/aasist.py) overrides this to use `audio` instead.
        """
        return self.score_features(features)
