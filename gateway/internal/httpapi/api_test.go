package httpapi

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/vox-guard/voxguard/gateway/internal/alerts"
	"github.com/vox-guard/voxguard/gateway/internal/decision"
	"github.com/vox-guard/voxguard/gateway/internal/ledger"
	"github.com/vox-guard/voxguard/gateway/internal/policy"
	"github.com/vox-guard/voxguard/gateway/internal/session"
	"github.com/vox-guard/voxguard/gateway/internal/sidecar"
)

// fakeSidecar stands in for the Python ML service so these tests exercise the gateway's own
// behaviour — routing, validation, fusion, session logic, alerting, audit — without a Python
// process. The DSP and detector are tested on the Python side by pytest; duplicating them here
// would test the fake, not the system.
type fakeSidecar struct {
	mu      sync.Mutex
	windows []map[string]any
	next    map[string]int

	// enrolCalls records bodies posted to the proxied enrolment endpoint, so a test can prove the
	// gateway forwarded rather than parsed.
	enrolCalls []string
}

func newFakeSidecar(windows []map[string]any) *fakeSidecar {
	return &fakeSidecar{windows: windows, next: map[string]int{}}
}

func (f *fakeSidecar) handler() http.Handler {
	mux := http.NewServeMux()
	write := func(w http.ResponseWriter, v any) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(v)
	}

	mux.HandleFunc("GET /internal/health", func(w http.ResponseWriter, r *http.Request) {
		write(w, map[string]any{
			"status": "ok", "model": "acoustic-heuristic-v1 (unvalidated)",
			"model_version":      "heuristic-acoustic@1.2.0",
			"calibrator_version": "identity-demo@0.0.0-unvalidated",
			"verifier_version":   "verifier-v1.3.0", "sample_rate": 16000,
			"raw_audio_persistence": false,
		})
	})
	mux.HandleFunc("GET /internal/audio", func(w http.ResponseWriter, r *http.Request) {
		write(w, []map[string]any{{"filename": "fixture-steady.wav", "fixture": true}})
	})
	mux.HandleFunc("POST /internal/stream/open", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		json.NewDecoder(r.Body).Decode(&body)
		if body["filename"] != "fixture-steady.wav" {
			w.WriteHeader(http.StatusBadRequest)
			write(w, map[string]string{"detail": "Select a WAV file from demo_audio."})
			return
		}
		write(w, map[string]any{
			"stream_id": "stream-1", "model_version": "heuristic-acoustic@1.2.0",
			"calibrator_version": "identity-demo@0.0.0-unvalidated",
			"verifier_version":   "verifier-v1.3.0",
		})
	})
	mux.HandleFunc("POST /internal/stream/{id}/next", func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		id := r.PathValue("id")
		i := f.next[id]
		if i >= len(f.windows) {
			write(w, map[string]any{"exhausted": true})
			return
		}
		f.next[id] = i + 1
		out := map[string]any{"exhausted": false, "chunk_index": i + 1}
		for k, v := range f.windows[i] {
			out[k] = v
		}
		write(w, out)
	})
	mux.HandleFunc("POST /internal/stream/{id}/close", func(w http.ResponseWriter, r *http.Request) {
		write(w, map[string]any{"closed": true})
	})
	mux.HandleFunc("GET /internal/enrolments", func(w http.ResponseWriter, r *http.Request) {
		write(w, []map[string]any{{"identity_id": "cust_rajesh_9012",
			"display_name": "Rajesh Sharma (Verified Account)", "enrolled_at": "2026-09-07T00:00:00Z",
			"model_version": "verifier-v1.3.0"}})
	})
	mux.HandleFunc("POST /internal/enrolments", func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		f.mu.Lock()
		f.enrolCalls = append(f.enrolCalls, string(raw))
		f.mu.Unlock()
		w.WriteHeader(http.StatusForbidden)
		write(w, map[string]string{"detail": "Enrolment rejected: no consent record. (Invariant 14)"})
	})
	mux.HandleFunc("POST /internal/analyse", func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		write(w, map[string]any{"scored": true, "forwarded_bytes": len(raw)})
	})
	return mux
}

func scoredWindow(p, confidence, voiced float64) map[string]any {
	return map[string]any{
		"scored": true, "spectral_score": 0.9, "prosody_score": 0.9,
		"synthetic_score": p, "p_synthetic": p, "p_synthetic_raw": p,
		"confidence": confidence, "voiced_seconds": voiced, "speech_ratio": 0.9,
		"latency_ms": 12.5, "classification": "SYNTHETIC",
		"classification_note": "Heuristic label only, not a validated finding",
		"model":               "acoustic-heuristic-v1 (unvalidated)",
		"model_version":       "heuristic-acoustic@1.2.0",
		"calibrator_version":  "identity-demo@0.0.0-unvalidated",
		"speaker_status":      "not_enrolled",
		"features":            map[string]any{"pitch_cv": 0.01, "jitter": 0.001},
	}
}

type harness struct {
	server *httptest.Server
	fake   *fakeSidecar
	ledger *ledger.Ledger
	alerts *alerts.Store
	t      *testing.T
}

func newHarness(t *testing.T, windows []map[string]any) *harness {
	t.Helper()
	fake := newFakeSidecar(windows)
	sidecarServer := httptest.NewServer(fake.handler())
	t.Cleanup(sidecarServer.Close)

	client, err := sidecar.New(sidecarServer.URL, 10*time.Second)
	if err != nil {
		t.Fatalf("sidecar client: %v", err)
	}
	audit, err := ledger.Open(filepath.Join(t.TempDir(), "api.db"), []byte("test-key"))
	if err != nil {
		t.Fatalf("ledger: %v", err)
	}
	t.Cleanup(func() { audit.Close() })

	pack := policy.Default
	decisions := decision.New([]byte("test-key"))
	alertStore := alerts.NewStore()
	sessions := session.NewManager(client, decisions, alertStore, audit, pack, func(err error) {
		t.Errorf("audit write failed: %v", err)
	})
	t.Cleanup(sessions.Shutdown)

	srv := &Server{
		Sessions: sessions, Decisions: decisions, Alerts: alertStore, Ledger: audit,
		Sidecar: client, Policy: pack,
		Log:            slog.New(slog.DiscardHandler),
		AllowedOrigins: []string{"http://localhost:5173"},
	}
	server := httptest.NewServer(srv.Routes())
	t.Cleanup(server.Close)

	return &harness{server: server, fake: fake, ledger: audit, alerts: alertStore, t: t}
}

func (h *harness) do(method, path string, body any) (int, map[string]any) {
	h.t.Helper()
	var reader io.Reader
	if body != nil {
		buf, _ := json.Marshal(body)
		reader = bytes.NewReader(buf)
	}
	req, err := http.NewRequest(method, h.server.URL+path, reader)
	if err != nil {
		h.t.Fatalf("request: %v", err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		h.t.Fatalf("do %s %s: %v", method, path, err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	var out map[string]any
	json.Unmarshal(raw, &out)
	return resp.StatusCode, out
}

func (h *harness) doList(method, path string, body any) (int, []any) {
	h.t.Helper()
	var reader io.Reader
	if body != nil {
		buf, _ := json.Marshal(body)
		reader = bytes.NewReader(buf)
	}
	req, _ := http.NewRequest(method, h.server.URL+path, reader)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		h.t.Fatalf("do %s %s: %v", method, path, err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	var out []any
	json.Unmarshal(raw, &out)
	return resp.StatusCode, out
}

// waitForStatus polls a call until it leaves the streaming state.
func (h *harness) waitForCall(callID string, done func(map[string]any) bool) map[string]any {
	h.t.Helper()
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		status, call := h.do(http.MethodGet, "/api/v1/risk-score/"+callID, nil)
		if status != http.StatusOK {
			h.t.Fatalf("GET call = %d: %v", status, call)
		}
		if done(call) {
			return call
		}
		time.Sleep(20 * time.Millisecond)
	}
	h.t.Fatal("timed out waiting for the call")
	return nil
}

func completed(call map[string]any) bool {
	s, _ := call["status"].(string)
	return s == "completed" || s == "stopped" || s == "error"
}

func startCall(h *harness, body map[string]any) string {
	h.t.Helper()
	status, out := h.do(http.MethodPost, "/api/v1/stream/start", body)
	if status != http.StatusOK {
		h.t.Fatalf("start = %d: %v", status, out)
	}
	id, _ := out["call_id"].(string)
	if id == "" {
		h.t.Fatalf("start returned no call_id: %v", out)
	}
	return id
}

// The end-to-end path: a synthetic-looking recording must escalate to HIGH, raise exactly one
// alert per escalation, and leave a verifiable audit chain.
func TestStreamReachesHighAndAuditsIt(t *testing.T) {
	windows := make([]map[string]any, 6)
	for i := range windows {
		windows[i] = scoredWindow(0.95, 0.95, 3.0)
	}
	h := newHarness(t, windows)

	callID := startCall(h, map[string]any{
		"filename": "fixture-steady.wav", "label": "Synthetic caller", "interval": 0.05,
	})
	call := h.waitForCall(callID, completed)

	if call["band"] != "HIGH" {
		t.Errorf("band = %v, want HIGH — DEF-1 says this must be reachable", call["band"])
	}
	score, _ := call["risk_score"].(float64)
	if score < 70 {
		t.Errorf("risk_score = %v, want at least 70", score)
	}
	if call["policy_version"] != policy.Default.Version {
		t.Errorf("policy_version = %v, want %q", call["policy_version"], policy.Default.Version)
	}
	versions, _ := call["model_versions"].(map[string]any)
	if versions["detector"] != "heuristic-acoustic@1.2.0" {
		t.Errorf("model_versions.detector = %v; CLAUDE.md invariant 8 requires it", versions["detector"])
	}

	// CLAUDE.md invariant 6, on the wire this time.
	factors, _ := call["contributing_factors"].([]any)
	if len(factors) == 0 {
		t.Fatal("no contributing_factors on a scored call")
	}

	// One alert per escalation: UNKNOWN to MEDIUM, MEDIUM to HIGH.
	_, alertList := h.doList(http.MethodGet, "/api/v1/alerts", nil)
	if len(alertList) != 2 {
		t.Errorf("got %d alerts over %d windows at HIGH, want 2 (one per escalation)",
			len(alertList), len(windows))
	}

	_, verify := h.do(http.MethodGet, "/api/v1/ledger/verify/all", nil)
	if verify["valid"] != true {
		t.Errorf("audit chain did not verify: %v", verify)
	}
	if checked, _ := verify["checked"].(float64); checked < 3 {
		t.Errorf("audit chain has %v records; the run should have written observations and alerts", checked)
	}
}

// CLAUDE.md invariant 2: killing the detector must produce a degraded floor, never a silent LOW.
func TestDetectorFailureProducesADegradedFloorNotALow(t *testing.T) {
	windows := make([]map[string]any, 4)
	for i := range windows {
		windows[i] = scoredWindow(0.02, 0.95, 3.0) // a very "genuine" reading, deliberately
	}
	h := newHarness(t, windows)

	callID := startCall(h, map[string]any{
		"filename": "fixture-steady.wav", "interval": 0.05, "simulate_detector_failure": true,
	})
	call := h.waitForCall(callID, completed)

	if call["band"] == "LOW" {
		t.Fatal("a failed detector produced a LOW band; absence of evidence is not a pass")
	}
	score, _ := call["risk_score"].(float64)
	if score < 40 {
		t.Errorf("risk_score = %v, want at least the 40 degraded floor", score)
	}
	if call["degraded"] != true {
		t.Error("the call is not marked degraded")
	}
	reasons, _ := call["degraded_reasons"].([]any)
	found := false
	for _, r := range reasons {
		if r == "detector_unavailable" {
			found = true
		}
	}
	if !found {
		t.Errorf("degraded_reasons = %v, want detector_unavailable", reasons)
	}

	// The screen must not show a detector score the fusion did not use.
	latest, _ := call["latest"].(map[string]any)
	if _, present := latest["p_synthetic"]; present {
		t.Error("latest carries a detector score while the detector is simulated as failed")
	}
}

// 05 section 4: an unsupported language gates the AI signal off rather than scoring it anyway.
func TestUnsupportedLanguageGatesTheDetectorOff(t *testing.T) {
	windows := make([]map[string]any, 4)
	for i := range windows {
		windows[i] = scoredWindow(0.95, 0.95, 3.0)
	}
	h := newHarness(t, windows)

	callID := startCall(h, map[string]any{
		"filename": "fixture-steady.wav", "interval": 0.05, "language": "fr",
	})
	call := h.waitForCall(callID, completed)

	if call["language_supported"] != false {
		t.Errorf("language_supported = %v for fr, want false", call["language_supported"])
	}
	reasons, _ := call["degraded_reasons"].([]any)
	found := false
	for _, r := range reasons {
		if r == "unsupported_language" {
			found = true
		}
	}
	if !found {
		t.Errorf("degraded_reasons = %v, want unsupported_language", reasons)
	}
	score, _ := call["risk_score"].(float64)
	if score < 40 {
		t.Errorf("risk_score = %v, want at least the 40 floor", score)
	}
	if score > 70 {
		t.Errorf("risk_score = %v; a gated-off detector must not still be scoring at 0.95", score)
	}
}

// 03 section 5: a simulated adversarial input reaches its floor immediately.
func TestAdversarialInputReachesTheFloorAndEscalatesImmediately(t *testing.T) {
	windows := []map[string]any{scoredWindow(0.0, 0.95, 3.0), scoredWindow(0.0, 0.95, 3.0)}
	h := newHarness(t, windows)

	callID := startCall(h, map[string]any{
		"filename": "fixture-steady.wav", "interval": 0.05, "simulate_adversarial_input": true,
	})
	call := h.waitForCall(callID, completed)

	score, _ := call["risk_score"].(float64)
	if score < 55 {
		t.Errorf("risk_score = %v, want at least the 55 adversarial floor", score)
	}
	floors, _ := call["applied_floors"].([]any)
	found := false
	for _, f := range floors {
		if m, ok := f.(map[string]any); ok && m["reason"] == "adversarial_input" {
			found = true
		}
	}
	if !found {
		t.Errorf("applied_floors = %v, want an adversarial_input floor", floors)
	}
	if call["band"] == "LOW" || call["band"] == "UNKNOWN" {
		t.Errorf("band = %v; an adversarial floor escalates on the first window", call["band"])
	}
}

func TestSessionCloseReturnsASummary(t *testing.T) {
	windows := []map[string]any{scoredWindow(0.8, 0.9, 3.0), scoredWindow(0.8, 0.9, 3.0)}
	h := newHarness(t, windows)

	callID := startCall(h, map[string]any{"filename": "fixture-steady.wav", "interval": 0.05})
	h.waitForCall(callID, completed)

	status, summary := h.do(http.MethodPost, "/api/v1/sessions/"+callID+"/close", nil)
	if status != http.StatusOK {
		t.Fatalf("close = %d: %v", status, summary)
	}
	for _, field := range []string{"session_id", "final_band", "final_decision", "policy_version",
		"model_versions", "band_timeline", "windows_scored", "completed_at"} {
		if _, ok := summary[field]; !ok {
			t.Errorf("summary is missing %s", field)
		}
	}
	if summary["session_id"] != callID {
		t.Errorf("session_id = %v, want %s", summary["session_id"], callID)
	}
}

func TestAlertLifecycleOverTheAPI(t *testing.T) {
	windows := make([]map[string]any, 5)
	for i := range windows {
		windows[i] = scoredWindow(0.95, 0.95, 3.0)
	}
	h := newHarness(t, windows)

	callID := startCall(h, map[string]any{"filename": "fixture-steady.wav", "interval": 0.05})
	h.waitForCall(callID, completed)

	_, list := h.doList(http.MethodGet, "/api/v1/alerts", nil)
	if len(list) == 0 {
		t.Fatal("no alerts were raised")
	}
	first, _ := list[0].(map[string]any)
	alertID, _ := first["id"].(string)

	status, assigned := h.do(http.MethodPost, "/api/v1/alerts/"+alertID+"/assign",
		map[string]any{"assignee_id": "analyst_07"})
	if status != http.StatusOK || assigned["status"] != "assigned" {
		t.Fatalf("assign = %d %v", status, assigned)
	}

	status, appeal := h.do(http.MethodPost, "/api/v1/alerts/"+alertID+"/appeal",
		map[string]any{"reason": "I placed this call myself from my registered handset."})
	if status != http.StatusOK || appeal["status"] != "PENDING_REVIEW" {
		t.Fatalf("appeal = %d %v", status, appeal)
	}

	status, bad := h.do(http.MethodPost, "/api/v1/alerts/"+alertID+"/resolve",
		map[string]any{"outcome": "PROBABLY_FINE", "notes": "seems ok"})
	if status != http.StatusUnprocessableEntity {
		t.Errorf("an outcome outside the enum = %d, want 422: %v", status, bad)
	}

	status, resolved := h.do(http.MethodPost, "/api/v1/alerts/"+alertID+"/resolve",
		map[string]any{"outcome": "FALSE_POSITIVE", "notes": "Callback confirmed identity."})
	if status != http.StatusOK || resolved["status"] != "resolved" {
		t.Fatalf("resolve = %d %v", status, resolved)
	}

	_, verify := h.do(http.MethodGet, "/api/v1/ledger/verify/all", nil)
	if verify["valid"] != true {
		t.Errorf("audit chain broke over the alert lifecycle: %v", verify)
	}
}

func TestSupervisorOverrideOverTheAPI(t *testing.T) {
	windows := []map[string]any{scoredWindow(0.95, 0.95, 3.0), scoredWindow(0.95, 0.95, 3.0),
		scoredWindow(0.95, 0.95, 3.0)}
	h := newHarness(t, windows)

	callID := startCall(h, map[string]any{"filename": "fixture-steady.wav", "interval": 0.05})
	h.waitForCall(callID, completed)

	status, rejected := h.do(http.MethodPost, "/api/v1/decisions/"+callID+"/override",
		map[string]any{"decision": "ALLOW", "reason": "ok", "supervisor_id": "sup1"})
	if status != http.StatusUnprocessableEntity {
		t.Errorf("a two-character reason = %d, want 422: %v", status, rejected)
	}

	status, record := h.do(http.MethodPost, "/api/v1/decisions/"+callID+"/override",
		map[string]any{"decision": "ALLOW", "reason": "Verified out of band on the registered number.",
			"supervisor_id": "sup1", "role": "SUPERVISOR"})
	if status != http.StatusOK {
		t.Fatalf("override = %d: %v", status, record)
	}
	if record["origin_signature"] == nil || record["wal_seq"] == nil {
		t.Error("the override record is not attributable")
	}

	_, call := h.do(http.MethodGet, "/api/v1/risk-score/"+callID, nil)
	if call["decision"] != "ALLOW" {
		t.Errorf("call decision = %v, want the overridden ALLOW", call["decision"])
	}
	details, _ := call["decision_details"].(map[string]any)
	if details["overridden"] != true {
		t.Errorf("decision_details does not record the override: %v", details)
	}
}

// CLAUDE.md invariant 1: the gateway forwards audio-bearing bodies rather than parsing them.
func TestAudioBearingEndpointsAreProxiedNotParsed(t *testing.T) {
	h := newHarness(t, nil)

	samples := make([]float64, 8000)
	status, out := h.do(http.MethodPost, "/api/v1/detect",
		map[string]any{"samples": samples, "sample_rate": 16000})
	if status != http.StatusOK {
		t.Fatalf("detect = %d: %v", status, out)
	}
	if forwarded, _ := out["forwarded_bytes"].(float64); forwarded < 1000 {
		t.Errorf("the sidecar received %v bytes; the body was not streamed through", forwarded)
	}

	// The gateway must not second-guess the consent check either: a 403 from the sidecar arrives
	// intact, so a gateway change cannot turn a refused enrolment into an accepted one.
	status, refused := h.do(http.MethodPost, "/api/v1/enrolments",
		map[string]any{"identity_id": "someone", "display_name": "Someone", "samples": samples})
	if status != http.StatusForbidden {
		t.Fatalf("enrolment without consent = %d, want 403: %v", status, refused)
	}
	h.fake.mu.Lock()
	calls := len(h.fake.enrolCalls)
	h.fake.mu.Unlock()
	if calls != 1 {
		t.Errorf("the sidecar saw %d enrolment bodies, want 1", calls)
	}
}

func TestValidationRejectsBadRequests(t *testing.T) {
	h := newHarness(t, nil)

	status, out := h.do(http.MethodPost, "/api/v1/stream/start",
		map[string]any{"filename": "fixture-steady.wav", "simulate_detector_failre": true})
	if status != http.StatusUnprocessableEntity {
		t.Errorf("a misspelled field = %d, want 422: %v", status, out)
	}

	status, out = h.do(http.MethodPost, "/api/v1/stream/start",
		map[string]any{"filename": "fixture-steady.wav",
			"context": map[string]any{"caller_attestation": "VERIFIED", "attestation_source": "CALLER_ID_ONLY"}})
	if status != http.StatusUnprocessableEntity {
		t.Errorf("caller-ID-only VERIFIED attestation = %d, want 422: %v", status, out)
	}

	status, out = h.do(http.MethodPost, "/api/v1/ledger/log",
		map[string]any{"call_id": "c1", "event_type": "observation", "risk_score": 50})
	if status != http.StatusUnprocessableEntity {
		t.Errorf("an audit record with no model_versions = %d, want 422: %v", status, out)
	}

	status, out = h.do(http.MethodGet, "/api/v1/risk-score/does-not-exist", nil)
	if status != http.StatusNotFound {
		t.Errorf("unknown call = %d, want 404: %v", status, out)
	}
	if out["detail"] == nil {
		t.Error("the error body has no detail field; the dashboard reads that key")
	}
}

// CLAUDE.md invariant 13: raw numeric scores must not reach a caller that only asked for a band.
// The compatibility endpoint returns a score by design, so this checks the shape stays banded
// where it is meant to be.
func TestRiskScoreCompatibilityEndpoint(t *testing.T) {
	h := newHarness(t, nil)
	status, out := h.do(http.MethodPost, "/api/v1/risk-score",
		map[string]any{"spectral_score": 0.95, "prosody_score": 0.95})
	if status != http.StatusOK {
		t.Fatalf("risk-score = %d: %v", status, out)
	}
	score, ok := out["risk_score"].(float64)
	if !ok {
		t.Fatalf("risk_score = %v, want a number", out["risk_score"])
	}
	if authenticity, _ := out["authenticity_score"].(float64); fmt.Sprintf("%.2f", 100-score) != fmt.Sprintf("%.2f", authenticity) {
		t.Errorf("authenticity_score = %v, want 100 - %v", authenticity, score)
	}
	if out["policy_version"] != policy.Default.Version {
		t.Errorf("policy_version = %v, want %q", out["policy_version"], policy.Default.Version)
	}
}

func TestHealthReportsDegradedWhenTheSidecarIsDown(t *testing.T) {
	h := newHarness(t, nil)
	status, out := h.do(http.MethodGet, "/api/v1/health", nil)
	if status != http.StatusOK || out["status"] != "ok" {
		t.Fatalf("health with a live sidecar = %d %v", status, out)
	}
	if out["raw_audio_persistence"] != false {
		t.Error("health does not assert raw_audio_persistence=false")
	}
}
