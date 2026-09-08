// Package scoring is the Risk Fusion Engine and Session Aggregator (docs/01 section 2, [Go]).
//
// Ported from backend/app/risk_scoring.py. The port is guarded by golden_test.go, which replays
// vectors emitted from the Python reference implementation, because the arithmetic here is the
// part of the system the v2 spec exists to correct: v1's version of this file made a HIGH band
// mathematically unreachable (DR-003 / DEF-1) and let a spoofed caller ID lower a score (DR-007).
package scoring

import (
	"errors"
	"math"
	"sort"

	"github.com/vox-guard/voxguard/gateway/internal/numeric"
	"github.com/vox-guard/voxguard/gateway/internal/policy"
)

// BandRank orders the bands so hysteresis can compare them (04 section 4).
var BandRank = map[string]int{"UNKNOWN": -1, "LOW": 0, "MEDIUM": 1, "HIGH": 2}

// ErrMatchScoreContract reports a violation of CLAUDE.md invariant 7: match_score and
// reference_available must agree. It is returned, never coerced away.
var ErrMatchScoreContract = errors.New("match_score contract violated")

// BandFor maps a score to a band. A nil score is UNKNOWN — never LOW (CLAUDE.md invariant 2).
func BandFor(score *float64, p policy.Pack, contextDegraded bool) string {
	if score == nil {
		return "UNKNOWN"
	}
	// 03 section 6: under Context-Service failure high_min drops by degraded_threshold_delta,
	// because a score built without context evidence deserves less benefit of the doubt.
	highMin := p.Bands.HighMin
	if contextDegraded {
		highMin -= p.DegradedThresholdDelta
	}
	switch {
	case *score >= highMin:
		return "HIGH"
	case *score >= p.Bands.MediumMin:
		return "MEDIUM"
	default:
		return "LOW"
	}
}

// contextOrder fixes the sub-signal evaluation order so context_details is deterministic and
// matches the Python dict insertion order.
var contextOrder = []string{"attestation", "urgency", "history", "transaction"}

// ContextTerm renormalises the context sub-signals over those actually present (03 section 2).
// It returns nil when no sub-signal is available, so the caller marks the whole context signal
// inactive rather than scoring an absent one as safe.
func ContextTerm(c *Context, p policy.Pack) (*float64, []ContextDetail) {
	if c == nil {
		return nil, nil
	}
	values := map[string]float64{}

	if c.CallerAttestation != nil {
		switch *c.CallerAttestation {
		case "VERIFIED":
			values["attestation"] = 0.0
		case "KNOWN_UNVERIFIED":
			values["attestation"] = 0.5
		case "UNKNOWN":
			values["attestation"] = 1.0
		}
	}
	if c.RequestUrgency != nil {
		var value float64
		switch *c.RequestUrgency {
		case "low":
			value = 0.0
		case "normal":
			value = 0.3
		case "high":
			value = 1.0
		}
		// 03 section 4 / CLAUDE.md invariant 5: a caller who claims their request is not urgent
		// must not be able to buy a lower score with that claim. Clamp the claim upward to the
		// neutral value; it may raise risk, never lower it.
		if c.urgencySource() == "CALLER_CLAIMED" {
			value = math.Max(value, 0.3)
		}
		values["urgency"] = value
	}
	if c.ConfirmedFraudFlags90d != nil {
		values["history"] = math.Min(float64(*c.ConfirmedFraudFlags90d)/3, 1.0)
	}
	if c.TransactionValue != nil {
		ratio := *c.TransactionValue / p.RoutineTransactionThreshold
		value := math.Min(math.Log10(1+ratio)/math.Log10(11), 1.0)
		if c.beneficiaryIsNew() {
			value = math.Min(value+0.2, 1.0)
		}
		values["transaction"] = value
	}

	if len(values) == 0 {
		return nil, nil
	}

	denominator, weighted := 0.0, 0.0
	details := make([]ContextDetail, 0, len(values))
	for _, key := range contextOrder {
		value, ok := values[key]
		if !ok {
			continue
		}
		weight := p.ContextWeight(key)
		denominator += weight
		weighted += weight * value
		details = append(details, ContextDetail{Factor: "context_" + key, Value: numeric.Round(value, 4)})
	}
	return f64(weighted / denominator), details
}

// TrustDiscountEligible reports whether every independent gate in 03 section 4 is satisfied.
//
// Every clause is an AND on purpose. The discount is the only path by which a score goes down,
// so each gate must rest on evidence the caller cannot manufacture: an attestation from an
// independently trusted source, a voice match against an enrolled reference, and a transaction
// inside the routine band.
func TrustDiscountEligible(c *Context, v *Verification, d *Detection, degraded bool, p policy.Pack) bool {
	if c.Empty() || v == nil || d == nil || degraded {
		return false
	}
	if c.attestation() != "VERIFIED" {
		return false
	}
	if src := c.attestationSource(); src != "STIR_SHAKEN_A" && src != "AUTHENTICATED_APP_SESSION" {
		return false
	}
	if !v.ReferenceAvailable || v.VoicedSecondsUsed < 2 {
		return false
	}
	// Invariant 7: only read match_score behind reference_available, and never treat a missing
	// one as a passing one.
	if v.MatchScore == nil || *v.MatchScore < 0.85 {
		return false
	}
	if c.TransactionValue == nil || *c.TransactionValue > p.RoutineTransactionThreshold {
		return false
	}
	return !d.AdversarialFlag && !v.ReplaySuspected
}

var factorNames = map[string]string{
	"ai":      "ai_synthetic",
	"speaker": "speaker_mismatch",
	"context": "context_risk",
	"intent":  "intent_risk",
}

// signalOrder fixes the top-level signal order so active_signals and contributing_factors are
// deterministic and match the Python dict insertion order.
var signalOrder = []string{"ai", "speaker", "context", "intent"}

// ScoreWindow fuses one window's signals into a 0-100 score (03 section 1).
//
// Evaluation order is load-bearing: signals are gated first, the active ones are renormalised so
// no inactive signal's weight is left in the denominator (CLAUDE.md invariant 4 — this is the
// defect that made HIGH unreachable), the trust discount applies to the base only, and the floors
// are combined last with max() rather than added (invariant 3).
func ScoreWindow(d *Detection, c *Context, v *Verification, p policy.Pack) (*WindowResult, error) {
	var intentRisk *float64
	if c != nil {
		intentRisk = c.IntentRisk
	}
	return ScoreWindowWithIntent(d, c, v, intentRisk, p)
}

// ScoreWindowWithIntent fuses a window's sidecar-supplied intent score without
// mutating the call-level context. Intent is derived afresh for every audio
// window; keeping it separate prevents a prior window's score leaking into the
// next one and preserves the context-unavailable degraded floor.
func ScoreWindowWithIntent(d *Detection, c *Context, v *Verification, intentRisk *float64, p policy.Pack) (*WindowResult, error) {
	active := map[string]float64{}
	terms := map[string]float64{}
	inactive := map[string]string{}
	var floors []Floor
	var degradedReasons []string
	var contextDetails []ContextDetail

	// --- AI detection signal -------------------------------------------------------------
	switch {
	case d == nil:
		inactive["ai"] = "detector_unavailable"
		degradedReasons = append(degradedReasons, "detector_unavailable")
		floors = append(floors, Floor{Reason: "detector_unavailable", Value: 40})
	case !d.LangSupported():
		inactive["ai"] = "unsupported_language"
		degradedReasons = append(degradedReasons, "unsupported_language")
		floors = append(floors, Floor{Reason: "unsupported_language", Value: 40})
	case d.VoicedSeconds < 1.5:
		inactive["ai"] = "insufficient_voiced_audio"
	case d.Confidence < p.MinConfidence:
		inactive["ai"] = "low_confidence"
	default:
		// Calibrated probability shrunk toward 0.5 by the model's own confidence: a hesitant
		// detector moves the score less than a certain one.
		terms["ai"] = 0.5 + (d.PSynthetic-0.5)*d.Confidence
		active["ai"] = p.Weights.AI
	}

	// --- Speaker verification signal -----------------------------------------------------
	switch {
	case v == nil:
		inactive["speaker"] = "not_enrolled"
	case v.Failed:
		inactive["speaker"] = "verifier_failed"
		degradedReasons = append(degradedReasons, "verifier_failed")
		floors = append(floors, Floor{Reason: "verifier_failed", Value: 40})
	case v.ReferenceAvailable:
		if v.MatchScore == nil {
			return nil, ErrMatchScoreContract
		}
		if v.VoicedSecondsUsed >= 2 {
			terms["speaker"] = 1 - *v.MatchScore
			active["speaker"] = p.Weights.Speaker
		} else {
			inactive["speaker"] = "insufficient_voiced_audio"
		}
	default:
		if v.MatchScore != nil {
			return nil, ErrMatchScoreContract
		}
		inactive["speaker"] = "not_enrolled"
		if p.MandatorySpeakerVerification {
			floors = append(floors, Floor{Reason: "speaker_verification_required", Value: 50})
		}
	}

	// --- Context signal ------------------------------------------------------------------
	if c == nil {
		inactive["context"] = "context_unavailable"
		degradedReasons = append(degradedReasons, "context_unavailable")
		floors = append(floors, Floor{Reason: "context_unavailable", Value: 40})
	} else {
		value, details := ContextTerm(c, p)
		contextDetails = details
		if value == nil {
			inactive["context"] = "no_context_signals"
		} else {
			terms["context"] = *value
			active["context"] = p.Weights.Context
		}
	}

	// --- Intent / Content Risk signal (I_risk from Whisper STT) --------------------------
	// IntentRisk is the 0.15-weighted 4th signal. When Whisper is not available the
	// Python sidecar returns nil; we renormalise the remaining three over 0.85 rather
	// than leaving 0.15 floating as a phantom safe-intent score.
	if intentRisk != nil {
		terms["intent"] = *intentRisk
		active["intent"] = p.Weights.Intent
	} else {
		inactive["intent"] = "intent_scorer_unavailable"
	}

	// --- Floors from adversarial conditions ----------------------------------------------
	if d != nil && d.AdversarialFlag {
		floors = append(floors, Floor{Reason: "adversarial_input", Value: 55})
	}
	if v != nil && v.ReplaySuspected {
		floors = append(floors, Floor{Reason: "replay_suspected", Value: 60})
	}

	// --- Not enough evidence to score at all ----------------------------------------------
	insufficientAudio := d != nil && d.VoicedSeconds < p.MinVoicedSeconds
	if insufficientAudio || len(active) == 0 {
		reasons := degradedReasons
		if len(reasons) == 0 {
			if insufficientAudio {
				reasons = []string{"insufficient_voiced_audio"}
			} else {
				reasons = []string{"no_active_signals"}
			}
		}
		// Too little voiced audio is not evidence of anything, so it carries no floor either:
		// the window is reported UNKNOWN and the session simply does not count it.
		reported := floors
		if insufficientAudio {
			reported = nil
		}
		return result(nil, nil, 0, active, inactive, reported, nil, contextDetails,
			len(degradedReasons) > 0, reasons, p), nil
	}

	// --- Renormalise over active signals only (CLAUDE.md invariant 4) ---------------------
	denominator := 0.0
	for _, w := range active {
		denominator += w
	}
	factors := make([]Factor, 0, len(active))
	base := 0.0
	for _, key := range signalOrder {
		weight, ok := active[key]
		if !ok {
			continue
		}
		effective := weight / denominator
		points := numeric.Round(100*effective*terms[key], 6)
		factors = append(factors, Factor{
			Factor: factorNames[key],
			Weight: numeric.Round(effective, 6),
			Value:  numeric.Round(terms[key], 6),
			Points: points,
		})
		base += points
	}
	base = numeric.Round(base, 6)

	discount := 0.0
	if TrustDiscountEligible(c, v, d, len(degradedReasons) > 0, p) {
		discount = p.TrustDiscount
	}
	score := math.Max(0, base-discount)
	// Invariant 3: floors are the last step and combine with max(), never as an additive penalty.
	for _, floor := range floors {
		score = math.Max(score, floor.Value)
	}
	score = numeric.Round(math.Min(100, score), 2)

	// Invariant 6 asserted in code, not only in tests: the explanation on the operator's screen
	// must actually account for the number next to it.
	sum := 0.0
	for _, f := range factors {
		sum += f.Points
	}
	if numeric.Round(sum, 6) != base {
		panic("contributing_factors do not sum to base_score (CLAUDE.md invariant 6)")
	}

	return result(&score, &base, discount, active, inactive, floors, factors, contextDetails,
		len(degradedReasons) > 0, degradedReasons, p), nil
}

func result(score, base *float64, trustDiscount float64, active map[string]float64,
	inactive map[string]string, floors []Floor, factors []Factor, contextDetails []ContextDetail,
	degraded bool, reasons []string, p policy.Pack) *WindowResult {

	contextDegraded := false
	for _, r := range reasons {
		if r == "context_unavailable" {
			contextDegraded = true
			break
		}
	}

	activeList := make([]string, 0, len(active))
	inactiveList := make([]string, 0, len(inactive))
	for _, key := range signalOrder {
		if _, ok := active[key]; ok {
			activeList = append(activeList, key)
		}
		if _, ok := inactive[key]; ok {
			inactiveList = append(inactiveList, key)
		}
	}

	if floors == nil {
		floors = []Floor{}
	}
	sort.SliceStable(floors, func(i, j int) bool { return floors[i].Value < floors[j].Value })
	if factors == nil {
		factors = []Factor{}
	}
	if contextDetails == nil {
		contextDetails = []ContextDetail{}
	}
	if reasons == nil {
		reasons = []string{}
	}
	if inactive == nil {
		inactive = map[string]string{}
	}

	return &WindowResult{
		WindowScore:         score,
		BaseScore:           base,
		TrustDiscount:       trustDiscount,
		Band:                BandFor(score, p, contextDegraded),
		ContextDegraded:     contextDegraded,
		ActiveSignals:       activeList,
		InactiveSignals:     inactiveList,
		InactiveReasons:     inactive,
		AppliedFloors:       floors,
		ContributingFactors: factors,
		ContextDetails:      contextDetails,
		Degraded:            degraded,
		DegradedReasons:     reasons,
		PolicyVersion:       p.Version,
	}
}

// Fuse is the single-window compatibility path behind POST /api/v1/risk-score.
func Fuse(spectral, prosody float64, speaker *float64, c *Context, p policy.Pack) (*float64, error) {
	supported := true
	d := &Detection{
		PSynthetic:        0.55*spectral + 0.45*prosody,
		Confidence:        1.0,
		LanguageSupported: &supported,
		VoicedSeconds:     3.0,
	}
	var v *Verification
	if speaker != nil {
		v = &Verification{ReferenceAvailable: true, MatchScore: speaker, VoicedSecondsUsed: 3.0}
	}
	res, err := ScoreWindow(d, c, v, p)
	if err != nil {
		return nil, err
	}
	return res.WindowScore, nil
}
