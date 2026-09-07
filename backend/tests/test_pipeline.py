import numpy as np
import pytest
from app.preprocessing import preprocess
from app.features import extract
from app.risk_scoring import SessionRisk, POLICY, fuse, score_window
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

def window(value, floors=None):
    return {'window_score': value, 'applied_floors': floors or []}

def test_session_uses_peak_memory_and_two_stage_hysteresis():
    risk = SessionRisk('session')
    assert risk.update(window(90))['band'] == 'UNKNOWN'
    medium = risk.update(window(90))
    assert medium['band'] == 'MEDIUM' and medium['band_changed']
    high = risk.update(window(90))
    assert high['band'] == 'HIGH' and high['band_changed']
    assert risk.update(window(5))['session_score'] >= 80
    assert risk.escalation_seq == 2

def detection(probability=.5, confidence=1, voiced=3, **extra):
    return {'p_synthetic': probability, 'confidence': confidence,
            'language_supported': True, 'voiced_seconds': voiced, **extra}

def test_active_signal_renormalisation_and_phase0_high_reachability():
    result = score_window(detection(.95, .95), None)
    assert result['band'] == 'HIGH'
    assert result['active_signals'] == ['ai']
    assert result['window_score'] == pytest.approx(92.75)

def test_floor_unknown_and_explainability_invariants():
    adversarial = score_window(detection(0, 1, adversarial_flag=True), None)
    assert adversarial['window_score'] >= 55
    assert adversarial['band'] == 'MEDIUM'
    assert adversarial['applied_floors'] == [
        {'reason':'context_unavailable','value':40},
        {'reason':'adversarial_input','value':55},
    ]
    assert sum(f['points'] for f in adversarial['contributing_factors']) == adversarial['base_score']
    assert score_window(detection(.9, 1, voiced=1), None)['band'] == 'UNKNOWN'
    assert score_window(None, None)['band'] == 'UNKNOWN'

def test_detector_failure_and_unsupported_language_fail_safe():
    context = {'caller_attestation':'UNKNOWN','transaction_value':250000,
               'beneficiary_is_new':True,'request_urgency':'high'}
    failed = score_window(None, context)
    assert failed['band'] in {'MEDIUM', 'HIGH'}
    assert failed['window_score'] >= 40
    assert failed['degraded']
    unsupported = score_window(detection(.1, language_supported=False), context)
    assert unsupported['band'] in {'MEDIUM', 'HIGH'}
    assert unsupported['window_score'] >= 40

def test_alert_storm_is_bounded_and_peak_resists_dilution():
    risk = SessionRisk('storm')
    keys = [risk.update(window(95))['alert_key'] for _ in range(600)]
    assert len([key for key in keys if key]) == 2
    diluted = SessionRisk('dilution')
    for _ in range(30):
        diluted.update(window(10))
    for _ in range(4):
        result = diluted.update(window(90))
    assert result['band'] in {'MEDIUM', 'HIGH'}

def test_no_reference_is_not_failure_and_match_gate_is_strict():
    no_reference = {'reference_available': False, 'match_score': None}
    result = score_window(detection(.5), None, no_reference)
    assert 'verifier_failed' not in result['degraded_reasons']
    with pytest.raises(ValueError):
        score_window(detection(.5), None, {'reference_available': False, 'match_score': .5})

def test_adversary_controlled_context_cannot_buy_low_risk():
    base = {'caller_attestation':'KNOWN_UNVERIFIED','attestation_source':'CALLER_ID_ONLY',
            'transaction_value':800000,'beneficiary_is_new':True,'confirmed_fraud_flags_90d':0,
            'urgency_source':'CALLER_CLAIMED'}
    low_claim = score_window(detection(.5), {**base,'request_urgency':'low'})['window_score']
    normal_claim = score_window(detection(.5), {**base,'request_urgency':'normal'})['window_score']
    assert low_claim == normal_claim

def test_trust_discount_requires_every_independent_gate():
    trusted = {'caller_attestation':'VERIFIED','attestation_source':'STIR_SHAKEN_A',
               'transaction_value':1000,'request_urgency':'normal'}
    verified = {'reference_available':True,'match_score':.9,'voiced_seconds_used':3}
    eligible = score_window(detection(.5), trusted, verified)
    assert eligible['trust_discount'] == 10
    caller_id_only = {**trusted, 'caller_attestation':'KNOWN_UNVERIFIED', 'attestation_source':'CALLER_ID_ONLY'}
    assert score_window(detection(.5), caller_id_only, verified)['trust_discount'] == 0
    assert score_window(detection(.5, adversarial_flag=True), trusted, verified)['trust_discount'] == 0

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
        call_id = client.post('/api/v1/stream/start',json={'filename':'fixture-steady.wav','interval':.05,'context':{
            'caller_attestation':'UNKNOWN','attestation_source':'NONE','transaction_value':250000,
            'beneficiary_is_new':True,'request_urgency':'high','urgency_source':'AGENT_ASSERTED'}}).json()['call_id']
        with client.websocket_connect(f'/ws/audio/{call_id}') as ws:
            while True:
                event = ws.receive_json()
                if event['type']=='complete':
                    break
        call = client.get(f'/api/v1/risk-score/{call_id}').json()
        assert call['status']=='completed'
        assert len(call['history'])==22
        assert call['latency_ms'] > 0
        assert call['band']=='HIGH' and call['decision']=='STEP_UP'
        assert call['policy_version']==POLICY['version']
        assert sum(f['points'] for f in call['contributing_factors']) == pytest.approx(call['latest']['base_score'], abs=1e-6)
        alerts = client.get('/api/v1/alerts').json()
        assert [a['band'] for a in alerts] == ['HIGH','MEDIUM']
        assert all(a['auto_block'] is False for a in alerts)
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
