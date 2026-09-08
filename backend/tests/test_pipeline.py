"""Tests for the Python half of VoxGuard: DSP, VAD, features, the detector, speaker
verification, consent, and the audio boundary.

Everything downstream of the detector — risk fusion, session aggregation, decisions, alerts and
the audit ledger — moved to Go with the architecture in docs/01 section 2. Those tests live in
`gateway/internal/*/`. In particular the fusion is guarded by `gateway/internal/scoring/
golden_test.go`, which replays vectors emitted from the Python implementation that used to live
here, so the port cannot have silently changed the arithmetic.
"""
import asyncio
import re
from uuid import uuid4
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import sidecar
from app.audiosocket import AudioSocketIngest, FRAME_AUDIO_8K, FRAME_HANGUP, FRAME_UUID
from app.detection import HeuristicClassifier, classifier
from app.features import extract
from app.preprocessing import preprocess
from app.speaker_verification import verifier

ROOT = Path(__file__).resolve().parents[2]


def audiosocket_frame(frame_type: int, payload: bytes = b'') -> bytes:
    return bytes([frame_type]) + len(payload).to_bytes(2, 'big') + payload


# --------------------------------------------------------------------------------------------
# Preprocessing, VAD, features
# --------------------------------------------------------------------------------------------

def test_silence_and_noise_rejected():
    assert preprocess(np.zeros(48000)) is None
    assert preprocess(np.random.default_rng(42).normal(0, .03, 48000)) is None


def test_features_finite():
    t = np.arange(48000) / 16000
    audio = (.25 * np.sin(2 * np.pi * 170 * t)).astype(np.float32)
    prepared = preprocess(audio)
    assert prepared is not None
    f = extract(prepared[0])
    assert len(f['mfcc_mean']) == 13
    assert len(f['mel_mean_db']) == 40
    assert len(f['rms_envelope']) == 96
    assert 150 < np.median(f['pitch_hz']) < 190
    assert all(np.isfinite(v).all() for v in f.values())


def test_pitch_outliers_do_not_dominate_prosody():
    t = np.arange(48000) / 16000
    prepared = preprocess((.25 * np.sin(2 * np.pi * 170 * t)).astype(np.float32))
    assert prepared is not None
    assert extract(prepared[0])['pitch_cv'] < 0.02


# --------------------------------------------------------------------------------------------
# Detector
# --------------------------------------------------------------------------------------------

def test_detector_output_range_is_attainable():
    """DEF-1 / DR-003: an unreachable upper range makes a HIGH alert impossible one layer down.

    This locks in HeuristicClassifier's own numeric behaviour, so it targets that class directly
    rather than the process-wide `classifier` singleton -- since AASIST-L (backend/app/aasist.py),
    `classifier` may now be a different detector with an entirely different score_features/analyze
    implementation, and this regression test is specifically about the heuristic's log-axis
    flatness remap, not about whichever detector happens to be active.
    """
    heuristic = HeuristicClassifier()
    synthetic = heuristic.score_features(
        {'spectral_flatness': 1e-7, 'pitch_cv': 0., 'jitter': 0., 'shimmer': 0.})
    genuine = heuristic.score_features(
        {'spectral_flatness': 0.5, 'pitch_cv': .6, 'jitter': .3, 'shimmer': .5})
    assert synthetic['synthetic_score'] >= 0.9, 'upper range unreachable'
    assert genuine['synthetic_score'] <= 0.1, 'lower range unreachable'
    for name in ('spectral_score', 'prosody_score'):
        assert synthetic[name] >= 0.9 and genuine[name] <= 0.1, f'{name} range unreachable'


def test_phase0_detector_output_would_reach_high_in_the_gateway():
    """The Phase 0 exit gate, checked from the Python side.

    The fusion now lives in Go, so this asserts what this half is responsible for: the detector
    emits an output high enough that the Go fusion lands it in HIGH with margin. The matching
    assertion on the fusion itself is TestActiveSignalRenormalisationMakesHighReachable in
    gateway/internal/scoring. Keeping both is the point: DEF-1 was a defect that each layer
    could pass in isolation while the pair of them failed.
    """
    import soundfile as sf
    from generate_fixtures import generate

    generate()
    audio, sr = sf.read(str(ROOT / 'demo_audio' / 'fixture-steady.wav'), dtype='float32')
    result = sidecar.analyse_buffer(audio[:sr * 3].copy())
    assert result['scored']

    # The Go fusion with only the AI signal active: score = 100 * (0.5 + (p - 0.5) * confidence).
    p, confidence = result['p_synthetic'], result['confidence']
    projected = 100 * (0.5 + (p - 0.5) * confidence)
    assert projected >= 75, (
        f'detector-only projection was {projected:.2f} at p={p} conf={confidence}; '
        'HIGH reached with no margin will drift back to MEDIUM'
    )


# --------------------------------------------------------------------------------------------
# The audio boundary (CLAUDE.md invariant 1)
# --------------------------------------------------------------------------------------------

def test_buffer_cleared_after_analysis():
    audio = np.sin(np.arange(48000) * .06).astype(np.float32) * .2
    result = sidecar.analyse_buffer(audio)
    assert result['scored']
    assert not np.any(audio), 'the analysed buffer was not zeroed'


def test_buffer_is_cleared_even_when_analysis_rejects_the_window():
    audio = np.zeros(48000, dtype=np.float32)
    audio[:100] = 0.5
    result = sidecar.analyse_buffer(audio)
    assert not result['scored']
    assert not np.any(audio), 'a rejected window left its buffer resident'


def test_sidecar_never_returns_audio_samples():
    """The Go gateway must be structurally unable to obtain audio from this service.

    Enforced by inspecting every response body for a samples-shaped payload rather than by
    trusting the handler signatures, because the failure mode this guards against is somebody
    adding a debug field.
    """
    from generate_fixtures import generate
    generate()

    forbidden = {'samples', 'audio', 'pcm', 'waveform', 'frames', 'raw'}
    with TestClient(sidecar.app) as client:
        opened = client.post('/internal/stream/open', json={'filename': 'fixture-steady.wav'})
        assert opened.status_code == 200, opened.text
        stream_id = opened.json()['stream_id']
        try:
            for _ in range(4):
                window = client.post(f'/internal/stream/{stream_id}/next').json()
                if window.get('exhausted'):
                    break
                leaked = forbidden & set(window)
                assert not leaked, f'the sidecar returned audio-shaped keys: {leaked}'
                for value in window.values():
                    assert not (isinstance(value, list) and len(value) > 4000), \
                        'a response field is long enough to be a raw window'
        finally:
            client.post(f'/internal/stream/{stream_id}/close')


def test_stream_lifecycle_and_unknown_stream():
    from generate_fixtures import generate
    generate()

    with TestClient(sidecar.app) as client:
        assert client.post('/internal/stream/open', json={'filename': '../secret.wav'}).status_code == 400
        assert client.post('/internal/stream/missing/next').status_code == 404

        opened = client.post('/internal/stream/open', json={'filename': 'fixture-steady.wav'}).json()
        stream_id = opened['stream_id']
        assert opened['model_version'] == classifier.model_version
        assert opened['calibrator_version'] == classifier.calibrator_version

        first = client.post(f'/internal/stream/{stream_id}/next').json()
        assert first['exhausted'] is False
        assert first['chunk_index'] == 1

        assert client.post(f'/internal/stream/{stream_id}/close').json()['closed'] is True
        assert client.post(f'/internal/stream/{stream_id}/next').status_code == 404


def test_sidecar_health_reports_model_versions():
    """CLAUDE.md invariant 8: the gateway stamps every decision with versions it reads here."""
    with TestClient(sidecar.app) as client:
        health = client.get('/internal/health').json()
    assert health['model_version'] == classifier.model_version
    assert health['calibrator_version'] == classifier.calibrator_version
    assert health['raw_audio_persistence'] is False


def test_generated_tts_samples_are_marked_as_fixtures(tmp_path, monkeypatch):
    monkeypatch.setattr(sidecar, 'AUDIO_DIR', tmp_path)
    sample = tmp_path / 'tts-scenario.wav'
    sample.touch()
    with TestClient(sidecar.app) as client:
        item = next(row for row in client.get('/internal/audio').json()
                    if row['filename'] == sample.name)
        assert item['fixture'] is True


# --------------------------------------------------------------------------------------------
# Speaker verification and consent (docs/02 section 4, CLAUDE.md invariant 14)
# --------------------------------------------------------------------------------------------

def test_speaker_enrolment_and_verification_contract():
    t = np.arange(48000) / 16000
    synth_voice = (.25 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)

    res = verifier.enroll(
        identity_id="test_user_p01",
        display_name="P01 Verified Enrolment",
        audio=synth_voice.copy(),
        consent_token="SIGNED_CONSENT_TOKEN_12345",
    )
    assert res["status"] == "active"
    assert res["identity_id"] == "test_user_p01"

    verify_res = verifier.verify(synth_voice, "test_user_p01")
    assert verify_res["reference_available"] is True
    assert 0.0 <= verify_res["match_score"] <= 1.0

    # DR-012 / CLAUDE.md invariant 7: no reference means match_score is None, never a sentinel.
    no_ref = verifier.verify(synth_voice, "non_existent_identity")
    assert no_ref["reference_available"] is False
    assert no_ref["match_score"] is None

    with pytest.raises(PermissionError):
        verifier.enroll("unconsented_person", "No Consent", synth_voice.copy(), consent_token="")

    assert verifier.revoke("test_user_p01") is True
    assert verifier.verify(synth_voice, "test_user_p01")["reference_available"] is False


def test_enrolment_without_consent_is_refused_over_http():
    with TestClient(sidecar.app) as client:
        response = client.post('/internal/enrolments', json={
            'identity_id': 'unconsented_person',
            'display_name': 'No Consent',
            'consent_token': '',
            'samples': [0.1] * 8000,
        })
    # The empty token fails schema validation before it reaches the consent check; either way the
    # enrolment must not succeed.
    assert response.status_code in (403, 422), response.text


def test_consent_log_gates_every_human_voice_file():
    log = (ROOT / 'docs' / 'CONSENT_LOG.md').read_text(encoding='utf-8')
    consented = set(re.findall(r'^\|\s*(P-\d+)\s*\|', log, re.MULTILINE))
    revoked = set(re.findall(r'^\|\s*(P-\d+)\s*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|\s*yes\s*\|',
                             log, re.MULTILINE))
    offenders = []
    for wav in (ROOT / 'demo_audio').glob('*.wav'):
        if wav.name.startswith(('fixture-', 'tts-')):
            continue
        # The Asterisk adapter streams live call audio through a named pipe in this
        # directory (telephony_adapter/server.py). A FIFO holds no bytes, so it cannot
        # be stored unconsented audio -- and an aborted run leaves one behind, which
        # made this control fail on a file that was never a recording. Path.is_file()
        # is False for a FIFO, so this skips pipes while still catching any regular
        # file that happens to be named live-*.wav.
        if not wav.is_file():
            continue
        matched = re.match(r'consented-(P-\d+)-', wav.name)
        if not matched:
            offenders.append(f'{wav.name}: no consent-log reference in the filename')
        elif matched.group(1) not in consented:
            offenders.append(f'{wav.name}: {matched.group(1)} has no row in docs/CONSENT_LOG.md')
        elif matched.group(1) in revoked:
            offenders.append(f'{wav.name}: {matched.group(1)} revoked consent; delete this file')
    assert not offenders, 'Unconsented voice audio present: ' + '; '.join(offenders)


# --------------------------------------------------------------------------------------------
# Retention (T-4.7)
# --------------------------------------------------------------------------------------------

def test_audiosocket_ingest_forwards_only_derived_windows():
    class FakeGateway:
        def __init__(self):
            self.started = []
            self.windows = []
            self.closed = []

        async def start(self, call_id, metadata=None):
            self.started.append((call_id, metadata))

        async def push(self, call_id, window):
            self.windows.append((call_id, window))

        async def close(self, call_id):
            self.closed.append(call_id)

    seen_lengths = []

    def fake_analyser(audio, identity_id):
        seen_lengths.append(len(audio))
        audio.fill(0)
        return {
            'scored': True,
            'spectral_score': .8,
            'prosody_score': .7,
            'synthetic_score': .75,
            'p_synthetic': .75,
            'p_synthetic_raw': .75,
            'confidence': .9,
            'voiced_seconds': 3.0,
            'speech_ratio': 1.0,
            'latency_ms': 1.0,
            'features': {'rms_envelope': [.1, .2]},
            'model_version': 'test-detector@1',
            'calibrator_version': 'test-calibrator@1',
        }

    async def scenario():
        gateway = FakeGateway()
        ingest = AudioSocketIngest(fake_analyser, gateway=gateway, host='127.0.0.1', port=0)
        await ingest.start()
        try:
            _, writer = await asyncio.open_connection('127.0.0.1', ingest.bound_port)
            call_id = uuid4()
            pcm = np.full(8000 * 3, 1000, dtype='<i2').tobytes()
            writer.write(audiosocket_frame(FRAME_UUID, call_id.bytes))
            writer.write(audiosocket_frame(FRAME_AUDIO_8K, pcm))
            writer.write(audiosocket_frame(FRAME_HANGUP))
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            for _ in range(100):
                if gateway.closed:
                    break
                await asyncio.sleep(.01)
            assert gateway.started == [(str(call_id), None)]
            assert gateway.closed == [str(call_id)]
            assert len(gateway.windows) == 1
            forwarded = gateway.windows[0][1]
            assert forwarded['chunk_index'] == 1
            assert not ({'samples', 'audio', 'pcm', 'waveform'} & forwarded.keys())
            assert seen_lengths == [16000 * 3]
        finally:
            await ingest.close()

    asyncio.run(scenario())

def test_t4_7_retention_sweep_proves_no_raw_audio_on_disk():
    """No audio may exist outside demo_audio/, on either side of the Go/Python split."""
    from generate_fixtures import generate
    generate()

    with TestClient(sidecar.app) as client:
        opened = client.post('/internal/stream/open', json={'filename': 'fixture-steady.wav'}).json()
        stream_id = opened['stream_id']
        for _ in range(3):
            if client.post(f'/internal/stream/{stream_id}/next').json().get('exhausted'):
                break
        client.post(f'/internal/stream/{stream_id}/close')

    audio_extensions = ('*.wav', '*.flac', '*.pcm', '*.raw', '*.mp3', '*.ogg', '*.opus', '*.amr')
    swept = [ROOT / 'backend', ROOT / 'gateway']
    leaked = []
    for directory in swept:
        if not directory.exists():
            continue
        for pattern in audio_extensions:
            leaked.extend(str(p) for p in directory.rglob(pattern))

    assert not leaked, f'Raw audio leaked to disk outside demo_audio/: {leaked}'
