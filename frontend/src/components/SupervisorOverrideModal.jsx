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
    <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50 animate-in fade-in duration-200">
      <div className="w-[90%] max-w-[520px] bg-[var(--color-surface-elevated)] border border-[var(--color-border-subtle)] rounded-2xl p-6 shadow-2xl flex flex-col font-sans text-[var(--color-text-primary)]">

        <div className="flex justify-between items-center mb-5 pb-4 border-b border-[var(--color-border-subtle)]">
          <h2 className="flex items-center gap-2 text-base font-semibold">
            <Gavel className="w-5 h-5 text-[var(--color-accent-indigo)]" /> Supervisor Decision Override
          </h2>
          <button onClick={onClose} className="p-1 rounded hover:bg-[var(--color-hover-overlay)] transition-colors text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="bg-[var(--color-surface-layer)] p-3 rounded-lg mb-5 text-sm border border-[var(--color-border-subtle)] flex flex-col gap-1">
          <div><strong className="text-[var(--color-text-secondary)] font-medium">Session ID:</strong> <span className="font-mono text-xs">{call.call_id}</span></div>
          <div><strong className="text-[var(--color-text-secondary)] font-medium">Current Automated Decision:</strong> <span className="text-[var(--color-threat-elevated)] font-medium">{call.decision || "WARN"}</span> (Risk: {Math.round(call.risk_score || 0)}/100)</div>
          <div><strong className="text-[var(--color-text-secondary)] font-medium">Policy:</strong> {call.policy_version}</div>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-[var(--color-text-tertiary)]">Target Override Decision:</label>
            <select
              value={decision}
              onChange={(e) => setDecision(e.target.value)}
              className="w-full p-2.5 bg-[var(--color-surface-base)] text-[var(--color-text-primary)] border border-[var(--color-border-subtle)] rounded-lg text-sm focus:outline-none focus:border-[var(--color-focus-ring)] transition-colors appearance-none"
            >
              <option value="ALLOW">ALLOW (Release holds, permit transaction)</option>
              <option value="WARN">WARN (Standard agent caution)</option>
              <option value="STEP_UP">STEP_UP (Require mandatory MFA / OTP)</option>
              <option value="ESCALATE">ESCALATE (Escalate to senior fraud committee)</option>
              <option value="BLOCK">BLOCK (Immediate account & transaction freeze)</option>
            </select>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-[var(--color-text-tertiary)]">Supervisor ID:</label>
              <input
                type="text"
                value={supervisorId}
                onChange={(e) => setSupervisorId(e.target.value)}
                className="w-full p-2.5 bg-[var(--color-surface-base)] text-[var(--color-text-primary)] border border-[var(--color-border-subtle)] rounded-lg text-sm focus:outline-none focus:border-[var(--color-focus-ring)] transition-colors"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-[var(--color-text-tertiary)]">Role:</label>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className="w-full p-2.5 bg-[var(--color-surface-base)] text-[var(--color-text-primary)] border border-[var(--color-border-subtle)] rounded-lg text-sm focus:outline-none focus:border-[var(--color-focus-ring)] transition-colors appearance-none"
              >
                <option value="SUPERVISOR">SUPERVISOR</option>
                <option value="FRAUD_ANALYST">FRAUD_ANALYST</option>
                <option value="INCIDENT_COMMANDER">INCIDENT_COMMANDER</option>
                <option value="ADMIN">ADMIN</option>
              </select>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-[var(--color-text-tertiary)]">Mandatory Justification Rationale:</label>
            <textarea
              required
              minLength={5}
              placeholder="E.g. In-person branch verification completed or secondary biometric matched..."
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className="w-full p-3 bg-[var(--color-surface-base)] text-[var(--color-text-primary)] border border-[var(--color-border-subtle)] rounded-lg text-sm focus:outline-none focus:border-[var(--color-focus-ring)] transition-colors min-h-[80px] resize-none"
            />
          </div>

          <div className="flex items-start gap-2 mt-1">
            <ShieldCheck className="w-4 h-4 text-[var(--color-text-tertiary)] shrink-0 mt-0.5" />
            <p className="text-[11px] text-[var(--color-text-tertiary)] leading-snug">
              This action produces a cryptographically signed WAL entry and is permanently logged in the immutable audit ledger.
            </p>
          </div>

          <div className="flex justify-end gap-3 mt-4 pt-4 border-t border-[var(--color-border-subtle)]">
            <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg text-sm font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-hover-overlay)] transition-colors">
              Cancel
            </button>
            <button type="submit" disabled={busy || reason.trim().length < 5} className="px-5 py-2 rounded-lg text-sm font-medium bg-[var(--color-accent-indigo)] text-white hover:bg-[#4f46e5] disabled:opacity-50 disabled:cursor-not-allowed transition-colors border border-transparent focus:outline-none focus:border-[var(--color-focus-ring)] focus:ring-2 focus:ring-[var(--color-focus-ring)]">
              {busy ? "Applying..." : "Apply Signed Override"}
            </button>
          </div>

        </form>
      </div>
    </div>
  );
}
