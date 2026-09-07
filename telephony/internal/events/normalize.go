// Package events provides a normalized event model for VoxGuard telephony.
//
// It maps raw events from Asterisk ARI, SIP, WebRTC, and the detector
// into a unified voxguard.call-event.v1 schema so that downstream
// consumers (dashboard, webhooks, ledger) are independent of the
// underlying telephony platform.
package events

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"time"
)

// Schema is the canonical event schema identifier.
const Schema = "voxguard.call-event.v1"

// CallEvent is the normalized event envelope sent to webhooks and logged.
type CallEvent struct {
	Schema     string            `json:"schema"`
	EventID    string            `json:"event_id"`
	Type       string            `json:"type"`
	OccurredAt string            `json:"occurred_at"`
	TraceID    string            `json:"trace_id,omitempty"`
	CallID     string            `json:"call_id"`
	SessionID  string            `json:"session_id,omitempty"`
	Seq        int               `json:"seq,omitempty"`
	From       *Endpoint         `json:"from,omitempty"`
	To         *Endpoint         `json:"to,omitempty"`
	PBX        *PBXInfo          `json:"pbx,omitempty"`
	Media      *MediaInfo        `json:"media,omitempty"`
	Detection  *DetectionInfo    `json:"detection,omitempty"`
	Summary    *CallSummary      `json:"summary,omitempty"`
	Reason     string            `json:"reason,omitempty"`
	Extra      map[string]string `json:"extra,omitempty"`
}

// Endpoint identifies a SIP endpoint.
type Endpoint struct {
	URI         string `json:"uri,omitempty"`
	DisplayName string `json:"display_name,omitempty"`
}

// PBXInfo carries Asterisk-specific channel identifiers.
type PBXInfo struct {
	CallerChannelID string `json:"caller_channel_id,omitempty"`
	CalleeChannelID string `json:"callee_channel_id,omitempty"`
	BridgeID        string `json:"bridge_id,omitempty"`
	SnoopChannelID  string `json:"snoop_channel_id,omitempty"`
}

// MediaInfo describes the media stream configuration.
type MediaInfo struct {
	Encoding     string `json:"encoding,omitempty"`
	SampleRateHz int    `json:"sample_rate_hz,omitempty"`
	Channels     int    `json:"channels,omitempty"`
	AnalyzedLeg  string `json:"analyzed_leg,omitempty"`
	CodecAtEP    string `json:"codec_at_endpoint,omitempty"`
}

// DetectionInfo carries a single detector verdict.
type DetectionInfo struct {
	WindowScore     *float64 `json:"window_score,omitempty"`
	SessionScore    *float64 `json:"session_score,omitempty"`
	Band            string   `json:"band,omitempty"`
	BandChanged     bool     `json:"band_changed,omitempty"`
	Degraded        bool     `json:"degraded,omitempty"`
	DegradedReasons []string `json:"degraded_reasons,omitempty"`
	LatencyMs       float64  `json:"latency_ms,omitempty"`
	Decision        string   `json:"decision,omitempty"`
}

// CallSummary is included in call.ended events.
type CallSummary struct {
	DurationMs    int64   `json:"duration_ms"`
	WindowsScored int     `json:"windows_scored"`
	FinalScore    float64 `json:"final_score"`
	FinalBand     string  `json:"final_band"`
	HighestBand   string  `json:"highest_band"`
	Degraded      bool    `json:"degraded"`
}

// randomHex generates a short random hex string for event/trace IDs.
func randomHex(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// NewEventID creates a unique event identifier.
func NewEventID() string {
	return "evt_" + randomHex(4)
}

// NewTraceID creates a unique trace identifier for correlating related events.
func NewTraceID() string {
	return "tr_" + randomHex(4)
}

// Now returns the current UTC time in ISO 8601 format.
func Now() string {
	return time.Now().UTC().Format(time.RFC3339Nano)
}

// ---------------------------------------------------------------------------
// Normalization functions: source-specific events → CallEvent
// ---------------------------------------------------------------------------

// Normalized event types.
const (
	TypeCallInitiated      = "call.initiated"
	TypeCallTrying         = "call.trying"
	TypeCallRinging        = "call.ringing"
	TypeCallEarlyMedia     = "call.early_media"
	TypeCallAnswered       = "call.answered"
	TypeCallCancelled      = "call.cancelled"
	TypeCallEnded          = "call.ended"
	TypeCallTransfer       = "call.transfer.requested"
	TypeCallControlStarted = "call.control.started"
	TypeCallMediaConnected = "call.media.connected"
	TypeDTMFReceived       = "dtmf.received"
	TypeMediaStreamStarted = "media.stream.started"
	TypeMediaBackpressOn   = "media.backpressure.on"
	TypeMediaBackpressOff  = "media.backpressure.off"
	TypeDetectorVerdict    = "detector.verdict"
	TypeDetectorAlert      = "detector.alert"
	TypeClientConnecting   = "client.call.connecting"
	TypeClientConnected    = "client.call.connected"
	TypeClientTerminated   = "client.call.terminated"
	TypeWebRTCState        = "webrtc.connection.state"
	TypeWebRTCICEState     = "webrtc.ice.state"
	TypeWebRTCMedia        = "webrtc.remote_media.available"
	TypeWebRTCICEError     = "webrtc.ice.error"
)

// NormalizeARIEvent maps an Asterisk ARI event type to a normalized CallEvent.
func NormalizeARIEvent(ariType, callID, sessionID, traceID string, pbx *PBXInfo) *CallEvent {
	var eventType string
	switch ariType {
	case "StasisStart":
		eventType = TypeCallControlStarted
	case "ChannelStateChange":
		eventType = TypeCallAnswered
	case "ChannelEnteredBridge":
		eventType = TypeCallMediaConnected
	case "ChannelDtmfReceived":
		eventType = TypeDTMFReceived
	case "StasisEnd", "ChannelDestroyed":
		eventType = TypeCallEnded
	default:
		eventType = fmt.Sprintf("asterisk.%s", ariType)
	}
	return &CallEvent{
		Schema:     Schema,
		EventID:    NewEventID(),
		Type:       eventType,
		OccurredAt: Now(),
		TraceID:    traceID,
		CallID:     callID,
		SessionID:  sessionID,
		PBX:        pbx,
	}
}

// NormalizeMediaEvent maps Asterisk WebSocket media control events.
func NormalizeMediaEvent(mediaEventType, callID, sessionID, traceID string, media *MediaInfo) *CallEvent {
	var eventType string
	switch mediaEventType {
	case "MEDIA_START":
		eventType = TypeMediaStreamStarted
	case "MEDIA_XOFF":
		eventType = TypeMediaBackpressOn
	case "MEDIA_XON":
		eventType = TypeMediaBackpressOff
	default:
		eventType = fmt.Sprintf("asterisk.media.%s", mediaEventType)
	}
	return &CallEvent{
		Schema:     Schema,
		EventID:    NewEventID(),
		Type:       eventType,
		OccurredAt: Now(),
		TraceID:    traceID,
		CallID:     callID,
		SessionID:  sessionID,
		Media:      media,
	}
}

// NewDetectorVerdict creates a normalized detector verdict event.
func NewDetectorVerdict(callID, sessionID, traceID string, seq int, detection *DetectionInfo, media *MediaInfo) *CallEvent {
	return &CallEvent{
		Schema:     Schema,
		EventID:    NewEventID(),
		Type:       TypeDetectorVerdict,
		OccurredAt: Now(),
		TraceID:    traceID,
		CallID:     callID,
		SessionID:  sessionID,
		Seq:        seq,
		Detection:  detection,
		Media:      media,
	}
}

// NewCallEnded creates a normalized call.ended event with summary.
func NewCallEnded(callID, sessionID, traceID, reason string, summary *CallSummary) *CallEvent {
	return &CallEvent{
		Schema:     Schema,
		EventID:    NewEventID(),
		Type:       TypeCallEnded,
		OccurredAt: Now(),
		TraceID:    traceID,
		CallID:     callID,
		SessionID:  sessionID,
		Reason:     reason,
		Summary:    summary,
	}
}
