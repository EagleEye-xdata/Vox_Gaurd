import { useState } from "react";
import { ShieldAlert, ArrowUpRight, Check, UserCheck, MessageSquare, AlertOctagon, CheckCircle, HelpCircle } from "lucide-react";
import { bandMeta } from "./Dashboard";

export default function AlertPopup({ alerts, onEscalate, onAssign, onResolve, onOpenAppeal, busy }) {
  const [resolvingId, setResolvingId] = useState(null);
  const [resolutionOutcome, setResolutionOutcome] = useState("CONFIRMED_FRAUD");
  const [resolutionNotes, setResolutionNotes] = useState("");

  const handleResolveSubmit = (alertId) => {
    if (!resolutionNotes.trim()) return;
    onResolve(alertId, resolutionOutcome, resolutionNotes);
    setResolvingId(null);
    setResolutionNotes("");
  };

  return (
    <section className="panel alert-panel">
      <div className="panel-heading">
        <h2>
          <ShieldAlert size={17} /> Security & Alert Ops
        </h2>
        <span className="count">{alerts.length}</span>
      </div>

      {!alerts.length ? (
        <div className="empty small">
          <Check size={23} />
          <h3>No verification requests</h3>
          <p>
            Elevated or high sessions will create a deduplicated alert here.
          </p>
        </div>
      ) : (
        alerts.map((alert) => {
          const meta = bandMeta(alert.band);
          const isResolved = alert.status === "resolved";
          const isResolving = resolvingId === alert.id;

          return (
            <article
              className="alert-item"
              key={alert.id}
              style={{
                opacity: isResolved ? 0.7 : 1,
                borderLeft: isResolved ? "3px solid var(--color-safe)" : undefined,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span className={`eyebrow ${meta.className}`}>
                  {meta.glyph} {meta.label.toUpperCase()} ASSESSMENT
                </span>
                <span style={{ fontSize: "11px", color: "var(--color-subtle)", fontFamily: "var(--font-mono)" }}>
                  Status: {alert.status?.toUpperCase()}
                </span>
              </div>

              <h3>{alert.message}</h3>
              <small>
                Call {alert.call_id?.slice(0, 8)} · Risk {Math.round(alert.risk_score)}/100
                {alert.assigned_to && ` · Assigned: ${alert.assigned_to}`}
              </small>

              {/* SLA Timers */}
              <div style={{ display: "flex", gap: "12px", fontSize: "11px", color: "var(--color-muted)", margin: "4px 0" }}>
                <span>Ack SLA: {new Date(alert.sla_ack_deadline).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                <span>Resolve SLA: {new Date(alert.sla_resolve_deadline).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
              </div>

              {/* Resolution Summary if already resolved */}
              {isResolved && alert.resolution && (
                <div style={{ background: "var(--color-paper-2)", padding: "6px 8px", borderRadius: "4px", fontSize: "12px", marginTop: "4px" }}>
                  <strong>Outcome:</strong> {alert.resolution.outcome} <br />
                  <strong>Notes:</strong> {alert.resolution.notes} <br />
                  <small style={{ color: "var(--color-subtle)" }}>By {alert.resolution.resolver_id} at {new Date(alert.resolution.resolved_at).toLocaleTimeString()}</small>
                </div>
              )}

              {/* Appeals logged */}
              {alert.appeals?.length > 0 && (
                <div style={{ background: "var(--color-paper-3)", padding: "4px 8px", borderRadius: "4px", fontSize: "11px", color: "var(--color-warning)", marginTop: "4px" }}>
                  ⚖️ {alert.appeals.length} Customer Dispute(s) Filed ({alert.appeals[0].status})
                </div>
              )}

              {/* Inline Resolution Box */}
              {!isResolved && isResolving && (
                <div style={{ background: "var(--color-paper-2)", padding: "8px", borderRadius: "6px", marginTop: "8px" }}>
                  <label style={{ fontSize: "12px", display: "block", marginBottom: "4px" }}>Investigation Outcome:</label>
                  <select
                    value={resolutionOutcome}
                    onChange={(e) => setResolutionOutcome(e.target.value)}
                    style={{ width: "100%", padding: "6px", background: "var(--color-paper-3)", color: "var(--color-ink)", border: "1px solid var(--color-rule)", borderRadius: "4px", marginBottom: "6px" }}
                  >
                    <option value="CONFIRMED_FRAUD">CONFIRMED FRAUD (Synthetic voice / clone attack)</option>
                    <option value="FALSE_POSITIVE">FALSE POSITIVE (Legitimate voice validated)</option>
                    <option value="INCONCLUSIVE">INCONCLUSIVE (Insufficient caller audio)</option>
                  </select>
                  <textarea
                    placeholder="Document mandatory investigation notes..."
                    value={resolutionNotes}
                    onChange={(e) => setResolutionNotes(e.target.value)}
                    style={{ width: "100%", padding: "6px", background: "var(--color-paper-3)", color: "var(--color-ink)", border: "1px solid var(--color-rule)", borderRadius: "4px", minHeight: "48px", marginBottom: "6px" }}
                  />
                  <div style={{ display: "flex", gap: "6px", justifyContent: "flex-end" }}>
                    <button className="quiet" onClick={() => setResolvingId(null)} style={{ minHeight: "32px", padding: "4px 8px", fontSize: "12px" }}>Cancel</button>
                    <button onClick={() => handleResolveSubmit(alert.id)} disabled={!resolutionNotes.trim() || busy} style={{ minHeight: "32px", padding: "4px 10px", fontSize: "12px", background: "var(--color-accent)", color: "var(--color-accent-ink)" }}>Submit Verdict</button>
                  </div>
                </div>
              )}

              {/* Action Buttons */}
              {!isResolved && !isResolving && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "8px" }}>
                  {!alert.assigned_to && (
                    <button
                      className="quiet"
                      disabled={busy}
                      style={{ minHeight: "32px", padding: "4px 8px", fontSize: "12px" }}
                      onClick={() => onAssign(alert.id, "analyst_kapoor")}
                    >
                      <UserCheck size={13} /> Assign Me
                    </button>
                  )}

                  <button
                    className="escalate"
                    disabled={alert.status === "escalated" || busy}
                    style={{ minHeight: "32px", padding: "4px 8px", fontSize: "12px" }}
                    onClick={() => onEscalate(alert.id)}
                  >
                    {alert.status === "escalated" ? <><Check size={13} /> Escalated</> : <><ArrowUpRight size={13} /> Escalate</>}
                  </button>

                  <button
                    className="quiet"
                    disabled={busy}
                    style={{ minHeight: "32px", padding: "4px 8px", fontSize: "12px" }}
                    onClick={() => setResolvingId(alert.id)}
                  >
                    Resolve
                  </button>

                  <button
                    className="quiet"
                    disabled={busy}
                    style={{ minHeight: "32px", padding: "4px 8px", fontSize: "12px" }}
                    onClick={() => onOpenAppeal(alert)}
                  >
                    <MessageSquare size={13} /> Dispute
                  </button>
                </div>
              )}
            </article>
          );
        })
      )}

      <div className="panel-note">
        SLAs Enforced (High: 2m Ack / 30m Resolve · Medium: 15m Ack / 4h Resolve)
      </div>
    </section>
  );
}
