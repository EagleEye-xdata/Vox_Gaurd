import { useState } from "react";
import { Gavel, X, AlertTriangle, ShieldCheck } from "lucide-react";

export default function SupervisorOverrideModal({ call, isOpen, onClose, onOverrideSubmit, busy }) {
  const [decision, setDecision] = useState("ALLOW");
  const [reason, setReason] = useState("");
  const [supervisorId, setSupervisorId] = useState("sup_singh_42");
  const [role, setRole] = useState("SUPERVISOR");

  if (!isOpen || !call) return null;

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!reason.trim() || reason.length < 5) return;
    onOverrideSubmit(call.call_id, {
      decision,
      reason,
      supervisor_id: supervisorId,
      role,
    });
  };

  return (
    <div className="modal-overlay" style={{
      position: "fixed", inset: 0, backgroundColor: "rgba(0, 0, 0, 0.75)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
      backdropFilter: "blur(4px)"
    }}>
      <div className="panel" style={{ width: "90%", maxWidth: "520px", background: "var(--color-paper)", border: "1px solid var(--color-rule)", borderRadius: "8px", padding: "20px", boxShadow: "0 20px 40px rgba(0,0,0,0.5)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px", borderBottom: "1px solid var(--color-rule)", paddingBottom: "12px" }}>
          <h2 style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "16px" }}>
            <Gavel size={18} color="var(--color-accent)" /> Supervisor Decision Override
          </h2>
          <button className="quiet" onClick={onClose} style={{ minHeight: "32px", padding: "4px" }}>
            <X size={18} />
          </button>
        </div>

        <div style={{ background: "var(--color-paper-2)", padding: "10px", borderRadius: "6px", marginBottom: "16px", fontSize: "13px" }}>
          <div><strong>Session ID:</strong> {call.call_id}</div>
          <div><strong>Current Automated Decision:</strong> {call.decision || "WARN"} (Risk: {Math.round(call.risk_score || 0)}/100)</div>
          <div><strong>Policy:</strong> {call.policy_version}</div>
        </div>

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <div>
            <label style={{ display: "block", fontSize: "12px", color: "var(--color-muted)", marginBottom: "4px" }}>Target Override Decision:</label>
            <select
              value={decision}
              onChange={(e) => setDecision(e.target.value)}
              style={{ width: "100%", padding: "8px", background: "var(--color-paper-3)", color: "var(--color-ink)", border: "1px solid var(--color-rule)", borderRadius: "4px" }}
            >
              <option value="ALLOW">ALLOW (Release holds, permit transaction)</option>
              <option value="WARN">WARN (Standard agent caution)</option>
              <option value="STEP_UP">STEP_UP (Require mandatory MFA / OTP)</option>
              <option value="ESCALATE">ESCALATE (Escalate to senior fraud committee)</option>
              <option value="BLOCK">BLOCK (Immediate account & transaction freeze)</option>
            </select>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
            <div>
              <label style={{ display: "block", fontSize: "12px", color: "var(--color-muted)", marginBottom: "4px" }}>Supervisor ID:</label>
              <input
                type="text"
                value={supervisorId}
                onChange={(e) => setSupervisorId(e.target.value)}
                style={{ width: "100%", padding: "8px", background: "var(--color-paper-3)", color: "var(--color-ink)", border: "1px solid var(--color-rule)", borderRadius: "4px" }}
              />
            </div>
            <div>
              <label style={{ display: "block", fontSize: "12px", color: "var(--color-muted)", marginBottom: "4px" }}>Role:</label>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value)}
                style={{ width: "100%", padding: "8px", background: "var(--color-paper-3)", color: "var(--color-ink)", border: "1px solid var(--color-rule)", borderRadius: "4px" }}
              >
                <option value="SUPERVISOR">SUPERVISOR</option>
                <option value="FRAUD_ANALYST">FRAUD_ANALYST</option>
                <option value="INCIDENT_COMMANDER">INCIDENT_COMMANDER</option>
                <option value="ADMIN">ADMIN</option>
              </select>
            </div>
          </div>

          <div>
            <label style={{ display: "block", fontSize: "12px", color: "var(--color-muted)", marginBottom: "4px" }}>Mandatory Justification Rationale:</label>
            <textarea
              required
              minLength={5}
              placeholder="E.g. In-person branch verification completed or secondary biometric matched..."
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              style={{ width: "100%", padding: "8px", background: "var(--color-paper-3)", color: "var(--color-ink)", border: "1px solid var(--color-rule)", borderRadius: "4px", minHeight: "72px" }}
            />
          </div>

          <p style={{ fontSize: "11px", color: "var(--color-subtle)", margin: 0 }}>
            * This action produces a cryptographically signed WAL entry and is permanently logged in the audit ledger.
          </p>

          <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "12px" }}>
            <button type="button" className="quiet" onClick={onClose}>Cancel</button>
            <button type="submit" disabled={busy || reason.trim().length < 5} style={{ background: "var(--color-accent)", color: "var(--color-accent-ink)", fontWeight: 600 }}>
              Apply Signed Override
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
