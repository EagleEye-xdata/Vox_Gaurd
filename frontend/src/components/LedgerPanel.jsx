import { Link2, ShieldCheck } from "lucide-react";
export default function LedgerPanel({
  entries = [],
  onVerify,
  verification,
  busy,
  expanded = false,
}) {
  return (
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
  );
}
