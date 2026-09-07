"""AASIST-L neural anti-spoofing detector (ONNX Runtime), behind the SpoofClassifier interface.

Source: https://huggingface.co/SpeechAntiSpoofingBenchmarks/AASIST-L (MIT licence, inherited from
the upstream https://github.com/clovaai/aasist ASVspoof2019 LA checkpoint). This module wraps the
published `aasist-l.onnx` export; it does not retrain or fine-tune anything.

CLAUDE.md decision D-1 puts a real neural detector behind `SpoofClassifier`, with the heuristic as
a fast, always-available fallback. AASIST-L is that real detector for Phase 0: it is CPU-only,
~85k parameters, and needs no PyTorch/torchaudio in the sidecar's default dependency set.

Contract, confirmed by inspecting the ONNX graph directly with onnxruntime rather than assumed
(`onnxruntime.InferenceSession(...).get_inputs()/.get_outputs()`):

    input  "wav"     float32  (batch, 64600)
    output "logits"  float32  (batch, 2)   logits[:, 0] = spoof, logits[:, 1] = bona fide

`aasist_l.py` (the maintainer's own inference wrapper, shipped in the same HF repo) confirms the
same ordering in its docstring and returns `logits[:, 1]` as "the Arena score ... higher = more
bona fide". Softmax-ing the two logits turns that into a bounded score; nothing here treats it as
a calibrated probability (CLAUDE.md invariant 10) -- see `calibrator_version` below.

CALIBRATION -- read before trusting this score
------------------------------------------------
`p_spoof = sigmoid(logit_spoof - logit_bonafide)` is a deterministic, principled squash of the raw
two-class logit (not itself a probability estimate the model was trained to produce), computed the
same way for every window. It has not been fit or validated against any held-out calibration split
for this project's audio (`05-ML_MODEL_LIFECYCLE.md` section 3 requires that before a score may be
trusted numerically). Until that work is done, `calibrator_version` is stamped
"identity-sigmoid@0.0.0-unvalidated" -- the same honesty convention `HeuristicClassifier` already
uses for its own unvalidated identity calibrator. Do not read `synthetic_score` here as "N% chance
of being AI-generated"; read it as "the neural detector's evidence, uncalibrated."

`AASIST_THRESHOLD` (default 0.5, i.e. the model's own decision boundary between its two classes)
is a provisional, configuration-driven cut point for the human-readable `classification` label
only. It has no effect on the numeric score that reaches the Go risk fusion.

GENERALISATION -- also read before trusting this score
--------------------------------------------------------
Per the model card, AASIST-L reaches 0.99% EER on its own ASVspoof2019 LA benchmark but 44.45% EER
on the InTheWild out-of-domain set (near coin-flip). Treat it as one signal in VoxGuard's
multi-signal fusion, never as a standalone verdict -- this is exactly what the Go fusion already
does for every detector behind this interface.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from .spoof_classifier import SpoofClassifier

log = logging.getLogger("voiceshield.aasist")

# --- Model identity and source -------------------------------------------------------------
MODEL_REPO = "SpeechAntiSpoofingBenchmarks/AASIST-L"
MODEL_FILENAME = "aasist-l.onnx"
MODEL_URL = f"https://huggingface.co/{MODEL_REPO}/resolve/main/{MODEL_FILENAME}"
# Pinned at integration time (2026-09) by downloading the published file and hashing it. Update
# this if the maintainer republishes the file under the same path with different bytes; a mismatch
# fails the download rather than silently loading an unverified binary.
EXPECTED_SHA256 = "f43f0a638b52846f5d0e630c0a738d10e9306325945127c6f8662d559585f218"

MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
DEFAULT_MODEL_PATH = MODEL_DIR / MODEL_FILENAME

# --- Contract constants, confirmed against the ONNX graph, not assumed --------------------
SAMPLE_RATE = 16000
NB_SAMPLES = 64600  # ~4.0375 s; matches the upstream clovaai/aasist eval window exactly.
ONNX_INPUT_NAME = "wav"
ONNX_OUTPUT_NAME = "logits"


def pad_fixed(x: np.ndarray, max_len: int = NB_SAMPLES) -> np.ndarray:
    """Deterministic eval window: first `max_len` samples; tile-repeat if shorter.

    This is the exact behaviour of clovaai/aasist's `data_utils.pad()` used at dev/eval time (no
    random crop) -- confirmed against `aasist_l.py`'s own `pad_fixed`, shipped in the model repo.
    An all-empty window returns silence of the required length rather than raising, since it is
    reachable only through a defensive/test path (the real pipeline never hands this function
    fewer than 4000 samples -- see `preprocessing.preprocess` and `sidecar.AnalyseRequest`).
    """
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    n = x.shape[0]
    if n == 0:
        return np.zeros(max_len, dtype=np.float32)
    if n >= max_len:
        return x[:max_len]
    reps = max_len // n + 1
    return np.tile(x, reps)[:max_len].astype(np.float32)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    # Numerically stable logistic sigmoid; the inputs here are two logits' difference, not raw
    # network activations, so the usual overflow risk from unbounded pre-activations doesn't
    # apply, but this form is cheap and avoids relying on scipy.special being importable.
    return np.where(x >= 0, 1.0 / (1.0 + np.exp(-x)), np.exp(x) / (1.0 + np.exp(x)))


def download_model(dest_path: Path = DEFAULT_MODEL_PATH, url: str = MODEL_URL,
                    expected_sha256: str | None = EXPECTED_SHA256, timeout: float = 60.0) -> bool:
    """Fetch the published ONNX file to `dest_path`, verifying its hash before installing it.

    Uses only the standard library (no huggingface_hub dependency) since this is a one-shot
    bootstrap, not something ordinary inference should depend on. Downloads to a temp file in the
    same directory and renames into place, so a failed or interrupted download can never leave a
    corrupt file at `dest_path` for a later run to load silently.
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(dest_path.parent), prefix=".aasist-download-")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as tmp_file:
                with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 (public model file only)
                    hasher = hashlib.sha256()
                    while True:
                        block = response.read(1 << 20)
                        if not block:
                            break
                        hasher.update(block)
                        tmp_file.write(block)
            digest = hasher.hexdigest()
            if expected_sha256 and digest != expected_sha256:
                log.error("AASIST-L download hash mismatch: got %s, expected %s", digest, expected_sha256)
                tmp_path.unlink(missing_ok=True)
                return False
            tmp_path.replace(dest_path)
            log.info("AASIST-L model downloaded to %s (sha256=%s)", dest_path, digest)
            return True
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        log.warning("AASIST-L model download failed: %s", error)
        return False


class AASISTLClassifier(SpoofClassifier):
    """ONNX Runtime inference for the official AASIST-L checkpoint.

    Operates on the *raw* resampled 16 kHz mono waveform -- the same buffer `ingestion.chunks()`
    and `audiosocket._to_analysis_rate()` already produce, before `preprocessing.preprocess()`'s
    band-pass filter and noise gate touch it. AASIST-L is a raw-waveform model; feeding it the
    heavily filtered/gated signal built for the hand-crafted feature path would be feeding it
    something closer to what it was not evaluated on.
    """

    name = "aasist-l-onnx (neural, uncalibrated)"
    model_version = "aasist-l@SpeechAntiSpoofingBenchmarks-AASIST-L-onnx"
    calibrator_version = "identity-sigmoid@0.0.0-unvalidated"

    def __init__(self, model_path: Path | str | None = None):
        self.model_path = Path(model_path) if model_path else Path(
            os.getenv("AASIST_MODEL_PATH", str(DEFAULT_MODEL_PATH)))
        self.auto_download = os.getenv("AASIST_AUTO_DOWNLOAD", "true").strip().lower() not in (
            "0", "false", "no")
        self.threshold = float(os.getenv("AASIST_THRESHOLD", "0.5"))
        self.use_cuda = os.getenv("AASIST_USE_CUDA", "false").strip().lower() in ("1", "true", "yes")
        self._session = None
        self.provider: str | None = None
        self.load_error: str | None = None

    def is_available(self) -> bool:
        return self._session is not None

    def load(self) -> bool:
        """Create the ONNX Runtime session once. Idempotent: a second call is a no-op if the
        first already succeeded, and re-attempts only if the first attempt failed."""
        if self._session is not None:
            return True
        try:
            if not self.model_path.exists():
                if not self.auto_download:
                    raise FileNotFoundError(
                        f"AASIST-L model not found at {self.model_path} and AASIST_AUTO_DOWNLOAD is disabled")
                log.info("AASIST-L model missing at %s; downloading once from %s", self.model_path, MODEL_URL)
                if not download_model(self.model_path):
                    raise RuntimeError("AASIST-L model download failed; see log for details")

            import onnxruntime as ort  # imported lazily so a missing dependency degrades cleanly

            providers = []
            if self.use_cuda and "CUDAExecutionProvider" in ort.get_available_providers():
                providers.append("CUDAExecutionProvider")
            providers.append("CPUExecutionProvider")

            session = ort.InferenceSession(str(self.model_path), providers=providers)
            input_meta = session.get_inputs()[0]
            output_meta = session.get_outputs()[0]
            last_dim = input_meta.shape[-1] if input_meta.shape else None
            if isinstance(last_dim, int) and last_dim != NB_SAMPLES:
                log.warning("AASIST-L ONNX input's fixed dimension is %s, not the expected %d; "
                            "continuing with the discovered value is not attempted here -- "
                            "onnxruntime will raise a clear shape error on the first inference "
                            "if this model file does not actually match this adapter.",
                            last_dim, NB_SAMPLES)

            self._session = session
            self._input_name = input_meta.name
            self._output_name = output_meta.name
            self.provider = session.get_providers()[0]
            self.load_error = None
            log.info("AASIST-L loaded from %s (provider=%s, input=%s, output=%s)",
                     self.model_path, self.provider, input_meta.shape, output_meta.shape)
            return True
        except Exception as error:
            self._session = None
            self.load_error = str(error)
            log.warning("AASIST-L failed to load, falling back to the heuristic detector: %s", error)
            return False

    def _infer_logits(self, audio: np.ndarray) -> np.ndarray:
        if self._session is None:
            raise RuntimeError("AASIST-L session is not loaded")
        windowed = pad_fixed(audio, NB_SAMPLES).reshape(1, NB_SAMPLES)
        if not np.isfinite(windowed).all():
            raise ValueError("AASIST-L input contains non-finite samples")
        outputs = self._session.run([self._output_name], {self._input_name: windowed})
        logits = np.asarray(outputs[0], dtype=np.float64).reshape(-1)
        if logits.shape[0] != 2:
            raise ValueError(f"AASIST-L returned {logits.shape[0]} logits, expected 2")
        if not np.isfinite(logits).all():
            raise ValueError("AASIST-L produced a non-finite output")
        return logits

    def predict(self, chunk: np.ndarray) -> float:
        """SpoofClassifier contract: uncalibrated synthetic-likelihood indicator in [0, 1]."""
        logits = self._infer_logits(chunk)
        spoof_logit, bonafide_logit = logits[0], logits[1]
        return float(_sigmoid(spoof_logit - bonafide_logit))

    def analyze(self, audio: np.ndarray, features: dict) -> dict:
        """Full detector-result contract, matching `HeuristicClassifier.score_features`'s shape.

        `features` (the hand-crafted DSP features) is accepted for interface symmetry but not
        used -- AASIST-L consumes the raw waveform passed in `audio` instead. Callers that need
        the older heuristic behaviour instead should call `HeuristicClassifier().score_features`
        or `.analyze` directly; this method never falls back internally so a caller (the sidecar)
        can decide what "detector failed" should mean for that request.
        """
        p_spoof = float(self.predict(audio))
        classification = "SYNTHETIC" if p_spoof >= self.threshold else "REAL"
        synthetic_score = round(p_spoof, 4)
        return {
            # AASIST-L is a single end-to-end score, not a spectral/prosody decomposition; both
            # sub-fields mirror the overall score so the API contract (which requires both) stays
            # satisfied without inventing components the model does not actually produce.
            "spectral_score": synthetic_score,
            "prosody_score": synthetic_score,
            "speaker_match_score": None,
            "speaker_status": "not_enrolled",
            "synthetic_score": synthetic_score,
            "classification": classification,
            "classification_note": (
                "Neural detector (AASIST-L, ONNX, raw waveform). Uncalibrated sigmoid of the "
                "model's own two-class logit difference; spectral/prosody sub-scores are not "
                "decomposed and mirror the overall score. See docs/05-ML_MODEL_LIFECYCLE.md."
            ),
            "model": self.name,
            "model_version": self.model_version,
            "calibrator_version": self.calibrator_version,
        }
