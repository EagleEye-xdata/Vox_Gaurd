import { FileText, X, CheckCircle, ShieldAlert, Award, Clock, Activity } from "lucide-react";
import { bandMeta } from "./Dashboard";

export default function SessionSummaryModal({ call, isOpen, onClose }) {
  if (!isOpen || !call) return null;

  const summary = call.session_summary || {
    session_id: call.call_id,
    label: call.label,
    started_at: call.started_at,
    completed_at: new Date().toISOString(),
    chunks_processed: call.chunks_processed || 0,
    windows_scored: call.windows_scored || 0,
    peak_score: call.peak_score || call.risk_score || 0,
    final_session_score: call.risk_score || 0,
    final_band: call.band || "UNKNOWN",
    final_decision: call.decision || "ALLOW",
    escalation_count: call.escalation_seq || 0,
    policy_version: call.policy_version,
    model_versions: call.model_versions || {},
    band_timeline: call.band_timeline || [],
  };

  const meta = bandMeta(summary.final_band);

  return (
    <div className="modal-overlay" style={{
      position: "fixed", inset: 0, backgroundColor: "rgba(0, 0, 0, 0.8)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
      backdropFilter: "blur(6px)"
    }}>
      <div className="panel" style={{ width: "90%", maxWidth: "600px", background: "var(--color-paper)", border: "1px solid var(--color-rule)", borderRadius: "10px", padding: "24px", maxHeight: "90vh", overflowY: "auto", boxShadow: "0 24px 48px rgba(0,0,0,0.6)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px", borderBottom: "1px solid var(--color-rule)", paddingBottom: "12px" }}>
          <h2 style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "17px" }}>
            <FileText size={20} color="var(--color-accent)" /> Session Forensic Post-Mortem
          </h2>
          <button className="quiet" onClick={onClose} style={{ minHeight: "32px", padding: "4px" }}>
            <X size={18} />
          </button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "16px" }}>
          <div style={{ background: "var(--color-paper-2)", padding: "12px", borderRadius: "6px" }}>
            <span className="eyebrow">FINAL VERDICT</span>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "4px" }}>
              <span className={`badge band-badge ${meta.className}`}>
                {meta.glyph} {meta.label}
              </span>
              <strong style={{ fontSize: "16px" }}>Decision: {summary.final_decision}</strong>
            </div>
            <div style={{ fontSize: "12px", color: "var(--color-muted)", marginTop: "6px" }}>
              Peak Risk: {Math.round(summary.peak_score)}/100 · Final: {Math.round(summary.final_session_score)}/100
            </div>
          </div>

          <div style={{ background: "var(--color-paper-2)", padding: "12px", borderRadius: "6px" }}>
            <span className="eyebrow">SESSION METRICS</span>
            <div style={{ fontSize: "13px", marginTop: "4px" }}>
              <div><strong>Scored Windows:</strong> {summary.windows_scored}</div>
              <div><strong>Total Chunks:</strong> {summary.chunks_processed}</div>
              <div><strong>Escalations:</strong> {summary.escalation_count}</div>
            </div>
          </div>
        </div>

        {/* Timeline Progression */}
        <div style={{ marginBottom: "16px" }}>
          <h3 style={{ fontSize: "14px", marginBottom: "8px", display: "flex", alignItems: "center", gap: "6px" }}>
            <Activity size={15} /> Risk Band Timeline Progression
          </h3>
          <div style={{ background: "var(--color-paper-3)", padding: "10px", borderRadius: "6px", display: "flex", flexWrap: "wrap", gap: "8px" }}>
            {summary.band_timeline?.map((step, index) => {
              const sMeta = bandMeta(step.band);
              return (
                <div key={index} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px" }}>
                  <span className={`badge ${sMeta.className}`} style={{ padding: "2px 6px" }}>
                    W{step.window}: {sMeta.label}
                  </span>
                  {index < summary.band_timeline.length - 1 && <span style={{ color: "var(--color-subtle)" }}>→</span>}
                </div>
              );
            })}
          </div>
        </div>

        {/* Forensic Metadata */}
        <div style={{ background: "var(--color-paper-2)", padding: "12px", borderRadius: "6px", fontSize: "12px", color: "var(--color-muted)" }}>
          <div><strong>Session ID:</strong> <span style={{ fontFamily: "var(--font-mono)" }}>{summary.session_id}</span></div>
          <div><strong>Policy Pack:</strong> {summary.policy_version}</div>
          <div><strong>Detector:</strong> {summary.model_versions?.detector || "—"}</div>
          <div><strong>Calibrator:</strong> {summary.model_versions?.calibrator || "—"}</div>
          <div><strong>Completed At:</strong> {summary.completed_at}</div>
          <div><strong>Data Retention:</strong> Zero raw audio stored to disk (Ephemeral in-memory ring-buffer).</div>
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "16px" }}>
          <button onClick={onClose} style={{ background: "var(--color-accent)", color: "var(--color-accent-ink)", fontWeight: 600 }}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
