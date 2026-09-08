from app import prosody, session_drift, privacy_logger
import numpy as np

# Test prosody
p = prosody.ProsodyResult()
print("prosody_risk default:", p.prosody_risk)

# Test prosody analysis on a sine tone
sr = 16000
audio = np.sin(2 * np.pi * 200 * np.arange(sr * 3) / sr).astype(np.float32) * 0.3
result = prosody.analyse(audio, sr)
print("Prosody analysis OK:", result.analysis_available)
print("  pitch_mean_hz:", result.pitch_mean_hz)
print("  rhythm_regularity:", result.rhythm_regularity)
print("  prosody_risk:", result.prosody_risk)
print("  subscores:", result.subscores)

# Test drift
d = session_drift.DriftResult()
print("\ndrift_score default:", d.drift_score)
checker = session_drift.SessionDriftChecker()
emb = np.random.randn(24).astype(np.float32)
emb /= np.linalg.norm(emb)
checker.record_embedding("test_identity", emb.copy(), call_id="call_001", match_score=0.91)
dr = checker.check_drift("test_identity", emb)
print("drift_available:", dr.drift_available, "| reason:", dr.reason)

# Test privacy logger
cs = privacy_logger.compliance_statement()
print("\nCompliance statement inference_location:", cs["inference_location"])
print("edge_deployment:", cs["edge_deployment"])

print("\nAll PS modules OK!")
