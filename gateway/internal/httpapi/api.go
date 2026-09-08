// Package httpapi is the Edge / API Gateway (docs/01 section 2, docs/02 API contracts, [Go]).
//
// It terminates the dashboard's HTTP and WebSocket traffic, validates request bodies, and routes
// to the Go services. The two audio-bearing endpoints are reverse-proxied to the Python sidecar
// rather than handled here, so caller audio is never a value in this process (CLAUDE.md
// invariant 1).
package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"

	"github.com/vox-guard/voxguard/gateway/internal/alerts"
	"github.com/vox-guard/voxguard/gateway/internal/decision"
	"github.com/vox-guard/voxguard/gateway/internal/ledger"
	"github.com/vox-guard/voxguard/gateway/internal/policy"
	"github.com/vox-guard/voxguard/gateway/internal/schema"
	"github.com/vox-guard/voxguard/gateway/internal/scoring"
	"github.com/vox-guard/voxguard/gateway/internal/session"
	"github.com/vox-guard/voxguard/gateway/internal/sidecar"
)

// Server wires the gateway's dependencies.
type Server struct {
	Sessions  *session.Manager
	Decisions *decision.Service
	Alerts    *alerts.Store
	Ledger    *ledger.Ledger
	Sidecar   *sidecar.Client
	Policy    policy.Pack
	Log       *slog.Logger

	// AllowedOrigins are the browser origins permitted to open a WebSocket. An empty list means
	// same-origin only.
	AllowedOrigins []string
}

// Routes builds the mux.
func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /api/v1/health", s.health)
	mux.HandleFunc("GET /api/v1/audio", s.audioFiles)
	mux.HandleFunc("GET /api/v1/calls", s.listCalls)

	mux.HandleFunc("POST /api/v1/stream/start", s.startStream)
	mux.HandleFunc("POST /api/v1/stream/{call_id}/stop", s.stopStream)
	mux.HandleFunc("POST /api/v1/sessions/{call_id}/close", s.closeSession)
	mux.HandleFunc("/ws/audio/{call_id}", s.websocket)
	mux.HandleFunc("POST /internal/live-sessions/{call_id}", s.internalOnly(s.startLiveSession))
	mux.HandleFunc("POST /internal/live-sessions/{call_id}/windows", s.internalOnly(s.pushLiveWindow))
	mux.HandleFunc("POST /internal/live-sessions/{call_id}/close", s.internalOnly(s.finishLiveSession))

	// Audio in flight only: these two stream their bodies to the Python sidecar unparsed.
	mux.HandleFunc("POST /api/v1/detect", s.proxyDetect)
	mux.HandleFunc("POST /api/v1/enrolments", s.proxyEnrol)
	mux.HandleFunc("POST /api/v1/chat", s.proxyChat)
	mux.HandleFunc("POST /api/v1/tts/speak", s.proxySpeak)
	mux.HandleFunc("GET /api/v1/enrolments", s.listEnrolments)
	mux.HandleFunc("DELETE /api/v1/enrolments/{identity_id}", s.revokeEnrolment)


	mux.HandleFunc("POST /api/v1/risk-score", s.riskScore)
	mux.HandleFunc("GET /api/v1/risk-score/{call_id}", s.currentRisk)
	mux.HandleFunc("POST /api/v1/decisions/{call_id}/override", s.overrideDecision)

	mux.HandleFunc("POST /api/v1/alerts", s.createAlert)
	mux.HandleFunc("GET /api/v1/alerts", s.listAlerts)
	mux.HandleFunc("POST /api/v1/alerts/{alert_id}/assign", s.assignAlert)
	mux.HandleFunc("POST /api/v1/alerts/{alert_id}/resolve", s.resolveAlert)
	mux.HandleFunc("POST /api/v1/alerts/{alert_id}/appeal", s.appealAlert)
	mux.HandleFunc("POST /api/v1/alerts/{alert_id}/escalate", s.escalateAlert)

	mux.HandleFunc("POST /api/v1/ledger/log", s.logEvent)
	mux.HandleFunc("GET /api/v1/ledger", s.ledgerEntries)
	mux.HandleFunc("GET /api/v1/ledger/verify/{hash}", s.verifyLedger)

	return s.recoverPanics(s.cors(mux))
}

// --- middleware ------------------------------------------------------------------------------

func (s *Server) cors(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if origin := r.Header.Get("Origin"); origin != "" && s.originAllowed(origin) {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Vary", "Origin")
			w.Header().Set("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		}
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (s *Server) originAllowed(origin string) bool {
	for _, allowed := range s.AllowedOrigins {
		if allowed == origin {
			return true
		}
	}
	return false
}

// internalOnly keeps the derived Python-to-Go boundary local to this machine. Browsers and PBX
// clients never submit detector output themselves.
func (s *Server) internalOnly(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		host, _, err := net.SplitHostPort(r.RemoteAddr)
		ip := net.ParseIP(host)
		if err != nil || ip == nil || !ip.IsLoopback() {
			s.fail(w, http.StatusForbidden, "This endpoint is available only to the local analysis service.")
			return
		}
		next(w, r)
	}
}

// recoverPanics turns a panic into a 500 and a log line.
//
// The fusion asserts CLAUDE.md invariant 6 with a panic: if contributing_factors ever stop
// summing to the base score, that is a correctness failure and the request must fail, not return
// a number nobody can account for. This makes that failure visible instead of silently killing
// the connection.
func (s *Server) recoverPanics(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if recovered := recover(); recovered != nil {
				s.Log.Error("handler panic", "path", r.URL.Path, "panic", recovered)
				s.fail(w, http.StatusInternalServerError,
					"The request could not be completed. The incident has been logged.")
			}
		}()
		next.ServeHTTP(w, r)
	})
}

// --- helpers ---------------------------------------------------------------------------------

// fail writes an error in FastAPI's shape, because the dashboard reads `detail`.
func (s *Server) fail(w http.ResponseWriter, status int, detail string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(map[string]string{"detail": detail})
}

func (s *Server) ok(w http.ResponseWriter, value any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(value)
}

// decode reads and validates a body, writing a 422 on failure.
func decode[T interface{ Validate() error }](s *Server, w http.ResponseWriter, r *http.Request, dst T) bool {
	defer r.Body.Close()
	if err := schema.Decode(http.MaxBytesReader(w, r.Body, 8<<20), dst); err != nil {
		s.fail(w, http.StatusUnprocessableEntity, err.Error())
		return false
	}
	if err := dst.Validate(); err != nil {
		s.fail(w, http.StatusUnprocessableEntity, err.Error())
		return false
	}
	return true
}

// sidecarStatus maps a sidecar failure onto a status the dashboard can act on. A sidecar that is
// down is a 503, not a 500: the operator needs to know the ML service is missing, not that
// "something went wrong".
func sidecarStatus(err error) (int, string) {
	var se *sidecar.Error
	if errors.As(err, &se) {
		return se.Status, se.Detail
	}
	return http.StatusServiceUnavailable, "The analysis service is unavailable. Live assessment is paused."
}

// --- handlers --------------------------------------------------------------------------------

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	out := map[string]any{
		"status":                 "ok",
		"mode":                   "v2 full-pipeline simulation",
		"raw_audio_persistence":  false,
		"policy_version":         s.Policy.Version,
		"gateway":                "go",
		"analysis_service":       "python-sidecar",
		"analysis_service_state": "unreachable",
	}
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	if h, err := s.Sidecar.Health(ctx); err == nil {
		out["analysis_service_state"] = "ok"
		out["model"] = h.Model
		out["model_versions"] = map[string]string{
			"detector": h.ModelVersion, "calibrator": h.CalibratorVersion, "verifier": h.VerifierVersion,
		}
		if h.DetectorMode != "" {
			out["detector_mode"] = h.DetectorMode
		}
		if h.DetectorProvider != "" {
			out["detector_provider"] = h.DetectorProvider
		}
	} else {
		// CLAUDE.md invariant 2 at the health level: a missing analysis service is reported as
		// degraded, never as a healthy system with a quietly absent detector.
		out["status"] = "degraded"
		s.Log.Warn("sidecar health check failed", "error", err)
	}
	s.ok(w, out)
}

func (s *Server) audioFiles(w http.ResponseWriter, r *http.Request) {
	files, err := s.Sidecar.AudioFiles(r.Context())
	if err != nil {
		status, detail := sidecarStatus(err)
		s.fail(w, status, detail)
		return
	}
	s.ok(w, files)
}

func (s *Server) listCalls(w http.ResponseWriter, r *http.Request) {
	s.ok(w, s.Sessions.List())
}

func (s *Server) startStream(w http.ResponseWriter, r *http.Request) {
	var body schema.Start
	if !decode(s, w, r, &body) {
		return
	}
	identity := ""
	if body.IdentityID != nil {
		identity = *body.IdentityID
	}
	callID, err := s.Sessions.Start(r.Context(), session.StartOptions{
		Filename:                 body.Filename,
		Label:                    *body.Label,
		Interval:                 time.Duration(*body.Interval * float64(time.Second)),
		Language:                 *body.Language,
		IdentityID:               identity,
		SimulateDetectorFailure:  body.SimulateDetectorFailure,
		SimulateAdversarialInput: body.SimulateAdversarialInput,
		Context:                  body.Context.ToScoring(),
	})
	if err != nil {
		if errors.Is(err, session.ErrTooManyStreams) {
			s.fail(w, http.StatusTooManyRequests, err.Error())
			return
		}
		status, detail := sidecarStatus(err)
		s.fail(w, status, detail)
		return
	}
	s.ok(w, map[string]string{"call_id": callID})
}

func (s *Server) stopStream(w http.ResponseWriter, r *http.Request) {
	call, err := s.Sessions.Stop(r.PathValue("call_id"))
	if err != nil {
		s.fail(w, http.StatusNotFound, "Call not found")
		return
	}
	s.ok(w, call)
}

func (s *Server) closeSession(w http.ResponseWriter, r *http.Request) {
	summary, err := s.Sessions.Close(r.PathValue("call_id"))
	if err != nil {
		s.fail(w, http.StatusNotFound, "Call not found")
		return
	}
	s.ok(w, summary)
}

func (s *Server) startLiveSession(w http.ResponseWriter, r *http.Request) {
	var body schema.LiveStart
	if !decode(s, w, r, &body) {
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	health, err := s.Sidecar.Health(ctx)
	if err != nil {
		status, detail := sidecarStatus(err)
		s.fail(w, status, detail)
		return
	}
	identity := ""
	if body.IdentityID != nil {
		identity = *body.IdentityID
	}
	verifierVersion := "not_enrolled"
	if identity != "" {
		verifierVersion = health.VerifierVersion
	}
	err = s.Sessions.StartLive(r.PathValue("call_id"), session.StartOptions{
		Label: *body.Label, Language: *body.Language, IdentityID: identity,
		Context: body.Context.ToScoring(),
	}, map[string]string{
		"detector": health.ModelVersion, "calibrator": health.CalibratorVersion,
		"verifier": verifierVersion,
	})
	if err != nil {
		switch {
		case errors.Is(err, session.ErrTooManyStreams):
			s.fail(w, http.StatusTooManyRequests, err.Error())
		case errors.Is(err, session.ErrDuplicate):
			s.fail(w, http.StatusConflict, err.Error())
		default:
			s.fail(w, http.StatusUnprocessableEntity, err.Error())
		}
		return
	}
	call, _ := s.Sessions.Get(r.PathValue("call_id"))
	s.ok(w, call.Public())
}

func (s *Server) pushLiveWindow(w http.ResponseWriter, r *http.Request) {
	var body sidecar.Window
	if !decode(s, w, r, &body) {
		return
	}
	call, err := s.Sessions.PushLiveWindow(r.PathValue("call_id"), body)
	if err != nil {
		if errors.Is(err, session.ErrNotFound) {
			s.fail(w, http.StatusNotFound, "Call not found")
			return
		}
		s.fail(w, http.StatusConflict, err.Error())
		return
	}
	s.ok(w, call)
}

func (s *Server) finishLiveSession(w http.ResponseWriter, r *http.Request) {
	summary, err := s.Sessions.FinishLive(r.PathValue("call_id"))
	if err != nil {
		s.fail(w, http.StatusNotFound, "Call not found")
		return
	}
	s.ok(w, summary)
}

func (s *Server) websocket(w http.ResponseWriter, r *http.Request) {
	call, err := s.Sessions.Get(r.PathValue("call_id"))
	if err != nil {
		http.Error(w, "Call not found", http.StatusNotFound)
		return
	}
	conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{
		OriginPatterns: s.AllowedOrigins,
	})
	if err != nil {
		s.Log.Warn("websocket accept failed", "error", err)
		return
	}
	defer conn.CloseNow()

	ctx := conn.CloseRead(r.Context())
	cursor := 0
	for {
		events, ok := call.Events(ctx, cursor)
		if !ok {
			return
		}
		for _, event := range events {
			if err := wsjson.Write(ctx, conn, event); err != nil {
				return
			}
			cursor++
			if event.Type == "complete" {
				conn.Close(websocket.StatusNormalClosure, "")
				return
			}
		}
	}
}

// proxyDetect streams POST /api/v1/detect to the sidecar without decoding the samples.
func (s *Server) proxyDetect(w http.ResponseWriter, r *http.Request) {
	s.Sidecar.Proxy(w, r, "/internal/analyse")
}

// proxySpeak streams POST /api/v1/tts/speak to the sidecar.
//
// The response carries generated demo audio, so unlike the other proxied routes it is a body the
// gateway is *meant* to pass back. It is still streamed, not decoded: the gateway has no reason
// to hold a waveform in a Go value, and keeping it out of one keeps the audio boundary in
// docs/01-ARCHITECTURE.md section 2 exactly where it is for every other route.
//
// Scoring happens in the sidecar, next to the detector, so there is no path by which a gateway
// change could put a number in front of an operator that no model produced.
func (s *Server) proxySpeak(w http.ResponseWriter, r *http.Request) {
	s.Sidecar.Proxy(w, r, "/internal/tts/speak")
}

// proxyEnrol streams POST /api/v1/enrolments to the sidecar without decoding the samples.
//
// Consent is checked there against docs/CONSENT_LOG.md (CLAUDE.md invariant 14); the gateway
// deliberately has no say, so there is no path by which a gateway change could bypass it.
func (s *Server) proxyEnrol(w http.ResponseWriter, r *http.Request) {
	s.Sidecar.Proxy(w, r, "/internal/enrolments")
}

// proxyChat streams POST /api/v1/chat to the sidecar for LLM deepfake generation.
func (s *Server) proxyChat(w http.ResponseWriter, r *http.Request) {
	s.Sidecar.Proxy(w, r, "/internal/chat")
}

func (s *Server) listEnrolments(w http.ResponseWriter, r *http.Request) {
	out, err := s.Sidecar.Enrolments(r.Context())
	if err != nil {
		status, detail := sidecarStatus(err)
		s.fail(w, status, detail)
		return
	}
	s.ok(w, out)
}

func (s *Server) revokeEnrolment(w http.ResponseWriter, r *http.Request) {
	identity := r.PathValue("identity_id")
	if err := s.Sidecar.RevokeEnrolment(r.Context(), identity); err != nil {
		status, detail := sidecarStatus(err)
		s.fail(w, status, detail)
		return
	}
	s.ok(w, map[string]string{"status": "revoked", "identity_id": identity})
}


func (s *Server) riskScore(w http.ResponseWriter, r *http.Request) {
	var body schema.Scores
	if !decode(s, w, r, &body) {
		return
	}
	score, err := scoring.Fuse(*body.SpectralScore, *body.ProsodyScore, body.SpeakerMatchScore,
		body.Context.ToScoring(), s.Policy)
	if err != nil {
		s.fail(w, http.StatusUnprocessableEntity, err.Error())
		return
	}
	out := map[string]any{
		"risk_score":     score,
		"policy_version": s.Policy.Version,
		"aggregation": "single-window compatibility endpoint; sessions use EWMA, decaying peak, " +
			"and hysteresis",
	}
	if score != nil {
		out["authenticity_score"] = 100 - *score
	} else {
		out["authenticity_score"] = nil
	}
	s.ok(w, out)
}

func (s *Server) currentRisk(w http.ResponseWriter, r *http.Request) {
	call, err := s.Sessions.Get(r.PathValue("call_id"))
	if err != nil {
		s.fail(w, http.StatusNotFound, "Call not found")
		return
	}
	s.ok(w, call.Public())
}

func (s *Server) overrideDecision(w http.ResponseWriter, r *http.Request) {
	callID := r.PathValue("call_id")
	call, err := s.Sessions.Get(callID)
	if err != nil {
		s.fail(w, http.StatusNotFound, "Call not found")
		return
	}
	var body schema.DecisionOverrideRequest
	if !decode(s, w, r, &body) {
		return
	}
	record, err := s.Decisions.Override(callID, body.Decision, body.Reason, body.SupervisorID, *body.Role)
	if err != nil {
		s.fail(w, http.StatusBadRequest, err.Error())
		return
	}

	public := call.Public()
	s.Sessions.SetDecision(callID, s.Decisions.Decide(decision.Verdict{
		SessionID: callID, Band: public.Band, SessionScore: public.RiskScore,
		Degraded: public.Degraded,
	}, call.Context(), s.Policy))

	score := 0.0
	if public.RiskScore != nil {
		score = *public.RiskScore
	}
	s.Sessions.Audit(map[string]any{
		"call_id": callID, "event_type": "override", "risk_score": score,
		"band": public.Band, "policy_version": s.Policy.Version,
		"model_versions": anyMap(public.ModelVersions),
	})
	s.ok(w, record)
}

func (s *Server) createAlert(w http.ResponseWriter, r *http.Request) {
	var body schema.AlertRequest
	if !decode(s, w, r, &body) {
		return
	}
	alert, err := s.Sessions.EnsureManualAlert(body.CallID)
	if err != nil {
		if errors.Is(err, session.ErrNotFound) {
			s.fail(w, http.StatusNotFound, "Call not found")
			return
		}
		s.fail(w, http.StatusConflict, err.Error())
		return
	}
	s.ok(w, alert)
}

func (s *Server) listAlerts(w http.ResponseWriter, r *http.Request) {
	s.ok(w, s.Alerts.List())
}

func (s *Server) assignAlert(w http.ResponseWriter, r *http.Request) {
	var body schema.AlertAssignRequest
	if !decode(s, w, r, &body) {
		return
	}
	alert, err := s.Alerts.Assign(r.PathValue("alert_id"), body.AssigneeID)
	if err != nil {
		s.fail(w, http.StatusNotFound, "Alert not found")
		return
	}
	s.ok(w, alert)
}

func (s *Server) resolveAlert(w http.ResponseWriter, r *http.Request) {
	var body schema.AlertResolveRequest
	if !decode(s, w, r, &body) {
		return
	}
	alert, err := s.Alerts.Resolve(r.PathValue("alert_id"), body.Outcome, body.Notes, *body.ResolverID)
	if err != nil {
		if errors.Is(err, alerts.ErrNotFound) {
			s.fail(w, http.StatusNotFound, "Alert not found")
			return
		}
		s.fail(w, http.StatusUnprocessableEntity, err.Error())
		return
	}
	s.Sessions.Audit(map[string]any{
		"call_id": alert.CallID, "event_type": "decision", "risk_score": alert.RiskScore,
		"band": alert.Band, "policy_version": s.Policy.Version,
		"model_versions": anyMap(alert.ModelVersions),
	})
	s.ok(w, alert)
}

func (s *Server) appealAlert(w http.ResponseWriter, r *http.Request) {
	var body schema.AppealCreateRequest
	if !decode(s, w, r, &body) {
		return
	}
	contact := ""
	if body.ContactInfo != nil {
		contact = *body.ContactInfo
	}
	appeal, err := s.Alerts.LodgeAppeal(r.PathValue("alert_id"), body.Reason, *body.AppellantType, contact)
	if err != nil {
		if errors.Is(err, alerts.ErrNotFound) {
			s.fail(w, http.StatusNotFound, "Alert not found")
			return
		}
		s.fail(w, http.StatusUnprocessableEntity, err.Error())
		return
	}
	s.ok(w, appeal)
}

func (s *Server) escalateAlert(w http.ResponseWriter, r *http.Request) {
	alert, changed, err := s.Alerts.Escalate(r.PathValue("alert_id"))
	if err != nil {
		s.fail(w, http.StatusNotFound, "Alert not found")
		return
	}
	if changed {
		s.Sessions.Audit(map[string]any{
			"call_id": alert.CallID, "event_type": "escalation", "risk_score": alert.RiskScore,
			"band": alert.Band, "policy_version": s.Policy.Version,
			"model_versions": anyMap(alert.ModelVersions),
		})
	}
	s.ok(w, alert)
}

func (s *Server) logEvent(w http.ResponseWriter, r *http.Request) {
	var body schema.LedgerEvent
	if !decode(s, w, r, &body) {
		return
	}
	entry, err := s.Ledger.Append(body.Map())
	if err != nil {
		s.fail(w, http.StatusInternalServerError, "The audit record could not be written.")
		return
	}
	s.ok(w, entry)
}

func (s *Server) ledgerEntries(w http.ResponseWriter, r *http.Request) {
	entries, err := s.Ledger.Entries()
	if err != nil {
		s.fail(w, http.StatusInternalServerError, "The audit log could not be read.")
		return
	}
	out := make([]ledger.Entry, 0, len(entries))
	for i := len(entries) - 1; i >= 0; i-- {
		out = append(out, entries[i])
	}
	s.ok(w, out)
}

func (s *Server) verifyLedger(w http.ResponseWriter, r *http.Request) {
	target := r.PathValue("hash")
	if strings.EqualFold(target, "all") {
		target = ""
	}
	result, err := s.Ledger.Verify(target)
	if err != nil {
		s.fail(w, http.StatusInternalServerError, "The audit log could not be read.")
		return
	}
	s.ok(w, result)
}

func anyMap(m map[string]string) map[string]any {
	out := make(map[string]any, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}
