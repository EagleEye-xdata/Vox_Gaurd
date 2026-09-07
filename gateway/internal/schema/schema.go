// Package schema validates request bodies at the gateway boundary (docs/01 section 2 "schema
// validate", docs/02 API contracts).
//
// The two audio-bearing endpoints are deliberately absent. POST /api/v1/detect and
// POST /api/v1/enrolments have their bodies streamed straight through to the Python sidecar,
// which owns their validation, because parsing them here would materialise a window of caller
// audio inside the gateway for no benefit (CLAUDE.md invariant 1).
//
// Ported from backend/app/schemas.py. Two Pydantic behaviours are load-bearing and reproduced
// deliberately: unknown fields are rejected rather than ignored, so a typo in a security-relevant
// field fails loudly instead of silently taking a default; and the attestation provenance rule is
// enforced here rather than in the scorer, so an untrusted claim can never reach the fusion in a
// trusted shape (CLAUDE.md invariant 5).
package schema

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"regexp"
	"strings"

	"github.com/vox-guard/voxguard/gateway/internal/scoring"
)

// ValidationError reports a rejected request body. It carries a message safe to return to the
// caller: field names and permitted values, never internal state.
type ValidationError struct{ Message string }

func (e *ValidationError) Error() string { return e.Message }

func invalid(format string, args ...any) error {
	return &ValidationError{Message: fmt.Sprintf(format, args...)}
}

// trustedAttestationSources are the sources that can support a VERIFIED attestation. Caller ID
// is not among them: it is trivially spoofable, and 03 section 4 forbids an adversary-controlled
// input from unlocking the only downward path in the score.
var trustedAttestationSources = map[string]bool{
	"STIR_SHAKEN_A": true, "AUTHENTICATED_APP_SESSION": true,
}

var (
	attestations       = set("VERIFIED", "KNOWN_UNVERIFIED", "UNKNOWN")
	attestationSources = set("STIR_SHAKEN_A", "AUTHENTICATED_APP_SESSION", "CALLER_ID_ONLY", "NONE")
	transactionTypes   = set("wire_transfer", "card_payment", "cash_withdrawal", "account_change", "none")
	urgencies          = set("low", "normal", "high")
	urgencySources     = set("AGENT_ASSERTED", "POLICY_DERIVED", "CALLER_CLAIMED")
	appellantTypes     = set("CUSTOMER", "AGENT", "SUPERVISOR")
	resolutionOutcomes = set("CONFIRMED_FRAUD", "FALSE_POSITIVE", "INCONCLUSIVE")
	decisions          = set("ALLOW", "WARN", "STEP_UP", "ESCALATE", "BLOCK")
	roles              = set("SUPERVISOR", "FRAUD_ANALYST", "INCIDENT_COMMANDER", "ADMIN")
	ledgerEventTypes   = set("observation", "alert", "escalation", "decision", "override", "call_completed")
	bands              = set("LOW", "MEDIUM", "HIGH", "UNKNOWN")
)

var (
	currencyPattern   = regexp.MustCompile(`^[A-Z]{3}$`)
	languagePattern   = regexp.MustCompile(`^[a-z]{2}(-[a-z]{2})?$`)
	identityIDPattern = regexp.MustCompile(`^[a-zA-Z0-9_-]+$`)
)

func set(values ...string) map[string]bool {
	m := make(map[string]bool, len(values))
	for _, v := range values {
		m[v] = true
	}
	return m
}

// Decode reads a JSON body into dst, rejecting unknown fields and trailing content.
func Decode(r io.Reader, dst any) error {
	dec := json.NewDecoder(r)
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		return invalid("Request body is not valid for this endpoint: %s", err.Error())
	}
	if dec.More() {
		return invalid("Request body must contain exactly one JSON object.")
	}
	return nil
}

// Context is the contextual-signal request model.
//
// The defaults match backend/app/schemas.py: an omitted field is not "no signal", it is the
// stated default, and callers that genuinely have no context omit the whole object.
type Context struct {
	CallerAttestation      *string  `json:"caller_attestation"`
	AttestationSource      *string  `json:"attestation_source"`
	TransactionValue       *float64 `json:"transaction_value"`
	TransactionCurrency    *string  `json:"transaction_currency"`
	TransactionType        *string  `json:"transaction_type"`
	BeneficiaryIsNew       *bool    `json:"beneficiary_is_new"`
	RequestUrgency         *string  `json:"request_urgency"`
	UrgencySource          *string  `json:"urgency_source"`
	ConfirmedFraudFlags90d *int     `json:"confirmed_fraud_flags_90d"`
}

// Validate applies the defaults and the provenance rule.
func (c *Context) Validate() error {
	defaultString(&c.CallerAttestation, "UNKNOWN")
	defaultString(&c.AttestationSource, "NONE")
	defaultString(&c.TransactionCurrency, "INR")
	defaultString(&c.TransactionType, "wire_transfer")
	defaultString(&c.RequestUrgency, "normal")
	defaultString(&c.UrgencySource, "POLICY_DERIVED")
	defaultBool(&c.BeneficiaryIsNew, false)
	defaultInt(&c.ConfirmedFraudFlags90d, 0)

	if !attestations[*c.CallerAttestation] {
		return invalid("caller_attestation must be VERIFIED, KNOWN_UNVERIFIED or UNKNOWN.")
	}
	if !attestationSources[*c.AttestationSource] {
		return invalid("attestation_source must be STIR_SHAKEN_A, AUTHENTICATED_APP_SESSION, CALLER_ID_ONLY or NONE.")
	}
	if !transactionTypes[*c.TransactionType] {
		return invalid("transaction_type is not one of the permitted values.")
	}
	if !urgencies[*c.RequestUrgency] {
		return invalid("request_urgency must be low, normal or high.")
	}
	if !urgencySources[*c.UrgencySource] {
		return invalid("urgency_source must be AGENT_ASSERTED, POLICY_DERIVED or CALLER_CLAIMED.")
	}
	if !currencyPattern.MatchString(*c.TransactionCurrency) {
		return invalid("transaction_currency must be a three-letter uppercase code.")
	}
	if c.TransactionValue != nil {
		v := *c.TransactionValue
		if math.IsNaN(v) || math.IsInf(v, 0) || v < 0 || v > 1e12 {
			return invalid("transaction_value must be a finite number between 0 and 1e12.")
		}
	}
	if n := *c.ConfirmedFraudFlags90d; n < 0 || n > 100 {
		return invalid("confirmed_fraud_flags_90d must be between 0 and 100.")
	}

	// CLAUDE.md invariant 5, enforced at the boundary rather than in the scorer: a VERIFIED
	// attestation must rest on a source the caller cannot forge.
	if *c.CallerAttestation == "VERIFIED" && !trustedAttestationSources[*c.AttestationSource] {
		return invalid("VERIFIED attestation requires an independently trusted source " +
			"(STIR_SHAKEN_A or AUTHENTICATED_APP_SESSION).")
	}
	if *c.AttestationSource == "CALLER_ID_ONLY" && *c.CallerAttestation == "VERIFIED" {
		return invalid("Caller ID alone cannot produce VERIFIED attestation.")
	}
	return nil
}

// ToScoring converts the validated request model into the scorer's context. Every field is
// present by construction, so the scorer sees a fully populated context and renormalises nothing
// away by accident.
func (c *Context) ToScoring() *scoring.Context {
	out := &scoring.Context{
		CallerAttestation:      c.CallerAttestation,
		AttestationSource:      c.AttestationSource,
		TransactionValue:       c.TransactionValue,
		TransactionCurrency:    c.TransactionCurrency,
		TransactionType:        c.TransactionType,
		BeneficiaryIsNew:       c.BeneficiaryIsNew,
		RequestUrgency:         c.RequestUrgency,
		UrgencySource:          c.UrgencySource,
		ConfirmedFraudFlags90d: c.ConfirmedFraudFlags90d,
	}
	count := 8 // every field above except transaction_value, which stays optional
	if c.TransactionValue != nil {
		count++
	}
	out.SetKeys(count)
	return out
}

// Start opens a simulated call stream.
type Start struct {
	Filename                 string   `json:"filename"`
	Label                    *string  `json:"label"`
	Context                  *Context `json:"context"`
	Interval                 *float64 `json:"interval"`
	SimulateDetectorFailure  bool     `json:"simulate_detector_failure"`
	Language                 *string  `json:"language"`
	SimulateAdversarialInput bool     `json:"simulate_adversarial_input"`
	IdentityID               *string  `json:"identity_id"`
}

// AICall asks the sidecar to place a scripted attacker call into the live path. It carries no
// audio and no scores: the sidecar synthesises the persona's script itself and streams it over
// AudioSocket exactly as Asterisk would.
type AICall struct {
	Persona string   `json:"persona"`
	Pace    *float64 `json:"pace"`
}

// Validate bounds the persona name and the playback pace.
func (a *AICall) Validate() error {
	if l := len(a.Persona); l < 1 || l > 80 || !identityIDPattern.MatchString(a.Persona) {
		return invalid("persona must be a known persona id.")
	}
	if a.Pace == nil {
		a.Pace = ptr(1.0)
	}
	// Real time is 1.0. The ceiling keeps a demo call from arriving faster than the detector can
	// consume it, which would shed windows rather than score them.
	if p := *a.Pace; math.IsNaN(p) || p < 0.25 || p > 40 {
		return invalid("pace must be between 0.25 and 40 times real time.")
	}
	return nil
}

// LiveStart opens a live Asterisk session. Raw media arrives at the Python AudioSocket listener;
// this request carries only metadata used by the Go session and scoring layers.
type LiveStart struct {
	Label      *string  `json:"label"`
	Context    *Context `json:"context"`
	Language   *string  `json:"language"`
	IdentityID *string  `json:"identity_id"`
}

// Validate applies the same language, identity and context rules as a simulated session.
func (s *LiveStart) Validate() error {
	defaultString(&s.Label, "Live Asterisk call")
	if l := len(*s.Label); l < 1 || l > 80 {
		return invalid("label must be between 1 and 80 characters.")
	}
	defaultString(&s.Language, "en")
	if !languagePattern.MatchString(*s.Language) {
		return invalid("language must be a two-letter code, optionally with a region (for example hi or hi-en).")
	}
	if s.IdentityID != nil {
		if len(*s.IdentityID) > 80 || (*s.IdentityID != "" && !identityIDPattern.MatchString(*s.IdentityID)) {
			return invalid("identity_id must contain only letters, numbers, underscores or hyphens and be at most 80 characters.")
		}
	}
	if s.Context == nil {
		s.Context = &Context{}
	}
	return s.Context.Validate()
}

// Validate applies defaults and bounds.
func (s *Start) Validate() error {
	if l := len(s.Filename); l < 1 || l > 200 {
		return invalid("filename must be between 1 and 200 characters.")
	}
	defaultString(&s.Label, "Simulated call")
	if l := len(*s.Label); l < 1 || l > 80 {
		return invalid("label must be between 1 and 80 characters.")
	}
	if s.Interval == nil {
		s.Interval = ptr(1.0)
	}
	if *s.Interval < 0.05 || *s.Interval > 4 {
		return invalid("interval must be between 0.05 and 4 seconds.")
	}
	// 05 section 4: the language is operator-asserted, never detected. It is recorded as given
	// and checked against the supported set at scoring time, where an unsupported language gates
	// the AI signal off instead of silently scoring it anyway.
	defaultString(&s.Language, "en")
	if !languagePattern.MatchString(*s.Language) {
		return invalid("language must be a two-letter code, optionally with a region (for example hi or hi-en).")
	}
	if s.IdentityID != nil && len(*s.IdentityID) > 80 {
		return invalid("identity_id must be at most 80 characters.")
	}
	if s.Context == nil {
		s.Context = &Context{}
	}
	return s.Context.Validate()
}

// Scores is the single-window compatibility body for POST /api/v1/risk-score.
type Scores struct {
	SpectralScore     *float64 `json:"spectral_score"`
	ProsodyScore      *float64 `json:"prosody_score"`
	SpeakerMatchScore *float64 `json:"speaker_match_score"`
	Context           *Context `json:"context"`
}

// Validate bounds the scores to the unit interval.
func (s *Scores) Validate() error {
	if s.SpectralScore == nil || s.ProsodyScore == nil {
		return invalid("spectral_score and prosody_score are required.")
	}
	for name, v := range map[string]*float64{
		"spectral_score": s.SpectralScore, "prosody_score": s.ProsodyScore,
		"speaker_match_score": s.SpeakerMatchScore,
	} {
		if v == nil {
			continue
		}
		if math.IsNaN(*v) || math.IsInf(*v, 0) || *v < 0 || *v > 1 {
			return invalid("%s must be a finite number between 0 and 1.", name)
		}
	}
	if s.Context == nil {
		s.Context = &Context{}
	}
	return s.Context.Validate()
}

// AlertRequest raises an alert for a call.
type AlertRequest struct {
	CallID string `json:"call_id"`
}

// Validate checks the call id is present.
func (a *AlertRequest) Validate() error {
	if a.CallID == "" {
		return invalid("call_id is required.")
	}
	return nil
}

// AlertAssignRequest routes an alert to an investigator.
type AlertAssignRequest struct {
	AssigneeID string `json:"assignee_id"`
}

// Validate bounds the assignee id.
func (a *AlertAssignRequest) Validate() error {
	if l := len(a.AssigneeID); l < 2 || l > 60 {
		return invalid("assignee_id must be between 2 and 60 characters.")
	}
	return nil
}

// AlertResolveRequest closes an alert.
type AlertResolveRequest struct {
	Outcome    string  `json:"outcome"`
	Notes      string  `json:"notes"`
	ResolverID *string `json:"resolver_id"`
}

// Validate enforces the closed outcome enum and mandatory notes.
func (a *AlertResolveRequest) Validate() error {
	if !resolutionOutcomes[a.Outcome] {
		return invalid("outcome must be CONFIRMED_FRAUD, FALSE_POSITIVE or INCONCLUSIVE.")
	}
	if l := len(a.Notes); l < 3 || l > 1000 {
		return invalid("notes must be between 3 and 1000 characters.")
	}
	defaultString(&a.ResolverID, "analyst_01")
	if l := len(*a.ResolverID); l < 2 || l > 60 {
		return invalid("resolver_id must be between 2 and 60 characters.")
	}
	return nil
}

// AppealCreateRequest lodges a challenge against an alert.
type AppealCreateRequest struct {
	Reason        string  `json:"reason"`
	AppellantType *string `json:"appellant_type"`
	ContactInfo   *string `json:"contact_info"`
}

// Validate bounds the appeal fields.
func (a *AppealCreateRequest) Validate() error {
	if l := len(a.Reason); l < 5 || l > 1000 {
		return invalid("reason must be between 5 and 1000 characters.")
	}
	defaultString(&a.AppellantType, "CUSTOMER")
	if !appellantTypes[*a.AppellantType] {
		return invalid("appellant_type must be CUSTOMER, AGENT or SUPERVISOR.")
	}
	if a.ContactInfo != nil && len(*a.ContactInfo) > 120 {
		return invalid("contact_info must be at most 120 characters.")
	}
	return nil
}

// DecisionOverrideRequest records a supervisor override.
type DecisionOverrideRequest struct {
	Decision     string  `json:"decision"`
	Reason       string  `json:"reason"`
	SupervisorID string  `json:"supervisor_id"`
	Role         *string `json:"role"`
}

// Validate enforces the decision enum, the mandatory reason, and an authorised role.
func (d *DecisionOverrideRequest) Validate() error {
	if !decisions[d.Decision] {
		return invalid("decision must be ALLOW, WARN, STEP_UP, ESCALATE or BLOCK.")
	}
	if l := len(strings.TrimSpace(d.Reason)); l < 5 || l > 500 {
		return invalid("reason must be between 5 and 500 characters.")
	}
	if l := len(d.SupervisorID); l < 2 || l > 60 {
		return invalid("supervisor_id must be between 2 and 60 characters.")
	}
	defaultString(&d.Role, "SUPERVISOR")
	if !roles[*d.Role] {
		return invalid("role must be SUPERVISOR, FRAUD_ANALYST, INCIDENT_COMMANDER or ADMIN.")
	}
	return nil
}

// LedgerEvent is a manually submitted audit record.
type LedgerEvent struct {
	CallID        string            `json:"call_id"`
	EventType     string            `json:"event_type"`
	RiskScore     *float64          `json:"risk_score"`
	Band          *string           `json:"band"`
	PolicyVersion *string           `json:"policy_version"`
	ModelVersions map[string]string `json:"model_versions"`
	Details       map[string]any    `json:"details"`
}

// Validate enforces CLAUDE.md invariant 8: no audit record without the versions that produced it.
func (l *LedgerEvent) Validate() error {
	if len(l.CallID) > 80 {
		return invalid("call_id must be at most 80 characters.")
	}
	if !ledgerEventTypes[l.EventType] {
		return invalid("event_type must be observation, alert, escalation, decision, override or call_completed.")
	}
	if l.RiskScore == nil {
		return invalid("risk_score is required.")
	}
	if v := *l.RiskScore; math.IsNaN(v) || math.IsInf(v, 0) || v < 0 || v > 100 {
		return invalid("risk_score must be a finite number between 0 and 100.")
	}
	defaultString(&l.Band, "UNKNOWN")
	if !bands[*l.Band] {
		return invalid("band must be LOW, MEDIUM, HIGH or UNKNOWN.")
	}
	defaultString(&l.PolicyVersion, "demo-detector-first@2.1.0")
	if len(*l.PolicyVersion) > 80 {
		return invalid("policy_version must be at most 80 characters.")
	}
	if len(l.ModelVersions) == 0 {
		return invalid("model_versions is required: a decision without its model versions " +
			"cannot be reproduced or defended.")
	}
	return nil
}

// Map renders the event for the ledger.
func (l *LedgerEvent) Map() map[string]any {
	versions := make(map[string]any, len(l.ModelVersions))
	for k, v := range l.ModelVersions {
		versions[k] = v
	}
	out := map[string]any{
		"call_id":        l.CallID,
		"event_type":     l.EventType,
		"risk_score":     *l.RiskScore,
		"band":           *l.Band,
		"policy_version": *l.PolicyVersion,
		"model_versions": versions,
	}
	if l.Details != nil {
		out["details"] = l.Details
	}
	return out
}

func defaultString(p **string, value string) {
	if *p == nil {
		v := value
		*p = &v
	}
}

func defaultBool(p **bool, value bool) {
	if *p == nil {
		v := value
		*p = &v
	}
}

func defaultInt(p **int, value int) {
	if *p == nil {
		v := value
		*p = &v
	}
}

func ptr(v float64) *float64 { return &v }
