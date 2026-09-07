// Package webhook provides HMAC-SHA256 signed event delivery for VoxGuard.
//
// Events are signed using timestamp.body with a shared secret, matching
// the verification pattern described in the telephony integration spec.
// The receiver checks X-VoxGuard-Timestamp and X-VoxGuard-Signature headers
// and rejects requests with stale timestamps or invalid signatures.
package webhook

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"time"
)

// DefaultMaxRetries is the number of delivery attempts before giving up.
const DefaultMaxRetries = 3

// DefaultTimeout is the HTTP timeout for webhook delivery.
const DefaultTimeout = 10 * time.Second

// Sender delivers signed webhook events to a configured URL.
type Sender struct {
	URL        string
	Secret     []byte
	MaxRetries int
	Timeout    time.Duration
	Client     *http.Client
	Logger     *slog.Logger
}

// NewSender creates a Sender with sensible defaults.
func NewSender(url, secret string) *Sender {
	return &Sender{
		URL:        url,
		Secret:     []byte(secret),
		MaxRetries: DefaultMaxRetries,
		Timeout:    DefaultTimeout,
		Client:     &http.Client{Timeout: DefaultTimeout},
		Logger:     slog.Default(),
	}
}

// Sign computes the HMAC-SHA256 signature for timestamp.body.
func (s *Sender) Sign(timestamp string, body []byte) string {
	mac := hmac.New(sha256.New, s.Secret)
	mac.Write([]byte(timestamp))
	mac.Write([]byte("."))
	mac.Write(body)
	return hex.EncodeToString(mac.Sum(nil))
}

// Send delivers an event payload with HMAC signing and retry logic.
// It returns the HTTP status code of the last attempt and any error.
func (s *Sender) Send(ctx context.Context, event interface{}) (int, error) {
	body, err := json.Marshal(event)
	if err != nil {
		return 0, fmt.Errorf("webhook: marshal event: %w", err)
	}

	timestamp := strconv.FormatInt(time.Now().Unix(), 10)
	signature := s.Sign(timestamp, body)

	var lastStatus int
	var lastErr error

	retries := s.MaxRetries
	if retries <= 0 {
		retries = DefaultMaxRetries
	}

	for attempt := 1; attempt <= retries; attempt++ {
		req, err := http.NewRequestWithContext(ctx, http.MethodPost, s.URL, bytes.NewReader(body))
		if err != nil {
			return 0, fmt.Errorf("webhook: create request: %w", err)
		}

		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-VoxGuard-Timestamp", timestamp)
		req.Header.Set("X-VoxGuard-Signature", signature)
		req.Header.Set("User-Agent", "VoxGuard-Telephony/1.0")

		resp, err := s.Client.Do(req)
		if err != nil {
			lastErr = fmt.Errorf("webhook: attempt %d: %w", attempt, err)
			s.Logger.Warn("webhook delivery failed",
				"attempt", attempt,
				"url", s.URL,
				"error", err.Error(),
			)
		} else {
			_, _ = io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			lastStatus = resp.StatusCode

			if resp.StatusCode >= 200 && resp.StatusCode < 300 {
				s.Logger.Info("webhook delivered",
					"attempt", attempt,
					"url", s.URL,
					"status", resp.StatusCode,
				)
				return resp.StatusCode, nil
			}

			lastErr = fmt.Errorf("webhook: attempt %d: HTTP %d", attempt, resp.StatusCode)
			s.Logger.Warn("webhook delivery rejected",
				"attempt", attempt,
				"url", s.URL,
				"status", resp.StatusCode,
			)
		}

		// Exponential backoff: 500ms, 1s, 2s
		if attempt < retries {
			backoff := time.Duration(1<<uint(attempt-1)) * 500 * time.Millisecond
			select {
			case <-ctx.Done():
				return lastStatus, ctx.Err()
			case <-time.After(backoff):
			}
		}
	}

	return lastStatus, fmt.Errorf("webhook: all %d attempts failed: %w", retries, lastErr)
}

// Verify checks that a received webhook has a valid signature and timestamp.
// This is intended for use in the Express-compatible Go receiver.
func Verify(secret []byte, timestamp, providedSig string, body []byte, maxSkewSeconds int64) error {
	ts, err := strconv.ParseInt(timestamp, 10, 64)
	if err != nil {
		return fmt.Errorf("invalid timestamp")
	}

	age := time.Now().Unix() - ts
	if age < 0 {
		age = -age
	}
	if age > maxSkewSeconds {
		return fmt.Errorf("expired request: age %ds exceeds %ds", age, maxSkewSeconds)
	}

	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte(timestamp))
	mac.Write([]byte("."))
	mac.Write(body)
	expected := hex.EncodeToString(mac.Sum(nil))

	if !hmac.Equal([]byte(providedSig), []byte(expected)) {
		return fmt.Errorf("bad signature")
	}

	return nil
}
