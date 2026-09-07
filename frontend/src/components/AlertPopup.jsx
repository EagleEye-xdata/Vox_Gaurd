import { ShieldAlert, ArrowUpRight, Check } from "lucide-react";
import { bandMeta } from "./Dashboard";

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
            Elevated or high sessions will create a request here. Calls are
            never automatically blocked.
          </p>
        </div>
      ) : (
        alerts.map((alert) => {
          const meta = bandMeta(alert.band);
          return (
            <article className="alert-item" key={alert.id}>
              <span className={`eyebrow ${meta.className}`}>
                {meta.glyph} {meta.label.toUpperCase()} ASSESSMENT
              </span>
              <h3>Additional verification required</h3>
              <p>{alert.message}</p>
              <small>
                Call {alert.call_id.slice(0, 8)} · Risk{" "}
                {Math.round(alert.risk_score)}/100
              </small>
              <button
                className="escalate"
                disabled={alert.status === "escalated" || busy}
                onClick={() => onEscalate(alert.id)}
              >
                {alert.status === "escalated" ? (
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
          );
        })
      )}
      <div className="panel-note">
        Advisory only · No call blocking · No SMS sent
      </div>
    </section>
  );
}
