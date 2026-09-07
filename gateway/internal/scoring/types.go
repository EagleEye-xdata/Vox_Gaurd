package scoring

import "encoding/json"

// Detection is the AI-detection branch output. Ported from the Python detection dict; the
// pointer fields are the ones the Python code read with `.get(key, default)`, so absence and a
// present zero must stay distinguishable.
type Detection struct {
	PSynthetic        float64 `json:"p_synthetic"`
	Confidence        float64 `json:"confidence"`
	LanguageSupported *bool   `json:"language_supported,omitempty"`
	VoicedSeconds     float64 `json:"voiced_seconds"`
	AdversarialFlag   bool    `json:"adversarial_flag,omitempty"`
}

// LangSupported mirrors `detection.get("language_supported", True)`.
func (d *Detection) LangSupported() bool {
	if d == nil || d.LanguageSupported == nil {
		return true
	}
	return *d.LanguageSupported
}

// Verification is the speaker-verification branch output.
//
// MatchScore is a pointer on purpose. CLAUDE.md invariant 7 forbids reading it without checking
// ReferenceAvailable, and DR-012 forbids coercing "no reference" into a sentinel number. A nil
// pointer cannot be silently read as 0.
type Verification struct {
	Failed             bool     `json:"failed,omitempty"`
	ReferenceAvailable bool     `json:"reference_available"`
	MatchScore         *float64 `json:"match_score"`
	VoicedSecondsUsed  float64  `json:"voiced_seconds_used,omitempty"`
	ReplaySuspected    bool     `json:"replay_suspected,omitempty"`
	Confidence         float64  `json:"confidence,omitempty"`
	ModelVersion       string   `json:"model_version,omitempty"`
	SpeakerTrack       string   `json:"speaker_track,omitempty"`
	EnrolledIdentity   string   `json:"enrolled_identity,omitempty"`
}

// Context carries the contextual signals. Every field is a pointer because a context signal that
// was not supplied must be renormalised out (03 section 2), not treated as a zero-risk value —
// that would be CLAUDE.md invariant 2 in miniature.
type Context struct {
	CallerAttestation      *string  `json:"caller_attestation,omitempty"`
	AttestationSource      *string  `json:"attestation_source,omitempty"`
	TransactionValue       *float64 `json:"transaction_value,omitempty"`
	TransactionCurrency    *string  `json:"transaction_currency,omitempty"`
	TransactionType        *string  `json:"transaction_type,omitempty"`
	BeneficiaryIsNew       *bool    `json:"beneficiary_is_new,omitempty"`
	RequestUrgency         *string  `json:"request_urgency,omitempty"`
	UrgencySource          *string  `json:"urgency_source,omitempty"`
	ConfirmedFraudFlags90d *int     `json:"confirmed_fraud_flags_90d,omitempty"`

	// keys counts the members actually present in the decoded object. The Python code branches on
	// `not context`, and in Python an empty dict is falsy while a populated one is not. Without
	// this counter a `{}` context would decode to the same all-nil struct as a genuinely absent
	// one and would wrongly become eligible for the trust discount.
	keys int
}

// UnmarshalJSON decodes the context and records how many members were present.
func (c *Context) UnmarshalJSON(data []byte) error {
	type alias Context
	var a alias
	if err := json.Unmarshal(data, &a); err != nil {
		return err
	}
	var present map[string]json.RawMessage
	if err := json.Unmarshal(data, &present); err != nil {
		return err
	}
	*c = Context(a)
	c.keys = len(present)
	return nil
}

// Empty reports whether the context object carried no members, matching Python's `not context`.
func (c *Context) Empty() bool { return c == nil || c.keys == 0 }

// SetKeys records member count for contexts built in Go rather than decoded from JSON.
func (c *Context) SetKeys(n int) { c.keys = n }

func (c *Context) attestation() string {
	if c == nil || c.CallerAttestation == nil {
		return ""
	}
	return *c.CallerAttestation
}

func (c *Context) attestationSource() string {
	if c == nil || c.AttestationSource == nil {
		return ""
	}
	return *c.AttestationSource
}

func (c *Context) beneficiaryIsNew() bool {
	return c != nil && c.BeneficiaryIsNew != nil && *c.BeneficiaryIsNew
}

func (c *Context) urgencySource() string {
	if c == nil || c.UrgencySource == nil {
		return ""
	}
	return *c.UrgencySource
}

// Floor is a score floor applied last, via max() (03 section 5, CLAUDE.md invariant 3).
type Floor struct {
	Reason string  `json:"reason"`
	Value  float64 `json:"value"`
}

// Factor is one contributing signal. CLAUDE.md invariant 6: Points across all factors must sum
// to the base score.
type Factor struct {
	Factor string  `json:"factor"`
	Weight float64 `json:"weight"`
	Value  float64 `json:"value"`
	Points float64 `json:"points"`
}

// ContextDetail is one context sub-signal's normalised value, surfaced for explainability.
type ContextDetail struct {
	Factor string  `json:"factor"`
	Value  float64 `json:"value"`
}

// WindowResult is the fusion output for one voiced window (03 section 1).
type WindowResult struct {
	WindowScore     *float64 `json:"window_score"`
	BaseScore       *float64 `json:"base_score"`
	TrustDiscount   float64  `json:"trust_discount"`
	Band            string   `json:"band"`
	ContextDegraded bool     `json:"context_degraded"`

	ActiveSignals   []string          `json:"active_signals"`
	InactiveSignals []string          `json:"inactive_signals"`
	InactiveReasons map[string]string `json:"inactive_reasons"`

	AppliedFloors       []Floor         `json:"applied_floors"`
	ContributingFactors []Factor        `json:"contributing_factors"`
	ContextDetails      []ContextDetail `json:"context_details"`

	Degraded        bool     `json:"degraded"`
	DegradedReasons []string `json:"degraded_reasons"`
	PolicyVersion   string   `json:"policy_version"`
}

// SessionSnapshot is the session aggregator output (04 section 3).
type SessionSnapshot struct {
	SessionScore      *float64    `json:"session_score"`
	AuthenticityScore *float64    `json:"authenticity_score"`
	Band              string      `json:"band"`
	BandChanged       bool        `json:"band_changed"`
	History           []float64   `json:"history"`
	PeakScore         float64     `json:"peak_score"`
	EWMAScore         *float64    `json:"ewma_score"`
	BandTimeline      []BandPoint `json:"band_timeline"`
	EscalationSeq     int         `json:"escalation_seq"`
	AlertKey          *string     `json:"alert_key"`
}

// BandPoint records the window index at which the session band changed.
type BandPoint struct {
	Window int    `json:"window"`
	Band   string `json:"band"`
}

func f64(v float64) *float64 { return &v }
