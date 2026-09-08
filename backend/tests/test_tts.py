"""Tests for the neural-TTS demo path (backend/app/tts.py + /internal/tts/speak).

The behaviour under test is the one the module exists to guarantee: the score shown next to a
generated turn is the score a detector returned for that exact waveform, and every failure mode
is reported rather than filled in with a plausible-looking number.

Nothing here downloads a checkpoint. Synthesis is stubbed so the contract can be tested on a
machine with no torch, no weights and no network; the real end-to-end measurement lives in the
work-unit report, not in a test that would make CI depend on a 145 MB download.
"""
import base64
import io
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import tts
from app.sidecar import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _speech_like(seconds=7.0, sample_rate=tts.SAMPLE_RATE):
    """A voiced, non-silent buffer that survives `preprocessing.preprocess`'s noise gate.

    Harmonic stack with a moving F0 and an amplitude envelope, i.e. something with a pitch track
    to extract. It is a signal, not a voice: no person is recorded or imitated.
    """
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    f0 = 150 + 30 * np.sin(2 * np.pi * 0.7 * t)
    phase = 2 * np.pi * np.cumsum(f0) / sample_rate
    signal = sum(np.sin(k * phase) / k for k in range(1, 9))
    envelope = 0.35 + 0.65 * np.sin(2 * np.pi * 2.2 * t) ** 2
    return (0.25 * signal * envelope).astype(np.float32)


# --- voice routing -------------------------------------------------------------------------

def test_romanised_hinglish_routes_away_from_the_devanagari_checkpoint():
    """The measured failure this guard exists for: mms-tts-hin cannot tokenise Latin script and
    raises inside its attention layer rather than returning bad audio."""
    assert tts.select_voice("Namaste, main HDFC se Rohit bol raha hoon") == "eng"


def test_devanagari_routes_to_hindi():
    assert tts.select_voice("नमस्ते, मैं रोहित बोल रहा हूँ") == "hin"


def test_explicit_hindi_request_is_overridden_for_latin_text():
    """An explicit `voice` may not force a crash: routing follows the script that is present."""
    assert tts.select_voice("Namaste sir", requested="hin") == "eng"
    assert tts.select_voice("नमस्ते", requested="hin") == "hin"


def test_other_indic_scripts_route_to_their_own_checkpoints():
    assert tts.select_voice("வணக்கம்") == "tam"
    assert tts.select_voice("నమస్కారం") == "tel"
    assert tts.select_voice("নমস্কার") == "ben"


def test_model_allowlist_holds_only_text_only_checkpoints():
    """CLAUDE.md invariant 14: adding a voice-cloning checkpoint must be a deliberate code change.

    Every allowed id is a single-speaker MMS-TTS model, which takes text and nothing else.
    """
    assert set(tts.ALLOWED_MODELS) == {"eng", "hin", "tam", "tel", "ben"}
    assert all(model.startswith("facebook/mms-tts-") for model in tts.ALLOWED_MODELS.values())


# --- WAV encoding --------------------------------------------------------------------------

def test_wav_encoding_is_in_memory_16k_mono_pcm16(tmp_path):
    audio = _speech_like(seconds=1.0)
    data = tts.to_wav_bytes(audio)
    with wave.open(io.BytesIO(data), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == tts.SAMPLE_RATE
        assert handle.getnframes() == len(audio)
    # Invariant 1: encoding produced bytes, not a file anywhere on disk.
    assert list(tmp_path.iterdir()) == []


# --- endpoint contract ---------------------------------------------------------------------

def test_speak_returns_audio_scored_by_the_live_detector(client, monkeypatch):
    """The returned score must come from the detector, for the returned audio.

    The stub returns a known waveform; the assertion is that `detection` reflects whatever the
    configured classifier said about *that* buffer, with the versions needed to reproduce it.
    """
    audio = _speech_like(seconds=7.0)
    monkeypatch.setattr(
        tts.SYNTHESIZER, "synthesize",
        lambda text, voice=None: (audio.copy(), "eng", "facebook/mms-tts-eng"))

    response = client.post("/internal/tts/speak", json={"text": "Please confirm the code now."})
    assert response.status_code == 200
    body = response.json()

    decoded = base64.b64decode(body["audio_wav_base64"])
    with wave.open(io.BytesIO(decoded), "rb") as handle:
        assert handle.getframerate() == tts.SAMPLE_RATE
        assert handle.getnframes() == len(audio)

    assert body["detection"]["status"] == "SCORED"
    assert body["detection"]["windows_scored"] >= 2
    assert 0.0 <= body["detection"]["p_synthetic_max"] <= 1.0
    # The aggregate is the max of the per-window scores, not a separate number.
    per_window = [w["p_synthetic"] for w in body["windows"] if w["scored"]]
    assert body["detection"]["p_synthetic_max"] == pytest.approx(round(max(per_window), 4))

    # Invariant 8: a decision that cannot name its versions cannot be defended.
    assert body["model_versions"]["detector"]
    assert body["model_versions"]["calibrator"]
    assert body["model_versions"]["tts"] == "facebook/mms-tts-eng@mms-tts-vits"
    # Decision D-3's licence posture applies to MMS too, and the response says so.
    assert "NC" in body["tts_licence"]


def test_unscoreable_audio_reports_unknown_not_a_low_score(client, monkeypatch):
    """CLAUDE.md invariant 2. Silence is the absence of evidence, so it must not read as a pass."""
    monkeypatch.setattr(
        tts.SYNTHESIZER, "synthesize",
        lambda text, voice=None: (np.zeros(7 * tts.SAMPLE_RATE, dtype=np.float32),
                                  "eng", "facebook/mms-tts-eng"))

    body = client.post("/internal/tts/speak", json={"text": "silence"}).json()
    assert body["detection"]["status"] == "UNKNOWN"
    assert body["detection"]["p_synthetic_max"] is None
    assert body["detection"]["windows_scored"] == 0
    assert not any(window["scored"] for window in body["windows"])


def test_tts_unavailable_is_surfaced_not_silently_substituted(client, monkeypatch):
    """A missing dependency must fail the turn loudly.

    Returning empty audio with a score would be exactly the fabricated result this endpoint was
    written to remove, so the fail-safe is a 503 the caller has to handle.
    """
    def unavailable(text, voice=None):
        raise tts.MMSTTSUnavailable("transformers/torch not installed")

    monkeypatch.setattr(tts.SYNTHESIZER, "synthesize", unavailable)
    response = client.post("/internal/tts/speak", json={"text": "hello"})
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"].lower()


def test_wrong_script_is_a_422_not_an_internal_error(client, monkeypatch):
    def wrong_script(text, voice=None):
        raise ValueError("Text produced no tokens for facebook/mms-tts-hin")

    monkeypatch.setattr(tts.SYNTHESIZER, "synthesize", wrong_script)
    assert client.post("/internal/tts/speak", json={"text": "..."}).status_code == 422


def test_empty_and_oversized_text_are_rejected_by_the_schema(client):
    assert client.post("/internal/tts/speak", json={"text": ""}).status_code == 422
    assert client.post("/internal/tts/speak", json={"text": "a" * 401}).status_code == 422
    # StrictModel forbids unknown fields, so a typo'd parameter cannot be silently ignored.
    assert client.post(
        "/internal/tts/speak", json={"text": "hi", "vioce": "hin"}).status_code == 422


def test_generated_audio_is_never_written_to_the_audio_directory(client, monkeypatch):
    """CLAUDE.md invariant 1: the demo's own generated audio does not land in demo_audio/."""
    from app.ingestion import AUDIO_DIR

    before = sorted(p.name for p in AUDIO_DIR.glob("*.wav"))
    monkeypatch.setattr(
        tts.SYNTHESIZER, "synthesize",
        lambda text, voice=None: (_speech_like(), "eng", "facebook/mms-tts-eng"))
    client.post("/internal/tts/speak", json={"text": "hello there"})
    assert sorted(p.name for p in AUDIO_DIR.glob("*.wav")) == before
