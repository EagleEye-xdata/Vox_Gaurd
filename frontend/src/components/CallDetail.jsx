import { AudioLines, Fingerprint, Activity, Timer, Square } from "lucide-react";
import { riskClass, riskLabel } from "./Dashboard";
export default function CallDetail({ call, onStop, busy }) {
  const latest = call?.latest,
    score = call?.risk_score,
    history = call?.history || [];
  const env = latest?.features.rms_envelope || [];
  const points = history
    .map(
      (v, i) =>
        `${20 + (i * 660) / Math.max(history.length - 1, 1)},${150 - v * 1.2}`,
    )
    .join(" ");
  return (
    <section className="panel detail">
      <div className="panel-heading">
        <h2>
          <AudioLines size={17} /> Signal analysis
        </h2>
        <span className={`badge ${riskClass(score)}`}>{riskLabel(score)}</span>
      </div>
      <div className="analysis-title">
        <div>
          <span className="eyebrow">SELECTED SESSION</span>
          <h3>{call?.label || "Waiting for a call"}</h3>
          <p>
            {call
              ? `${call.chunks_processed} windows processed · ${call.dropped_chunks} skipped by VAD`
              : "Start a simulation to see acoustic evidence."}
          </p>
          {latest?.classification && (
            <p className="classification">
              Latest window: <strong>{latest.classification}</strong> ·
              heuristic label
            </p>
          )}
          {call?.error && (
            <p role="alert" className="danger">
              {call.error}
            </p>
          )}
        </div>
        {call?.status === "streaming" && (
          <button className="quiet" disabled={busy} onClick={onStop}>
            <Square size={13} /> Stop
          </button>
        )}
      </div>
      <div className="signal-layout">
        <div className="authenticity">
          <span className="eyebrow">VOICE AUTHENTICITY</span>
          <div className={`big-score ${riskClass(score)}`}>
            {score == null ? "—" : Math.round(100 - score)}
            <span>/100</span>
          </div>
          <p>
            Inverse of rolling risk.
            <br />
            Uncalibrated demo indicator.
          </p>
        </div>
        <div className="wave-box">
          <div className="wave-heading">
            <span>Audio energy envelope</span>
            <span>16 kHz · 3s windows</span>
          </div>
          <svg
            viewBox="0 0 540 105"
            role="img"
            aria-label="Measured RMS audio energy envelope"
          >
            <line x1="0" y1="52" x2="540" y2="52" className="baseline" />
            {env.map((v, i) => (
              <line
                key={i}
                x1={i * 5.6 + 3}
                y1={52 - Math.min(47, v * 180)}
                x2={i * 5.6 + 3}
                y2={52 + Math.min(47, v * 180)}
                className="wave-bar"
              />
            ))}
          </svg>
          <div className="wave-footer">
            <span>
              <i
                className={`dot ${call?.status === "streaming" ? "" : "muted-dot"}`}
              />
              {latest
                ? "Derived from latest scored window"
                : "No audio processed"}
            </span>
            <span>No raw audio retained</span>
          </div>
        </div>
      </div>
      <div className="subscores">
        {[
          [AudioLines, "Spectral artifacts", latest?.spectral_score],
          [Activity, "Prosody irregularity", latest?.prosody_score],
          [Fingerprint, "Speaker match", null],
        ].map(([Icon, label, v]) => (
          <div className="subscore" key={label}>
            <div>
              <Icon size={16} />
              <span>{label}</span>
            </div>
            <strong>{v == null ? "—" : `${Math.round(v * 100)}%`}</strong>
            <div className="meter">
              <span style={{ width: `${(v || 0) * 100}%` }} />
            </div>
            <small>
              {v == null
                ? label === "Speaker match"
                  ? "Not enrolled"
                  : "Awaiting audio"
                : "Higher indicates more suspicion"}
            </small>
          </div>
        ))}
      </div>
      <div className="chart-title">
        <h3>Risk over time</h3>
        <span>
          <i className="legend" />
          Rolling risk <i className="legend threshold" />
          Alert threshold · 65
        </span>
      </div>
      <div className="chart">
        <svg
          viewBox="0 0 710 178"
          role="img"
          aria-label="Rolling risk across scored windows, threshold 65"
        >
          {[0, 50, 100].map((v) => (
            <g key={v}>
              <line
                x1="20"
                y1={150 - v * 1.2}
                x2="680"
                y2={150 - v * 1.2}
                className="gridline"
              />
              <text x="685" y={154 - v * 1.2}>
                {v}
              </text>
            </g>
          ))}
          <line x1="20" y1="72" x2="680" y2="72" className="threshold-line" />
          {history.length > 0 && (
            <>
              <polyline points={points} className="risk-line" />
              {history.map((v, i) => (
                <circle
                  key={i}
                  cx={20 + (i * 660) / Math.max(history.length - 1, 1)}
                  cy={150 - v * 1.2}
                  r="3"
                  className="chart-point"
                />
              ))}
            </>
          )}
          <text x="20" y="173">
            {history.length ? "WINDOW 1" : "AWAITING SCORED WINDOWS"}
          </text>
          {history.length > 1 && (
            <text x="590" y="173">
              WINDOW {history.length}
            </text>
          )}
        </svg>
      </div>
      <div className="detail-footer">
        <span>
          <Timer size={14} />
          Detection latency:{" "}
          <b>
            {call?.latency_ms == null
              ? "—"
              : `${call.latency_ms.toFixed(1)} ms`}
          </b>
        </span>
        <span>EMA α 0.30 · measured per window</span>
      </div>
    </section>
  );
}
