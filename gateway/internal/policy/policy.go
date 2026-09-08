// Package policy holds the versioned scoring policy pack.
//
// Ported from backend/app/risk_scoring.py POLICY. The values are unchanged by the port; changing
// one is a policy decision, not a refactor, and requires a new version string so that
// CLAUDE.md invariant 8 keeps holding for every decision already recorded.
package policy

// Pack is a tenant scoring policy. Every field maps 1:1 onto a key of the Python POLICY dict.
type Pack struct {
	Version string `json:"version"`

	Weights        Weights        `json:"weights"`
	ContextWeights ContextWeights `json:"context_weights"`
	Bands          Bands          `json:"bands"`

	RoutineTransactionThreshold  float64 `json:"routine_transaction_threshold"`
	MandatorySpeakerVerification bool    `json:"mandatory_speaker_verification"`
	MinConfidence                float64 `json:"min_confidence"`
	MinVoicedSeconds             float64 `json:"min_voiced_seconds"`
	DefaultDegradedDecision      string  `json:"default_degraded_decision"`

	// 03 section 6: with context missing we cannot afford the usual HIGH threshold, so lower it.
	DegradedThresholdDelta float64 `json:"degraded_threshold_delta"`

	// 05 section 4: the languages this build claims. Anything else gates the AI signal off.
	SupportedLanguages []string `json:"supported_languages"`

	EWMAAlpha     float64 `json:"ewma_alpha"`
	PeakDecay     float64 `json:"peak_decay"`
	PeakDiscount  float64 `json:"peak_discount"`
	TrustDiscount float64 `json:"trust_discount"`
}

// Weights are the top-level signal weights, renormalised over active signals at scoring time.
type Weights struct {
	AI      float64 `json:"ai"`
	Speaker float64 `json:"speaker"`
	Context float64 `json:"context"`
	// Intent is the weight for the Whisper STT intent-risk signal (I_risk).
	// When the intent scorer is unavailable the signal is renormalised out, so the
	// remaining three absorb its weight proportionally.
	Intent float64 `json:"intent"`
}

// ContextWeights are the context sub-signal weights, renormalised over present sub-signals.
type ContextWeights struct {
	Attestation float64 `json:"attestation"`
	Urgency     float64 `json:"urgency"`
	History     float64 `json:"history"`
	Transaction float64 `json:"transaction"`
}

// Bands are the lower bounds of the MEDIUM and HIGH bands.
type Bands struct {
	MediumMin float64 `json:"medium_min"`
	HighMin   float64 `json:"high_min"`
}

// Default is the Phase 0 pack.
//
// It is NOT the 03 section 7 banking pack (ai .45 / speaker .30 / context .25, bands 35/65,
// mandatory_speaker_verification true). Named honestly: these weights are tuned for a
// detector-first Phase 0 with no speaker enrolment, where the banking pack's
// mandatory-verification floor of 50 would pin every window to Elevated. Deviation DEV-1 in
// docs/HANDOFF.md.
var Default = Pack{
	Version:                      "demo-detector-first@2.2.0",
	Weights:                      Weights{AI: 0.55, Speaker: 0.15, Context: 0.15, Intent: 0.15},
	ContextWeights:               ContextWeights{Attestation: 0.25, Urgency: 0.15, History: 0.20, Transaction: 0.40},
	Bands:                        Bands{MediumMin: 40, HighMin: 70},
	RoutineTransactionThreshold:  50000,
	MandatorySpeakerVerification: false,
	MinConfidence:                0.35,
	MinVoicedSeconds:             3.0,
	DefaultDegradedDecision:      "WARN",
	DegradedThresholdDelta:       10,
	SupportedLanguages:           []string{"en", "hi", "ta", "te", "bn", "hi-en"},
	EWMAAlpha:                    0.35,
	PeakDecay:                    0.98,
	PeakDiscount:                 8,
	TrustDiscount:                10,
}

// ContextWeight returns the sub-signal weight by its key, mirroring the Python dict lookup.
func (p Pack) ContextWeight(key string) float64 {
	switch key {
	case "attestation":
		return p.ContextWeights.Attestation
	case "urgency":
		return p.ContextWeights.Urgency
	case "history":
		return p.ContextWeights.History
	case "transaction":
		return p.ContextWeights.Transaction
	}
	return 0
}

// SignalWeight returns the top-level weight by its key, mirroring the Python dict lookup.
func (p Pack) SignalWeight(key string) float64 {
	switch key {
	case "ai":
		return p.Weights.AI
	case "speaker":
		return p.Weights.Speaker
	case "context":
		return p.Weights.Context
	case "intent":
		return p.Weights.Intent
	}
	return 0
}

// SupportsLanguage reports whether this build claims the language (05 section 4).
func (p Pack) SupportsLanguage(lang string) bool {
	for _, l := range p.SupportedLanguages {
		if l == lang {
			return true
		}
	}
	return false
}
