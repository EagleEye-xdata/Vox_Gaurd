import React, { useState, useEffect } from 'react';
import { Shield, Search, User, Activity, AlertCircle, Fingerprint, Zap, Square, CheckCircle, ChevronRight, FileText, Phone, Gavel } from 'lucide-react';

import AlertPopup from './AlertPopup';

export default function TelemetryConsole({ calls, selected, onSelect, ledger, alerts, onEscalate, onAssign, onResolve, onOpenAppeal, onStop, onOpenOverride, onOpenSummary, onOpenTwoWay, busy }) {
  const call = calls.find(c => c.call_id === selected) || calls[0];
  const isAttack = call?.band === "HIGH" || call?.band === "MEDIUM";

  const getAuthenticity = (c) => c?.scores ? (c.scores.human * 100).toFixed(1) : 0;
  const getArtifacts = (c) => c?.scores ? (c.scores.spoof * 100).toFixed(1) : 0;

  const displayCall = {
    callerName: call?.label || "Waiting for signal...",
    role: call?.filename || "No active stream",
    status: call?.status || "Idle",
    authenticityScore: getAuthenticity(call),
    artifactProbability: getArtifacts(call),
    acousticMatch: call?.speaker_verification?.reference_available ? Math.round(call.speaker_verification.match_score * 100) : 0,
    latency: call?.latest?.latency || 45,
    decision: call?.decision || call?.latest?.decision?.decision || "UNKNOWN"
  };

  return (
    <div className="min-h-screen bg-[var(--color-surface-base)] text-[var(--color-text-secondary)] font-sans flex flex-col overflow-hidden">

      {/* Header */}
      <header className="h-14 border-b border-[var(--color-border-subtle)] bg-[var(--color-surface-elevated)] backdrop-blur-md flex items-center justify-between px-6 shrink-0 z-10">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-[var(--color-authentic-dim)] flex items-center justify-center border border-[var(--color-border-authentic)]">
            <Shield className="w-5 h-5 text-[var(--color-authentic)]" />
          </div>
          <span className="font-semibold text-[var(--color-text-primary)] tracking-wide">VoiceShield AI</span>
        </div>

        <div className="flex items-center gap-3 bg-[var(--color-surface-layer)] rounded-full px-4 py-1.5 border border-[var(--color-border-subtle)]">
          <div className={`w-2 h-2 rounded-full ${call?.status === 'streaming' ? 'animate-pulse' : ''} ${isAttack ? 'bg-[var(--color-threat-critical)]' : 'bg-[var(--color-authentic)]'}`}></div>
          <span className="text-sm font-medium text-[var(--color-text-primary)]">System Online</span>
        </div>

        <div className="flex items-center gap-4">
          <button onClick={onOpenTwoWay} className="px-3 py-1.5 rounded-lg bg-[var(--color-accent-indigo-dim)] border border-[var(--color-border-subtle)] hover:bg-[var(--color-surface-glass)] transition-colors text-xs font-medium text-[var(--color-accent-indigo)] flex items-center gap-2">
            <Activity className="w-3.5 h-3.5" /> Launch 2-Way Call
          </button>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="flex-1 overflow-y-auto p-4 flex gap-4 h-[calc(100vh-56px)]">

        {/* Left Sidebar: Calls List */}
        <div className="w-72 flex flex-col gap-3 shrink-0">
          <div className="bg-[var(--color-surface-glass)] border border-[var(--color-border-subtle)] rounded-xl p-4 flex flex-col h-full">
            <h3 className="text-xs font-semibold text-[var(--color-text-tertiary)] uppercase tracking-wider mb-4 flex justify-between">
              Live Sessions <span className="bg-[var(--color-surface-layer)] px-2 py-0.5 rounded text-[var(--color-text-primary)]">{calls.length}</span>
            </h3>
            <div className="flex flex-col gap-2 overflow-y-auto flex-1 pr-1">
              {calls.length === 0 ? (
                 <div className="text-center py-8 text-[var(--color-text-tertiary)] text-xs">No active calls. Start a simulation.</div>
              ) : calls.map(c => (
                <button
                  key={c.call_id}
                  onClick={() => onSelect(c.call_id)}
                  className={`text-left p-3 rounded-lg border transition-all ${selected === c.call_id ? 'bg-[var(--color-surface-layer)] border-[var(--color-focus-ring)]' : 'bg-transparent border-[var(--color-border-subtle)] hover:border-[var(--color-text-tertiary)]'}`}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <Phone className="w-3.5 h-3.5 text-[var(--color-accent-blue)]" />
                    <span className="font-medium text-sm text-[var(--color-text-primary)] truncate">{c.label}</span>
                  </div>
                  <div className="flex items-center justify-between mt-2">
                    <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[var(--color-surface-base)] border border-[var(--color-border-subtle)] uppercase">
                      {c.status}
                    </span>
                    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${c.band === 'HIGH' ? 'bg-[var(--color-threat-critical-dim)] text-[var(--color-threat-critical)]' : c.band === 'MEDIUM' ? 'bg-[var(--color-threat-elevated-dim)] text-[var(--color-threat-elevated)]' : 'bg-[var(--color-authentic-dim)] text-[var(--color-authentic)]'}`}>
                      {c.band || "UNKNOWN"}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Right Main Area */}
        <div className="flex-1 flex flex-col gap-4 min-w-0">

          {/* Top Metrics Row */}
          <div className="grid grid-cols-4 gap-4 shrink-0">
            {/* Authenticity Score */}
            <div className="bg-[var(--color-surface-glass)] border border-[var(--color-border-subtle)] rounded-xl p-4 flex items-center gap-4">
              <div className="relative w-12 h-12 flex items-center justify-center">
                <svg className="w-12 h-12 transform -rotate-90">
                  <circle cx="24" cy="24" r="20" stroke="currentColor" strokeWidth="4" fill="transparent" className="text-white/5" />
                  <circle cx="24" cy="24" r="20" stroke="currentColor" strokeWidth="4" fill="transparent" strokeDasharray="125.6" strokeDashoffset={125.6 - (125.6 * (displayCall.authenticityScore / 100))} className="text-[var(--color-authentic)] transition-all duration-500" />
                </svg>
                <Activity className="w-4 h-4 text-[var(--color-authentic)] absolute" />
              </div>
              <div>
                <div className="text-[10px] text-[var(--color-text-secondary)] font-bold uppercase tracking-wider mb-1">Authenticity</div>
                <div className="text-xl font-semibold text-[var(--color-text-primary)] tracking-tight">{displayCall.authenticityScore}%</div>
              </div>
            </div>

            {/* Artifact Probability */}
            <div className="bg-[var(--color-surface-glass)] border border-[var(--color-border-subtle)] rounded-xl p-4 flex items-center gap-4">
              <div className={`w-12 h-12 rounded-full border flex items-center justify-center transition-colors ${isAttack ? 'bg-[var(--color-threat-critical-dim)] border-[var(--color-border-threat)]' : 'bg-[var(--color-surface-layer)] border-[var(--color-border-subtle)]'}`}>
                <AlertCircle className={`w-5 h-5 ${isAttack ? 'text-[var(--color-threat-critical)]' : 'text-[var(--color-accent-blue)]'}`} />
              </div>
              <div>
                <div className="text-[10px] text-[var(--color-text-secondary)] font-bold uppercase tracking-wider mb-1">AI Artifacts</div>
                <div className="text-xl font-semibold text-[var(--color-text-primary)] tracking-tight">{displayCall.artifactProbability}%</div>
              </div>
            </div>

            {/* Acoustic Match */}
            <div className="bg-[var(--color-surface-glass)] border border-[var(--color-border-subtle)] rounded-xl p-4 flex items-center gap-4">
              <div className={`w-12 h-12 rounded-full border flex items-center justify-center ${displayCall.acousticMatch > 0 ? 'bg-[var(--color-authentic-dim)] border-[var(--color-border-authentic)]' : 'bg-[var(--color-surface-layer)] border-[var(--color-border-subtle)]'}`}>
                <Fingerprint className={`w-5 h-5 ${displayCall.acousticMatch > 0 ? 'text-[var(--color-authentic)]' : 'text-[var(--color-text-secondary)]'}`} />
              </div>
              <div>
                <div className="text-[10px] text-[var(--color-text-secondary)] font-bold uppercase tracking-wider mb-1">Voice Match</div>
                <div className="text-xl font-semibold text-[var(--color-text-primary)] tracking-tight">{displayCall.acousticMatch > 0 ? `${displayCall.acousticMatch}%` : 'N/A'}</div>
              </div>
            </div>

            {/* Overall Risk */}
            <div className="bg-[var(--color-surface-glass)] border border-[var(--color-border-subtle)] rounded-xl p-4 flex items-center gap-4">
              <div className="w-12 h-12 rounded-full bg-[var(--color-surface-layer)] border border-[var(--color-border-subtle)] flex items-center justify-center">
                <Zap className={`w-5 h-5 ${isAttack ? 'text-[var(--color-threat-critical)]' : 'text-[var(--color-authentic)]'}`} />
              </div>
              <div>
                <div className="text-[10px] text-[var(--color-text-secondary)] font-bold uppercase tracking-wider mb-1">Risk Score</div>
                <div className="text-xl font-semibold text-[var(--color-text-primary)] tracking-tight">{call?.risk_score != null ? Math.round(call.risk_score) : '--'}/100</div>
              </div>
            </div>
          </div>

          {/* Decision Engine & Actions */}
          <div className="grid grid-cols-3 gap-4 h-full min-h-0">

            {/* Center Telemetry Card */}
            <div className="col-span-2 bg-[var(--color-surface-glass)] border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col min-h-0">
              <div className="flex items-center justify-between mb-6">
                <div>
                  <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
                    <Activity className="w-4 h-4 text-[var(--color-authentic)]" /> Acoustic Energy Envelope
                  </h2>
                  <p className="text-xs text-[var(--color-text-tertiary)]">{displayCall.callerName} • {displayCall.role}</p>
                </div>
                <div className="flex gap-2">
                  <div className="px-3 py-1 rounded-full bg-[var(--color-surface-layer)] border border-[var(--color-border-subtle)] text-[var(--color-text-secondary)] text-[10px] font-mono">
                    {displayCall.status.toUpperCase()}
                  </div>
                </div>
              </div>

              {/* Dynamic Waveform Plot using actual env data if available */}
              <div className="flex-1 relative rounded-xl bg-[var(--color-surface-base)] border border-[var(--color-border-subtle)] overflow-hidden p-4 min-h-[140px] flex items-end">
                <div className="absolute inset-0 bg-gradient-to-b from-transparent to-[var(--color-surface-layer)] pointer-events-none z-0"></div>
                <div className="relative z-10 w-full h-full flex items-end justify-between gap-0.5">
                  {(call?.latest?.features?.rms_envelope || [...Array(60).fill(0)]).map((val, i) => {
                     const h = Math.min(100, Math.max(5, val * 180));
                     return (
                       <div key={i} className={`flex-1 rounded-t-sm opacity-80 ${isAttack ? 'bg-[var(--color-threat-critical)]' : 'bg-[var(--color-accent-blue)]'}`} style={{ height: `${h}%`, transition: 'height 0.2s ease' }}></div>
                     )
                  })}
                </div>
              </div>
            </div>

            {/* Interventions Panel */}
            <div className="col-span-1 bg-[var(--color-surface-glass)] border border-[var(--color-border-subtle)] rounded-2xl p-5 flex flex-col gap-4 overflow-y-auto">
              <h3 className="text-xs font-semibold text-[var(--color-text-tertiary)] uppercase tracking-wider border-b border-[var(--color-border-subtle)] pb-2">Policy Decision</h3>

              <div className="flex flex-col gap-2">
                <div className={`p-4 rounded-xl border ${displayCall.decision === 'BLOCK' || displayCall.decision === 'ESCALATE' ? 'bg-[var(--color-threat-critical-dim)] border-[var(--color-border-threat)]' : 'bg-[var(--color-surface-layer)] border-[var(--color-border-subtle)]'}`}>
                  <div className="text-xs text-[var(--color-text-secondary)] mb-1">Automated Verdict</div>
                  <div className={`text-xl font-bold tracking-tight ${displayCall.decision === 'BLOCK' ? 'text-[var(--color-threat-critical)]' : 'text-[var(--color-text-primary)]'}`}>
                    {displayCall.decision}
                  </div>
                  {call?.error && <div className="text-xs text-[var(--color-threat-critical)] mt-2 font-mono">{call.error}</div>}
                  {call?.degraded && <div className="text-xs text-[var(--color-threat-elevated)] mt-2 font-mono">Assessment Degraded</div>}
                </div>
              </div>

              <div className="flex flex-col gap-2 mt-auto pt-4 border-t border-[var(--color-border-subtle)]">
                {call?.status === "streaming" && (
                  <button
                    disabled={busy}
                    onClick={onStop}
                    className="w-full py-2.5 rounded-lg bg-[var(--color-surface-layer)] border border-[var(--color-border-subtle)] text-[var(--color-text-primary)] text-sm font-semibold hover:bg-[var(--color-hover-overlay)] transition-colors flex justify-center items-center gap-2 disabled:opacity-50"
                  >
                    <Square className="w-4 h-4" /> Stop Stream
                  </button>
                )}

                {call && (
                  <button
                    onClick={() => onOpenOverride(call)}
                    className="w-full py-2.5 rounded-lg bg-[var(--color-threat-elevated-dim)] border border-[var(--color-threat-elevated)] text-[var(--color-threat-elevated)] text-sm font-semibold hover:bg-[var(--color-threat-elevated)] hover:text-[var(--color-surface-base)] transition-colors flex justify-center items-center gap-2"
                  >
                    <Gavel className="w-4 h-4" /> Manual Override
                  </button>
                )}

                {call?.status !== "streaming" && call?.windows_scored > 0 && (
                  <button
                    onClick={() => onOpenSummary(call)}
                    className="w-full py-2.5 rounded-lg bg-[var(--color-accent-indigo-dim)] border border-[var(--color-border-subtle)] text-[var(--color-accent-indigo)] text-sm font-semibold hover:bg-[var(--color-accent-indigo)] hover:text-white transition-colors flex justify-center items-center gap-2"
                  >
                    <FileText className="w-4 h-4" /> View Summary
                  </button>
                )}
              </div>
            </div>

          </div>

          {/* Alerts Row */}
          <div className="shrink-0 mt-4">
            <AlertPopup
              alerts={alerts || []}
              onEscalate={onEscalate}
              onAssign={onAssign}
              onResolve={onResolve}
              onOpenAppeal={onOpenAppeal}
              busy={busy}
            />
          </div>
        </div>
      </main>
    </div>
  );
}
