import { AudioLines, Activity, Timer, Square, ShieldCheck, Gavel, UserCheck, FileText, CheckCircle2, AlertTriangle, ShieldX } from "lucide-react";
import { bandMeta } from "./Dashboard";

const titleCase = (value = "") =>
  value.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());

const decisionMeta = (decision = "WARN") => {
  switch (decision) {
    case "ALLOW":
      return { label: "ALLOW", icon: CheckCircle2, className: "band-low", desc: "Routine processing permitted" };
    case "WARN":
      return { label: "WARN", icon: AlertTriangle, className: "band-medium", desc: "Caution: proceed with agent verification" };
    case "STEP_UP":
      return { label: "STEP_UP", icon: ShieldCheck, className: "band-high", desc: "Multi-factor verification required" };
    case "ESCALATE":
      return { label: "ESCALATE", icon: Gavel, className: "band-high", desc: "Supervisor manual review required" };
    case "BLOCK":
      return { label: "BLOCK", icon: ShieldX, className: "band-high", desc: "Transaction hold / call freeze" };
    default:
      return { label: decision, icon: AlertTriangle, className: "band-unknown", desc: "Awaiting decision" };
  }
};

export default function CallDetail({ call, onStop, busy, onOpenOverride, onOpenSummary }) {
  const latest = call?.latest;
  const history = call?.history || [];
  const env = latest?.features?.rms_envelope || [];
  const meta = bandMeta(call?.band);
  const decision = call?.decision || latest?.decision?.decision || "WARN";
  const dMeta = decisionMeta(decision);
  const decisionDetails = call?.decision_details || latest?.decision;
  const verification = call?.speaker_verification;

  const points = history
    .map(
      (value, index) =>
        `${20 + (index * 660) / Math.max(history.length - 1, 1)},${150 - value * 1.2}`,
    )
    .join(" ");

  return (
    <section className="panel detail">
      {call?.degraded && (
        <div className="degraded-strip" role="status">
          Assessment is degraded ·{" "}
          {call.degraded_reasons?.map(titleCase).join(" · ")}
        </div>
      )}

      <div className="panel-heading">
        <h2>
          <AudioLines size={17} /> Signal analysis & Decision
        </h2>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <span
            className={`badge band-badge ${meta.className}`}
            aria-live="assertive"
            aria-label={`Assessment: ${meta.label}`}
          >
            <span aria-hidden="true">{meta.glyph}</span>
            {meta.label}
          </span>
          <span className={`badge ${dMeta.className}`} style={{ fontWeight: 600, padding: "4px 8px", borderRadius: "4px" }}>
            DECISION: {dMeta.label}
          </span>
        </div>
      </div>

      <div className="analysis-title">
        <div>
          <span className="eyebrow">SELECTED SESSION</span>
          <h3>{call?.label || "Waiting for a call"}</h3>
          <p>
            {call
              ? `${call.windows_scored} scored · ${call.unassessed_windows} not assessed · ${call.dropped_chunks} rejected by VAD`
              : "Start a simulation to see acoustic evidence."}
          </p>
          {latest?.classification && (
            <p className="classification">
              Latest heuristic label: <strong>{latest.classification}</strong>
            </p>
          )}
          {call?.error && (
            <p role="alert" className="danger">
              {call.error}
            </p>
          )}
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          {call?.status === "streaming" && (
            <button className="quiet" disabled={busy} onClick={onStop}>
              <Square size={13} /> Stop Stream
            </button>
          )}
          {call?.status !== "streaming" && call?.windows_scored > 0 && (
            <button className="quiet" onClick={() => onOpenSummary(call)}>
              <FileText size={13} /> View Summary
            </button>
          )}
        </div>
      </div>

      <div className="signal-layout">
        <div className="authenticity">
          <span className="eyebrow">SESSION RISK</span>
          <div className={`big-score ${meta.className}`} aria-live="off">
            {call?.risk_score == null ? "—" : Math.round(call.risk_score)}
            <span>/100</span>
          </div>
          <p>
            {call?.risk_score == null
              ? "Not assessed. More voiced audio or an active signal is required."
              : "Decision support score. Higher means more verification is required."}
          </p>
        </div>
        <div className="wave-box">
          <div className="wave-heading">
            <span>Audio energy envelope</span>
            <span>16 kHz · 3 s window / 1 s hop</span>
          </div>
          <svg
            viewBox="0 0 540 105"
            role="img"
            aria-label="Measured RMS audio energy envelope"
          >
            <line x1="0" y1="52" x2="540" y2="52" className="baseline" />
            {env.map((value, index) => (
              <line
                key={index}
                x1={index * 5.6 + 3}
                y1={52 - Math.min(47, value * 180)}
                x2={index * 5.6 + 3}
                y2={52 + Math.min(47, value * 180)}
                className="wave-bar"
              />
            ))}
          </svg>
          <div className="wave-footer">
            <span>
              <i
                className={`dot ${call?.status === "streaming" ? "" : "muted-dot"}`}
              />
              {latest ? "Latest derived envelope" : "No audio processed"}
            </span>
            <span>No raw audio retained (Zero Disk Policy)</span>
          </div>
        </div>
      </div>

      {/* Decision Service & Speaker Verification Row */}
      <div className="explainability-grid" style={{ marginBottom: "16px" }}>
        <div className="explainability-block" style={{ borderLeft: "3px solid var(--color-accent)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
            <h3 style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <Gavel size={15} /> Automated Decision Engine
            </h3>
            {call && (
              <button
                className="quiet"
                style={{ minHeight: "28px", padding: "4px 8px", fontSize: "12px" }}
                onClick={() => onOpenOverride(call)}
              >
                Override
              </button>
            )}
          </div>
          <p><strong>Verdict:</strong> {dMeta.label} ({dMeta.desc})</p>
          {decisionDetails?.reason_code && (
            <p style={{ fontSize: "12px", color: "var(--color-muted)", fontFamily: "var(--font-mono)" }}>
              Reason: {decisionDetails.reason_code}
            </p>
          )}
          {decisionDetails?.explanation_for_human && (
            <p style={{ fontSize: "13px", marginTop: "4px" }}>
              {decisionDetails.explanation_for_human}
            </p>
          )}
          {decisionDetails?.origin_signature && (
            <div style={{ fontSize: "11px", color: "var(--color-subtle)", fontFamily: "var(--font-mono)", marginTop: "6px", wordBreak: "break-all" }}>
              Proof: {decisionDetails.origin_signature.slice(0, 32)}...
            </div>
          )}
          {decisionDetails?.overridden && (
            <div style={{ background: "var(--color-danger-bg)", color: "var(--color-danger)", padding: "4px 8px", borderRadius: "4px", fontSize: "12px", marginTop: "6px" }}>
              ⚠️ Overridden by {decisionDetails.override_details?.supervisor_id} ({decisionDetails.override_details?.role})
            </div>
          )}
        </div>

        <div className="explainability-block">
          <h3>
            <UserCheck size={15} /> Speaker Verification
          </h3>
          {verification?.reference_available ? (
            <div>
              <p style={{ color: "var(--color-safe)", fontWeight: 500 }}>
                ✓ Enrolled Identity Match: <strong>{verification.enrolled_identity}</strong>
              </p>
              <div className="factor-row" style={{ marginTop: "4px" }}>
                <span>Acoustic Profile Match</span>
                <strong>{(verification.match_score * 100).toFixed(1)}%</strong>
              </div>
              <p style={{ fontSize: "12px", color: "var(--color-muted)" }}>
                Model: {verification.model_version} · Voiced: {verification.voiced_seconds_used}s
              </p>
            </div>
          ) : (
            <div>
              <p style={{ color: "var(--color-muted)" }}>
                ◌ No enrolled reference profile for this caller identity.
              </p>
              <p style={{ fontSize: "12px", color: "var(--color-subtle)" }}>
                Speaker verification gated inactive (DR-012 compliant).
              </p>
            </div>
          )}
        </div>
      </div>

      <div className="explainability-grid">
        <div className="explainability-block">
          <h3>
            <Activity size={15} /> Contributing factors
          </h3>
          {call?.contributing_factors?.length ? (
            call.contributing_factors.map((factor) => (
              <div className="factor-row" key={factor.factor}>
                <span>{titleCase(factor.factor)}</span>
                <strong>{factor.points.toFixed(2)} pts</strong>
              </div>
            ))
          ) : (
            <p>Factors will appear after a scored window.</p>
          )}
          {latest?.base_score != null && (
            <>
              <div className="factor-total"><span>Base score</span><strong>{latest.base_score.toFixed(2)} pts</strong></div>
              {latest.trust_discount > 0 && <div className="factor-row"><span>Verified trust discount</span><strong>−{latest.trust_discount} pts</strong></div>}
            </>
          )}
        </div>
        <div className="explainability-block">
          <h3>
            <ShieldCheck size={15} /> Availability and floors
          </h3>
          <p>
            Active:{" "}
            {latest?.active_signals?.map(titleCase).join(", ") || "None"}
          </p>
          <p>
            Inactive:{" "}
            {latest?.inactive_signals?.map(titleCase).join(", ") || "None"}
          </p>
          {call?.applied_floors?.length ? (
            call.applied_floors.map((floor) => (
              <div className="floor-row" key={floor.reason}>
                <span>{titleCase(floor.reason)}</span>
                <strong>minimum {floor.value}</strong>
              </div>
            ))
          ) : (
            <p>No policy floor applied.</p>
          )}
        </div>
      </div>

      <div className="chart-title">
        <h3>Session risk over time</h3>
        <span>
          <i className="legend" /> Session score{" "}
          <i className="legend threshold" /> Elevated 40 · High 70
        </span>
      </div>
      <div className="chart">
        <svg
          viewBox="0 0 710 178"
          role="img"
          aria-label="Session risk across assessed windows, elevated threshold 40 and high threshold 70"
        >
          {[0, 50, 100].map((value) => (
            <g key={value}>
              <line
                x1="20"
                y1={150 - value * 1.2}
                x2="680"
                y2={150 - value * 1.2}
                className="gridline"
              />
              <text x="685" y={154 - value * 1.2}>
                {value}
              </text>
            </g>
          ))}
          <line x1="20" y1="102" x2="680" y2="102" className="threshold-line" />
          <line
            x1="20"
            y1={66}
            x2="680"
            y2="66"
            className="threshold-line high"
          />
          {history.length > 0 && (
            <>
              <polyline points={points} className="risk-line" />
              {history.map((value, index) => (
                <circle
                  key={index}
                  cx={20 + (index * 660) / Math.max(history.length - 1, 1)}
                  cy={150 - value * 1.2}
                  r="3"
                  className="chart-point"
                />
              ))}
            </>
          )}
          <text x="20" y="173">
            {history.length ? "ASSESSED WINDOW 1" : "AWAITING ASSESSED WINDOWS"}
          </text>
          {history.length > 1 && (
            <text x="570" y="173">
              WINDOW {history.length}
            </text>
          )}
        </svg>
      </div>
      <div className="detail-footer">
        <span>
          <Timer size={14} /> Latency:{" "}
          <b>
            {call?.latency_ms == null
              ? "—"
              : `${call.latency_ms.toFixed(1)} ms`}
          </b>
        </span>
        <span>
          EWMA α 0.35 · peak decay 0.98 · policy {call?.policy_version || "—"}
        </span>
        <span>
          Detector {call?.model_versions?.detector || "—"} · calibrator{" "}
          {call?.model_versions?.calibrator || "—"}
        </span>
      </div>
    </section>
  );
}
