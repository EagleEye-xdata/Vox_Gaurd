"""Neural TTS for the 2-way call demo: Meta MMS-TTS (VITS) run locally, in-process.

WHY THIS EXISTS
---------------
The 2-way call demo used to narrate its scam personas through the browser's `speechSynthesis`
and then display a *hardcoded* "AASIST-L: P_synth = 0.97" next to it. The number was scenery: no
detector ever saw that audio. This module makes the demo real. The bot's turn is synthesised
here as an actual neural-vocoder waveform, that waveform is scored by whichever detector
`detection.classifier` selected, and the score the operator sees is the score the model returned.

WHAT IT IS NOT (CLAUDE.md invariant 14)
---------------------------------------
MMS-TTS checkpoints are single-speaker, generic, anonymous voices. They cannot clone anyone: the
architecture takes text and nothing else -- no reference audio input, no speaker embedding, no
voice-conversion path. `ALLOWED_MODELS` below is an allowlist, not a default, precisely so that
pointing this module at a zero-shot cloning checkpoint (XTTS, OpenVoice, a fine-tuned RVC model)
is a code change that has to be argued for, not a config typo. Cloning a real person still
requires a signed entry in docs/CONSENT_LOG.md and is out of scope here.

AUDIO BOUNDARY (CLAUDE.md invariant 1)
--------------------------------------
Generated audio is an in-process numpy buffer; the only place it goes is the WAV body returned to
our own frontend for playback. Nothing is written to disk, and no audio is sent to a third-party
API -- the model runs locally on CPU. (Invariant 1 forbids shipping *call* audio out; this module
never touches call audio at all, only text it was given.)

LICENCE -- read before shipping anything but a hackathon demo
-------------------------------------------------------------
facebook/mms-tts-* is released under CC BY-NC 4.0, the same non-commercial constraint CLAUDE.md
decision D-3 already accepted for MLAAD. Audio generated here is therefore research/demo-only.
Nothing produced by this module may be used to train anything or ship in a commercial build.

SCRIPT REQUIREMENT -- measured, not assumed
--------------------------------------------
`mms-tts-hin` tokenises Devanagari. Handing it romanised Hinglish ("Namaste, main HDFC se...")
strips almost every character as out-of-vocabulary and the model then raises deep inside its
attention layer (`RuntimeError: narrow(): length must be non-negative`). `select_voice()` routes
on the script actually present in the string, so Hinglish -- which is written in Latin -- goes to
`mms-tts-eng`, which pronounces it with an accent rather than crashing.
"""
from __future__ import annotations

import io
import logging
import os
import re
import threading
import wave

import numpy as np

log = logging.getLogger("voiceshield.tts")

SAMPLE_RATE = 16000  # MMS-TTS native rate, and exactly AASIST-L's expected rate: no resampling.

# Allowlist of text-only, non-cloning checkpoints. See the invariant-14 note above.
ALLOWED_MODELS: dict[str, str] = {
    "eng": "facebook/mms-tts-eng",
    "hin": "facebook/mms-tts-hin",
    "tam": "facebook/mms-tts-tam",
    "tel": "facebook/mms-tts-tel",
    "ben": "facebook/mms-tts-ben",
}
DEFAULT_VOICE = "eng"

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_TAMIL = re.compile(r"[஀-௿]")
_TELUGU = re.compile(r"[ఀ-౿]")
_BENGALI = re.compile(r"[ঀ-৿]")

# Bound generation so a long persona line cannot pin the CPU for minutes on a demo box.
MAX_CHARS = int(os.getenv("VOXGUARD_TTS_MAX_CHARS", "400"))


def select_voice(text: str, requested: str | None = None) -> str:
    """Pick a checkpoint key for `text`, honouring an explicit request when it is allowed.

    Routing is by script, not by declared language, because that is what the tokeniser actually
    constrains -- see the SCRIPT REQUIREMENT note. Romanised Hinglish deliberately resolves to
    `eng`; sending it to `hin` is not a worse voice, it is a crash.
    """
    if requested and requested in ALLOWED_MODELS:
        if requested == "hin" and not _DEVANAGARI.search(text):
            log.info("TTS voice 'hin' requested for non-Devanagari text; routing to 'eng'.")
            return "eng"
        return requested
    if _DEVANAGARI.search(text):
        return "hin"
    if _TAMIL.search(text):
        return "tam"
    if _TELUGU.search(text):
        return "tel"
    if _BENGALI.search(text):
        return "ben"
    return DEFAULT_VOICE


class MMSTTSUnavailable(RuntimeError):
    """Raised when torch/transformers or the weights cannot be loaded.

    A caller must surface this, never substitute silence: a demo that quietly plays nothing while
    still showing a risk score would be exactly the scenery this module was written to remove.
    """


class MMSSynthesizer:
    """Lazily-loaded, process-wide cache of MMS-TTS VITS checkpoints.

    Loading is guarded by a lock because FastAPI serves these from a thread pool and two
    concurrent first-requests for the same voice would otherwise each pull ~145 MB of weights.
    """

    def __init__(self) -> None:
        self._models: dict[str, tuple] = {}
        self._lock = threading.Lock()
        self.load_error: str | None = None

    def is_loaded(self, voice: str) -> bool:
        return voice in self._models

    def _get(self, voice: str):
        if voice in self._models:
            return self._models[voice]
        with self._lock:
            if voice in self._models:  # another thread won the race while we waited
                return self._models[voice]
            model_id = ALLOWED_MODELS[voice]
            try:
                # Imported lazily: torch + transformers are heavy and optional. A sidecar without
                # them must still start and serve every other route.
                from transformers import AutoTokenizer, VitsModel
            except ImportError as error:
                self.load_error = f"transformers/torch not installed: {error}"
                raise MMSTTSUnavailable(self.load_error) from error
            try:
                tokenizer = AutoTokenizer.from_pretrained(model_id)
                model = VitsModel.from_pretrained(model_id)
                model.eval()
            except Exception as error:  # network, cache corruption, revoked repo
                self.load_error = f"could not load {model_id}: {error}"
                raise MMSTTSUnavailable(self.load_error) from error
            if model.config.sampling_rate != SAMPLE_RATE:
                # Guard the "no resampling needed" claim in this module's docstring rather than
                # trusting it: a checkpoint at another rate would silently mis-pitch the audio and
                # feed AASIST-L a time-scaled window.
                raise MMSTTSUnavailable(
                    f"{model_id} is {model.config.sampling_rate} Hz, expected {SAMPLE_RATE} Hz")
            self._models[voice] = (tokenizer, model, model_id)
            log.info("Loaded TTS checkpoint %s (voice=%s)", model_id, voice)
            return self._models[voice]

    def synthesize(self, text: str, voice: str | None = None) -> tuple[np.ndarray, str, str]:
        """Return (float32 mono waveform at 16 kHz, voice key, model id)."""
        text = (text or "").strip()
        if not text:
            raise ValueError("TTS text must not be empty.")
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS]
        chosen = select_voice(text, voice)
        tokenizer, model, model_id = self._get(chosen)

        import torch

        inputs = tokenizer(text, return_tensors="pt")
        if inputs["input_ids"].shape[-1] < 2:
            # The failure mode described in SCRIPT REQUIREMENT, caught before it reaches the
            # model and becomes an opaque RuntimeError from inside the attention layer.
            raise ValueError(
                f"Text produced no tokens for {model_id}; it is likely in the wrong script.")
        with torch.no_grad():
            waveform = model(**inputs).waveform[0].detach().cpu().numpy()
        audio = np.clip(np.asarray(waveform, dtype=np.float32), -1.0, 1.0)
        return audio, chosen, model_id


SYNTHESIZER = MMSSynthesizer()


def to_wav_bytes(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Encode to a 16-bit PCM WAV *in memory*. Never a file (CLAUDE.md invariant 1)."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()
