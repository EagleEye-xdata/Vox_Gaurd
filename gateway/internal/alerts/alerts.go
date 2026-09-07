// Package alerts is the Alert Service (docs/01 section 2, docs/02 sections 10 and 10.1,
// DR-008, DR-019, [Go]).
//
// It holds deduplicated, idempotency-keyed alerts — one per band escalation, never one per
// window (CLAUDE.md invariant 11) — tracks the human-in-the-loop SLA, and records resolution
// outcomes and customer appeals.
package alerts

import (
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
)

// ErrInvalidOutcome reports a resolution outcome outside the closed enum. The enum is closed
// because these outcomes are the feedback signal a future detector is trained on; free text
// would make the loop unusable.
var ErrInvalidOutcome = errors.New("invalid resolution outcome")

// ErrNotesRequired reports a resolution with no investigation notes.
var ErrNotesRequired = errors.New("investigation notes are mandatory when resolving an alert")

// ErrReasonRequired reports an appeal with no stated reason.
var ErrReasonRequired = errors.New("a detailed explanation is required to lodge an appeal")

// ErrNotFound reports an unknown alert id.
var ErrNotFound = errors.New("alert not found")

var validOutcomes = map[string]bool{
	"CONFIRMED_FRAUD": true, "FALSE_POSITIVE": true, "INCONCLUSIVE": true,
}

// Resolution closes an alert with an outcome an analyst stands behind.
type Resolution struct {
	Outcome    string `json:"outcome"`
	Notes      string `json:"notes"`
	ResolverID string `json:"resolver_id"`
	ResolvedAt string `json:"resolved_at"`
}

// Appeal is a customer or agent challenge to an alert or a stepped-up decision (docs/09 §5).
type Appeal struct {
	AppealID              string `json:"appeal_id"`
	AlertID               string `json:"alert_id"`
	CallID                string `json:"call_id"`
	AppellantType         string `json:"appellant_type"`
	Reason                string `json:"reason"`
	ContactInfo           string `json:"contact_info"`
	Status                string `json:"status"`
	SLAResolutionDeadline string `json:"sla_resolution_deadline"`
	FiledAt               string `json:"filed_at"`
}

// Alert is one escalation raised to the fraud desk.
type Alert struct {
	ID        string  `json:"id"`
	CallID    string  `json:"call_id"`
	RiskScore float64 `json:"risk_score"`
	Band      string  `json:"band"`
	Status    string  `json:"status"`

	CreatedAt string `json:"created_at"`
	UpdatedAt string `json:"updated_at"`

	SLAAckDeadline     string `json:"sla_ack_deadline"`
	SLAResolveDeadline string `json:"sla_resolve_deadline"`

	AssignedTo *string     `json:"assigned_to"`
	Resolution *Resolution `json:"resolution"`
	Appeals    []Appeal    `json:"appeals"`

	// Message and Recommendations are read by an agent mid-call. CLAUDE.md invariant 12: they
	// say what verification is required, never that the person on the line is a fraudster.
	Message         string   `json:"message"`
	Recommendations []string `json:"recommendations"`

	AutoBlock           bool   `json:"auto_block"`
	NotificationChannel string `json:"notification_channel"`

	PolicyVersion string            `json:"policy_version"`
	ModelVersions map[string]string `json:"model_versions"`
}

// SLADeadlines computes the acknowledge and resolve deadlines for a band (docs/02 §10.1).
func SLADeadlines(band string, createdAt time.Time) (string, string) {
	var ack, resolve time.Duration
	switch band {
	case "HIGH":
		ack, resolve = 2*time.Minute, 30*time.Minute
	case "MEDIUM":
		ack, resolve = 15*time.Minute, 4*time.Hour
	default:
		ack, resolve = time.Hour, 24*time.Hour
	}
	return createdAt.Add(ack).Format(time.RFC3339Nano), createdAt.Add(resolve).Format(time.RFC3339Nano)
}

// Store holds the alerts for this process.
type Store struct {
	mu      sync.RWMutex
	byID    map[string]*Alert
	ordered []*Alert
}

// NewStore builds an empty alert store.
func NewStore() *Store {
	return &Store{byID: map[string]*Alert{}}
}

// Ensure returns the alert for an idempotency key, creating it on first sight and refreshing the
// score on every later sight. The key is the escalation key from the session aggregator, so a
// session that stays HIGH for hundreds of windows still yields exactly one alert per escalation.
// Created reports whether this call was the one that raised it.
func (s *Store) Ensure(key, callID string, score float64, band, policyVersion string,
	modelVersions map[string]string) (alert *Alert, created bool) {

	s.mu.Lock()
	defer s.mu.Unlock()

	now := time.Now().UTC()
	if existing, ok := s.byID[key]; ok {
		existing.RiskScore = score
		existing.UpdatedAt = now.Format(time.RFC3339Nano)
		return existing, false
	}

	ack, resolve := SLADeadlines(band, now)
	a := &Alert{
		ID:                 key,
		CallID:             callID,
		RiskScore:          score,
		Band:               band,
		Status:             "active",
		CreatedAt:          now.Format(time.RFC3339Nano),
		UpdatedAt:          now.Format(time.RFC3339Nano),
		SLAAckDeadline:     ack,
		SLAResolveDeadline: resolve,
		Appeals:            []Appeal{},
		Message:            "Additional verification required. Voice synthesis and contextual anomalies detected.",
		Recommendations: []string{
			"Out-of-band callback using verified profile number",
			"Require in-app biometric / hardware token MFA",
			"Hold high-value transactions pending supervisor sign-off",
		},
		AutoBlock:           band == "HIGH",
		NotificationChannel: "DashboardChannel (with Webhook retry lane)",
		PolicyVersion:       policyVersion,
		ModelVersions:       modelVersions,
	}
	s.byID[key] = a
	s.ordered = append(s.ordered, a)
	return a, true
}

// Get returns one alert by id.
func (s *Store) Get(id string) (*Alert, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	a, ok := s.byID[id]
	if !ok {
		return nil, ErrNotFound
	}
	return a, nil
}

// List returns every alert, newest first.
func (s *Store) List() []*Alert {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]*Alert, 0, len(s.ordered))
	for i := len(s.ordered) - 1; i >= 0; i-- {
		out = append(out, s.ordered[i])
	}
	return out
}

// Assign routes an alert to an investigator.
func (s *Store) Assign(id, assigneeID string) (*Alert, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	a, ok := s.byID[id]
	if !ok {
		return nil, ErrNotFound
	}
	a.AssignedTo = &assigneeID
	if a.Status == "active" {
		a.Status = "assigned"
	}
	a.UpdatedAt = time.Now().UTC().Format(time.RFC3339Nano)
	return a, nil
}

// Resolve closes an alert with a mandatory closed-enum outcome and notes.
func (s *Store) Resolve(id, outcome, notes, resolverID string) (*Alert, error) {
	if !validOutcomes[outcome] {
		return nil, fmt.Errorf("%w %q: must be CONFIRMED_FRAUD, FALSE_POSITIVE or INCONCLUSIVE",
			ErrInvalidOutcome, outcome)
	}
	if len(strings.TrimSpace(notes)) < 3 {
		return nil, ErrNotesRequired
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	a, ok := s.byID[id]
	if !ok {
		return nil, ErrNotFound
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	a.Status = "resolved"
	a.UpdatedAt = now
	a.Resolution = &Resolution{Outcome: outcome, Notes: notes, ResolverID: resolverID, ResolvedAt: now}
	return a, nil
}

// Escalate marks an alert escalated. It reports whether this call was the transition, so the
// caller writes exactly one audit record rather than one per click.
func (s *Store) Escalate(id string) (*Alert, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	a, ok := s.byID[id]
	if !ok {
		return nil, false, ErrNotFound
	}
	if a.Status == "escalated" {
		return a, false, nil
	}
	a.Status = "escalated"
	a.UpdatedAt = time.Now().UTC().Format(time.RFC3339Nano)
	return a, true, nil
}

// LodgeAppeal files a challenge against an alert (docs/09 §5).
//
// The appeal path is not optional politeness. A system that can require step-up verification on
// the strength of an unvalidated acoustic model owes the person on the other end a route to
// contest it.
func (s *Store) LodgeAppeal(id, reason, appellantType, contactInfo string) (*Appeal, error) {
	if len(strings.TrimSpace(reason)) < 5 {
		return nil, ErrReasonRequired
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	a, ok := s.byID[id]
	if !ok {
		return nil, ErrNotFound
	}
	if contactInfo == "" {
		contactInfo = "Not provided"
	}
	if appellantType == "" {
		appellantType = "CUSTOMER"
	}
	now := time.Now().UTC()
	appeal := Appeal{
		AppealID:              "app-" + uuid.NewString()[:8],
		AlertID:               a.ID,
		CallID:                a.CallID,
		AppellantType:         appellantType,
		Reason:                reason,
		ContactInfo:           contactInfo,
		Status:                "PENDING_REVIEW",
		SLAResolutionDeadline: now.Add(24 * time.Hour).Format(time.RFC3339Nano),
		FiledAt:               now.Format(time.RFC3339Nano),
	}
	a.Appeals = append(a.Appeals, appeal)
	a.UpdatedAt = now.Format(time.RFC3339Nano)
	return &appeal, nil
}
