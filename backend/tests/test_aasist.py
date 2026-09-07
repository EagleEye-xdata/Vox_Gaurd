"""Tests for the AASIST-L adapter (backend/app/aasist.py).

Unit tests here mock the ONNX session so they run with no network access and no model file.
`test_aasist_real_model_smoke` is the exception: it loads the real downloaded model and runs one
real inference, and is skipped automatically when the model file is not present (it is not
committed -- see backend/models/README.md) rather than failing the suite for everyone.
"""
import numpy as np
import pytest

from app.aasist import AASISTLClassifier, DEFAULT_MODEL_PATH, NB_SAMPLES, SAMPLE_RATE, pad_fixed
from app.detection import HEURISTIC_FALLBACK, HeuristicClassifier, SpoofClassifier


# --------------------------------------------------------------------------------------------
# pad_fixed: the deterministic eval-window adapter (matches clovaai/aasist data_utils.pad())
# --------------------------------------------------------------------------------------------

def test_pad_fixed_exact_length_is_unchanged():
    x = np.arange(NB_SAMPLES, dtype=np.float32)
    out = pad_fixed(x)
    assert out.shape == (NB_SAMPLES,)
    assert np.array_equal(out, x)


def test_pad_fixed_crops_longer_input_from_the_start():
    """No random crop, no centering -- the first NB_SAMPLES samples, matching the reference."""
    x = np.arange(NB_SAMPLES + 5000, dtype=np.float32)
    out = pad_fixed(x)
    assert out.shape == (NB_SAMPLES,)
    assert np.array_equal(out, x[:NB_SAMPLES])


def test_pad_fixed_tiles_shorter_input():
    n = 48000  # a real 3-second, 16 kHz window
    x = np.arange(n, dtype=np.float32)
    out = pad_fixed(x)
    assert out.shape == (NB_SAMPLES,)
    reps = NB_SAMPLES // n + 1
    expected = np.tile(x, reps)[:NB_SAMPLES]
    assert np.array_equal(out, expected)
    # The tail must be the tiled repeat, not silence or zero-padding.
    assert out[n] == x[0]


def test_pad_fixed_handles_empty_input_without_crashing():
    out = pad_fixed(np.zeros(0, dtype=np.float32))
    assert out.shape == (NB_SAMPLES,)
    assert np.all(out == 0)


def test_pad_fixed_converts_to_float32():
    out = pad_fixed(np.arange(100, dtype=np.float64))
    assert out.dtype == np.float32


# --------------------------------------------------------------------------------------------
# A fake ONNX session so the rest of the adapter can be tested with no model file and no network
# --------------------------------------------------------------------------------------------

class FakeSession:
    """Stands in for onnxruntime.InferenceSession. Records call count; returns fixed logits."""

    def __init__(self, logits=(0.0, 0.0)):
        self.logits = np.array([logits], dtype=np.float32)
        self.run_count = 0

    def run(self, output_names, feed):
        self.run_count += 1
        wav = feed["wav"]
        assert wav.shape == (1, NB_SAMPLES), f"unexpected input shape {wav.shape}"
        assert wav.dtype == np.float32
        return [self.logits]


def _loaded_classifier(logits=(0.0, 0.0)) -> tuple[AASISTLClassifier, FakeSession]:
    """An AASISTLClassifier wired to a fake session, bypassing load()/onnxruntime entirely."""
    clf = AASISTLClassifier()
    session = FakeSession(logits)
    clf._session = session
    clf._input_name = "wav"
    clf._output_name = "logits"
    clf.provider = "CPUExecutionProvider"
    return clf, session


# --------------------------------------------------------------------------------------------
# Interface compatibility
# --------------------------------------------------------------------------------------------

def test_aasist_classifier_is_a_spoof_classifier():
    assert issubclass(AASISTLClassifier, SpoofClassifier)


def test_analyze_returns_the_same_keys_as_the_heuristic():
    clf, _ = _loaded_classifier((0.0, 2.0))  # confidently bona fide
    audio = np.zeros(48000, dtype=np.float32)
    aasist_keys = set(clf.analyze(audio, {}))
    heuristic_keys = set(HeuristicClassifier().score_features(
        {"spectral_flatness": 1e-3, "pitch_cv": 0.1, "jitter": 0.01, "shimmer": 0.01}))
    assert aasist_keys == heuristic_keys


def test_analyze_output_is_finite_and_in_range():
    clf, _ = _loaded_classifier((1.5, -0.7))
    result = clf.analyze(np.zeros(48000, dtype=np.float32), {})
    for key in ("spectral_score", "prosody_score", "synthetic_score"):
        assert np.isfinite(result[key])
        assert 0.0 <= result[key] <= 1.0


# --------------------------------------------------------------------------------------------
# Score direction: higher bona-fide logit -> lower synthetic_score, and vice versa
# --------------------------------------------------------------------------------------------

def test_score_direction_confidently_bonafide():
    clf, _ = _loaded_classifier((-4.0, 4.0))  # spoof_logit, bonafide_logit
    result = clf.analyze(np.zeros(48000, dtype=np.float32), {})
    assert result["synthetic_score"] < 0.01
    assert result["classification"] == "REAL"


def test_score_direction_confidently_spoof():
    clf, _ = _loaded_classifier((4.0, -4.0))
    result = clf.analyze(np.zeros(48000, dtype=np.float32), {})
    assert result["synthetic_score"] > 0.99
    assert result["classification"] == "SYNTHETIC"


def test_score_direction_at_the_decision_boundary():
    clf, _ = _loaded_classifier((0.0, 0.0))  # equal logits -> p_spoof == 0.5
    result = clf.analyze(np.zeros(48000, dtype=np.float32), {})
    assert abs(result["synthetic_score"] - 0.5) < 1e-9
    # classification uses >= threshold (default 0.5), so an exact tie reads as SYNTHETIC.
    assert result["classification"] == "SYNTHETIC"


def test_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv("AASIST_THRESHOLD", "0.9")
    clf = AASISTLClassifier()
    assert clf.threshold == 0.9


# --------------------------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------------------------

def test_predict_rejects_non_finite_input():
    clf, _ = _loaded_classifier((0.0, 0.0))
    bad = np.zeros(48000, dtype=np.float32)
    bad[100] = np.nan
    with pytest.raises(ValueError):
        clf.predict(bad)


def test_predict_rejects_wrong_output_arity():
    clf, session = _loaded_classifier()
    session.logits = np.array([[0.1, 0.2, 0.3]], dtype=np.float32)  # 3 logits, not 2
    with pytest.raises(ValueError):
        clf.predict(np.zeros(48000, dtype=np.float32))


def test_predict_rejects_non_finite_model_output():
    clf, session = _loaded_classifier()
    session.logits = np.array([[np.inf, 0.0]], dtype=np.float32)
    with pytest.raises(ValueError):
        clf.predict(np.zeros(48000, dtype=np.float32))


def test_shorter_and_longer_inputs_both_reach_the_model_at_the_fixed_length():
    clf, session = _loaded_classifier((0.0, 1.0))
    for n in (4000, 48000, 64600, 96000):
        clf.predict(np.zeros(n, dtype=np.float32))
    assert session.run_count == 4


# --------------------------------------------------------------------------------------------
# Loading, availability, and fallback behaviour
# --------------------------------------------------------------------------------------------

def test_is_available_false_before_load():
    clf = AASISTLClassifier()
    assert clf.is_available() is False


def test_load_failure_when_model_missing_and_auto_download_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("AASIST_AUTO_DOWNLOAD", "false")
    clf = AASISTLClassifier(model_path=tmp_path / "does-not-exist.onnx")
    assert clf.load() is False
    assert clf.is_available() is False
    assert clf.load_error is not None


def test_load_failure_on_a_corrupt_model_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AASIST_AUTO_DOWNLOAD", "false")
    bad_model = tmp_path / "corrupt.onnx"
    bad_model.write_bytes(b"not a real onnx file")
    clf = AASISTLClassifier(model_path=bad_model)
    assert clf.load() is False
    assert clf.is_available() is False
    assert "onnx" in clf.load_error.lower() or clf.load_error  # some ORT-raised message


def test_load_is_idempotent_and_does_not_reload(monkeypatch):
    """Loaded once per process: a second call must not recreate the session."""
    clf, session = _loaded_classifier()
    assert clf.load() is True  # already loaded; must be a no-op, not attempt a real load
    assert clf._session is session


def test_sidecar_falls_back_to_heuristic_when_the_active_detector_raises(monkeypatch):
    """CLAUDE.md invariant 2: a detector failure degrades the window, it does not crash the call."""
    from app import sidecar

    class AlwaysFails(SpoofClassifier):
        name = "always-fails-test-double"
        model_version = "test@0"
        calibrator_version = "test@0"

        def predict(self, chunk):
            raise RuntimeError("simulated detector failure")

        def analyze(self, audio, features):
            raise RuntimeError("simulated detector failure")

    monkeypatch.setattr(sidecar, "classifier", AlwaysFails())

    t = np.arange(48000) / SAMPLE_RATE
    audio = (0.25 * np.sin(2 * np.pi * 170 * t)).astype(np.float32)
    result = sidecar.analyse_buffer(audio)

    assert result["scored"] is True
    assert result["model_version"] == HEURISTIC_FALLBACK.model_version
    assert result["model"] == HEURISTIC_FALLBACK.name


def test_health_reports_detector_mode_for_each_path(monkeypatch):
    from app import sidecar

    monkeypatch.setattr(sidecar, "classifier", HEURISTIC_FALLBACK)
    assert sidecar.health()["detector_mode"] == "heuristic-fallback"

    aasist, _ = _loaded_classifier()
    monkeypatch.setattr(sidecar, "classifier", aasist)
    health = sidecar.health()
    assert health["detector_mode"] == "aasist-l"
    assert health["detector_provider"] == "CPUExecutionProvider"


# --------------------------------------------------------------------------------------------
# Real model smoke test -- only runs if the file has actually been fetched
# --------------------------------------------------------------------------------------------

@pytest.mark.skipif(not DEFAULT_MODEL_PATH.exists(),
                     reason="aasist-l.onnx not present; run backend/tools/download_aasist_model.py")
def test_aasist_real_model_smoke():
    clf = AASISTLClassifier()
    assert clf.load() is True
    assert clf.is_available()
    assert clf.provider in ("CPUExecutionProvider", "CUDAExecutionProvider")

    t = np.arange(48000) / SAMPLE_RATE
    audio = (0.25 * np.sin(2 * np.pi * 170 * t)).astype(np.float32)
    result = clf.analyze(audio, {})
    assert np.isfinite(result["synthetic_score"])
    assert 0.0 <= result["synthetic_score"] <= 1.0

    # Loaded once: a second call must reuse the same session, not reload the model file.
    session_before = clf._session
    clf.load()
    assert clf._session is session_before
