import { useState } from "react";
import { MessageSquare, X, Scale } from "lucide-react";

export default function AppealModal({ alert, isOpen, onClose, onAppealSubmit, busy }) {
  const [reason, setReason] = useState("");
  const [appellantType, setAppellantType] = useState("CUSTOMER");
  const [contactInfo, setContactInfo] = useState("+91 98765 43210");

  if (!isOpen || !alert) return null;

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!reason.trim() || reason.length < 5) return;
    onAppealSubmit(alert.id, {
      reason,
      appellant_type: appellantType,
      contact_info: contactInfo,
    });
  };

  return (
    <div className="modal-overlay" style={{
      position: "fixed", inset: 0, backgroundColor: "rgba(0, 0, 0, 0.75)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
      backdropFilter: "blur(4px)"
    }}>
      <div className="panel" style={{ width: "90%", maxWidth: "500px", background: "var(--color-paper)", border: "1px solid var(--color-rule)", borderRadius: "8px", padding: "20px", boxShadow: "0 20px 40px rgba(0,0,0,0.5)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px", borderBottom: "1px solid var(--color-rule)", paddingBottom: "12px" }}>
          <h2 style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "16px" }}>
            <Scale size={18} color="var(--color-warning)" /> Lodge Customer Dispute / Appeal
          </h2>
          <button className="quiet" onClick={onClose} style={{ minHeight: "32px", padding: "4px" }}>
            <X size={18} />
          </button>
        </div>

        <div style={{ background: "var(--color-paper-2)", padding: "10px", borderRadius: "6px", marginBottom: "16px", fontSize: "13px" }}>
          <div><strong>Alert ID:</strong> {alert.id.slice(0, 16)}...</div>
          <div><strong>Associated Call:</strong> {alert.call_id?.slice(0, 8)} (Risk: {Math.round(alert.risk_score)}/100)</div>
          <div style={{ color: "var(--color-muted)", fontSize: "12px", marginTop: "4px" }}>
            Complies with statutory consumer protection and algorithmic fairness guidelines (docs/06 §7).
          </div>
        </div>

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <div>
            <label style={{ display: "block", fontSize: "12px", color: "var(--color-muted)", marginBottom: "4px" }}>Filing On Behalf Of:</label>
            <select
              value={appellantType}
              onChange={(e) => setAppellantType(e.target.value)}
              style={{ width: "100%", padding: "8px", background: "var(--color-paper-3)", color: "var(--color-ink)", border: "1px solid var(--color-rule)", borderRadius: "4px" }}
            >
              <option value="CUSTOMER">Account Holder / Caller (Direct Dispute)</option>
              <option value="AGENT">Call Center Agent (Advocacy Filing)</option>
              <option value="SUPERVISOR">Compliance Officer (Internal Review)</option>
            </select>
          </div>

          <div>
            <label style={{ display: "block", fontSize: "12px", color: "var(--color-muted)", marginBottom: "4px" }}>Contact Information for Resolution:</label>
            <input
              type="text"
              value={contactInfo}
              onChange={(e) => setContactInfo(e.target.value)}
              placeholder="Phone number, email or customer ID"
              style={{ width: "100%", padding: "8px", background: "var(--color-paper-3)", color: "var(--color-ink)", border: "1px solid var(--color-rule)", borderRadius: "4px" }}
            />
          </div>

          <div>
            <label style={{ display: "block", fontSize: "12px", color: "var(--color-muted)", marginBottom: "4px" }}>Dispute Statement & Context:</label>
            <textarea
              required
              minLength={5}
              placeholder="Explain why the acoustic risk or step-up decision was inaccurate (e.g. cellular line noise, speaker illness, non-standard dialect)..."
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              style={{ width: "100%", padding: "8px", background: "var(--color-paper-3)", color: "var(--color-ink)", border: "1px solid var(--color-rule)", borderRadius: "4px", minHeight: "80px" }}
            />
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "12px" }}>
            <button type="button" className="quiet" onClick={onClose}>Cancel</button>
            <button type="submit" disabled={busy || reason.trim().length < 5} style={{ background: "var(--color-warning)", color: "var(--color-accent-ink)", fontWeight: 600 }}>
              Submit Appeal Ticket
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
