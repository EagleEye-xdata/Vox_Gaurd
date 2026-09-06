"""Conservative energy/periodicity VAD. Not a trained speech/noise separator."""
import numpy as np
from scipy.signal import butter, sosfilt

SAMPLE_RATE = 16000

def preprocess(audio: np.ndarray, sr: int = SAMPLE_RATE):
    x = np.asarray(audio, dtype=np.float32).copy()
    if x.size < sr // 4 or not np.isfinite(x).all():
        return None
    x -= x.mean()
    x = sosfilt(butter(3, [80, 3800], btype="bandpass", fs=sr, output="sos"), x).astype(np.float32)
    frames = x[:len(x)//480*480].reshape(-1, 480)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    power = np.abs(np.fft.rfft(frames * np.hanning(480), axis=1))**2 + 1e-12
    # Measure within the passband; filtered-out bins otherwise make noise look tonal.
    frequencies = np.fft.rfftfreq(480, 1/sr)
    power = power[:, (frequencies >= 150) & (frequencies <= 3500)]
    flatness = np.exp(np.mean(np.log(power), axis=1)) / power.mean(axis=1)
    # Broadband noise and silence do not produce a score. Speech-like tones can pass.
    voiced = (rms > 0.008) & (flatness < 0.35)
    if voiced.sum() < 5 or voiced.mean() < 0.15:
        x.fill(0)
        return None
    # Gentle noise gate; preserve temporal structure for prosody.
    for i, active in enumerate(voiced):
        if not active:
            x[i*480:(i+1)*480] *= 0.1
    return x, float(voiced.mean())
