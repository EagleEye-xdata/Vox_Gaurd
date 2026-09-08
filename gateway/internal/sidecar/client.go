// Package sidecar talks to the Python ML service (docs/01 section 2, the [Python] branches).
//
// THE AUDIO BOUNDARY (CLAUDE.md invariant 1)
// -------------------------------------------
// Nothing in this package carries audio samples. The gateway asks the sidecar for the *analysis*
// of the next window and receives derived features and scores; the samples are allocated,
// analysed and zeroed inside the Python process. The two endpoints that do carry caller audio —
// POST /api/v1/detect and POST /api/v1/enrolments — are reverse-proxied by Proxy below, which
// streams the body through without decoding it, so a window never becomes a Go value either.
package sidecar

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"time"
)

// Client is an HTTP client for the loopback ML sidecar.
type Client struct {
	base  *url.URL
	http  *http.Client
	proxy *httputil.ReverseProxy
}

// Health is the sidecar's model inventory, used to populate model_versions on every decision
// (CLAUDE.md invariant 8).
type Health struct {
	Status              string `json:"status"`
	Model               string `json:"model"`
	ModelVersion        string `json:"model_version"`
	CalibratorVersion   string `json:"calibrator_version"`
	VerifierVersion     string `json:"verifier_version"`
	SampleRate          int    `json:"sample_rate"`
	RawAudioPersistence bool   `json:"raw_audio_persistence"`
	// DetectorMode and DetectorProvider are additive: they let a caller tell "AASIST-L loaded"
	// apart from "running on the heuristic fallback" without parsing ModelVersion strings. Both
	// are optional so an older sidecar response (without these fields) still decodes cleanly.
	DetectorMode     string `json:"detector_mode,omitempty"`
	DetectorProvider string `json:"detector_provider,omitempty"`
}

// AudioFile is one fixture available to stream. Names only; never contents.
type AudioFile struct {
	Filename string `json:"filename"`
	Fixture  bool   `json:"fixture"`
}

// StreamHandle identifies an open window stream inside the sidecar.
type StreamHandle struct {
	StreamID          string `json:"stream_id"`
	ModelVersion      string `json:"model_version"`
	CalibratorVersion string `json:"calibrator_version"`
	VerifierVersion   string `json:"verifier_version"`
}

// Verification is the speaker-verification branch result as the sidecar reports it.
type Verification struct {
	ReferenceAvailable bool     `json:"reference_available"`
	MatchScore         *float64 `json:"match_score"`
	Confidence         float64  `json:"confidence"`
	VoicedSecondsUsed  float64  `json:"voiced_seconds_used"`
	ReplaySuspected    bool     `json:"replay_suspected"`
	Failed             bool     `json:"failed"`
	ModelVersion       string   `json:"model_version"`
	SpeakerTrack       string   `json:"speaker_track"`
	EnrolledIdentity   string   `json:"enrolled_identity"`
}

// Window is one window's analysis. Note what is absent: there is no samples field, and there is
// no way to ask for one.
type Window struct {
	Exhausted  bool `json:"exhausted"`
	Scored     bool `json:"scored"`
	ChunkIndex int  `json:"chunk_index"`

	Reason string `json:"reason"`

	SpectralScore      float64  `json:"spectral_score"`
	ProsodyScore       float64  `json:"prosody_score"`
	SyntheticScore     float64  `json:"synthetic_score"`
	SpeakerMatchScore  *float64 `json:"speaker_match_score"`
	SpeakerStatus      string   `json:"speaker_status"`
	Classification     string   `json:"classification"`
	ClassificationNote string   `json:"classification_note"`

	Model             string `json:"model"`
	ModelVersion      string `json:"model_version"`
	CalibratorVersion string `json:"calibrator_version"`

	PSynthetic    float64 `json:"p_synthetic"`
	PSyntheticRaw float64 `json:"p_synthetic_raw"`
	Confidence    float64 `json:"confidence"`
	VoicedSeconds float64 `json:"voiced_seconds"`
	SpeechRatio   float64 `json:"speech_ratio"`
	LatencyMS     float64 `json:"latency_ms"`

	Features     map[string]any `json:"features"`
	Verification *Verification  `json:"verification"`

	// IRisk is the intent/content risk score ∈ [0,1] from the Whisper STT + phrase
	// classifier pipeline.  Nil means the intent scorer was unavailable for this window;
	// the fusion engine treats nil as "signal not active" and renormalises without it.
	IRisk          *float64        `json:"i_risk,omitempty"`
	IntentMetadata *IntentMetadata `json:"intent,omitempty"`
}

// IntentMetadata carries diagnostics from the intent scorer for operator transparency.
type IntentMetadata struct {
	Transcript        string   `json:"transcript"`
	MatchedPhrases    []string `json:"matched_phrases"`
	HeuristicHits     []string `json:"heuristic_hits"`
	WhisperAvailable  bool     `json:"whisper_available"`
	LanguageDetected  *string  `json:"language_detected,omitempty"`
	LatencyMS         float64  `json:"latency_ms"`
	Error             *string  `json:"error,omitempty"`
}

// Validate rejects malformed derived windows at the private Python-to-Go boundary. Audio is
// deliberately absent from this type; only the analysis needed by fusion may cross the boundary.
func (w *Window) Validate() error {
	if w.ChunkIndex < 1 {
		return fmt.Errorf("chunk_index must be at least 1")
	}
	if !finiteBetween(w.LatencyMS, 0, 600000) {
		return fmt.Errorf("latency_ms must be a finite non-negative number")
	}
	if !w.Scored {
		if strings.TrimSpace(w.Reason) == "" {
			return fmt.Errorf("an unscored window must include a reason")
		}
		return nil
	}
	for name, value := range map[string]float64{
		"spectral_score": w.SpectralScore, "prosody_score": w.ProsodyScore,
		"synthetic_score": w.SyntheticScore, "p_synthetic": w.PSynthetic,
		"p_synthetic_raw": w.PSyntheticRaw, "confidence": w.Confidence,
		"speech_ratio": w.SpeechRatio,
	} {
		if !finiteBetween(value, 0, 1) {
			return fmt.Errorf("%s must be a finite number between 0 and 1", name)
		}
	}
	if !finiteBetween(w.VoicedSeconds, 0, 10) {
		return fmt.Errorf("voiced_seconds must be a finite number between 0 and 10")
	}
	if strings.TrimSpace(w.ModelVersion) == "" || strings.TrimSpace(w.CalibratorVersion) == "" {
		return fmt.Errorf("model_version and calibrator_version are required")
	}
	if v := w.Verification; v != nil {
		if !v.ReferenceAvailable && v.MatchScore != nil {
			return fmt.Errorf("match_score must be absent when reference_available is false")
		}
		if v.ReferenceAvailable && !v.Failed && v.MatchScore == nil {
			return fmt.Errorf("match_score is required when reference_available is true")
		}
		if v.MatchScore != nil && !finiteBetween(*v.MatchScore, 0, 1) {
			return fmt.Errorf("match_score must be a finite number between 0 and 1")
		}
	}
	return nil
}

func finiteBetween(value, low, high float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0) && value >= low && value <= high
}


// Enrolment is an enrolled speaker profile as listed by the sidecar.
type Enrolment struct {
	IdentityID   string `json:"identity_id"`
	DisplayName  string `json:"display_name"`
	EnrolledAt   string `json:"enrolled_at"`
	ModelVersion string `json:"model_version"`
}

// Error is a non-2xx reply from the sidecar, carrying its status so the gateway can pass a
// meaningful code back rather than flattening everything to 500.
type Error struct {
	Status int
	Detail string
}

func (e *Error) Error() string { return fmt.Sprintf("sidecar %d: %s", e.Status, e.Detail) }

// New builds a client for a base URL such as http://127.0.0.1:8801.
func New(base string, timeout time.Duration) (*Client, error) {
	u, err := url.Parse(base)
	if err != nil {
		return nil, fmt.Errorf("sidecar base URL: %w", err)
	}
	proxy := httputil.NewSingleHostReverseProxy(u)
	proxy.FlushInterval = -1
	return &Client{
		base:  u,
		http:  &http.Client{Timeout: timeout},
		proxy: proxy,
	}, nil
}

// Health reports the sidecar's model versions.
func (c *Client) Health(ctx context.Context) (Health, error) {
	var out Health
	err := c.do(ctx, http.MethodGet, "/internal/health", nil, &out)
	return out, err
}

// AudioFiles lists the fixtures available to stream.
func (c *Client) AudioFiles(ctx context.Context) ([]AudioFile, error) {
	out := []AudioFile{}
	err := c.do(ctx, http.MethodGet, "/internal/audio", nil, &out)
	return out, err
}

// OpenStream asks the sidecar to open a windowed read over a fixture.
func (c *Client) OpenStream(ctx context.Context, filename, identityID string) (StreamHandle, error) {
	body := map[string]any{"filename": filename}
	if identityID != "" {
		body["identity_id"] = identityID
	}
	var out StreamHandle
	err := c.do(ctx, http.MethodPost, "/internal/stream/open", body, &out)
	return out, err
}

// NextWindow pulls the next window's analysis. Exhausted marks the end of the recording.
func (c *Client) NextWindow(ctx context.Context, streamID string) (Window, error) {
	var out Window
	err := c.do(ctx, http.MethodPost, "/internal/stream/"+url.PathEscape(streamID)+"/next", nil, &out)
	return out, err
}

// CloseStream releases the sidecar's file handle and generator.
func (c *Client) CloseStream(ctx context.Context, streamID string) error {
	return c.do(ctx, http.MethodPost, "/internal/stream/"+url.PathEscape(streamID)+"/close", nil, nil)
}

// Enrolments lists enrolled speaker profiles.
func (c *Client) Enrolments(ctx context.Context) ([]Enrolment, error) {
	out := []Enrolment{}
	err := c.do(ctx, http.MethodGet, "/internal/enrolments", nil, &out)
	return out, err
}

// RevokeEnrolment erases an enrolled profile.
func (c *Client) RevokeEnrolment(ctx context.Context, identityID string) error {
	return c.do(ctx, http.MethodDelete, "/internal/enrolments/"+url.PathEscape(identityID), nil, nil)
}


// Proxy streams a request straight through to the sidecar under the given path.
//
// This is how the two audio-bearing endpoints are served. The body is copied, never decoded, so
// caller audio passes through the gateway as bytes in flight and is never assembled into a Go
// value that could be logged, buffered or spilled.
func (c *Client) Proxy(w http.ResponseWriter, r *http.Request, targetPath string) {
	outbound := r.Clone(r.Context())
	outbound.URL.Path = targetPath
	outbound.URL.RawQuery = ""
	c.proxy.ServeHTTP(w, outbound)
}

func (c *Client) do(ctx context.Context, method, path string, body any, out any) error {
	var reader io.Reader
	if body != nil {
		buf, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(buf)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base.String()+path, reader)
	if err != nil {
		return err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("sidecar unreachable: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		var detail struct {
			Detail any `json:"detail"`
		}
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<10))
		message := string(raw)
		if json.Unmarshal(raw, &detail) == nil && detail.Detail != nil {
			if s, ok := detail.Detail.(string); ok {
				message = s
			}
		}
		return &Error{Status: resp.StatusCode, Detail: message}
	}
	if out == nil {
		io.Copy(io.Discard, resp.Body)
		return nil
	}
	return json.NewDecoder(resp.Body).Decode(out)
}
