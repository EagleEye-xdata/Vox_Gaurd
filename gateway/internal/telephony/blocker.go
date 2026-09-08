// Package telephony turns a REJECT_AND_BLOCK decision into a terminated call.
//
// This closes the gap the README's own architecture diagram marks with a dotted
// line: the policy engine reaches REJECT_AND_BLOCK, seals an audit record, and
// then nothing happens to the call. The scammer stays on the line. A system that
// logs "blocked" without blocking is worse than one that does not claim to,
// because the operator believes they are protected.
//
// Drop in at: gateway/internal/telephony/blocker.go
//
// ORDERING INVARIANT
//
// The ledger write happens BEFORE the hangup, always. If Twilio times out we
// still hold a signed record that the decision was made; the reverse ordering
// loses blocked calls from the audit trail entirely on any transport failure,
// and the audit trail is the part of this system a bank would actually be
// audited on.
package telephony

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Blocker terminates a live call. Implementations are PBX-specific; the
// decision engine depends on this interface only, so swapping Twilio for
// Asterisk does not touch policy code.
type Blocker interface {
	Terminate(ctx context.Context, callID, reason string) error
	Name() string
}

// ---------------------------------------------------------------------------
// Twilio
// ---------------------------------------------------------------------------

// TwilioBlocker ends a call via the Twilio REST API by transitioning the call
// resource to "completed".
//
// Deliberately uses net/http rather than the Twilio Go SDK: this is one form
// POST, and the SDK would pull a large dependency tree into a gateway whose
// only other dependency is the standard library.
type TwilioBlocker struct {
	AccountSID string
	AuthToken  string
	HTTP       *http.Client
	Log        *slog.Logger
}

func NewTwilioBlocker(sid, token string, logger *slog.Logger) *TwilioBlocker {
	return &TwilioBlocker{
		AccountSID: sid,
		AuthToken:  token,
		HTTP:       &http.Client{Timeout: 5 * time.Second},
		Log:        logger,
	}
}

func (t *TwilioBlocker) Name() string { return "twilio" }

func (t *TwilioBlocker) Terminate(ctx context.Context, callSID, reason string) error {
	if t.AccountSID == "" || t.AuthToken == "" {
		return errors.New("twilio credentials not configured")
	}

	form := url.Values{"Status": {"completed"}}
	endpoint := fmt.Sprintf(
		"https://api.twilio.com/2010-04-01/Accounts/%s/Calls/%s.json",
		t.AccountSID, callSID)

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint,
		strings.NewReader(form.Encode()))
	if err != nil {
		return fmt.Errorf("build hangup request: %w", err)
	}
	req.SetBasicAuth(t.AccountSID, t.AuthToken)
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	resp, err := t.HTTP.Do(req)
	if err != nil {
		return fmt.Errorf("twilio hangup %s: %w", callSID, err)
	}
	defer resp.Body.Close()

	// 404 means the call already ended (caller hung up first). That is the
	// desired end state, so it is not an error worth alerting a human about.
	if resp.StatusCode == http.StatusNotFound {
		t.Log.Info("call already ended before block took effect", "call_sid", callSID)
		return nil
	}
	if resp.StatusCode >= 300 {
		return fmt.Errorf("twilio hangup %s: status %d", callSID, resp.StatusCode)
	}

	t.Log.Warn("call terminated by policy", "call_sid", callSID, "reason", reason)
	return nil
}

// ---------------------------------------------------------------------------
// Asterisk ARI
// ---------------------------------------------------------------------------

// AsteriskBlocker hangs up a channel through the Asterisk REST Interface.
// Used when the deployment is self-hosted and call audio must not leave the
// operator's own infrastructure.
type AsteriskBlocker struct {
	BaseURL  string // e.g. http://127.0.0.1:8088/ari
	Username string
	Password string
	HTTP     *http.Client
	Log      *slog.Logger
}

func NewAsteriskBlocker(baseURL, user, pass string, logger *slog.Logger) *AsteriskBlocker {
	return &AsteriskBlocker{
		BaseURL:  strings.TrimRight(baseURL, "/"),
		Username: user, Password: pass,
		HTTP: &http.Client{Timeout: 5 * time.Second},
		Log:  logger,
	}
}

func (a *AsteriskBlocker) Name() string { return "asterisk-ari" }

func (a *AsteriskBlocker) Terminate(ctx context.Context, channelID, reason string) error {
	endpoint := fmt.Sprintf("%s/channels/%s", a.BaseURL, url.PathEscape(channelID))
	req, err := http.NewRequestWithContext(ctx, http.MethodDelete, endpoint, nil)
	if err != nil {
		return fmt.Errorf("build ARI hangup: %w", err)
	}
	req.SetBasicAuth(a.Username, a.Password)

	resp, err := a.HTTP.Do(req)
	if err != nil {
		return fmt.Errorf("ari hangup %s: %w", channelID, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		a.Log.Info("channel already gone", "channel", channelID)
		return nil
	}
	if resp.StatusCode >= 300 {
		return fmt.Errorf("ari hangup %s: status %d", channelID, resp.StatusCode)
	}
	a.Log.Warn("channel terminated by policy", "channel", channelID, "reason", reason)
	return nil
}

// ---------------------------------------------------------------------------
// Demo / no-PBX mode
// ---------------------------------------------------------------------------

// LoopbackBlocker records the termination without contacting a PBX. This is the
// blocker the browser-based two-way call demo runs with: the front end observes
// the block through the existing WebSocket, so the full decision path is
// exercised end to end with no telephony account involved.
//
// It is a real implementation of the interface, not a stub — the demo path and
// the production path differ only in which Blocker is wired in at startup.
type LoopbackBlocker struct {
	Log     *slog.Logger
	OnBlock func(callID, reason string) // notify the WebSocket hub
}

func (l *LoopbackBlocker) Name() string { return "loopback-demo" }

func (l *LoopbackBlocker) Terminate(_ context.Context, callID, reason string) error {
	l.Log.Warn("call terminated by policy (loopback)", "call_id", callID, "reason", reason)
	if l.OnBlock != nil {
		l.OnBlock(callID, reason)
	}
	return nil
}

// ---------------------------------------------------------------------------
// Retry wrapper
// ---------------------------------------------------------------------------

// EnforceBlock applies the ordering invariant and retry policy around any
// Blocker. Call this from the decision path once the ledger record is sealed.
//
// A block that silently fails is more dangerous than no block at all: the
// operator sees "BLOCKED" on screen and stops watching a call that is still
// live. So a terminal failure raises an alert rather than returning quietly.
func EnforceBlock(ctx context.Context, b Blocker, callID, reason string,
	onFailure func(callID, reason string, err error), logger *slog.Logger) error {
	if b == nil {
		return errors.New("call blocker is not configured")
	}
	if logger == nil {
		logger = slog.Default()
	}

	var lastErr error
	for attempt := 1; attempt <= 3; attempt++ {
		attemptCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
		err := b.Terminate(attemptCtx, callID, reason)
		cancel()
		if err == nil {
			return nil
		}
		lastErr = err
		logger.Warn("hangup attempt failed",
			"blocker", b.Name(), "call_id", callID, "attempt", attempt, "err", err)
		if attempt < 3 {
			time.Sleep(time.Duration(attempt*attempt) * 200 * time.Millisecond)
		}
	}

	logger.Error("BLOCK ISSUED BUT HANGUP FAILED — call may still be live",
		"call_id", callID, "err", lastErr)
	if onFailure != nil {
		onFailure(callID, reason, lastErr)
	}
	return fmt.Errorf("hangup failed after 3 attempts: %w", lastErr)
}
