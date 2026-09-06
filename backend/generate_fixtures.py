"""Deterministic DSP fixtures. Neither file is genuine speech or a cloned person."""
from pathlib import Path
import numpy as np
import soundfile as sf

def generate():
    out = Path(__file__).resolve().parents[1]/"demo_audio"
    out.mkdir(exist_ok=True)
    sr = 16000
    t = np.arange(sr*24)/sr
    for name, pitch in [("steady", np.full_like(t, 170)), ("variable", 165+45*np.sin(2*np.pi*0.8*t)+22*np.sin(2*np.pi*2.3*t))]:
        phase = 2*np.pi*np.cumsum(pitch)/sr
        signal = sum(np.sin(k*phase)/k for k in range(1, 9))
        envelope = (0.3+0.7*np.sin(2*np.pi*2.5*t)**2) if name=="variable" else np.ones_like(t)
        signal = 0.22*signal*envelope
        sf.write(out/f"fixture-{name}.wav", signal, sr)
    sf.write(out/"fixture-silence.wav", np.zeros(sr*9), sr)
    print(f"Created 3 clearly labelled test signals in {out}")

if __name__ == "__main__":
    generate()
