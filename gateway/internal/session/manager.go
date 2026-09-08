// Package session is the Session Orchestrator (docs/01 section 2, [Go]).
//
// It owns one call's lifecycle: it pulls window analyses from the Python sidecar, fuses them,
// folds them into the session verdict, asks the Decision Service for an action, raises at most
// one alert per escalation, and writes the audit trail.
//
// It never holds audio. The sidecar returns analyses; see internal/sidecar for why.
package session

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/vox-guard/voxguard/gateway/internal/alerts"
	"github.com/vox-guard/voxguard/gateway/internal/decision"
	"github.com/vox-guard/voxguard/gateway/internal/ledger"
	"github.com/vox-guard/voxguard/gateway/internal/policy"
	"github.com/vox-guard/voxguard/gateway/internal/scoring"
	"github.com/vox-guard/voxguard/gateway/internal/sidecar"
	"github.com/vox-guard/voxguard/gateway/internal/telephony"
)

// MaxConcurrentStreams bounds simultaneous demo calls.
const MaxConcurrentStreams = 20

// MaxRetainedCalls bounds the in-memory call history.
const MaxRetainedCalls = 100

// Event is one message pushed to the dashboard over the WebSocket.
type Event struct {
	Type           string         `json:"type"`
	Chunk          int            `json:"chunk,omitempty"`
	Scored         *bool          `json:"scored,omitempty"`
	Reason         string         `json:"reason,omitempty"`
	LatencyMS      float64        `json:"latency_ms,omitempty"`
	Features       map[string]any `json:"features,omitempty"`
	SpeechRatio    float64        `json:"speech_ratio,omitempty"`
	Call           *PublicCall    `json:"call"`
	SessionSummary *Summary       `json:"session_summary,omitempty"`
}

// PublicCall is the call state as the dashboard sees it.
//
// It is a separate type from Call on purpose: the internal state holds the session aggregator and
// the raw event log, and neither belongs on the wire.
type PublicCall struct {
	CallID    string `json:"call_id"`
	Label     string `json:"label"`
	Filename  string `json:"filename"`
	Status    string `json:"status"`
	StartedAt string `json:"started_at"`
	Error     string `json:"error,omitempty"`

	RiskScore         *float64 `json:"risk_score"`
	AuthenticityScore *float64 `json:"authenticity_score"`
	Band              string   `json:"band"`
	PeakScore         float64  `json:"peak_score"`
	EWMAScore         *float64 `json:"ewma_score"`

	Decision        string           `json:"decision"`
	DecisionDetails *decision.Record `json:"decision_details"`

	History       []float64           `json:"history"`
	BandTimeline  []scoring.BandPoint `json:"band_timeline"`
	EscalationSeq int                 `json:"escalation_seq"`

	ChunksProcessed   int `json:"chunks_processed"`
	WindowsScored     int `json:"windows_scored"`
	UnassessedWindows int `json:"unassessed_windows"`
	DroppedChunks     int `json:"dropped_chunks"`

	LatencyMS *float64 `json:"latency_ms"`

	Degraded            bool                    `json:"degraded"`
	DegradedReasons     []string                `json:"degraded_reasons"`
	ContributingFactors []scoring.Factor        `json:"contributing_factors"`
	AppliedFloors       []scoring.Floor         `json:"applied_floors"`
	ContextDetails      []scoring.ContextDetail `json:"context_details"`

	Latest map[string]any `json:"latest"`

	Language          string `json:"language"`
	LanguageSupported bool   `json:"language_supported"`
	IdentityID        string `json:"identity_id,omitempty"`

	SimulateDetectorFailure  bool `json:"simulate_detector_failure"`
	SimulateAdversarialInput bool `json:"simulate_adversarial_input"`

	PolicyVersion string            `json:"policy_version"`
	ModelVersions map[string]string `json:"model_versions"`

	SessionSummary *Summary `json:"session_summary"`
}

// Summary is the end-of-call record (docs/02 section 2, docs/04 section 7).
type Summary struct {
	SessionID   string `json:"session_id"`
	Label       string `json:"label"`
	Filename    string `json:"filename"`
	Status      string `json:"status"`
	StartedAt   string `json:"started_at"`
	CompletedAt string `json:"completed_at"`

	ChunksProcessed   int `json:"chunks_processed"`
	WindowsScored     int `json:"windows_scored"`
	UnassessedWindows int `json:"unassessed_windows"`

	PeakScore         float64          `json:"peak_score"`
	FinalSessionScore *float64         `json:"final_session_score"`
	FinalBand         string           `json:"final_band"`
	FinalDecision     string           `json:"final_decision"`
	DecisionDetails   *decision.Record `json:"decision_details"`

	EscalationCount int      `json:"escalation_count"`
	Degraded        bool     `json:"degraded"`
	DegradedReasons []string `json:"degraded_reasons"`

	BandTimeline     []scoring.BandPoint `json:"band_timeline"`
	EnrolledIdentity string              `json:"enrolled_identity,omitempty"`

	PolicyVersion string            `json:"policy_version"`
	ModelVersions map[string]string `json:"model_versions"`
}

// Call is the internal per-call state.
type Call struct {
	mu     sync.RWMutex
	public PublicCall
	risk   *scoring.SessionRisk
	ctx    *scoring.Context
	opts   StartOptions

	events *eventLog
	cancel context.CancelFunc
}

// eventLog is an append-only log with a broadcast channel.
//
// A sync.Cond is the obvious choice and the wrong one here: the call's state is guarded by an
// RWMutex, and a Cond built on its read-locker cannot be waited on by a writer. Publishing a
// fresh channel per append keeps the notification independent of whichever lock the writer holds.
type eventLog struct {
	mu      sync.Mutex
	entries []Event
	changed chan struct{}
}

func newEventLog() *eventLog { return &eventLog{changed: make(chan struct{})} }

func (l *eventLog) append(e Event) {
	l.mu.Lock()
	l.entries = append(l.entries, e)
	l.mu.Unlock()
	l.wake()
}

// wake releases every waiter so it re-reads the log. Called on state changes that are not
// themselves events, such as a stop, so a blocked reader notices the call ended.
func (l *eventLog) wake() {
	l.mu.Lock()
	previous := l.changed
	l.changed = make(chan struct{})
	l.mu.Unlock()
	close(previous)
}

// since returns entries from cursor onward, blocking until at least one exists or ctx ends.
func (l *eventLog) since(ctx context.Context, cursor int) ([]Event, bool) {
	for {
		l.mu.Lock()
		if len(l.entries) > cursor {
			out := make([]Event, len(l.entries)-cursor)
			copy(out, l.entries[cursor:])
			l.mu.Unlock()
			return out, true
		}
		wait := l.changed
		l.mu.Unlock()

		select {
		case <-wait:
		case <-ctx.Done():
			return nil, false
		}
	}
}

// Manager owns every call in this process.
type Manager struct {
	mu      sync.RWMutex
	calls   map[string]*Call
	ordered []*Call

	sidecar   *sidecar.Client
	decisions *decision.Service
	alerts    *alerts.Store
	ledger    *ledger.Ledger
	pack      policy.Pack
	blocker   telephony.Blocker

	// onLedgerError is called when an audit write fails. Audit is an interface, not a dependency
	// (docs/01 section 1.5): a ledger outage must not stop the call being scored, but it must not
	// pass unnoticed either.
	onLedgerError func(error)
}

// SetBlocker attaches the transport-specific call terminator. It is deliberately
// configured separately from NewManager so existing embedders keep the same
// construction contract and can opt into their appropriate PBX implementation.
func (m *Manager) SetBlocker(blocker telephony.Blocker) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.blocker = blocker
}

// NewManager builds the orchestrator.
func NewManager(sc *sidecar.Client, ds *decision.Service, as *alerts.Store, l *ledger.Ledger,
	pack policy.Pack, onLedgerError func(error)) *Manager {
	if onLedgerError == nil {
		onLedgerError = func(error) {}
	}
	return &Manager{
		calls: map[string]*Call{}, sidecar: sc, decisions: ds, alerts: as,
		ledger: l, pack: pack, onLedgerError: onLedgerError,
	}
}

// StartOptions describes a call to open.
type StartOptions struct {
	Filename                 string
	Label                    string
	Interval                 time.Duration
	Language                 string
	IdentityID               string
	SimulateDetectorFailure  bool
	SimulateAdversarialInput bool
	Context                  *scoring.Context
}

// ErrTooManyStreams reports the concurrent-call cap.
var ErrTooManyStreams = fmt.Errorf("%d simultaneous demo calls are supported", MaxConcurrentStreams)

// ErrNotFound reports an unknown call id.
var ErrNotFound = fmt.Errorf("call not found")

// ErrDuplicate reports an AudioSocket UUID that is already active or retained.
var ErrDuplicate = fmt.Errorf("call already exists")

// ErrOutOfOrder reports a replayed or reordered derived window.
var ErrOutOfOrder = fmt.Errorf("window is out of order")

// Start opens a call and begins streaming it in the background.
func (m *Manager) Start(ctx context.Context, opts StartOptions) (string, error) {
	m.mu.Lock()
	streaming := 0
	for _, c := range m.calls {
		if c.snapshotStatus() == "streaming" {
			streaming++
		}
	}
	if streaming >= MaxConcurrentStreams {
		m.mu.Unlock()
		return "", ErrTooManyStreams
	}
	m.mu.Unlock()

	handle, err := m.sidecar.OpenStream(ctx, opts.Filename, opts.IdentityID)
	if err != nil {
		return "", err
	}

	callID := uuid.NewString()
	verifier := "not_enrolled"
	if opts.IdentityID != "" {
		verifier = handle.VerifierVersion
	}
	modelVersions := map[string]string{
		"detector":   handle.ModelVersion,
		"calibrator": handle.CalibratorVersion,
		"verifier":   verifier,
	}

	call := &Call{
		risk:   scoring.NewSessionRisk(callID),
		ctx:    opts.Context,
		opts:   opts,
		events: newEventLog(),
		public: PublicCall{
			CallID:                   callID,
			Label:                    opts.Label,
			Filename:                 opts.Filename,
			Status:                   "streaming",
			StartedAt:                nowISO(),
			Band:                     "UNKNOWN",
			Decision:                 "WARN",
			History:                  []float64{},
			BandTimeline:             []scoring.BandPoint{{Window: 0, Band: "UNKNOWN"}},
			DegradedReasons:          []string{},
			ContributingFactors:      []scoring.Factor{},
			AppliedFloors:            []scoring.Floor{},
			ContextDetails:           []scoring.ContextDetail{},
			Language:                 opts.Language,
			LanguageSupported:        m.pack.SupportsLanguage(opts.Language),
			IdentityID:               opts.IdentityID,
			SimulateDetectorFailure:  opts.SimulateDetectorFailure,
			SimulateAdversarialInput: opts.SimulateAdversarialInput,
			PolicyVersion:            m.pack.Version,
			ModelVersions:            modelVersions,
		},
	}
	// cancel is set before the call becomes reachable. Publishing the call first and assigning
	// cancel afterwards would let a concurrent Stop read the field while it is being written.
	runCtx, cancel := context.WithCancel(context.Background())
	call.cancel = cancel

	m.mu.Lock()
	m.evictLocked()
	m.calls[callID] = call
	m.ordered = append(m.ordered, call)
	m.mu.Unlock()

	go m.run(runCtx, call, handle.StreamID, opts)

	return callID, nil
}

// StartLive registers a call whose raw media is owned by the Python AudioSocket listener. Only
// model metadata and later derived windows cross into this Go process.
func (m *Manager) StartLive(callID string, opts StartOptions, modelVersions map[string]string) error {
	if _, err := uuid.Parse(callID); err != nil {
		return fmt.Errorf("call id must be a canonical UUID: %w", err)
	}
	if opts.Filename == "" {
		opts.Filename = "live:asterisk-audiosocket"
	}

	m.mu.Lock()
	defer m.mu.Unlock()
	if _, exists := m.calls[callID]; exists {
		return ErrDuplicate
	}
	streaming := 0
	for _, c := range m.calls {
		if c.snapshotStatus() == "streaming" {
			streaming++
		}
	}
	if streaming >= MaxConcurrentStreams {
		return ErrTooManyStreams
	}

	call := &Call{
		risk: scoring.NewSessionRisk(callID), ctx: opts.Context, opts: opts, events: newEventLog(),
		public: PublicCall{
			CallID: callID, Label: opts.Label, Filename: opts.Filename, Status: "streaming",
			StartedAt: nowISO(), Band: "UNKNOWN", Decision: "WARN", History: []float64{},
			BandTimeline:    []scoring.BandPoint{{Window: 0, Band: "UNKNOWN"}},
			DegradedReasons: []string{}, ContributingFactors: []scoring.Factor{},
			AppliedFloors: []scoring.Floor{}, ContextDetails: []scoring.ContextDetail{},
			Language: opts.Language, LanguageSupported: m.pack.SupportsLanguage(opts.Language),
			IdentityID: opts.IdentityID, PolicyVersion: m.pack.Version, ModelVersions: modelVersions,
		},
	}
	m.evictLocked()
	m.calls[callID] = call
	m.ordered = append(m.ordered, call)
	return nil
}

// PushLiveWindow applies one derived Python analysis to a live session.
func (m *Manager) PushLiveWindow(callID string, window sidecar.Window) (PublicCall, error) {
	call, err := m.Get(callID)
	if err != nil {
		return PublicCall{}, err
	}
	call.mu.RLock()
	status := call.public.Status
	lastChunk := call.public.ChunksProcessed
	opts := call.opts
	call.mu.RUnlock()
	if status != "streaming" {
		return PublicCall{}, fmt.Errorf("call is not streaming")
	}
	if window.ChunkIndex <= lastChunk {
		return PublicCall{}, ErrOutOfOrder
	}
	if skipped := window.ChunkIndex - lastChunk - 1; skipped > 0 {
		call.mu.Lock()
		call.public.DroppedChunks += skipped
		call.mu.Unlock()
	}
	m.foldWindow(call, window, opts)
	return call.Public(), nil
}

// FinishLive completes a live call and emits the same summary/audit event as fixture streaming.
func (m *Manager) FinishLive(callID string) (Summary, error) {
	call, err := m.Get(callID)
	if err != nil {
		return Summary{}, err
	}
	call.mu.RLock()
	alreadyFinished := call.public.Status != "streaming"
	existing := call.public.SessionSummary
	call.mu.RUnlock()
	if alreadyFinished && existing != nil {
		return *existing, nil
	}
	call.finish(m)
	call.mu.RLock()
	defer call.mu.RUnlock()
	if call.public.SessionSummary != nil {
		return *call.public.SessionSummary, nil
	}
	return call.summaryLocked(m.pack), nil
}

// evictLocked drops the oldest finished call once the history cap is reached.
func (m *Manager) evictLocked() {
	if len(m.calls) < MaxRetainedCalls {
		return
	}
	for i, c := range m.ordered {
		if c.snapshotStatus() != "streaming" {
			delete(m.calls, c.public.CallID)
			m.ordered = append(m.ordered[:i], m.ordered[i+1:]...)
			return
		}
	}
}

// run is the per-call streaming loop.
func (m *Manager) run(ctx context.Context, call *Call, streamID string, opts StartOptions) {
	defer func() {
		closeCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		m.sidecar.CloseStream(closeCtx, streamID)
		cancel()
	}()

	ticker := time.NewTicker(opts.Interval)
	defer ticker.Stop()

	for {
		if ctx.Err() != nil || call.snapshotStatus() == "stopped" {
			break
		}
		window, err := m.sidecar.NextWindow(ctx, streamID)
		if err != nil {
			if ctx.Err() != nil {
				break
			}
			call.fail(fmt.Sprintf("Audio processing failed: %v", err))
			break
		}
		if window.Exhausted {
			break
		}
		m.foldWindow(call, window, opts)

		select {
		case <-ctx.Done():
		case <-ticker.C:
		}
	}

	call.finish(m)
}

// foldWindow turns one sidecar analysis into a scored window, a session verdict, a decision, and
// the audit records that go with them.
func (m *Manager) foldWindow(call *Call, window sidecar.Window, opts StartOptions) {
	call.mu.Lock()
	call.public.ChunksProcessed = window.ChunkIndex
	latency := window.LatencyMS
	call.public.LatencyMS = &latency

	if !window.Scored {
		call.public.DroppedChunks++
		public := call.public
		call.mu.Unlock()
		scored := false
		call.emit(Event{
			Type: "chunk", Chunk: window.ChunkIndex, Scored: &scored,
			Reason: window.Reason, LatencyMS: window.LatencyMS, Call: &public,
		})
		return
	}
	call.mu.Unlock()

	// The detector's own output is discarded when the chaos toggle is on, so the fusion sees a
	// genuinely absent signal rather than a fabricated one. That is the whole point of the
	// toggle: it must exercise the degraded path, not a mock of it.
	var det *scoring.Detection
	if !opts.SimulateDetectorFailure {
		supported := m.pack.SupportsLanguage(opts.Language)
		det = &scoring.Detection{
			PSynthetic:        window.PSynthetic,
			Confidence:        window.Confidence,
			LanguageSupported: &supported,
			VoicedSeconds:     window.VoicedSeconds,
			AdversarialFlag:   opts.SimulateAdversarialInput,
		}
	}

	var ver *scoring.Verification
	if window.Verification != nil {
		v := window.Verification
		ver = &scoring.Verification{
			Failed:             v.Failed,
			ReferenceAvailable: v.ReferenceAvailable,
			MatchScore:         v.MatchScore,
			VoicedSecondsUsed:  v.VoicedSecondsUsed,
			ReplaySuspected:    v.ReplaySuspected,
			Confidence:         v.Confidence,
			ModelVersion:       v.ModelVersion,
			SpeakerTrack:       v.SpeakerTrack,
			EnrolledIdentity:   v.EnrolledIdentity,
		}
	}

	// Intent is derived for each audio window, whereas call.ctx is session-level
	// context supplied at call creation. Pass it separately so an old I_risk
	// cannot bleed into later windows and a missing context remains degraded.
	result, err := scoring.ScoreWindowWithIntent(det, call.ctx, ver, window.IRisk, m.pack)
	if err != nil {
		// A match_score contract violation is a bug in the verifier, not a scoreable condition.
		// Failing the call is correct: silently coercing it is what CLAUDE.md invariant 7 forbids.
		call.fail(fmt.Sprintf("Scoring contract violated: %v", err))
		return
	}

	call.mu.Lock()
	verdict := call.risk.Update(result, m.pack)
	call.public.RiskScore = verdict.SessionScore
	call.public.AuthenticityScore = verdict.AuthenticityScore
	call.public.Band = verdict.Band
	call.public.PeakScore = verdict.PeakScore
	call.public.EWMAScore = verdict.EWMAScore
	call.public.History = verdict.History
	call.public.BandTimeline = verdict.BandTimeline
	call.public.EscalationSeq = verdict.EscalationSeq
	call.public.Degraded = result.Degraded
	call.public.DegradedReasons = result.DegradedReasons
	call.public.ContributingFactors = result.ContributingFactors
	call.public.AppliedFloors = result.AppliedFloors
	call.public.ContextDetails = result.ContextDetails

	dec := m.decisions.Decide(decision.Verdict{
		SessionID: call.public.CallID, Band: verdict.Band,
		SessionScore: verdict.SessionScore, Degraded: result.Degraded,
	}, call.ctx, m.pack)
	call.public.Decision = dec.Decision
	call.public.DecisionDetails = &dec

	call.public.Latest = latestPayload(window, result, dec, opts)

	if result.WindowScore == nil {
		call.public.UnassessedWindows++
	} else {
		call.public.WindowsScored++
	}
	public := call.public
	modelVersions := call.public.ModelVersions
	call.mu.Unlock()

	if result.WindowScore != nil {
		m.audit(map[string]any{
			"call_id": public.CallID, "event_type": "observation",
			"risk_score": derefScore(public.RiskScore), "band": public.Band,
			"policy_version": m.pack.Version, "model_versions": toAny(modelVersions),
		})
	}

	// A BLOCK decision must be sealed before the call is terminated. Otherwise a
	// successful hangup can erase the only evidence that the gateway made the
	// decision. The call is marked stopped only after the blocker succeeds; a
	// failed hangup remains visible to the operator as a live, high-risk call.
	if dec.Decision == "BLOCK" {
		m.audit(map[string]any{
			"call_id": public.CallID, "event_type": "block_decision",
			"risk_score": derefScore(public.RiskScore), "band": public.Band,
			"policy_version": m.pack.Version, "model_versions": toAny(modelVersions),
			"decision": dec.Decision, "reason_code": dec.ReasonCode,
		})
		if m.enforceBlock(public.CallID, dec.ReasonCode) {
			public, _ = m.Stop(public.CallID)
		}
	}

	// CLAUDE.md invariant 11: the alert is keyed by the escalation, so a session that holds HIGH
	// for the rest of the call raises nothing further.
	if verdict.AlertKey != nil {
		m.ensureAlert(*verdict.AlertKey, public, modelVersions)
	}

	scored := true
	call.emit(Event{
		Type: "chunk", Chunk: window.ChunkIndex, Scored: &scored,
		LatencyMS: window.LatencyMS, Features: window.Features,
		SpeechRatio: window.SpeechRatio, Call: &public,
	})
}

// enforceBlock terminates a call after its decision record has been appended.
// Returning false deliberately leaves the call active: displaying it as blocked
// when the PBX hangup failed would mislead the operator.
func (m *Manager) enforceBlock(callID, reason string) bool {
	m.mu.RLock()
	blocker := m.blocker
	m.mu.RUnlock()
	if blocker == nil {
		m.audit(map[string]any{
			"call_id": callID, "event_type": "block_not_enforced",
			"reason": "no_call_blocker_configured", "policy_version": m.pack.Version,
		})
		return false
	}

	err := telephony.EnforceBlock(context.Background(), blocker, callID, reason,
		func(id, why string, failure error) {
			m.audit(map[string]any{
				"call_id": id, "event_type": "block_hangup_failed", "reason": why,
				"error": failure.Error(), "policy_version": m.pack.Version,
			})
		}, nil)
	return err == nil
}

// latestPayload assembles the per-window detail the dashboard renders.
//
// When the detector is simulated as failed its scores are omitted rather than passed through:
// showing a number the fusion did not use would be a lie on the operator's screen.
func latestPayload(window sidecar.Window, result *scoring.WindowResult, dec decision.Record,
	opts StartOptions) map[string]any {

	out := map[string]any{
		"scored":       true,
		"latency_ms":   window.LatencyMS,
		"features":     window.Features,
		"speech_ratio": window.SpeechRatio,
	}
	if !opts.SimulateDetectorFailure {
		out["spectral_score"] = window.SpectralScore
		out["prosody_score"] = window.ProsodyScore
		out["synthetic_score"] = window.SyntheticScore
		out["p_synthetic"] = window.PSynthetic
		out["p_synthetic_raw"] = window.PSyntheticRaw
		out["confidence"] = window.Confidence
		out["classification"] = window.Classification
		out["classification_note"] = window.ClassificationNote
		out["speaker_match_score"] = window.SpeakerMatchScore
		out["speaker_status"] = window.SpeakerStatus
		out["model"] = window.Model
		out["model_version"] = window.ModelVersion
		out["calibrator_version"] = window.CalibratorVersion
		out["voiced_seconds"] = window.VoicedSeconds
		out["language"] = opts.Language
		out["adversarial_flag"] = opts.SimulateAdversarialInput
		out["adversarial_flag_source"] = "none"
		if opts.SimulateAdversarialInput {
			out["adversarial_flag_source"] = "simulated"
		}
	}

	// Merge the fusion result so the screen shows the score next to the evidence that produced it.
	buf, _ := json.Marshal(result)
	var fused map[string]any
	json.Unmarshal(buf, &fused)
	for k, v := range fused {
		out[k] = v
	}
	out["decision"] = dec
	return out
}

func (m *Manager) ensureAlert(key string, call PublicCall, modelVersions map[string]string) {
	score := derefScore(call.RiskScore)
	_, created := m.alerts.Ensure(key, call.CallID, score, call.Band, m.pack.Version, modelVersions)
	if !created {
		return
	}
	m.audit(map[string]any{
		"call_id": call.CallID, "event_type": "alert",
		"risk_score": score, "band": call.Band,
		"policy_version": m.pack.Version, "model_versions": toAny(modelVersions),
	})
}

// EnsureManualAlert raises an alert on operator request. It returns an error when the session has
// not actually escalated, so the dashboard cannot manufacture an alert out of a quiet call.
func (m *Manager) EnsureManualAlert(callID string) (*alerts.Alert, error) {
	call, err := m.Get(callID)
	if err != nil {
		return nil, err
	}
	public := call.Public()
	if public.Band != "MEDIUM" && public.Band != "HIGH" {
		return nil, fmt.Errorf("the session has not reached an elevated risk band")
	}
	key := fmt.Sprintf("manual:%s:%s:%d", public.CallID, public.Band, public.EscalationSeq)
	alert, created := m.alerts.Ensure(key, public.CallID, derefScore(public.RiskScore),
		public.Band, m.pack.Version, public.ModelVersions)
	if created {
		m.audit(map[string]any{
			"call_id": public.CallID, "event_type": "alert",
			"risk_score": derefScore(public.RiskScore), "band": public.Band,
			"policy_version": m.pack.Version, "model_versions": toAny(public.ModelVersions),
		})
	}
	return alert, nil
}

// Audit appends one record, reporting a failure rather than swallowing it.
func (m *Manager) Audit(event map[string]any) { m.audit(event) }

func (m *Manager) audit(event map[string]any) {
	if _, err := m.ledger.Append(event); err != nil {
		m.onLedgerError(err)
	}
}

// Get returns one call.
func (m *Manager) Get(callID string) (*Call, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	c, ok := m.calls[callID]
	if !ok {
		return nil, ErrNotFound
	}
	return c, nil
}

// List returns every call, newest first.
func (m *Manager) List() []PublicCall {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := make([]PublicCall, 0, len(m.ordered))
	for i := len(m.ordered) - 1; i >= 0; i-- {
		out = append(out, m.ordered[i].Public())
	}
	return out
}

// Stop halts a streaming call and returns its state.
func (m *Manager) Stop(callID string) (PublicCall, error) {
	call, err := m.Get(callID)
	if err != nil {
		return PublicCall{}, err
	}
	call.mu.Lock()
	if call.public.Status == "streaming" {
		call.public.Status = "stopped"
		summary := call.summaryLocked(m.pack)
		call.public.SessionSummary = &summary
	}
	public := call.public
	call.mu.Unlock()
	if call.cancel != nil {
		call.cancel()
	}
	call.events.wake()
	return public, nil
}

// Close stops the call and returns the session summary, writing the closing audit record.
func (m *Manager) Close(callID string) (Summary, error) {
	call, err := m.Get(callID)
	if err != nil {
		return Summary{}, err
	}
	call.mu.Lock()
	if call.public.Status == "streaming" {
		call.public.Status = "stopped"
	}
	summary := call.summaryLocked(m.pack)
	call.public.SessionSummary = &summary
	modelVersions := call.public.ModelVersions
	call.mu.Unlock()
	if call.cancel != nil {
		call.cancel()
	}

	m.audit(map[string]any{
		"call_id": callID, "event_type": "call_completed",
		"risk_score": derefScore(summary.FinalSessionScore), "band": summary.FinalBand,
		"policy_version": m.pack.Version, "model_versions": toAny(modelVersions),
	})
	call.events.wake()
	return summary, nil
}

// SetDecision records a supervisor override on the call state.
func (m *Manager) SetDecision(callID string, record decision.Record) error {
	call, err := m.Get(callID)
	if err != nil {
		return err
	}
	call.mu.Lock()
	call.public.Decision = record.Decision
	call.public.DecisionDetails = &record
	call.mu.Unlock()
	call.events.wake()
	return nil
}

// Shutdown cancels every running call.
func (m *Manager) Shutdown() {
	m.mu.RLock()
	defer m.mu.RUnlock()
	for _, c := range m.calls {
		if c.cancel != nil {
			c.cancel()
		}
		c.events.wake()
	}
}

// --- Call ------------------------------------------------------------------------------------

// Public returns a copy of the call's dashboard state.
func (c *Call) Public() PublicCall {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.public
}

// Context returns the call's scoring context.
func (c *Call) Context() *scoring.Context {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.ctx
}

func (c *Call) snapshotStatus() string {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.public.Status
}

func (c *Call) fail(message string) {
	c.mu.Lock()
	c.public.Status = "error"
	c.public.Error = message
	c.mu.Unlock()
	c.events.wake()
}

func (c *Call) finish(m *Manager) {
	c.mu.Lock()
	if c.public.Status == "streaming" {
		c.public.Status = "completed"
	}
	var summary *Summary
	if c.public.RiskScore != nil {
		s := c.summaryLocked(m.pack)
		c.public.SessionSummary = &s
		summary = &s
	}
	public := c.public
	modelVersions := c.public.ModelVersions
	c.mu.Unlock()

	if summary != nil {
		m.audit(map[string]any{
			"call_id": public.CallID, "event_type": "call_completed",
			"risk_score": derefScore(public.RiskScore), "band": public.Band,
			"policy_version": m.pack.Version, "model_versions": toAny(modelVersions),
		})
	}
	c.emit(Event{Type: "complete", Call: &public, SessionSummary: summary})
}

func (c *Call) summaryLocked(pack policy.Pack) Summary {
	return Summary{
		SessionID:         c.public.CallID,
		Label:             c.public.Label,
		Filename:          c.public.Filename,
		Status:            c.public.Status,
		StartedAt:         c.public.StartedAt,
		CompletedAt:       nowISO(),
		ChunksProcessed:   c.public.ChunksProcessed,
		WindowsScored:     c.public.WindowsScored,
		UnassessedWindows: c.public.UnassessedWindows,
		PeakScore:         c.public.PeakScore,
		FinalSessionScore: c.public.RiskScore,
		FinalBand:         c.public.Band,
		FinalDecision:     c.public.Decision,
		DecisionDetails:   c.public.DecisionDetails,
		EscalationCount:   c.public.EscalationSeq,
		Degraded:          c.public.Degraded,
		DegradedReasons:   c.public.DegradedReasons,
		BandTimeline:      c.public.BandTimeline,
		EnrolledIdentity:  c.public.IdentityID,
		PolicyVersion:     pack.Version,
		ModelVersions:     c.public.ModelVersions,
	}
}

func (c *Call) emit(event Event) { c.events.append(event) }

// Events returns events from cursor onward, blocking until at least one is available or the
// context is cancelled. A late-connecting dashboard receives the backlog rather than joining
// mid-call with no history.
func (c *Call) Events(ctx context.Context, cursor int) ([]Event, bool) {
	return c.events.since(ctx, cursor)
}

// --- helpers ---------------------------------------------------------------------------------

func nowISO() string { return time.Now().UTC().Format("2006-01-02T15:04:05.000000Z07:00") }

func derefScore(p *float64) float64 {
	if p == nil {
		return 0
	}
	return *p
}

func toAny(m map[string]string) map[string]any {
	out := make(map[string]any, len(m))
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		out[k] = m[k]
	}
	return out
}
