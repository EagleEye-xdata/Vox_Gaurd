// VoxGuard ARI Controller
//
// Connects to Asterisk ARI via WebSocket, manages the lifecycle of monitored
// calls, and orchestrates the snoop → ExternalMedia → detector pipeline.
//
// Architecture:
//   Asterisk ARI WebSocket (ws://127.0.0.1:8088/ari/events)
//       ← StasisStart / ChannelStateChange / StasisEnd events
//       → REST calls to create bridges, originate, snoop, ExternalMedia
//
// Call flow:
//   1. Caller dials 7000, enters Stasis(voxguard,target=1002)
//   2. Controller receives StasisStart
//   3. Creates a mixing bridge, originates PJSIP/1002
//   4. Adds caller + callee to the call bridge
//   5. Snoops the CALLER channel (spy=in)
//   6. Creates an analysis bridge with snoop + AudioSocket → Python ML sidecar
//   7. Detector analyzes caller audio in real time
//   8. On StasisEnd: closes detector session, logs call.ended
//
// Usage:
//   ARI_URL=http://127.0.0.1:8088 \
//   ARI_USER=voxguard \
//   ARI_SECRET=replace-me \
//   AUDIOSOCKET_SERVICE=host.docker.internal:9019 \
//   go run ./cmd/controller
package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/gorilla/websocket"

	"github.com/EagleEye-xdata/voxguard-telephony/internal/events"
	"github.com/EagleEye-xdata/voxguard-telephony/internal/webhook"
)

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

var (
	ariURL         = env("ARI_URL", "http://127.0.0.1:8088")
	ariUser        = env("ARI_USER", "voxguard")
	ariSecret      = os.Getenv("ARI_SECRET")
	ariApp         = env("ARI_APP", "voxguard")
	audioSocketService = env("AUDIOSOCKET_SERVICE", "host.docker.internal:9019")
	webhookURL     = env("WEBHOOK_URL", "")
	webhookSecret  = os.Getenv("WEBHOOK_SECRET")
)

var logger = slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))

func newUUID() string {
	var value [16]byte
	if _, err := rand.Read(value[:]); err != nil {
		panic(fmt.Sprintf("generate call UUID: %v", err))
	}
	value[6] = (value[6] & 0x0f) | 0x40
	value[8] = (value[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", value[0:4], value[4:6], value[6:8], value[8:10], value[10:16])
}

// ---------------------------------------------------------------------------
// ARI REST helpers
// ---------------------------------------------------------------------------

func ariRequest(ctx context.Context, method, path string, body interface{}) ([]byte, int, error) {
	var bodyReader io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return nil, 0, err
		}
		bodyReader = bytes.NewReader(b)
	}

	u := ariURL + path
	req, err := http.NewRequestWithContext(ctx, method, u, bodyReader)
	if err != nil {
		return nil, 0, err
	}
	req.SetBasicAuth(ariUser, ariSecret)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	return respBody, resp.StatusCode, nil
}

func ariGET(ctx context.Context, path string) ([]byte, int, error) {
	return ariRequest(ctx, http.MethodGet, path, nil)
}

func ariPOST(ctx context.Context, path string, body interface{}) ([]byte, int, error) {
	return ariRequest(ctx, http.MethodPost, path, body)
}

func ariDELETE(ctx context.Context, path string) ([]byte, int, error) {
	return ariRequest(ctx, http.MethodDelete, path, nil)
}

// ---------------------------------------------------------------------------
// Call state tracking
// ---------------------------------------------------------------------------

// MonitoredCall tracks the state of a call being analyzed.
type MonitoredCall struct {
	CallID          string
	TraceID         string
	CallerChannelID string
	CalleeChannelID string
	Target          string
	CallBridgeID    string
	AnalysisBridgeID string
	SnoopChannelID  string
	ExternalMediaID string
	DetectorSession string
	StartedAt       time.Time
}

var (
	activeCalls   = make(map[string]*MonitoredCall) // keyed by caller channel ID
	activeCallsMu sync.RWMutex
)

// ---------------------------------------------------------------------------
// Call lifecycle orchestration
// ---------------------------------------------------------------------------

func handleStasisStart(ctx context.Context, event map[string]interface{}, webhookSender *webhook.Sender) {
	channel, _ := event["channel"].(map[string]interface{})
	if channel == nil {
		return
	}
	channelID, _ := channel["id"].(string)
	channelName, _ := channel["name"].(string)

	// Extract target from Stasis args: Stasis(voxguard,target=1002)
	args, _ := event["args"].([]interface{})
	target := ""
	for _, arg := range args {
		s, ok := arg.(string)
		if ok && strings.HasPrefix(s, "target=") {
			target = strings.TrimPrefix(s, "target=")
		}
	}

	if target == "" {
		// This might be a callee channel entering stasis, or an analysis channel
		logger.Debug("StasisStart without target, skipping orchestration",
			"channel_id", channelID,
			"channel_name", channelName,
		)
		return
	}

	// chan_audiosocket requires a canonical UUID and sends it as the first framed message. The
	// Python listener uses this same id when registering the Go session.
	callID := newUUID()
	traceID := events.NewTraceID()

	call := &MonitoredCall{
		CallID:          callID,
		TraceID:         traceID,
		CallerChannelID: channelID,
		Target:          target,
		StartedAt:       time.Now(),
		DetectorSession: callID,
	}

	activeCallsMu.Lock()
	activeCalls[channelID] = call
	activeCallsMu.Unlock()

	logger.Info("StasisStart: monitored call",
		"call_id", callID,
		"caller_channel", channelID,
		"target", target,
	)

	// Emit call.control.started event
	if webhookSender != nil {
		evt := events.NormalizeARIEvent("StasisStart", callID, "", traceID, &events.PBXInfo{
			CallerChannelID: channelID,
		})
		go func() {
			sctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
			defer cancel()
			webhookSender.Send(sctx, evt)
		}()
	}

	// Step 1: Create the normal call bridge (mixing)
	bridgeBody, status, err := ariPOST(ctx, "/ari/bridges?type=mixing&name=call-"+callID, nil)
	if err != nil || status >= 400 {
		logger.Error("failed to create call bridge", "error", err, "status", status, "body", string(bridgeBody))
		return
	}
	var bridgeResp map[string]interface{}
	json.Unmarshal(bridgeBody, &bridgeResp)
	call.CallBridgeID, _ = bridgeResp["id"].(string)
	logger.Info("call bridge created", "bridge_id", call.CallBridgeID)

	// Step 2: Add caller to bridge
	_, status, err = ariPOST(ctx, fmt.Sprintf("/ari/bridges/%s/addChannel?channel=%s", call.CallBridgeID, url.QueryEscape(channelID)), nil)
	if err != nil || status >= 400 {
		logger.Error("failed to add caller to bridge", "error", err, "status", status)
	}

	// Step 3: Originate callee
	originateURL := fmt.Sprintf(
		"/ari/channels?endpoint=PJSIP/%s&app=%s&appArgs=callee,%s&callerId=%s",
		target, ariApp, callID, url.QueryEscape(channelID),
	)
	calleeBody, status, err := ariPOST(ctx, originateURL, nil)
	if err != nil || status >= 400 {
		logger.Error("failed to originate callee", "error", err, "status", status, "body", string(calleeBody))
		return
	}
	var calleeResp map[string]interface{}
	json.Unmarshal(calleeBody, &calleeResp)
	call.CalleeChannelID, _ = calleeResp["id"].(string)
	logger.Info("callee originated", "callee_channel", call.CalleeChannelID, "target", target)

	// Step 4: Snoop the caller channel (spy=in — hear what the caller sends)
	snoopID := "snoop-" + callID
	snoopURL := fmt.Sprintf(
		"/ari/channels/%s/snoop?snoopId=%s&spy=in&whisper=none&app=%s&appArgs=analysis,%s",
		url.PathEscape(channelID), snoopID, ariApp, callID,
	)
	snoopBody, status, err := ariPOST(ctx, snoopURL, nil)
	if err != nil || status >= 400 {
		logger.Error("failed to create snoop", "error", err, "status", status, "body", string(snoopBody))
		return
	}
	call.SnoopChannelID = snoopID
	logger.Info("caller snoop created", "snoop_id", snoopID, "spy", "in")

	// Step 5: Create analysis bridge
	analysisBridgeBody, status, err := ariPOST(ctx, "/ari/bridges?type=mixing&name=analysis-"+callID, nil)
	if err != nil || status >= 400 {
		logger.Error("failed to create analysis bridge", "error", err, "status", status)
		return
	}
	var analysisBridgeResp map[string]interface{}
	json.Unmarshal(analysisBridgeBody, &analysisBridgeResp)
	call.AnalysisBridgeID, _ = analysisBridgeResp["id"].(string)
	logger.Info("analysis bridge created", "bridge_id", call.AnalysisBridgeID)

	// Add snoop to analysis bridge
	_, status, err = ariPOST(ctx, fmt.Sprintf("/ari/bridges/%s/addChannel?channel=%s", call.AnalysisBridgeID, snoopID), nil)
	if err != nil || status >= 400 {
		logger.Error("failed to add snoop to analysis bridge", "error", err, "status", status)
	}

	// Step 6: create an AudioSocket external-media channel directly to Python. Asterisk sends
	// signed-linear 16-bit, 8 kHz mono PCM; the sidecar owns and clears every raw buffer.
	extMediaURL := fmt.Sprintf(
		"/ari/channels/externalMedia?app=%s&external_host=%s&transport=tcp&encapsulation=audiosocket&format=slin&direction=both&data=%s",
		url.QueryEscape(ariApp), url.QueryEscape(audioSocketService), url.QueryEscape(callID),
	)
	extBody, status, err := ariPOST(ctx, extMediaURL, nil)
	if err != nil || status >= 400 {
		logger.Error("failed to create ExternalMedia", "error", err, "status", status, "body", string(extBody))
		return
	}
	var extResp map[string]interface{}
	json.Unmarshal(extBody, &extResp)
	call.ExternalMediaID, _ = extResp["id"].(string)
	logger.Info("ExternalMedia channel created", "channel_id", call.ExternalMediaID)

	// Add ExternalMedia to analysis bridge
	_, status, err = ariPOST(ctx, fmt.Sprintf("/ari/bridges/%s/addChannel?channel=%s",
		call.AnalysisBridgeID, url.QueryEscape(call.ExternalMediaID)), nil)
	if err != nil || status >= 400 {
		logger.Error("failed to add ExternalMedia to analysis bridge", "error", err, "status", status)
	}

	logger.Info("call fully orchestrated",
		"call_id", callID,
		"caller", channelID,
		"callee", call.CalleeChannelID,
		"call_bridge", call.CallBridgeID,
		"analysis_bridge", call.AnalysisBridgeID,
		"snoop", snoopID,
		"external_media", call.ExternalMediaID,
	)
}

func handleChannelStateChange(ctx context.Context, event map[string]interface{}, webhookSender *webhook.Sender) {
	channel, _ := event["channel"].(map[string]interface{})
	if channel == nil {
		return
	}
	channelID, _ := channel["id"].(string)
	state, _ := channel["state"].(string)

	if state != "Up" {
		return
	}

	// Check if this is a callee channel that just answered — add it to the call bridge
	activeCallsMu.RLock()
	for _, call := range activeCalls {
		if call.CalleeChannelID == channelID && call.CallBridgeID != "" {
			activeCallsMu.RUnlock()

			_, status, err := ariPOST(ctx, fmt.Sprintf("/ari/bridges/%s/addChannel?channel=%s",
				call.CallBridgeID, url.QueryEscape(channelID)), nil)
			if err != nil || status >= 400 {
				logger.Error("failed to add callee to bridge", "error", err, "status", status)
			} else {
				logger.Info("callee answered and added to bridge",
					"call_id", call.CallID,
					"callee_channel", channelID,
					"bridge_id", call.CallBridgeID,
				)
			}

			// Emit call.answered webhook
			if webhookSender != nil {
				evt := events.NormalizeARIEvent("ChannelStateChange", call.CallID, "", call.TraceID, &events.PBXInfo{
					CallerChannelID: call.CallerChannelID,
					CalleeChannelID: channelID,
					BridgeID:        call.CallBridgeID,
				})
				evt.Type = events.TypeCallAnswered
				go func() {
					sctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
					defer cancel()
					webhookSender.Send(sctx, evt)
				}()
			}
			return
		}
	}
	activeCallsMu.RUnlock()
}

func handleStasisEnd(ctx context.Context, event map[string]interface{}, webhookSender *webhook.Sender) {
	channel, _ := event["channel"].(map[string]interface{})
	if channel == nil {
		return
	}
	channelID, _ := channel["id"].(string)

	activeCallsMu.Lock()
	call, exists := activeCalls[channelID]
	if exists {
		delete(activeCalls, channelID)
	}
	activeCallsMu.Unlock()

	if !exists {
		return
	}

	duration := time.Since(call.StartedAt)
	logger.Info("call ended",
		"call_id", call.CallID,
		"channel_id", channelID,
		"duration_ms", duration.Milliseconds(),
	)

	// Clean up ARI resources
	cleanup := func(path, label string) {
		if path == "" {
			return
		}
		_, status, err := ariDELETE(ctx, path)
		if err != nil || status >= 400 {
			logger.Debug("cleanup "+label, "path", path, "status", status, "error", err)
		}
	}
	cleanup(fmt.Sprintf("/ari/channels/%s", call.ExternalMediaID), "AudioSocket channel")
	cleanup(fmt.Sprintf("/ari/channels/%s", call.SnoopChannelID), "snoop channel")
	cleanup(fmt.Sprintf("/ari/bridges/%s", call.AnalysisBridgeID), "analysis bridge")
	cleanup(fmt.Sprintf("/ari/bridges/%s", call.CallBridgeID), "call bridge")

	// Emit call.ended webhook
	if webhookSender != nil {
		evt := events.NewCallEnded(call.CallID, call.DetectorSession, call.TraceID, "normal_clearing", &events.CallSummary{
			DurationMs: duration.Milliseconds(),
		})
		go func() {
			sctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
			defer cancel()
			webhookSender.Send(sctx, evt)
		}()
	}
}

// ---------------------------------------------------------------------------
// ARI Event Loop
// ---------------------------------------------------------------------------

func runEventLoop(ctx context.Context) error {
	// Build ARI WebSocket URL
	ariWSURL := strings.Replace(ariURL, "http://", "ws://", 1)
	ariWSURL = strings.Replace(ariWSURL, "https://", "wss://", 1)
	ariWSURL += fmt.Sprintf("/ari/events?app=%s&subscribeAll=true&api_key=%s:%s",
		ariApp, url.QueryEscape(ariUser), url.QueryEscape(ariSecret))

	var webhookSender *webhook.Sender
	if webhookURL != "" {
		webhookSender = webhook.NewSender(webhookURL, webhookSecret)
	}

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		logger.Info("connecting to ARI", "url", ariURL+"/ari/events")

		conn, _, err := websocket.DefaultDialer.DialContext(ctx, ariWSURL, nil)
		if err != nil {
			logger.Error("ARI connection failed, retrying in 5s", "error", err.Error())
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(5 * time.Second):
			}
			continue
		}

		logger.Info("connected to ARI WebSocket")

		// Read events
		for {
			_, message, err := conn.ReadMessage()
			if err != nil {
				logger.Warn("ARI read error, reconnecting", "error", err.Error())
				conn.Close()
				break
			}

			var event map[string]interface{}
			if err := json.Unmarshal(message, &event); err != nil {
				continue
			}

			eventType, _ := event["type"].(string)
			switch eventType {
			case "StasisStart":
				go handleStasisStart(ctx, event, webhookSender)
			case "ChannelStateChange":
				go handleChannelStateChange(ctx, event, webhookSender)
			case "StasisEnd", "ChannelDestroyed":
				go handleStasisEnd(ctx, event, webhookSender)
			case "ChannelDtmfReceived":
				channel, _ := event["channel"].(map[string]interface{})
				digit, _ := event["digit"].(string)
				chID, _ := channel["id"].(string)
				logger.Info("DTMF received", "channel_id", chID, "digit", digit)
			default:
				logger.Debug("ARI event", "type", eventType)
			}
		}

		// Brief pause before reconnecting
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(2 * time.Second):
		}
	}
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

func main() {
	if ariSecret == "" {
		logger.Error("ARI_SECRET is required")
		os.Exit(2)
	}
	if webhookURL != "" && webhookSecret == "" {
		logger.Error("WEBHOOK_SECRET is required when WEBHOOK_URL is set")
		os.Exit(2)
	}
	logger.Info("VoxGuard ARI Controller starting",
		"ari_url", ariURL,
		"ari_app", ariApp,
		"audiosocket_service", audioSocketService,
		"webhook_url", webhookURL,
	)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Graceful shutdown
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		sig := <-sigCh
		logger.Info("shutting down", "signal", sig.String())
		cancel()
	}()

	if err := runEventLoop(ctx); err != nil && err != context.Canceled {
		logger.Error("event loop error", "error", err.Error())
		os.Exit(1)
	}
}
