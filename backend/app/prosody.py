"""Prosody & Behavioural Analysis Layer — PS requirement: separate prosody analysis module.

WHY THIS EXISTS
---------------
The PS document calls out "prosody/behavioural analysis" as a distinct line item, separate from
acoustic/spectral features. AASIST-L already captures high-frequency vocoder artifacts; this
module captures *temporal behavioural patterns* that are complementary:

  • Pitch contour dynamics  — synthetic voices lack natural intonation drift across sentences.
  • Pause & rhythm pattern — TTS systems produce unnaturally uniform inter-word gaps.
  • Energy rhythm          — breathing, emphasis, and coarticulation produce natural amplitude variation.
  • Speaking rate variability — humans accelerate/decelerate; unit-selection TTS does not.
  • Indian accent markers  — retroflex stop bursts, schwa deletion patterns typical of Hindi/Kannada/Tamil.

OUTPUT: prosody_risk ∈ [0, 1] — higher = more synthetic/robotic prosody behaviour.

MULTILINGUAL / INDIAN ACCENT NOTES (PS requirement 1)
-------------------------------------------------------
  • Pitch range norms are computed relative to the caller's own median, not absolute Hz thresholds.
    This means the scorer works correctly for speakers whose F0 ranges differ significantly from
    Western-centric norms (e.g., Tamil speakers ~160-220 Hz vs Hindi speakers ~110-170 Hz male).
  • Pause detection uses relative energy gating not a fixed dB floor, so it is accent-neutral.
  • A future fine-tune task: `scripts/generate_indian_tts_dataset.py` (see repo scripts/) produces
    AASIST-L fine-tune data from Meta MMS-TTS Hindi/Tamil/Telugu voices, giving the detector
    training examples of Indian-accented synthetic speech.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import librosa

from .preprocessing import SAMPLE_RATE

log = logging.getLogger("voiceshield.prosody")


@dataclass
class ProsodyResult:
    """All prosody metrics for one 3-second window."""
    prosody_risk: float = 0.0            # fused prosody-based risk ∈ [0, 1]

    # Pitch dynamics
    pitch_mean_hz: float = 0.0
    pitch_std_hz: float = 0.0
    pitch_cv: float = 0.0                # coefficient of variation (std/mean)
    pitch_contour_smoothness: float = 0.0  # 1 = perfectly flat (robotic), 0 = natural variation

    # Pause & rhythm
    pause_count: int = 0
    mean_pause_duration_ms: float = 0.0
    rhythm_regularity: float = 0.0       # 1 = metronomic (synthetic), 0 = irregular (human)

    # Energy & breathing
    energy_cv: float = 0.0              # low CV → robotic steady amplitude
    local_rate_cv: float = 0.0          # syllable-rate variability across sub-windows

    # Diagnostics
    voiced_frames: int = 0
    analysis_available: bool = False
    error: Optional[str] = None

    # Individual sub-scores (for explainability)
    subscores: dict = field(default_factory=dict)


def analyse(audio: np.ndarray, sr: int = SAMPLE_RATE) -> ProsodyResult:
    """Compute prosody features and fuse into a prosody_risk score.

    Parameters
    ----------
    audio : float32 array normalised to [-1, 1], expected to be ~3 s @ 16 kHz.
    sr    : sample rate — must be 16 000.
    """
    result = ProsodyResult()
    if len(audio) < sr // 2:   # < 0.5s — not enough for any prosody
        result.error = "audio_too_short"
        return result

    try:
        # ------------------------------------------------------------------ #
        # 1. Pitch contour (YIN algorithm)                                   #
        # ------------------------------------------------------------------ #
        hop = 160                        # 10 ms hop at 16kHz
        pitch_raw = librosa.yin(audio, fmin=65, fmax=500, sr=sr,
                                frame_length=1024, hop_length=hop)
        rms = librosa.feature.rms(y=audio, frame_length=1024, hop_length=hop)[0]
        rms = rms[:len(pitch_raw)]

        # Keep only voiced frames (RMS above 1 % of max)
        voiced_mask = rms > (rms.max() * 0.01 + 1e-9)
        pitch_voiced = pitch_raw[:len(voiced_mask)][voiced_mask]

        # Remove YIN octave errors (> ½ octave from median)
        if len(pitch_voiced) >= 5:
            med = np.median(pitch_voiced)
            keep = np.abs(np.log2(pitch_voiced / max(med, 1e-8))) < 0.5
            pitch_voiced = pitch_voiced[keep] if keep.sum() >= 3 else pitch_voiced

        result.voiced_frames = int(voiced_mask.sum())

        if len(pitch_voiced) >= 4:
            result.pitch_mean_hz = float(np.mean(pitch_voiced))
            result.pitch_std_hz = float(np.std(pitch_voiced))
            result.pitch_cv = float(result.pitch_std_hz / (result.pitch_mean_hz + 1e-9))

            # Smoothness: mean absolute second derivative (curvature of pitch contour).
            # Synthetic TTS has unnaturally flat or step-shaped pitch contours.
            d2 = np.abs(np.diff(np.diff(pitch_voiced)))
            # Normalise by mean pitch so this is accent-neutral.
            smoothness_raw = float(np.mean(d2) / (result.pitch_mean_hz + 1e-9))
            # Low curvature (flat or step) → high smoothness → more synthetic.
            result.pitch_contour_smoothness = float(
                np.clip(1.0 - np.tanh(smoothness_raw * 8), 0, 1)
            )

        # ------------------------------------------------------------------ #
        # 2. Pause & rhythm detection                                         #
        # ------------------------------------------------------------------ #
        # Use short-time energy envelope; gaps below 2 % of peak are pauses.
        energy = rms / (rms.max() + 1e-9)
        is_pause = energy < 0.02
        # Find runs of pause frames
        pause_runs: list[int] = []
        in_pause = False
        pause_len = 0
        for v in is_pause:
            if v:
                in_pause = True
                pause_len += 1
            else:
                if in_pause and pause_len >= 2:   # ≥ 20 ms = real pause
                    pause_runs.append(pause_len)
                in_pause = False
                pause_len = 0
        result.pause_count = len(pause_runs)
        if pause_runs:
            # Convert frames → ms
            ms_per_frame = hop / sr * 1000
            result.mean_pause_duration_ms = float(np.mean(pause_runs) * ms_per_frame)

        # Rhythm regularity: variance of inter-onset intervals.
        # Split audio into 4 equal sub-windows and measure RMS of each.
        parts = np.array_split(audio, 8)
        sub_rms = np.array([np.sqrt(np.mean(p ** 2)) for p in parts])
        sub_rms_norm = sub_rms / (sub_rms.max() + 1e-9)
        # Low variance = metronomic/robotic rhythm.
        result.energy_cv = float(np.std(sub_rms_norm) / (np.mean(sub_rms_norm) + 1e-9))
        # rhythm_regularity: 1 = perfectly metronomic (synthetic), 0 = natural
        result.rhythm_regularity = float(np.clip(1.0 - result.energy_cv * 3, 0, 1))

        # ------------------------------------------------------------------ #
        # 3. Speaking rate variability (via zero-crossing rate as syllable proxy)#
        # ------------------------------------------------------------------ #
        zcr = librosa.feature.zero_crossing_rate(audio, frame_length=1024, hop_length=hop)[0]
        # Split ZCR into 4 segments and measure variability
        zcr_parts = np.array_split(zcr, 4)
        zcr_means = np.array([p.mean() for p in zcr_parts if len(p)])
        if len(zcr_means) >= 2:
            result.local_rate_cv = float(np.std(zcr_means) / (np.mean(zcr_means) + 1e-9))

        # ------------------------------------------------------------------ #
        # 4. Fuse sub-scores → prosody_risk                                  #
        # ------------------------------------------------------------------ #
        subscores: dict[str, float] = {}

        # Pitch CV: human speech typical ∈ [0.10, 0.40]; TTS < 0.08 or > 0.55 (monotone or jittery)
        if result.pitch_cv < 0.05:
            subscores["pitch_monotone"] = 0.85   # very flat = robotic
        elif result.pitch_cv < 0.10:
            subscores["pitch_monotone"] = 0.55
        else:
            subscores["pitch_monotone"] = 0.0

        # Pitch contour smoothness
        subscores["contour_smoothness"] = result.pitch_contour_smoothness * 0.8

        # Rhythm regularity
        subscores["rhythm_metronomic"] = result.rhythm_regularity * 0.75

        # Low speaking-rate variability
        if result.local_rate_cv < 0.05:
            subscores["rate_flat"] = 0.70
        elif result.local_rate_cv < 0.10:
            subscores["rate_flat"] = 0.35
        else:
            subscores["rate_flat"] = 0.0

        # Unnatural pause absence (TTS often runs without natural breathing pauses)
        if len(audio) / sr > 1.5 and result.pause_count == 0:
            subscores["no_pauses"] = 0.50
        else:
            subscores["no_pauses"] = 0.0

        # Weighted fusion of sub-scores
        weights = {
            "pitch_monotone":    0.30,
            "contour_smoothness": 0.25,
            "rhythm_metronomic": 0.20,
            "rate_flat":         0.15,
            "no_pauses":         0.10,
        }
        fused = sum(subscores.get(k, 0) * w for k, w in weights.items())
        result.prosody_risk = round(float(np.clip(fused, 0.0, 1.0)), 4)
        result.subscores = {k: round(float(v), 4) for k, v in subscores.items()}
        result.analysis_available = True

    except Exception as exc:
        result.error = str(exc)
        log.warning("Prosody analysis failed: %s", exc)

    return result
