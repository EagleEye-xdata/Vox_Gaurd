// Package decision is the Decision Service (docs/01 section 2, docs/02 section 8, DR-013, [Go]).
//
// It maps a session verdict plus the tenant policy pack into an actionable decision, records a
// monotonic WAL sequence number and an origin signature for each one, and supports audited
// supervisor overrides.
//
// Every string here is read by a human under time pressure. CLAUDE.md invariant 12 forbids
// customer-facing copy that states a conclusion about a person: this service says what
// verification is required, never that someone is committing fraud.
package decision

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/vox-guard/voxguard/gateway/internal/policy"
	"github.com/vox-guard/voxguard/gateway/internal/scoring"
)

// ErrInvalidDecision reports a decision outside the closed enum.
var ErrInvalidDecision = errors.New("invalid decision")

// ErrReasonRequired reports a missing or too-short override justification.
var ErrReasonRequired = errors.New("a documented justification reason is required")

// ErrRoleNotAuthorised reports a role that may not override automated decisions.
var ErrRoleNotAuthorised = errors.New("role is not authorised to override automated decisions")

var validDecisions = map[string]bool{
	"ALLOW": true, "WARN": true, "STEP_UP": true, "ESCALATE": true, "BLOCK": true,
}

var authorisedRoles = map[string]bool{
	"SUPERVISOR": true, "FRAUD_ANALYST": true, "INCIDENT_COMMANDER": true, "ADMIN": true,
}

// Action is one required follow-up step attached to a decision.
type Action struct {
	Type         string `json:"type"`
	Channel      string `json:"channel,omitempty"`
	Mandatory    bool   `json:"mandatory,omitempty"`
	AutoExecute  bool   `json:"auto_execute,omitempty"`
	Assignee     string `json:"assignee,omitempty"`
	AuthorizedBy string `json:"authorized_by,omitempty"`
}

// Record is a decision as returned to callers and written to the audit log.
type Record struct {
	SessionID           string    `json:"session_id"`
	Decision            string    `json:"decision"`
	RequiredActions     []Action  `json:"required_actions"`
	ReasonCode          string    `json:"reason_code"`
	ExplanationForHuman string    `json:"explanation_for_human"`
	Reversible          bool      `json:"reversible"`
	PolicyVersion       string    `json:"policy_version"`
	WALSeq              int64     `json:"wal_seq"`
	OriginSignature     string    `json:"origin_signature"`
	Overridden          bool      `json:"overridden"`
	OverrideDetails     *Override `json:"override_details"`
}

// Override is an audited supervisor override of an automated decision.
type Override struct {
	SessionID       string   `json:"session_id"`
	Decision        string   `json:"decision"`
	RequiredActions []Action `json:"required_actions"`
	ReasonCode      string   `json:"reason_code"`
	Reason          string   `json:"reason"`
	SupervisorID    string   `json:"supervisor_id"`
	Role            string   `json:"role"`
	Timestamp       string   `json:"timestamp"`
	WALSeq          int64    `json:"wal_seq"`
	OriginSignature string   `json:"origin_signature"`
}

// Verdict is the session state a decision is computed from.
type Verdict struct {
	SessionID    string
	Band         string
	SessionScore *float64
	Degraded     bool
}

// Service owns the WAL sequence and the active overrides.
type Service struct {
	mu         sync.Mutex
	walSeq     int64
	overrides  map[string]Override
	signingKey []byte
}

// New builds a Decision Service. The signing key is the origin's, not the audit store's
// (CLAUDE.md invariant 9).
func New(signingKey []byte) *Service {
	return &Service{walSeq: 1000, overrides: map[string]Override{}, signingKey: signingKey}
}

func (s *Service) sign(payload string) string {
	mac := hmac.New(sha256.New, s.signingKey)
	mac.Write([]byte(payload))
	return "hmac-sha256:" + hex.EncodeToString(mac.Sum(nil))
}

func (s *Service) nextWALSeq() int64 {
	s.walSeq++
	return s.walSeq
}

// Decide computes the automated decision for a session verdict.
func (s *Service) Decide(v Verdict, c *scoring.Context, p policy.Pack) Record {
	s.mu.Lock()
	defer s.mu.Unlock()

	sessionID := v.SessionID
	if sessionID == "" {
		sessionID = "unknown-session"
	}

	if o, ok := s.overrides[sessionID]; ok {
		copied := o
		return Record{
			SessionID:       sessionID,
			Decision:        o.Decision,
			RequiredActions: o.RequiredActions,
			ReasonCode:      "OVERRIDE_" + o.ReasonCode,
			ExplanationForHuman: fmt.Sprintf("Supervisor Override by %s (%s): %s",
				o.SupervisorID, o.Role, o.Reason),
			Reversible:      true,
			PolicyVersion:   p.Version,
			WALSeq:          o.WALSeq,
			OriginSignature: o.OriginSignature,
			Overridden:      true,
			OverrideDetails: &copied,
		}
	}

	txnValue := 0.0
	if c != nil && c.TransactionValue != nil {
		txnValue = *c.TransactionValue
	}
	newBeneficiary := c != nil && c.BeneficiaryIsNew != nil && *c.BeneficiaryIsNew
	attestation := "UNKNOWN"
	if c != nil && c.CallerAttestation != nil {
		attestation = *c.CallerAttestation
	}

	walSeq := s.nextWALSeq()
	var decision, reasonCode, explanation string
	var actions []Action

	switch v.Band {
	case "HIGH":
		if txnValue > 100000 || newBeneficiary {
			decision = "BLOCK"
			reasonCode = "SYNTHETIC_HIGH_VALUE_OR_NEW_BENEFICIARY_RISK"
			explanation = "Elevated synthetic speech probability on a high-value or new beneficiary request requires immediate transaction hold."
			actions = []Action{
				{Type: "hold_transaction", Channel: "core_banking", AutoExecute: true},
				{Type: "callback_verification", Channel: "registered_phone", Mandatory: true},
				{Type: "supervisor_review", Channel: "fraud_ops"},
			}
		} else {
			decision = "STEP_UP"
			reasonCode = "SYNTHETIC_SPEECH_LIKELY_STEP_UP_REQUIRED"
			explanation = "Analysis indicates elevated synthetic speech markers. Multi-factor verification is required before continuing."
			actions = []Action{
				{Type: "mfa_push_or_otp", Channel: "registered_device", Mandatory: true},
				{Type: "out_of_band_prompt", Channel: "banking_app"},
			}
		}
	case "MEDIUM":
		if attestation != "VERIFIED" || newBeneficiary {
			decision = "STEP_UP"
			reasonCode = "ELEVATED_RISK_UNVERIFIED_CALLER"
			explanation = "Moderate acoustic risk combined with unverified caller status requires step-up confirmation."
			actions = []Action{{Type: "otp_challenge", Channel: "registered_device", Mandatory: true}}
		} else {
			decision = "WARN"
			reasonCode = "MODERATE_RISK_AGENT_CAUTION"
			explanation = "Moderate risk factors observed. Proceed with standard security verification protocol."
			actions = []Action{
				{Type: "agent_caution_banner", Channel: "operator_console"},
				{Type: "security_question_step", Channel: "call_script"},
			}
		}
	case "LOW":
		decision = "ALLOW"
		reasonCode = "ROUTINE_LOW_RISK_ALLOW"
		explanation = "Acoustic and contextual signals meet low-risk threshold. Routine processing permitted."
		actions = []Action{}
	default:
		// UNKNOWN or degraded. CLAUDE.md invariant 2: the absence of an assessment is not a pass,
		// so the circuit-breaker default is the policy pack's degraded decision, never ALLOW.
		decision = p.DefaultDegradedDecision
		reasonCode = "UNASSESSED_CALL_START"
		if v.Degraded {
			reasonCode = "INSUFFICIENT_EVIDENCE_DEGRADED_DEFAULT"
		}
		explanation = "Assessment incomplete or operating in degraded mode. Caution advised until sufficient voiced audio is assessed."
		actions = []Action{{Type: "agent_advisory", Channel: "operator_console"}}
	}

	return Record{
		SessionID:           sessionID,
		Decision:            decision,
		RequiredActions:     actions,
		ReasonCode:          reasonCode,
		ExplanationForHuman: explanation,
		Reversible:          true,
		PolicyVersion:       p.Version,
		WALSeq:              walSeq,
		OriginSignature: s.sign(fmt.Sprintf("%s:%s:%s:%d:%s",
			sessionID, decision, v.Band, walSeq, reasonCode)),
		Overridden:      false,
		OverrideDetails: nil,
	}
}

// Override registers an audited supervisor override.
//
// The role check and the mandatory reason are not ceremony: an override is the one place a human
// can move a decision toward ALLOW, so who did it and why must be recoverable afterwards.
func (s *Service) Override(sessionID, decision, reason, supervisorID, role string) (Override, error) {
	if !validDecisions[decision] {
		return Override{}, fmt.Errorf("%w: %q must be one of ALLOW, WARN, STEP_UP, ESCALATE, BLOCK",
			ErrInvalidDecision, decision)
	}
	if len(strings.TrimSpace(reason)) < 5 {
		return Override{}, fmt.Errorf("%w (minimum 5 characters) for supervisor overrides", ErrReasonRequired)
	}
	if !authorisedRoles[role] {
		return Override{}, fmt.Errorf("%w: %q", ErrRoleNotAuthorised, role)
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	walSeq := s.nextWALSeq()
	var actions []Action
	switch decision {
	case "ALLOW":
		actions = []Action{{Type: "override_release", AuthorizedBy: supervisorID}}
	case "STEP_UP", "ESCALATE":
		actions = []Action{{Type: "manual_investigation_required", Assignee: supervisorID}}
	case "BLOCK":
		actions = []Action{{Type: "manual_fraud_freeze", AuthorizedBy: supervisorID}}
	default:
		actions = []Action{}
	}

	record := Override{
		SessionID:       sessionID,
		Decision:        decision,
		RequiredActions: actions,
		ReasonCode:      "MANUAL_SUPERVISOR_ACTION",
		Reason:          reason,
		SupervisorID:    supervisorID,
		Role:            role,
		Timestamp:       time.Now().UTC().Format(time.RFC3339Nano),
		WALSeq:          walSeq,
		OriginSignature: s.sign(fmt.Sprintf("OVERRIDE:%s:%s:%d:%s", sessionID, decision, walSeq, supervisorID)),
	}
	s.overrides[sessionID] = record
	return record, nil
}

// ClearOverride removes an active override, if any.
func (s *Service) ClearOverride(sessionID string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.overrides[sessionID]; ok {
		delete(s.overrides, sessionID)
		return true
	}
	return false
}
