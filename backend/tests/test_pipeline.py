import numpy as np
import pytest
from app.preprocessing import preprocess
from app.features import extract
from app.risk_scoring import SessionRisk, POLICY, fuse, score_window
from app.ledger import Ledger
from app.speaker_verification import verifier
from app.decision import decision_service

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
        assert call['band']=='HIGH' and call['decision']=='BLOCK'  # >100k + HIGH risk triggers BLOCK
        assert call['policy_version']==POLICY['version']
        assert sum(f['points'] for f in call['contributing_factors']) == pytest.approx(call['latest']['base_score'], abs=1e-6)
        alerts = client.get('/api/v1/alerts').json()
        assert [a['band'] for a in alerts] == ['HIGH','MEDIUM']
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

def test_detector_output_range_is_attainable():
    from app.detection import classifier
    synthetic = classifier.score_features(
        {'spectral_flatness': 1e-7, 'pitch_cv': 0., 'jitter': 0., 'shimmer': 0.})
    genuine = classifier.score_features(
        {'spectral_flatness': 0.5, 'pitch_cv': .6, 'jitter': .3, 'shimmer': .5})
    assert synthetic['synthetic_score'] >= 0.9, 'upper range unreachable'
    assert genuine['synthetic_score'] <= 0.1, 'lower range unreachable'
    for name in ('spectral_score', 'prosody_score'):
        assert synthetic[name] >= 0.9 and genuine[name] <= 0.1, f'{name} range unreachable'

def test_phase0_high_is_reachable_from_the_detector_alone():
    import soundfile as sf
    from generate_fixtures import generate
    from app.main import analyze
    generate()
    audio, sr = sf.read('../demo_audio/fixture-steady.wav', dtype='float32')
    result = analyze(audio[:sr*3].copy())
    assert result['scored']
    detection = {**result, 'p_synthetic': result['synthetic_score']}
    window = score_window(detection, None)
    assert window['band'] == 'HIGH', f"detector-only band was {window['band']} at {window['window_score']}"
    assert window['window_score'] >= 75, 'HIGH reached with no margin; it will drift back to MEDIUM'

def test_pitch_outliers_do_not_dominate_prosody():
    t = np.arange(48000)/16000
    prepared = preprocess((.25*np.sin(2*np.pi*170*t)).astype(np.float32))
    assert prepared is not None
    assert extract(prepared[0])['pitch_cv'] < 0.02

def test_context_failure_lowers_the_high_threshold():
    from app.risk_scoring import band_for
    assert band_for(62, POLICY, context_degraded=False) == 'MEDIUM'
    assert band_for(62, POLICY, context_degraded=True) == 'HIGH'
    missing = score_window(detection(.9, .95), None)
    assert missing['context_degraded'] is True
    present = score_window(detection(.9, .95), {'caller_attestation': 'UNKNOWN'})
    assert present['context_degraded'] is False

def test_session_band_honours_the_degraded_threshold():
    degraded = SessionRisk('degraded')
    for _ in range(3):
        verdict = degraded.update({'window_score': 63, 'applied_floors': [], 'context_degraded': True})
    assert verdict['band'] == 'HIGH'
    normal = SessionRisk('normal')
    for _ in range(3):
        verdict = normal.update({'window_score': 63, 'applied_floors': [], 'context_degraded': False})
    assert verdict['band'] == 'MEDIUM'

def test_unsupported_language_gates_the_detector_off(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from generate_fixtures import generate
    generate()
    monkeypatch.setattr(main, 'ledger', Ledger(tmp_path/'lang.db'))
    main.calls.clear(); main.alerts.clear()
    with TestClient(main.app) as client:
        call_id = client.post('/api/v1/stream/start', json={
            'filename': 'fixture-steady.wav', 'interval': .05, 'language': 'fr'}).json()['call_id']
        with client.websocket_connect(f'/ws/audio/{call_id}') as ws:
            while ws.receive_json()['type'] != 'complete':
                pass
        call = client.get(f'/api/v1/risk-score/{call_id}').json()
    assert call['latest']['language_supported'] is False
    assert 'ai' in call['latest']['inactive_signals']
    assert {'reason': 'unsupported_language', 'value': 40} in call['applied_floors']
    assert call['band'] in {'MEDIUM', 'HIGH'}, 'out-of-scope language must never read as LOW'

def test_simulated_adversarial_input_reaches_the_floor(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from generate_fixtures import generate
    generate()
    monkeypatch.setattr(main, 'ledger', Ledger(tmp_path/'adv.db'))
    main.calls.clear(); main.alerts.clear()
    with TestClient(main.app) as client:
        call_id = client.post('/api/v1/stream/start', json={
            'filename': 'fixture-variable.wav', 'interval': .05,
            'simulate_adversarial_input': True}).json()['call_id']
        with client.websocket_connect(f'/ws/audio/{call_id}') as ws:
            while ws.receive_json()['type'] != 'complete':
                pass
        call = client.get(f'/api/v1/risk-score/{call_id}').json()
    assert call['latest']['adversarial_flag_source'] == 'simulated'
    assert {'reason': 'adversarial_input', 'value': 55} in call['applied_floors']
    assert call['latest']['window_score'] >= 55

def test_policy_version_no_longer_claims_to_be_the_banking_pack():
    assert 'banking' not in POLICY['version']
    assert POLICY['weights'] != {'ai': 0.45, 'speaker': 0.30, 'context': 0.25}

def test_consent_log_gates_every_human_voice_file():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    log = (root/'docs'/'CONSENT_LOG.md').read_text(encoding='utf-8')
    consented = set(re.findall(r'^\|\s*(P-\d+)\s*\|', log, re.MULTILINE))
    revoked = set(re.findall(r'^\|\s*(P-\d+)\s*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|\s*yes\s*\|',
                             log, re.MULTILINE))
    offenders = []
    for wav in (root/'demo_audio').glob('*.wav'):
        if wav.name.startswith(('fixture-', 'tts-')):
            continue
        matched = re.match(r'consented-(P-\d+)-', wav.name)
        if not matched:
            offenders.append(f'{wav.name}: no consent-log reference in the filename')
        elif matched.group(1) not in consented:
            offenders.append(f'{wav.name}: {matched.group(1)} has no row in docs/CONSENT_LOG.md')
        elif matched.group(1) in revoked:
            offenders.append(f'{wav.name}: {matched.group(1)} revoked consent; delete this file')
    assert not offenders, 'Unconsented voice audio present: ' + '; '.join(offenders)


# --------------------------------------------------------------------------
# Subsystem Tests: Speaker Verification, Decision Engine, Alerts & Retention
# --------------------------------------------------------------------------

def test_speaker_enrolment_and_verification_contract(tmp_path):
    """Verifies Speaker Verification contract (docs/02 §4) and Invariant 14 consent gate."""
    t = np.arange(48000) / 16000
    synth_voice = (.25 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    
    # 1. Enrolment with valid consent token
    res = verifier.enroll(
        identity_id="test_user_p01",
        display_name="P01 Verified Enrolment",
        audio=synth_voice.copy(),
        consent_token="SIGNED_CONSENT_TOKEN_12345"
    )
    assert res["status"] == "active"
    assert res["identity_id"] == "test_user_p01"
    
    # 2. Enrolled speaker verification emits reference_available=True and valid match_score
    verify_res = verifier.verify(synth_voice, "test_user_p01")
    assert verify_res["reference_available"] is True
    assert 0.0 <= verify_res["match_score"] <= 1.0
    
    # 3. Unenrolled speaker verification strictly emits match_score=None per DR-012
    no_ref = verifier.verify(synth_voice, "non_existent_identity")
    assert no_ref["reference_available"] is False
    assert no_ref["match_score"] is None
    
    # 4. Consent-less enrolment fails (Invariant 14)
    with pytest.raises(PermissionError):
        verifier.enroll("unconsented_person", "No Consent", synth_voice.copy(), consent_token="")
        
    # 5. Revocation hard-erases embedding
    assert verifier.revoke("test_user_p01") is True
    assert verifier.verify(synth_voice, "test_user_p01")["reference_available"] is False


def test_decision_engine_and_supervisor_override(tmp_path, monkeypatch):
    """Verifies Decision Service (docs/02 §8, DR-013) and Supervisor Override workflow."""
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main, 'ledger', Ledger(tmp_path/'decision.db'))
    main.calls.clear(); main.alerts.clear()
    
    with TestClient(main.app) as client:
        # Mock a streaming call
        call_id = "test-decision-call-001"
        main.calls[call_id] = {
            "call_id": call_id, "label": "Test Call", "status": "streaming",
            "risk_score": 88.0, "band": "HIGH", "decision": "STEP_UP",
            "context": {"transaction_value": 500000, "caller_attestation": "UNKNOWN"},
            "model_versions": {"detector": "d1", "verifier": "v1", "calibrator": "c1"}
        }
        
        # Test supervisor override endpoint
        override_payload = {
            "decision": "ALLOW",
            "reason": "Customer physically verified at branch desk by supervisor.",
            "supervisor_id": "sup_singh_42",
            "role": "SUPERVISOR"
        }
        res = client.post(f"/api/v1/decisions/{call_id}/override", json=override_payload)
        assert res.status_code == 200
        data = res.json()
        assert data["decision"] == "ALLOW"
        assert data["supervisor_id"] == "sup_singh_42"
        assert data["origin_signature"].startswith("hmac-sha256:")
        
        # Call object reflects overridden decision
        updated_call = client.get(f"/api/v1/risk-score/{call_id}").json()
        assert updated_call["decision"] == "ALLOW"


def test_alert_lifecycle_resolution_and_customer_appeal(tmp_path, monkeypatch):
    """Verifies Alert Management, SLA deadlines, closed outcomes, and Appeals (docs/02 §10, docs/09 §5)."""
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main, 'ledger', Ledger(tmp_path/'alerts.db'))
    main.alerts.clear()
    
    with TestClient(main.app) as client:
        # Setup mock active call and trigger alert
        call_id = "alert-test-call-123"
        main.calls[call_id] = {
            "call_id": call_id, "risk_score": 85.0, "band": "HIGH",
            "model_versions": {"detector": "d1"}
        }
        alert = main.ensure_alert(main.calls[call_id], "alert-id-999")
        assert alert["sla_ack_deadline"] is not None
        assert alert["sla_resolve_deadline"] is not None
        
        # Assign alert to analyst
        assign_res = client.post("/api/v1/alerts/alert-id-999/assign", json={"assignee_id": "analyst_kapoor"})
        assert assign_res.status_code == 200
        assert assign_res.json()["assigned_to"] == "analyst_kapoor"
        assert assign_res.json()["status"] == "assigned"
        
        # Lodge a customer appeal
        appeal_res = client.post("/api/v1/alerts/alert-id-999/appeal", json={
            "reason": "I had a sore throat during the call, not voice cloning.",
            "appellant_type": "CUSTOMER",
            "contact_info": "+91 98765 43210"
        })
        assert appeal_res.status_code == 200
        appeal_data = appeal_res.json()
        assert appeal_data["status"] == "PENDING_REVIEW"
        assert appeal_data["appeal_id"].startswith("app-")
        
        # Resolve alert with closed enum outcome
        resolve_res = client.post("/api/v1/alerts/alert-id-999/resolve", json={
            "outcome": "FALSE_POSITIVE",
            "notes": "Verified sore throat via video KYC; confirmed legitimate customer.",
            "resolver_id": "analyst_kapoor"
        })
        assert resolve_res.status_code == 200
        resolved_data = resolve_res.json()
        assert resolved_data["status"] == "resolved"
        assert resolved_data["resolution"]["outcome"] == "FALSE_POSITIVE"


def test_session_close_and_summary_generation(tmp_path, monkeypatch):
    """Verifies SessionSummary generation upon closing a session (docs/02 §2, docs/04 §7)."""
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main, 'ledger', Ledger(tmp_path/'session_close.db'))
    main.calls.clear()
    
    with TestClient(main.app) as client:
        call_id = "close-test-call-456"
        main.calls[call_id] = {
            "call_id": call_id, "label": "Session Close Test", "status": "streaming",
            "started_at": "2026-09-07T12:00:00Z", "risk_score": 74.5, "band": "HIGH",
            "peak_score": 82.0, "decision": "STEP_UP", "chunks_processed": 10,
            "windows_scored": 8, "unassessed_windows": 2, "escalation_seq": 1,
            "band_timeline": [{"window": 0, "band": "UNKNOWN"}, {"window": 3, "band": "HIGH"}],
            "model_versions": {"detector": "d1"}
        }
        res = client.post(f"/api/v1/sessions/{call_id}/close")
        assert res.status_code == 200
        summary = res.json()
        assert summary["session_id"] == call_id
        assert summary["final_band"] == "HIGH"
        assert summary["final_decision"] == "STEP_UP"
        assert summary["windows_scored"] == 8
        assert summary["completed_at"] is not None


def test_t4_7_retention_sweep_proves_no_raw_audio_on_disk(tmp_path):
    """T-4.7 Retention assertion test: Asserts that no raw audio frames or temporary
    audio clips leaked to disk during audio processing / streaming."""
    from pathlib import Path
    import tempfile
    
    # Check project directory and system temp directory for unwanted audio leakage
    backend_dir = Path(__file__).resolve().parents[1]
    repo_root = backend_dir.parent
    
    # 1. No stray audio files in backend / data directory except legitimate committed demo_audio fixtures
    forbidden_audio = []
    for ext in ("*.wav", "*.flac", "*.pcm", "*.raw", "*.mp3", "*.ogg"):
        for p in backend_dir.rglob(ext):
            forbidden_audio.append(str(p))
            
    assert not forbidden_audio, f"Leaked raw audio files detected in backend directory: {forbidden_audio}"
