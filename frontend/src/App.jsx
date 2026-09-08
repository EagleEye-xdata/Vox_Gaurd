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
  AlertTriangle,
  Activity,
  CheckCircle
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
import TelemetryConsole from "./components/TelemetryConsole";

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

  const renderModals = () => (
    <>
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
    </>
  );

  return (
    <div className="flex h-screen bg-[var(--color-surface-base)] text-[var(--color-text-secondary)] font-sans overflow-hidden">
      {/* Global Sidebar */}
      <aside className="w-64 bg-[var(--color-surface-elevated)] border-r border-[var(--color-border-subtle)] flex flex-col shrink-0 z-20">
        <div className="h-14 flex items-center px-6 border-b border-[var(--color-border-subtle)] shrink-0">
          <Shield className="w-5 h-5 text-[var(--color-authentic)] mr-3" />
          <span className="font-semibold text-[var(--color-text-primary)] tracking-wide">VoiceShield <span className="text-[var(--color-accent-blue)]">AI</span></span>
        </div>

        <div className="p-4 border-b border-[var(--color-border-subtle)]">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded bg-[var(--color-surface-layer)] border border-[var(--color-border-subtle)] flex items-center justify-center text-xs font-bold text-[var(--color-text-primary)]">
              VG
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-semibold text-[var(--color-text-primary)] truncate">vox_Guard</div>
              <div className="text-xs text-[var(--color-text-tertiary)] truncate">SIH 2026 Workspace</div>
            </div>
            <ChevronRight className="w-4 h-4 text-[var(--color-text-tertiary)]" />
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto py-4 px-3 flex flex-col gap-1">
          <div className="px-3 mb-2 text-[10px] font-bold text-[var(--color-text-tertiary)] uppercase tracking-wider">Workspace</div>

          {[
            [LayoutDashboard, "monitor", "Command Center"],
            [PhoneCall, "telephony", "Live Intercept"],
            [BookOpen, "ledger", "Forensic Ledger"],
          ].map(([Icon, key, title]) => (
            <button
              key={key}
              onClick={() => setPage(key)}
              className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-all ${page === key ? 'bg-[var(--color-surface-layer)] text-[var(--color-text-primary)] border border-[var(--color-border-subtle)] shadow-sm' : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-glass)] hover:text-[var(--color-text-primary)] border border-transparent'}`}
            >
              <Icon className={`w-4 h-4 ${page === key ? 'text-[var(--color-accent-blue)]' : ''}`} />
              {title}
              {key === "monitor" && (
                <span className="ml-auto bg-[var(--color-surface-base)] border border-[var(--color-border-subtle)] px-2 py-0.5 rounded text-[10px] font-bold">
                  {calls.filter((c) => c.status === "streaming").length}
                </span>
              )}
            </button>
          ))}

          <div className="mt-6 px-3 mb-2 text-[10px] font-bold text-[var(--color-text-tertiary)] uppercase tracking-wider">Configuration</div>

          <button
            onClick={() => setEnrolmentModalOpen(true)}
            className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-glass)] hover:text-[var(--color-text-primary)] transition-colors"
          >
            <UserCheck className="w-4 h-4 text-[var(--color-safe)]" />
            Voice Profiles
            <span className="ml-auto bg-[var(--color-surface-layer)] px-2 py-0.5 rounded text-[10px]">{enrolments.length}</span>
          </button>
        </nav>

        <div className="p-4 border-t border-[var(--color-border-subtle)] bg-[var(--color-surface-glass)]">
          <div className="flex items-center gap-2 mb-4 text-xs text-[var(--color-text-secondary)]">
            <LockKeyhole className="w-4 h-4 text-[var(--color-authentic)]" />
            <div>
              <div className="font-medium text-[var(--color-text-primary)]">Private Architecture</div>
              <div className="text-[10px] text-[var(--color-text-tertiary)]">Zero raw audio retained</div>
            </div>
          </div>
          <button onClick={() => setPage("guide")} className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-[var(--color-surface-layer)] border border-[var(--color-border-subtle)] text-xs font-medium hover:bg-[var(--color-hover-overlay)] transition-colors">
            <CircleHelp className="w-4 h-4" /> Documentation
          </button>
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="flex-1 flex flex-col min-w-0 bg-[var(--color-surface-base)] relative">
        {page === "monitor" ? (
          <TelemetryConsole
            calls={calls}
            selected={selected}
            onSelect={setSelected}
            ledger={ledger}
            alerts={alerts}
            onEscalate={(id) => act(() => api(`/alerts/${id}/escalate`))}
            onAssign={handleAlertAssign}
            onResolve={handleAlertResolve}
            onOpenAppeal={(a) => setAppealModalAlert(a)}
            onStop={() => act(() => api(`/stream/${selected}/stop`))}
            onOpenOverride={(c) => setOverrideModalCall(c)}
            onOpenSummary={(c) => setSummaryModalCall(c)}
            onOpenTwoWay={() => setTwoWayCallOpen(true)}
            busy={busy}
          />
        ) : (
          <div className="flex-1 overflow-y-auto">
            {/* Header for non-monitor pages */}
            <header className="h-14 border-b border-[var(--color-border-subtle)] bg-[var(--color-surface-elevated)] backdrop-blur-md flex items-center justify-between px-6 shrink-0 sticky top-0 z-10">
              <div className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
                Workspace <ChevronRight className="w-3.5 h-3.5" />
                <span className="font-semibold text-[var(--color-text-primary)]">
                  {page === "ledger" ? "Forensic Ledger" : page === "telephony" ? "Live Intercept" : "Documentation"}
                </span>
              </div>
              <div className="flex items-center gap-3">
                <div className={`w-2 h-2 rounded-full ${online ? 'bg-[var(--color-authentic)]' : 'bg-[var(--color-threat-critical)]'}`}></div>
                <span className="text-xs font-medium">{online ? "Backend Connected" : "Backend Offline"}</span>
                <span className="px-2 py-0.5 rounded bg-[var(--color-surface-layer)] border border-[var(--color-border-subtle)] text-[10px] font-mono text-[var(--color-text-tertiary)]">LOCAL DEMO</span>
              </div>
            </header>

            <div className="p-8 max-w-6xl mx-auto">
              <div className="mb-8">
                <h1 className="text-3xl font-bold text-[var(--color-text-primary)] tracking-tight mb-2">
                  {page === "ledger" ? "Forensic Audit Ledger" : page === "telephony" ? "Live SIP Intercept" : "VoiceShield Documentation"}
                </h1>
                <p className="text-[var(--color-text-secondary)]">
                  {page === "ledger" ? "Immutable SHA-256 hash chain and origin decision signatures." :
                   page === "telephony" ? "Connect to Asterisk PBX over SIP/WSS and monitor live phone calls." :
                   "System architecture and compliance overview."}
                </p>
              </div>

              {error && (
                <div className="mb-6 p-4 rounded-lg bg-[var(--color-threat-critical-dim)] border border-[var(--color-border-threat)] text-[var(--color-threat-critical)] text-sm flex items-start gap-3">
                  <AlertTriangle className="w-5 h-5 shrink-0" />
                  <div>{error}</div>
                </div>
              )}

              {page === "ledger" && (
                <LedgerPanel
                  entries={ledger}
                  calls={calls}
                  onVerify={verify}
                  verification={verification}
                  verifying={verifying}
                />
              )}

              {page === "telephony" && (
                <div className="flex flex-col items-center justify-center p-12 text-center border border-[var(--color-border-subtle)] rounded-2xl bg-[var(--color-surface-glass)]">
                  <div className="w-16 h-16 rounded-2xl bg-[var(--color-accent-indigo-dim)] border border-[var(--color-accent-indigo)] flex items-center justify-center mb-6 shadow-[0_0_30px_rgba(99,102,241,0.2)]">
                    <Radio className="w-8 h-8 text-[var(--color-accent-indigo)]" />
                  </div>
                  <h2 className="text-2xl font-bold text-[var(--color-text-primary)] mb-3">Real-Time Two-Way AI Voice Call</h2>
                  <p className="text-[var(--color-text-secondary)] max-w-lg mb-8">
                    Initiate an interactive live conversation with an AI Scammer bot. Speak into your microphone, hear the synthetic voice reply, and observe VoxGuard's real-time deepfake detection, risk scoring, and automated policy blocking.
                  </p>
                  <button
                    onClick={() => setTwoWayCallOpen(true)}
                    className="flex items-center gap-2 px-6 py-3 rounded-xl bg-[var(--color-accent-indigo)] text-white font-semibold hover:bg-indigo-600 transition-colors shadow-lg shadow-indigo-500/20"
                  >
                    <Activity className="w-5 h-5" /> Launch 2-Way Live Call
                  </button>
                </div>
              )}

              {page === "guide" && (
                <div className="border border-[var(--color-border-subtle)] rounded-2xl bg-[var(--color-surface-glass)] p-8">
                  <h2 className="text-xl font-bold text-[var(--color-text-primary)] mb-4">System Architecture & Compliance</h2>
                  <p className="text-[var(--color-text-secondary)] mb-8">
                    VoiceShield AI provides a real-time, explainable, and provably tamper-evident defense against generative voice cloning and impersonation attacks.
                  </p>

                  <div className="grid grid-cols-2 gap-6">
                    <div className="bg-[var(--color-surface-layer)] p-6 rounded-xl border border-[var(--color-border-subtle)]">
                      <h3 className="font-semibold text-[var(--color-text-primary)] mb-4 flex items-center gap-2">
                        <Shield className="w-4 h-4 text-[var(--color-authentic)]" /> Core Invariants
                      </h3>
                      <ul className="space-y-3 text-sm text-[var(--color-text-secondary)]">
                        <li className="flex items-start gap-2"><div className="w-1.5 h-1.5 rounded-full bg-[var(--color-accent-blue)] mt-1.5 shrink-0"></div> Zero raw audio touches disk or broker logs.</li>
                        <li className="flex items-start gap-2"><div className="w-1.5 h-1.5 rounded-full bg-[var(--color-accent-blue)] mt-1.5 shrink-0"></div> Floors applied last via max() to prevent masking attacks.</li>
                        <li className="flex items-start gap-2"><div className="w-1.5 h-1.5 rounded-full bg-[var(--color-accent-blue)] mt-1.5 shrink-0"></div> Absence of evidence is UNKNOWN, never LOW.</li>
                        <li className="flex items-start gap-2"><div className="w-1.5 h-1.5 rounded-full bg-[var(--color-accent-blue)] mt-1.5 shrink-0"></div> Origin cryptographic signing on all decisions.</li>
                      </ul>
                    </div>

                    <div className="bg-[var(--color-surface-layer)] p-6 rounded-xl border border-[var(--color-border-subtle)]">
                      <h3 className="font-semibold text-[var(--color-text-primary)] mb-4 flex items-center gap-2">
                        <CheckCircle className="w-4 h-4 text-[var(--color-safe)]" /> Compliance & Redress
                      </h3>
                      <ul className="space-y-3 text-sm text-[var(--color-text-secondary)]">
                        <li className="flex items-start gap-2"><div className="w-1.5 h-1.5 rounded-full bg-[var(--color-safe)] mt-1.5 shrink-0"></div> Complies with India DPDP Act & GDPR biometric constraints.</li>
                        <li className="flex items-start gap-2"><div className="w-1.5 h-1.5 rounded-full bg-[var(--color-safe)] mt-1.5 shrink-0"></div> Integrated customer dispute & appeal filing workflow.</li>
                        <li className="flex items-start gap-2"><div className="w-1.5 h-1.5 rounded-full bg-[var(--color-safe)] mt-1.5 shrink-0"></div> Closed-loop analyst resolution for continuous feedback.</li>
                      </ul>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </main>

      {renderModals()}
    </div>
  );
}
