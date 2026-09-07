import { Phone, ArrowUpRight } from "lucide-react";

export const bandMeta = (band = "UNKNOWN") =>
  ({
    LOW: { glyph: "●", label: "Low", className: "safe" },
    MEDIUM: { glyph: "◆", label: "Elevated", className: "warning" },
    HIGH: { glyph: "▲", label: "High", className: "danger" },
    UNKNOWN: { glyph: "◌", label: "Not assessed", className: "unknown" },
  })[band] || { glyph: "◌", label: "Not assessed", className: "unknown" };

export const riskClass = (band) => bandMeta(band).className;

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
        <span>ASSESSMENT</span>
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
      {calls.map((call) => {
        const meta = bandMeta(call.band);
        return (
          <button
            key={call.call_id}
            className={`call-row ${selected === call.call_id ? "selected" : ""}`}
            onClick={() => onSelect(call.call_id)}
          >
            <span className="call-name">
              <span className="call-icon">
                <Phone size={16} />
              </span>
              <span>
                <strong>{call.label}</strong>
                <small>{call.filename}</small>
              </span>
            </span>
            <span className="call-status">
              {call.status === "streaming" && <i className="dot" />}
              {call.status}
            </span>
            <span
              className={`badge band-badge ${meta.className}`}
              aria-label={`Assessment: ${meta.label}`}
            >
              <span aria-hidden="true">{meta.glyph}</span>
              {meta.label}
              <ArrowUpRight size={13} />
            </span>
          </button>
        );
      })}
    </section>
  );
}
