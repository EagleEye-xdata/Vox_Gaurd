import { useEffect, useRef, useState } from "react";
import {
  Shield,
  LayoutDashboard,
  BookOpen,
  Radio,
  ChevronRight,
  CircleHelp,
  LockKeyhole,
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
import TwoWayCallModal from "./components/TwoWayCallModal";

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
    [twoWayCallOpen, setTwoWayCallOpen] = useState(false),
    [notification, setNotification] = useState(null);

  // Modals state
  const [overrideModalCall, setOverrideModalCall] = useState(null);
  const [appealModalAlert, setAppealModalAlert] = useState(null);
  const [summaryModalCall, setSummaryModalCall] = useState(null);
  const [enrolmentModalOpen, setEnrolmentModalOpen] = useState(false);

  const seenAlerts = useRef(new Set());
  const ws = useRef();

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
            [PhoneCall, "telephony", "Live Phone"],
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
              ["telephony", "Live Phone"],
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
              <span className="eyebrow">
                {page === "monitor"
                  ? "CALL MONITOR"
                  : page === "ledger"
                    ? "AUDIT TRAIL"
                    : page === "telephony"
                      ? "TELEPHONY INTERFACE"
                      : "DOCUMENTATION"}
              </span>
              <h1>
                {page === "monitor"
                  ? "Voice fraud analysis"
                  : page === "ledger"
                    ? "Forensic audit ledger"
                    : page === "telephony"
                      ? "Live SIP softphone"
                      : "VoiceShield AI guide"}
              </h1>
              <p>
                {page === "monitor"
                  ? "Real-time acoustic analysis, speaker verification, and automated policy decision engine."
                  : page === "ledger"
                    ? "Immutable SHA-256 hash chain and origin decision signatures."
                    : page === "telephony"
                      ? "Connect to Asterisk PBX over SIP/WSS and monitor live phone calls in real time."
                      : "System architecture and judge Q&A summary."}
              </p>
            </div>
            {page === "monitor" && (
              <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
                <button
                  className="primary"
                  style={{
                    background: "linear-gradient(135deg, #4f46e5, #06b6d4)",
                    border: "none",
                    boxShadow: "0 0 15px rgba(79, 70, 229, 0.4)",
                  }}
                  onClick={() => setTwoWayCallOpen(true)}
                >
                  <Radio size={16} /> 2-Way Live AI Call
                </button>
              </div>
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



          {page === "ledger" && (
            <LedgerPanel
              entries={ledger}
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
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                gap: 20,
                padding: "36px 20px",
                maxWidth: 700,
                margin: "0 auto",
                textAlign: "center",
              }}
            >
              <div
                style={{
                  width: 64,
                  height: 64,
                  borderRadius: "50%",
                  background: "linear-gradient(135deg, #4f46e5, #06b6d4)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  boxShadow: "0 0 30px rgba(79, 70, 229, 0.4)",
                }}
              >
                <Radio size={32} color="white" />
              </div>

              <h2>Real-Time Two-Way AI Voice Call</h2>
              <p style={{ color: "var(--color-muted)", fontSize: 14, maxWidth: 500 }}>
                Initiate an interactive live conversation with an AI Scammer bot. Speak into your microphone, hear the synthetic voice reply, and observe VoxGuard's real-time deepfake detection, risk scoring, and automated policy blocking.
              </p>

              <button
                className="primary"
                style={{
                  padding: "12px 28px",
                  fontSize: 15,
                  fontWeight: 600,
                  background: "linear-gradient(135deg, #4f46e5, #06b6d4)",
                  border: "none",
                  borderRadius: 12,
                  boxShadow: "0 0 20px rgba(79, 70, 229, 0.5)",
                  cursor: "pointer",
                }}
                onClick={() => setTwoWayCallOpen(true)}
              >
                <Radio size={18} style={{ marginRight: 8 }} />
                Launch 2-Way Live Call
              </button>
            </div>
          )}
        </main>
      </div>

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

      <TwoWayCallModal
        isOpen={twoWayCallOpen}
        onClose={() => setTwoWayCallOpen(false)}
        onSessionCreated={(callId) => {
          setSelected(callId);
        }}
      />
    </div>
  );
}
