import { useEffect, useRef, useState } from "react";
import {
  Shield,
  LayoutDashboard,
  Workflow,
  BookOpen,
  Radio,
  Play,
  ChevronRight,
  CircleHelp,
  LockKeyhole,
  X,
  ExternalLink,
} from "lucide-react";
import { api, socket } from "./api";
import Dashboard from "./components/Dashboard";
import CallDetail from "./components/CallDetail";
import AlertPopup from "./components/AlertPopup";
import LedgerPanel from "./components/LedgerPanel";

export default function App() {
  const [calls, setCalls] = useState([]),
    [alerts, setAlerts] = useState([]),
    [ledger, setLedger] = useState([]),
    [audio, setAudio] = useState([]),
    [selected, setSelected] = useState(null),
    [online, setOnline] = useState(false),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [verifying, setVerifying] = useState(false),
    [verification, setVerification] = useState(null),
    [page, setPage] = useState("monitor"),
    [filename, setFilename] = useState(""),
    [label, setLabel] = useState("CFO transfer request"),
    [amount, setAmount] = useState(250000),
    [known, setKnown] = useState(false),
    [notification, setNotification] = useState(null);
  const seenAlerts = useRef(new Set());
  const dialog = useRef(),
    ws = useRef();
  async function refresh() {
    try {
      const [c, a, l, f] = await Promise.all([
        api("/calls"),
        api("/alerts"),
        api("/ledger"),
        api("/audio"),
      ]);
      setCalls(c);
      setAlerts(a);
      setLedger(l);
      setAudio(f);
      setOnline(true);
      setSelected((s) => s || c[0]?.call_id || null);
      setFilename(
        (s) =>
          s ||
          f.find((a) => a.filename === "fixture-steady.wav")?.filename ||
          f[0]?.filename ||
          "",
      );
    } catch {
      setOnline(false);
    }
  }
  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 2000);
    return () => clearInterval(id);
  }, []);
  useEffect(() => {
    const fresh = alerts.find(
      (a) => a.status === "active" && !seenAlerts.current.has(a.id),
    );
    alerts.forEach((a) => seenAlerts.current.add(a.id));
    if (fresh) setNotification(fresh);
  }, [alerts]);
  useEffect(() => {
    ws.current?.close();
    if (!selected) return;
    const s = socket(selected);
    ws.current = s;
    s.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setCalls((prev) =>
        prev.map((c) => (c.call_id === selected ? data.call : c)),
      );
      if (data.type === "complete") refresh();
    };
    return () => s.close();
  }, [selected]);
  const call = calls.find((c) => c.call_id === selected);
  async function act(fn) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function start(e) {
    e.preventDefault();
    await act(async () => {
      const result = await api("/stream/start", {
        filename,
        label,
        context: { known_number: known, transaction_size: Number(amount) },
        interval: 3,
      });
      setSelected(result.call_id);
      dialog.current.close();
      setPage("monitor");
    });
  }
  async function verify() {
    setVerifying(true);
    try {
      setVerification(await api("/ledger/verify/all"));
    } catch (e) {
      setError(e.message);
    } finally {
      setVerifying(false);
    }
  }
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setPage("monitor");
          }}
        >
          <span className="brand-mark">
            <Shield size={23} />
          </span>
          <span>
            VoiceShield<span className="brand-ai">AI</span>
          </span>
        </a>
        <div className="workspace">
          <span className="workspace-avatar">VG</span>
          <div>
            <strong>vox_Guard</strong>
            <small>SIH 2026 workspace</small>
          </div>
          <ChevronRight size={15} />
        </div>
        <span className="nav-label">WORKSPACE</span>
        <nav>
          {[
            [LayoutDashboard, "monitor", "Call monitor"],
            [Workflow, "pipeline", "Detection pipeline"],
            [BookOpen, "ledger", "Audit trail"],
          ].map(([Icon, key, title]) => (
            <button
              key={key}
              className={page === key ? "nav-item active" : "nav-item"}
              onClick={() => setPage(key)}
            >
              <Icon size={18} />
              {title}
              {key === "monitor" && (
                <span className="nav-count">
                  {calls.filter((c) => c.status === "streaming").length}
                </span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="privacy-mark">
            <LockKeyhole size={18} />
            <span>
              Private by architecture<small>Audio stays on this machine.</small>
            </span>
          </div>
          <button className="nav-item" onClick={() => setPage("guide")}>
            <CircleHelp size={18} />
            Demo guide
          </button>
          <div className="user">
            <span className="workspace-avatar">VG</span>
            <div>
              <strong>vox_Guard team</strong>
              <small>Local environment</small>
            </div>
            <span className="dot" />
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div>
            Workspace <ChevronRight size={13} />
            <strong>
              {page === "monitor"
                ? "Call monitor"
                : page === "pipeline"
                  ? "Detection pipeline"
                  : page === "ledger"
                    ? "Audit trail"
                    : "Demo guide"}
            </strong>
          </div>
          <span className="environment">
            <i className={`dot ${online ? "" : "muted-dot"}`} />
            {online ? "Backend connected" : "Backend offline"}
            <span className="local-tag">LOCAL DEMO</span>
          </span>
        </header>
        <main>
          <nav className="mobile-nav" aria-label="Mobile navigation">
            {[
              ["monitor", "Monitor"],
              ["pipeline", "Pipeline"],
              ["ledger", "Ledger"],
              ["guide", "Guide"],
            ].map(([key, title]) => (
              <button
                key={key}
                className={page === key ? "active" : ""}
                onClick={() => setPage(key)}
              >
                {title}
              </button>
            ))}
          </nav>
          <div className="page-heading">
            <div>
              <div className="eyebrow">VOICE INTELLIGENCE / SIH26104</div>
              <h1>
                {page === "monitor"
                  ? "Listen closer. Verify sooner."
                  : page === "pipeline"
                    ? "From sound to evidence."
                    : page === "ledger"
                      ? "A trace for every decision."
                      : "Run the story end to end."}
              </h1>
              <p>
                {page === "monitor"
                  ? "Inspect voice signals as a call unfolds. Keep the next decision human."
                  : page === "pipeline"
                    ? "A local pipeline designed around the architecture plan."
                    : page === "ledger"
                      ? "Inspect the local hash chain and recompute its integrity."
                      : "Simulate an executive impersonation scenario with your own recordings."}
              </p>
            </div>
            <button
              className="primary"
              onClick={() => dialog.current.showModal()}
              disabled={!online}
            >
              <Play size={16} />
              Run simulation
            </button>
          </div>
          {error && (
            <div className="error-banner" role="alert">
              {error}
              <button aria-label="Dismiss error" onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {!online && (
            <div className="error-banner">
              Backend unavailable. Start FastAPI on port 8000; this dashboard
              reconnects automatically.
            </div>
          )}
          <div className="mode-notice">
            <span className="badge outline">
              <Radio size={13} />
              HEURISTIC DEMO
            </span>
            <p>
              Acoustic indicators, not proof of a cloned voice. No trained
              detector or speaker enrollment is active.
            </p>
            <button onClick={() => setPage("pipeline")}>
              View pipeline <ChevronRight size={14} />
            </button>
          </div>
          {page === "monitor" ? (
            <>
              <div className="stats-strip">
                {[
                  [
                    "Live sessions",
                    calls.filter((c) => c.status === "streaming").length,
                    "Streaming from local WAV files",
                  ],
                  [
                    "Windows analyzed",
                    calls.reduce((s, c) => s + c.history.length, 0),
                    "After voice activity detection",
                  ],
                  [
                    "Verification requests",
                    alerts.length,
                    "Secondary checks recommended",
                  ],
                  ["Audit events", ledger.length, "Hash-linked · no raw audio"],
                ].map(([title, value, desc], i) => (
                  <div className="stat" key={title}>
                    <span>
                      {title}
                      <span className="stat-index">0{i + 1}</span>
                    </span>
                    <strong>{value.toString().padStart(2, "0")}</strong>
                    <small>{desc}</small>
                  </div>
                ))}
              </div>
              <div className="dashboard-grid">
                <div className="main-column">
                  <Dashboard
                    calls={calls}
                    selected={selected}
                    onSelect={setSelected}
                  />
                  <CallDetail
                    call={call}
                    busy={busy}
                    onStop={() =>
                      act(() => api(`/stream/${selected}/stop`, {}))
                    }
                  />
                </div>
                <aside className="right-column">
                  <AlertPopup
                    alerts={alerts}
                    busy={busy}
                    onEscalate={(id) =>
                      act(() => api(`/alerts/${id}/escalate`, {}))
                    }
                  />
                  <LedgerPanel
                    entries={ledger}
                    onVerify={verify}
                    verification={verification}
                    busy={verifying}
                  />
                  <div className="privacy-note">
                    <LockKeyhole size={17} />
                    <p>
                      <strong>Evidence without the recording.</strong>Audio
                      buffers are cleared after feature extraction. Only derived
                      signals reach the dashboard.
                    </p>
                  </div>
                </aside>
              </div>
            </>
          ) : page === "ledger" ? (
            <LedgerPanel
              entries={ledger}
              onVerify={verify}
              verification={verification}
              busy={verifying}
              expanded
            />
          ) : page === "pipeline" ? (
            <div className="pipeline-view">
              {[
                [
                  "01",
                  "Ingest",
                  "Local WAV → mono, 16 kHz",
                  "Three-second windows. File-based streaming only.",
                ],
                [
                  "02",
                  "Preprocess",
                  "Band-pass filter → voice activity gate",
                  "Energy and spectral flatness reject silence and broadband noise.",
                ],
                [
                  "03",
                  "Extract",
                  "MFCC · mel spectrum · pitch · jitter/shimmer",
                  "Frame-based proxies; spectral peaks are not validated formant tracks.",
                ],
                [
                  "04",
                  "Classify",
                  "Swappable SpoofClassifier",
                  "Unvalidated heuristic baseline. Speaker matching remains unavailable.",
                ],
                [
                  "05",
                  "Aggregate",
                  "Weighted fusion → exponential moving average",
                  "Context adds up to 20 risk points. EMA α = 0.30. Authenticity = 100 − risk.",
                ],
                [
                  "06",
                  "Verify & record",
                  "Sustained risk ≥ 65 → secondary verification",
                  "Minimum three scored windows and two consecutive high windows. SHA-256 local ledger.",
                ],
              ].map(([n, t, s, d]) => (
                <article className="pipeline-step" key={n}>
                  <span>{n}</span>
                  <div>
                    <h2>{t}</h2>
                    <h3>{s}</h3>
                    <p>{d}</p>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <div className="guide panel">
              <h2>Demo: the urgent CFO transfer</h2>
              <ol>
                <li>
                  Add consented genuine and cloned WAV recordings to{" "}
                  <code>demo_audio/</code>, then wait for the source list to
                  refresh.
                </li>
                <li>
                  Choose “Run simulation”, select a file, and add a transaction
                  context. Built-in fixtures are test tones, not speech.
                </li>
                <li>
                  Watch the energy envelope, sub-scores, rolling risk, and
                  actual processing latency update.
                </li>
                <li>
                  If risk stays high, request a callback or MFA. Escalation
                  records a local review event; it does not contact anyone.
                </li>
                <li>
                  Open the audit trail and verify the hash chain. Tampering with
                  a stored record invalidates subsequent hashes.
                </li>
              </ol>
              <h3>What this demo does not establish</h3>
              <p>
                It does not measure detection accuracy. Evaluate a trained
                checkpoint on held-out ASVspoof data and unseen generators
                before presenting accuracy, false-positive rates, or
                multilingual coverage. Real telecom, permissioned blockchain,
                and bank integrations are future work.
              </p>
              <a
                className="quiet"
                href="http://127.0.0.1:8000/docs"
                target="_blank"
                rel="noreferrer"
              >
                Open local API docs <ExternalLink size={14} />
              </a>
            </div>
          )}
          <footer>
            <span>
              <Shield size={13} /> VoiceShield AI · vox_Guard
            </span>
            <span>SIH 2026 / AICTE · Software prototype</span>
          </footer>
        </main>
      </div>
      <dialog
        ref={dialog}
        onClick={(e) => {
          if (e.target === dialog.current) dialog.current.close();
        }}
      >
        <form onSubmit={start}>
          <div className="panel-heading">
            <h2>Run a call simulation</h2>
            <button
              type="button"
              className="icon-button"
              aria-label="Close simulation dialog"
              onClick={() => dialog.current.close()}
            >
              <X size={18} />
            </button>
          </div>
          <p>Stream a local recording through the detection pipeline.</p>
          <label>
            Audio source
            <select
              autoFocus
              aria-label="Audio source"
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
              required
            >
              {!audio.length && <option value="">No WAV files found</option>}
              {audio.map((a) => (
                <option key={a.filename} value={a.filename}>
                  {a.filename}
                  {a.fixture ? " · test signal" : ""}
                </option>
              ))}
            </select>
          </label>
          <p className="helper">
            Fixtures exercise the pipeline; they are not genuine or cloned
            speech.
          </p>
          <label>
            Session label
            <input
              required
              maxLength={80}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
          </label>
          <label>
            Transaction amount (₹)
            <input
              type="number"
              min="0"
              max="1000000000000"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              required
            />
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={known}
              onChange={(e) => setKnown(e.target.checked)}
            />
            Known caller number
          </label>
          <p className="helper">
            Context changes the risk score. It does not change acoustic
            detection.
          </p>
          {error && (
            <p role="alert" className="danger">
              {error}
            </p>
          )}
          <button className="primary full" disabled={busy || !filename}>
            {busy ? "Starting…" : "Start simulated call"}
            <Play size={15} />
          </button>
        </form>
      </dialog>
      {notification && (
        <aside className="alert-toast" role="alert">
          <div className="toast-heading">
            <strong>Suspicious voice pattern detected</strong>
            <button
              className="icon-button"
              aria-label="Dismiss notification"
              onClick={() => setNotification(null)}
            >
              <X size={16} />
            </button>
          </div>
          <p>
            Recommend callback or MFA before proceeding. The call has not been
            blocked.
          </p>
          <button
            className="quiet"
            onClick={() => {
              setSelected(notification.call_id);
              setPage("monitor");
              setNotification(null);
            }}
          >
            Review call <ChevronRight size={14} />
          </button>
        </aside>
      )}
    </div>
  );
}
