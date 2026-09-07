import { useState } from "react";
import { UserCheck, X, Trash2, Plus, ShieldCheck } from "lucide-react";

export default function EnrolmentModal({ enrolments, isOpen, onClose, onEnroll, onRevoke, busy }) {
  const [identityId, setIdentityId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [consentToken, setConsentToken] = useState("CONSENT_RECORD_VERIFIED");
  const [isAdding, setIsAdding] = useState(false);

  if (!isOpen) return null;

  const handleEnrollSubmit = (e) => {
    e.preventDefault();
    if (!identityId.trim() || !displayName.trim()) return;
    
    // Generate 3 seconds synthetic voice samples for demo enrolment
    const sampleRate = 16000;
    const samples = Array.from({ length: sampleRate * 3 }, (_, i) => {
      const t = i / sampleRate;
      return 0.25 * Math.sin(2 * Math.PI * 180 * t) + 0.05 * Math.sin(2 * Math.PI * 360 * t);
    });

    onEnroll({
      identity_id: identityId,
      display_name: displayName,
      consent_token: consentToken,
      samples,
    });
    setIdentityId("");
    setDisplayName("");
    setIsAdding(false);
  };

  return (
    <div className="modal-overlay" style={{
      position: "fixed", inset: 0, backgroundColor: "rgba(0, 0, 0, 0.75)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
      backdropFilter: "blur(4px)"
    }}>
      <div className="panel" style={{ width: "90%", maxWidth: "560px", background: "var(--color-paper)", border: "1px solid var(--color-rule)", borderRadius: "8px", padding: "20px", boxShadow: "0 20px 40px rgba(0,0,0,0.5)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px", borderBottom: "1px solid var(--color-rule)", paddingBottom: "12px" }}>
          <h2 style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "16px" }}>
            <UserCheck size={18} color="var(--color-safe)" /> Consented Speaker Enrolments
          </h2>
          <button className="quiet" onClick={onClose} style={{ minHeight: "32px", padding: "4px" }}>
            <X size={18} />
          </button>
        </div>

        <p style={{ fontSize: "12px", color: "var(--color-muted)", marginBottom: "12px" }}>
          Active voice biometrics profiles. All enrolments enforce strict revocability and verified consent under Invariant 14.
        </p>

        {/* Existing enrolments list */}
        <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginBottom: "16px", maxHeight: "200px", overflowY: "auto" }}>
          {!enrolments?.length ? (
            <div style={{ textAlign: "center", padding: "16px", color: "var(--color-subtle)" }}>No profiles enrolled.</div>
          ) : (
            enrolments.map((profile) => (
              <div key={profile.identity_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", background: "var(--color-paper-2)", padding: "8px 12px", borderRadius: "6px", border: "1px solid var(--color-rule)" }}>
                <div>
                  <div style={{ fontWeight: 500, fontSize: "13px" }}>{profile.display_name}</div>
                  <div style={{ fontSize: "11px", color: "var(--color-muted)", fontFamily: "var(--font-mono)" }}>ID: {profile.identity_id} · Model: {profile.model_version}</div>
                </div>
                <button
                  className="quiet"
                  disabled={busy}
                  onClick={() => onRevoke(profile.identity_id)}
                  style={{ minHeight: "28px", padding: "4px 8px", color: "var(--color-danger)" }}
                  title="Revoke and permanently erase embedding"
                >
                  <Trash2 size={13} /> Revoke
                </button>
              </div>
            ))
          )}
        </div>

        {/* Add Enrolment Section */}
        {!isAdding ? (
          <button onClick={() => setIsAdding(true)} style={{ width: "100%", background: "var(--color-paper-3)", border: "1px dashed var(--color-rule)" }}>
            <Plus size={14} /> Enrol New Speaker Profile
          </button>
        ) : (
          <form onSubmit={handleEnrollSubmit} style={{ background: "var(--color-paper-2)", padding: "12px", borderRadius: "6px", display: "flex", flexDirection: "column", gap: "10px" }}>
            <div style={{ fontSize: "13px", fontWeight: 600 }}>New Voice Biometrics Registration</div>
            <div>
              <label style={{ display: "block", fontSize: "11px", color: "var(--color-muted)" }}>Identity ID (e.g. cust_ananya_331):</label>
              <input
                required
                type="text"
                value={identityId}
                onChange={(e) => setIdentityId(e.target.value)}
                style={{ width: "100%", padding: "6px", background: "var(--color-paper-3)", color: "var(--color-ink)", border: "1px solid var(--color-rule)", borderRadius: "4px" }}
              />
            </div>
            <div>
              <label style={{ display: "block", fontSize: "11px", color: "var(--color-muted)" }}>Display Name:</label>
              <input
                required
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                style={{ width: "100%", padding: "6px", background: "var(--color-paper-3)", color: "var(--color-ink)", border: "1px solid var(--color-rule)", borderRadius: "4px" }}
              />
            </div>
            <div>
              <label style={{ display: "block", fontSize: "11px", color: "var(--color-muted)" }}>Consent Verification Token:</label>
              <input
                type="text"
                value={consentToken}
                onChange={(e) => setConsentToken(e.target.value)}
                style={{ width: "100%", padding: "6px", background: "var(--color-paper-3)", color: "var(--color-ink)", border: "1px solid var(--color-rule)", borderRadius: "4px" }}
              />
            </div>
            <div style={{ display: "flex", gap: "6px", justifyContent: "flex-end", marginTop: "6px" }}>
              <button type="button" className="quiet" onClick={() => setIsAdding(false)}>Cancel</button>
              <button type="submit" disabled={busy || !identityId || !displayName} style={{ background: "var(--color-safe)", color: "var(--color-accent-ink)", fontWeight: 600 }}>
                Enrol & Extract Vector
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
