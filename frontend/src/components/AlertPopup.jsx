import { ShieldAlert, ArrowUpRight, Check } from "lucide-react";
export default function AlertPopup({ alerts, onEscalate, busy }) {
  return (
    <section className="panel alert-panel">
      <div className="panel-heading">
        <h2>
          <ShieldAlert size={17} /> Verification queue
        </h2>
        <span className="count">{alerts.length}</span>
      </div>
      {!alerts.length ? (
        <div className="empty small">
          <Check size={23} />
          <h3>No verification requests</h3>
          <p>
            Sustained high risk will create an alert here. Calls are never
            automatically blocked.
          </p>
        </div>
      ) : (
        alerts.map((a) => (
          <article className="alert-item" key={a.id}>
            <span className="eyebrow warning">SECONDARY VERIFICATION</span>
            <h3>Suspicious voice pattern</h3>
            <p>{a.message}</p>
            <small>
              Call {a.call_id.slice(0, 8)} · Risk {Math.round(a.risk_score)}/100
            </small>
            <button
              className="escalate"
              disabled={a.status === "escalated" || busy}
              onClick={() => onEscalate(a.id)}
            >
              {a.status === "escalated" ? (
                <>
                  <Check size={15} />
                  Escalated locally
                </>
              ) : (
                <>
                  Escalate for review
                  <ArrowUpRight size={15} />
                </>
              )}
            </button>
          </article>
        ))
      )}
      <div className="panel-note">
        Advisory only · No call blocking · No SMS sent
      </div>
    </section>
  );
}
