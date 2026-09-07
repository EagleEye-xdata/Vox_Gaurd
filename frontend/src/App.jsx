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
  UserCheck,
  Gavel,
  FileText,
  PhoneCall,
} from "lucide-react";
import { api, socket } from "./api";
import Dashboard from "./components/Dashboard";
import CallDetail from "./components/CallDetail";
import AlertPopup from "./components/AlertPopup";
import LedgerPanel from "./components/LedgerPanel";
import SupervisorOverrideModal from "./components/SupervisorOverrideModal";
import AppealModal from "./components/AppealModal";
import SessionSummaryModal from "./components/SessionSummaryModal";
import EnrolmentModal from "./components/EnrolmentModal";
import WebPhone from "./components/WebPhone";

export default function App() {
  const [calls, setCalls] = useState([]),
    [alerts, setAlerts] = useState([]),
    [ledger, setLedger] = useState([]),
    [audio, setAudio] = useState([]),
    [enrolments, setEnrolments] = useState([]),
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
    [newBeneficiary, setNewBeneficiary] = useState(true),
    [urgency, setUrgency] = useState("high"),
    [selectedIdentity, setSelectedIdentity] = useState("cust_rajesh_9012"),
    [simulateFailure, setSimulateFailure] = useState(false),
    [notification, setNotification] = useState(null);

  // Modals state
  const [overrideModalCall, setOverrideModalCall] = useState(null);
  const [appealModalAlert, setAppealModalAlert] = useState(null);
  const [summaryModalCall, setSummaryModalCall] = useState(null);
  const [enrolmentModalOpen, setEnrolmentModalOpen] = useState(false);

  const seenAlerts = useRef(new Set());
  const dialog = useRef(),
    ws = useRef();

  async function refresh() {
    try {
      const [c, a, l, f, e] = await Promise.all([
        api("/calls"),
        api("/alerts"),
        api("/ledger"),
        api("/audio"),
        api("/enrolments"),
      ]);
      setCalls(c);
      setAlerts(a);
      setLedger(l);
      setAudio(f);
      setEnrolments(e);
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
      if (data.type === "complete") {
        refresh();
        if (data.session_summary) {
          setSummaryModalCall(data.call);
        }
      }
    };
    s.onerror = () => setOnline(false);
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
        context: {
          caller_attestation: known ? "KNOWN_UNVERIFIED" : "UNKNOWN",
          attestation_source: known ? "CALLER_ID_ONLY" : "NONE",
          transaction_value: Number(amount),
          transaction_currency: "INR",
          transaction_type: "wire_transfer",
          beneficiary_is_new: newBeneficiary,
          request_urgency: urgency,
          urgency_source: "AGENT_ASSERTED",
          confirmed_fraud_flags_90d: 0,
        },
        interval: 1,
        simulate_detector_failure: simulateFailure,
        identity_id: selectedIdentity || null,
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

  async function handleSupervisorOverride(callId, overrideData) {
    await act(async () => {
      await api(`/decisions/${callId}/override`, overrideData);
      setOverrideModalCall(null);
    });
  }

  async function handleAlertAssign(alertId, assigneeId) {
    await act(async () => {
      await api(`/alerts/${alertId}/assign`, { assignee_id: assigneeId });
    });
  }

  async function handleAlertResolve(alertId, outcome, notes) {
    await act(async () => {
      await api(`/alerts/${alertId}/resolve`, {
        outcome,
        notes,
        resolver_id: "analyst_kapoor",
      });
    });
  }

  async function handleAppealSubmit(alertId, appealData) {
    await act(async () => {
      await api(`/alerts/${alertId}/appeal`, appealData);
      setAppealModalAlert(null);
    });
  }

  async function handleEnrollSpeaker(enrolmentData) {
    await act(async () => {
      await api("/enrolments", enrolmentData);
    });
  }

  async function handleRevokeEnrolment(identityId) {
    await act(async () => {
      await api(`/enrolments/${identityId}`, undefined, "DELETE");
    });
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
            [PhoneCall, "telephony", "Live Phone"],
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

        <div style={{ padding: "0 12px", marginTop: "12px" }}>
          <button
            className="nav-item"
            style={{
              width: "100%",
              justifyContent: "flex-start",
              background: "var(--color-paper-2)",
            }}
            onClick={() => setEnrolmentModalOpen(true)}
          >
            <UserCheck size={16} color="var(--color-safe)" />
            Voice Profiles ({enrolments.length})
          </button>
        </div>

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
                    : page === "telephony"
                      ? "Live Phone"
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
              ["telephony", "Live Phone"],
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
              <span className="eyebrow">
                {page === "monitor"
                  ? "CALL MONITOR"
                  : page === "pipeline"
                    ? "DETECTION PIPELINE"
                    : page === "ledger"
                      ? "AUDIT TRAIL"
                      : page === "telephony"
                        ? "TELEPHONY INTERFACE"
                        : "DOCUMENTATION"}
              </span>
              <h1>
                {page === "monitor"
                  ? "Voice fraud analysis"
                  : page === "pipeline"
                    ? "Audio processing pipeline"
                    : page === "ledger"
                      ? "Forensic audit ledger"
                      : page === "telephony"
                        ? "Live SIP softphone"
                        : "VoiceShield AI guide"}
              </h1>
              <p>
                {page === "monitor"
                  ? "Real-time acoustic analysis, speaker verification, and automated policy decision engine."
                  : page === "pipeline"
                    ? "Acoustic extraction and multi-factor fusion."
                    : page === "ledger"
                      ? "Immutable SHA-256 hash chain and origin decision signatures."
                      : page === "telephony"
                        ? "Connect to Asterisk PBX over SIP/WSS and monitor live phone calls in real time."
                        : "System architecture and judge Q&A summary."}
              </p>
            </div>
            {page === "monitor" && (
              <button
                className="primary"
                onClick={() => dialog.current.showModal()}
              >
                <Play size={16} /> Simulate call
              </button>
            )}
          </div>

          {error && <div className="error-banner">{error}</div>}

          {page === "monitor" && (
            <>
              <Dashboard
                calls={calls}
                selected={selected}
                onSelect={(id) => setSelected(id)}
              />
              <div className="main-grid">
                <CallDetail
                  call={call}
                  onStop={() => act(() => api(`/stream/${selected}/stop`))}
                  busy={busy}
                  onOpenOverride={(c) => setOverrideModalCall(c)}
                  onOpenSummary={(c) => setSummaryModalCall(c)}
                />
                <AlertPopup
                  alerts={alerts}
                  onEscalate={(id) => act(() => api(`/alerts/${id}/escalate`))}
                  onAssign={handleAlertAssign}
                  onResolve={handleAlertResolve}
                  onOpenAppeal={(a) => setAppealModalAlert(a)}
                  busy={busy}
                />
              </div>
            </>
          )}

          {page === "pipeline" && (
            <div className="pipeline-view">
              <section className="panel">
                <div className="panel-heading">
                  <h2>
                    <Radio size={17} /> Live pipeline architecture
                  </h2>
                </div>
                <div className="pipeline-steps">
                  <div className="step-card">
                    <span className="step-number">01</span>
                    <h3>Ingestion & Normalisation</h3>
                    <p>
                      16 kHz mono resampling, ring buffer (Zero Disk Storage).
                    </p>
                  </div>
                  <div className="step-card">
                    <span className="step-number">02</span>
                    <h3>Voice Activity & Butterworth Filtering</h3>
                    <p>
                      80-3800 Hz bandpass, energy + in-band spectral flatness
                      VAD.
                    </p>
                  </div>
                  <div className="step-card">
                    <span className="step-number">03</span>
                    <h3>Acoustic Feature Extraction</h3>
                    <p>
                      13 MFCCs, log-mel filterbanks, YIN pitch & prosody
                      statistics.
                    </p>
                  </div>
                  <div className="step-card">
                    <span className="step-number">04</span>
                    <h3>Parallel AI Detection & Speaker Match</h3>
                    <p>
                      Spoof classification and text-independent voice embedding
                      matching.
                    </p>
                  </div>
                  <div className="step-card">
                    <span className="step-number">05</span>
                    <h3>Active-Signal Risk Fusion</h3>
                    <p>
                      Active renormalisation, contextual weighting, and strict
                      score floors.
                    </p>
                  </div>
                  <div className="step-card">
                    <span className="step-number">06</span>
                    <h3>Decision Engine & Tamper-Evident WAL</h3>
                    <p>
                      Banded actions (ALLOW/WARN/STEP_UP/BLOCK) signed at
                      origin.
                    </p>
                  </div>
                </div>
              </section>
            </div>
          )}

          {page === "ledger" && (
            <LedgerPanel
              ledger={ledger}
              onVerify={verify}
              verification={verification}
              verifying={verifying}
            />
          )}

          {page === "guide" && (
            <div className="panel guide-panel" style={{ padding: "20px" }}>
              <h2>System Architecture & Compliance Overview</h2>
              <p style={{ marginTop: "8px" }}>
                VoiceShield AI provides a real-time, explainable, and provably
                tamper-evident defense against generative voice cloning and
                impersonation attacks in financial communications.
              </p>
              <div
                style={{
                  marginTop: "16px",
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: "16px",
                }}
              >
                <div
                  style={{
                    background: "var(--color-paper-2)",
                    padding: "12px",
                    borderRadius: "6px",
                  }}
                >
                  <h3>Core Invariants</h3>
                  <ul
                    style={{
                      paddingLeft: "20px",
                      fontSize: "13px",
                      color: "var(--color-muted)",
                    }}
                  >
                    <li>Zero raw audio touches disk or broker logs.</li>
                    <li>
                      Floors applied last via max() to prevent masking attacks.
                    </li>
                    <li>Absence of evidence is UNKNOWN, never LOW.</li>
                    <li>Origin cryptographic signing on all decisions.</li>
                  </ul>
                </div>
                <div
                  style={{
                    background: "var(--color-paper-2)",
                    padding: "12px",
                    borderRadius: "6px",
                  }}
                >
                  <h3>Compliance & Redress</h3>
                  <ul
                    style={{
                      paddingLeft: "20px",
                      fontSize: "13px",
                      color: "var(--color-muted)",
                    }}
                  >
                    <li>
                      Complies with India DPDP Act & GDPR biometric constraints.
                    </li>
                    <li>
                      Integrated customer dispute & appeal filing workflow.
                    </li>
                    <li>
                      Closed-loop analyst resolution for continuous feedback.
                    </li>
                  </ul>
                </div>
              </div>
            </div>
          )}

          {page === "telephony" && (
            <div style={{ display: "flex", justifyContent: "center", padding: "24px 0" }}>
              <WebPhone />
            </div>
          )}
        </main>
      </div>

      {/* Simulation Setup Dialog */}
      <dialog ref={dialog} className="dialog">
        <form onSubmit={start}>
          <div className="dialog-heading">
            <h2>Simulate live inbound call</h2>
            <button
              type="button"
              className="quiet"
              onClick={() => dialog.current.close()}
            >
              <X size={18} />
            </button>
          </div>
          <div className="dialog-body">
            <label>
              Audio sample:
              <select
                value={filename}
                onChange={(e) => setFilename(e.target.value)}
              >
                {audio.map((f) => (
                  <option key={f.filename} value={f.filename}>
                    {f.filename} {f.fixture ? "(Fixture)" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Scenario label:
              <input
                type="text"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
              />
            </label>
            <label>
              Speaker Enrolment Match:
              <select
                value={selectedIdentity}
                onChange={(e) => setSelectedIdentity(e.target.value)}
              >
                <option value="">No Enrolment (Reference Unavailable)</option>
                {enrolments.map((p) => (
                  <option key={p.identity_id} value={p.identity_id}>
                    {p.display_name} ({p.identity_id})
                  </option>
                ))}
              </select>
            </label>
            <label>
              Transaction amount (INR):
              <input
                type="number"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
            </label>
            <label>
              Urgency level:
              <select
                value={urgency}
                onChange={(e) => setUrgency(e.target.value)}
              >
                <option value="low">Low</option>
                <option value="normal">Normal</option>
                <option value="high">High</option>
              </select>
            </label>
            <div style={{ display: "flex", gap: "16px", marginTop: "8px" }}>
              <label
                style={{ display: "flex", alignItems: "center", gap: "6px" }}
              >
                <input
                  type="checkbox"
                  checked={newBeneficiary}
                  onChange={(e) => setNewBeneficiary(e.target.checked)}
                />
                New Beneficiary
              </label>
              <label
                style={{ display: "flex", alignItems: "center", gap: "6px" }}
              >
                <input
                  type="checkbox"
                  checked={simulateFailure}
                  onChange={(e) => setSimulateFailure(e.target.checked)}
                />
                Simulate Detector Chaos
              </label>
            </div>
          </div>
          <div className="dialog-footer">
            <button
              type="button"
              className="quiet"
              onClick={() => dialog.current.close()}
            >
              Cancel
            </button>
            <button type="submit" className="primary" disabled={busy}>
              Start Stream
            </button>
          </div>
        </form>
      </dialog>

      {/* Modals */}
      <SupervisorOverrideModal
        call={overrideModalCall}
        isOpen={Boolean(overrideModalCall)}
        onClose={() => setOverrideModalCall(null)}
        onOverrideSubmit={handleSupervisorOverride}
        busy={busy}
      />

      <AppealModal
        alert={appealModalAlert}
        isOpen={Boolean(appealModalAlert)}
        onClose={() => setAppealModalAlert(null)}
        onAppealSubmit={handleAppealSubmit}
        busy={busy}
      />

      <SessionSummaryModal
        call={summaryModalCall}
        isOpen={Boolean(summaryModalCall)}
        onClose={() => setSummaryModalCall(null)}
      />

      <EnrolmentModal
        enrolments={enrolments}
        isOpen={enrolmentModalOpen}
        onClose={() => setEnrolmentModalOpen(false)}
        onEnroll={handleEnrollSpeaker}
        onRevoke={handleRevokeEnrolment}
        busy={busy}
      />
    </div>
  );
}
