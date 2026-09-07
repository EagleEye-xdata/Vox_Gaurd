"""Read only one window at a time from user-provided source recordings."""
from pathlib import Path
import os
import stat
import soundfile as sf
import numpy as np
from scipy.signal import resample_poly
from math import gcd
from .preprocessing import SAMPLE_RATE

AUDIO_DIR = Path(__file__).resolve().parents[2] / "demo_audio"

def resolve_audio(name: str) -> Path:
    path = (AUDIO_DIR / name).resolve()
    if path.parent != AUDIO_DIR.resolve() or path.suffix.lower() != ".wav" or not path.exists():
        raise ValueError("Select a WAV file from demo_audio.")
    is_fifo = stat.S_ISFIFO(os.stat(path).st_mode) if path.exists() else False
    if not is_fifo:
        with sf.SoundFile(path) as f:
            if len(f)/f.samplerate > 300 or f.samplerate > 96000 or f.channels > 2:
                raise ValueError("Use a mono/stereo WAV up to 5 minutes and 96 kHz.")
    return path

def chunks(path: Path, seconds: float = 3, hop_seconds: float = 1):
    is_fifo = stat.S_ISFIFO(os.stat(path).st_mode) if path.exists() else False

    if is_fifo:
        with sf.SoundFile(path) as f:
            window_frames = int(f.samplerate * seconds)
            hop_frames = int(f.samplerate * hop_seconds)
            buffer = np.zeros((0, f.channels), dtype="float32")

            while True:
                needed = window_frames - len(buffer)
                if needed > 0:
                    raw = f.read(needed, dtype="float32", always_2d=True)
                    if len(raw) == 0:
                        break
                    buffer = np.vstack((buffer, raw))

                if len(buffer) >= window_frames:
                    mono = buffer[:window_frames].mean(axis=1)
                    divisor = gcd(f.samplerate, SAMPLE_RATE)
                    audio = resample_poly(mono, SAMPLE_RATE//divisor, f.samplerate//divisor).astype(np.float32)
                    try:
                        yield audio
                    finally:
                        audio.fill(0)
                    buffer = buffer[hop_frames:]
    else:
        with sf.SoundFile(path) as f:
            window_frames = int(f.samplerate * seconds)
            hop_frames = int(f.samplerate * hop_seconds)
            position = 0
            while position < len(f):
                f.seek(position)
                raw = f.read(window_frames, dtype="float32", always_2d=True)
                if len(raw) < window_frames:
                    raw.fill(0)
                    break
                mono = raw.mean(axis=1)
                raw.fill(0)
                divisor = gcd(f.samplerate, SAMPLE_RATE)
                audio = resample_poly(mono, SAMPLE_RATE//divisor, f.samplerate//divisor).astype(np.float32)
                mono.fill(0)
                try:
                    yield audio
                finally:
                    audio.fill(0)
                position += hop_frames
