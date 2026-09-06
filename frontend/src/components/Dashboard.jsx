import { Phone, ArrowUpRight } from "lucide-react";
export const riskLabel = (score) =>
  score == null
    ? "Awaiting audio"
    : score >= 65
      ? "High risk"
      : score >= 40
        ? "Review"
        : "Low risk";
export const riskClass = (score) =>
  score == null
    ? ""
    : score >= 65
      ? "danger"
      : score >= 40
        ? "warning"
        : "safe";
export default function Dashboard({ calls, selected, onSelect }) {
  return (
    <section className="panel calls-panel">
      <div className="panel-heading">
        <h2>
          Call sessions <span className="count">{calls.length}</span>
        </h2>
        <span className="eyebrow">LOCAL SOURCE</span>
      </div>
      <div className="table-labels">
        <span>CALL / SOURCE</span>
        <span>STATUS</span>
        <span>RISK</span>
      </div>
      {!calls.length && (
        <div className="empty">
          <Phone size={26} />
          <h3>Your first call starts here.</h3>
          <p>
            Choose an audio source, then run a simulation to inspect its voice
            signals.
          </p>
        </div>
      )}
      {calls.map((c) => (
        <button
          key={c.call_id}
          className={`call-row ${selected === c.call_id ? "selected" : ""}`}
          onClick={() => onSelect(c.call_id)}
        >
          <span className="call-name">
            <span className="call-icon">
              <Phone size={16} />
            </span>
            <span>
              <strong>{c.label}</strong>
              <small>{c.filename}</small>
            </span>
          </span>
          <span className="call-status">
            {c.status === "streaming" && <i className="dot" />}
            {c.status}
          </span>
          <span className={`badge ${riskClass(c.risk_score)}`}>
            {c.risk_score == null ? "—" : Math.round(c.risk_score)}
            <ArrowUpRight size={13} />
          </span>
        </button>
      ))}
    </section>
  );
}
