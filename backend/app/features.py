import numpy as np
import librosa
from scipy.signal import find_peaks
from .preprocessing import SAMPLE_RATE

def extract(audio, sr=SAMPLE_RATE):
    mel = librosa.feature.melspectrogram(y=audio, sr=sr, n_fft=512, hop_length=160, n_mels=40)
    log_mel = librosa.power_to_db(mel, ref=np.max)
    mfcc = librosa.feature.mfcc(S=log_mel, n_mfcc=13)
    pitch = librosa.yin(audio, fmin=65, fmax=450, sr=sr, frame_length=1024, hop_length=320)
    rms = librosa.feature.rms(y=audio, frame_length=1024, hop_length=320)[0]
    pitch = pitch[:len(rms)][rms[:len(pitch)] > 0.008]
    # YIN emits octave errors and edge artefacts (a constant 170 Hz tone yields a 457 Hz first
    # frame), which alone inflated pitch_cv from ~0 to 0.14 and dominated the prosody score.
    # Reject frames more than half an octave from the median. This is deliberately generous, but
    # it can still clip genuine wide intonation, which biases such speech toward "synthetic".
    if len(pitch) >= 5:
        keep = np.abs(np.log2(pitch / max(np.median(pitch), 1e-8))) < 0.5
        if keep.sum() >= 3:
            pitch = pitch[keep]
    jitter = float(np.mean(np.abs(np.diff(pitch))) / (np.mean(pitch)+1e-8)) if len(pitch)>1 else 0.
    shimmer = float(np.mean(np.abs(np.diff(rms))) / (np.mean(rms)+1e-8))
    flatness = float(librosa.feature.spectral_flatness(y=audio, n_fft=512).mean())
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    frequencies = np.fft.rfftfreq(len(audio), 1/sr)
    peaks, _ = find_peaks(spectrum, distance=max(1, int(150*len(audio)/sr)))
    peaks = sorted((p for p in peaks if 200 < frequencies[p] < 3500), key=lambda p:spectrum[p], reverse=True)[:3]
    # Coarse spectral peaks, explicitly not validated LPC formant trajectories.
    envelope = [float(np.sqrt(np.mean(part**2))) for part in np.array_split(audio, 96)]
    return {"mfcc_mean": mfcc.mean(axis=1).tolist(), "mel_mean_db": log_mel.mean(axis=1).tolist(),
            "pitch_hz": pitch[::4].tolist(), "pitch_cv": float(np.std(pitch)/(np.mean(pitch)+1e-8)) if len(pitch) else 0.,
            "jitter": jitter, "shimmer": shimmer, "spectral_flatness": flatness,
            "spectral_peak_hz": sorted(float(frequencies[p]) for p in peaks), "rms_envelope": envelope}
