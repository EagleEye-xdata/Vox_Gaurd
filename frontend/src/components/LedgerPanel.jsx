import { Link2, ShieldCheck } from "lucide-react";
export default function LedgerPanel({
  entries = [],
  calls = [],
  onVerify,
  verification,
  busy,
  expanded = false,
}) {
  // Calculate metrics based on the immutable ledger entries instead of the active session calls
  const passedCalls = entries.filter(e => e.event_type === "call_completed").length;
  const highRiskCalls = entries.filter(e => e.event_type === "alert" || e.event_type === "decision").length;

  return (
    <div className="ledger-wrapper">
      <div className="metrics-row" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '20px', marginBottom: '24px' }}>
         <div className="metric-card panel" style={{ padding: '20px', borderBottom: '2px solid var(--color-safe)' }}>
           <h3 style={{ fontSize: '12px', color: 'var(--color-muted)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '1px' }}>Calls Passed (Clean)</h3>
           <div style={{ fontSize: '32px', color: 'var(--color-safe)', fontWeight: '700', textShadow: '0 0 12px var(--color-safe)' }}>{passedCalls}</div>
         </div>
         <div className="metric-card panel" style={{ padding: '20px', borderBottom: '2px solid var(--color-danger)' }}>
           <h3 style={{ fontSize: '12px', color: 'var(--color-muted)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '1px' }}>Calls Blocked (Fraud)</h3>
           <div style={{ fontSize: '32px', color: 'var(--color-danger)', fontWeight: '700', textShadow: '0 0 12px var(--color-danger)' }}>{highRiskCalls}</div>
         </div>
         <div className="metric-card panel" style={{ padding: '20px', borderBottom: '2px solid var(--color-accent)' }}>
           <h3 style={{ fontSize: '12px', color: 'var(--color-muted)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '1px' }}>Total Ledger Entries</h3>
           <div style={{ fontSize: '32px', color: 'var(--color-accent)', fontWeight: '700', textShadow: '0 0 12px var(--color-accent)' }}>{entries.length}</div>
         </div>
      </div>
    <section className="panel ledger-panel">
      <div className="panel-heading">
        <h2>
          <Link2 size={17} /> Audit ledger
        </h2>
        <button className="quiet" onClick={onVerify} disabled={busy}>
          <ShieldCheck size={14} />
          {busy ? "Verifying…" : "Verify chain"}
        </button>
      </div>
      <p className="ledger-description">
        Local, hash-chained evidence. Scores and events only.
      </p>
      {verification && (
        <div
          role="status"
          className={`verify-result ${verification.valid ? "safe" : "danger"}`}
        >
          {verification.valid ? "Verified snapshot" : "Verification failed"} ·{" "}
          {verification.checked} entries checked
        </div>
      )}
      {!entries.length ? (
        <div className="empty small">
          <Link2 size={23} />
          <p>Processed calls will leave a verifiable trail here.</p>
        </div>
      ) : (
        <div className="ledger-list">
          {(expanded ? entries : entries.slice(0, 6)).map((e) => (
            <div className="ledger-entry" key={e.hash}>
              <span className="block-number">
                {String(e.id).padStart(2, "0")}
              </span>
              <div>
                <strong>{e.event_type.replaceAll("_", " ")}</strong>
                <code title={e.hash}>
                  {e.hash.slice(0, 14)}…{e.hash.slice(-6)}
                </code>
              </div>
              <time>
                {new Date(e.timestamp).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                })}
              </time>
            </div>
          ))}
        </div>
      )}
      <div className="panel-note">
        SHA-256 · {entries.length} entries · Mock blockchain
      </div>
    </section>
    </div>
  );
}
