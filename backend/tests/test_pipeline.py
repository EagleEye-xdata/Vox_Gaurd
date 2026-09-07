import numpy as np
import pytest
from app.preprocessing import preprocess
from app.features import extract
from app.risk_scoring import RollingRisk, fuse
from app.ledger import Ledger

def test_silence_and_noise_rejected():
    assert preprocess(np.zeros(48000)) is None
    assert preprocess(np.random.default_rng(42).normal(0, .03, 48000)) is None

def test_features_finite():
    t = np.arange(48000)/16000
    audio = (.25*np.sin(2*np.pi*170*t)).astype(np.float32)
    prepared = preprocess(audio)
    assert prepared is not None
    f = extract(prepared[0])
    assert len(f['mfcc_mean']) == 13
    assert len(f['mel_mean_db']) == 40
    assert len(f['rms_envelope']) == 96
    assert 150 < np.median(f['pitch_hz']) < 190
    assert all(np.isfinite(v).all() for v in f.values())

def test_rolling_damps_outlier_and_needs_sustained_evidence():
    r = RollingRisk()
    r.update(10)
    assert r.update(100)['risk_score'] == 37
    assert not r.update(10)['sustained_high_risk']
    r = RollingRisk()
    assert not r.update(90)['sustained_high_risk']
    assert not r.update(90)['sustained_high_risk']
    assert r.update(90)['sustained_high_risk']

def test_missing_speaker_and_context_bounds():
    assert fuse(0.5,0.5,None) == 50
    assert fuse(1,1,None,{'known_number':False,'transaction_size':200000,'hour':1}) == 100
    assert fuse(0,0,1) == 0

def test_tampering_and_unknown_hash(tmp_path):
    ledger = Ledger(tmp_path/'ledger.db')
    first = ledger.append({'risk_score':20})
    second = ledger.append({'risk_score':70})
    assert ledger.verify(second['hash'])['valid']
    assert not ledger.verify('unknown')['valid']
    with ledger.connect() as db:
        db.execute('UPDATE ledger SET payload=? WHERE id=1', ('{"risk_score":99}',))
    assert not ledger.verify(second['hash'])['valid']

def test_buffer_cleared_after_detection():
    from app.main import analyze
    audio = np.sin(np.arange(48000)*.06).astype(np.float32)*.2
    result = analyze(audio)
    assert result['scored']
    assert not np.any(audio)

def test_api_and_stream(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from generate_fixtures import generate
    generate()
    monkeypatch.setattr(main,'ledger',Ledger(tmp_path/'api.db'))
    main.calls.clear()
    main.alerts.clear()
    with TestClient(main.app) as client:
        assert client.get('/api/v1/health').status_code == 200
        assert client.post('/api/v1/stream/start',json={'filename':'../secret.wav'}).status_code == 400
        assert client.post('/api/v1/risk-score',json={'spectral_score':2,'prosody_score':.5}).status_code == 422
        assert client.post('/api/v1/ledger/log',json={'call_id':'x','event_type':'observation','risk_score':1,'audio':[1,2]}).status_code == 422
        result = client.post('/api/v1/detect',json={'samples':[0]*48000}).json()
        assert not result['scored']
        call_id = client.post('/api/v1/stream/start',json={'filename':'fixture-steady.wav','interval':.1,'context':{'known_number':False,'transaction_size':250000}}).json()['call_id']
        with client.websocket_connect(f'/ws/audio/{call_id}') as ws:
            while True:
                event = ws.receive_json()
                if event['type']=='complete':
                    break
        call = client.get(f'/api/v1/risk-score/{call_id}').json()
        assert call['status']=='completed'
        assert len(call['history'])==8
        assert call['latency_ms'] > 0
        alerts = client.get('/api/v1/alerts').json()
        assert len(alerts)==1 and alerts[0]['auto_block'] is False
        assert client.post(f"/api/v1/alerts/{alerts[0]['id']}/escalate",json={}).json()['status']=='escalated'
        assert client.get('/api/v1/ledger/verify/all').json()['valid']
        assert not any('features' in e or 'samples' in e for e in client.get('/api/v1/ledger').json())

def test_generated_tts_samples_are_marked_as_fixtures(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main, 'AUDIO_DIR', tmp_path)
    sample = tmp_path / 'tts-scenario.wav'
    sample.touch()
    with TestClient(main.app) as client:
        item = next(row for row in client.get('/api/v1/audio').json() if row['filename'] == sample.name)
        assert item['fixture'] is True
