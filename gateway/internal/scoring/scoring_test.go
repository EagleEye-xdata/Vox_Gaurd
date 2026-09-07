package scoring

import (
	"errors"
	"testing"

	"github.com/vox-guard/voxguard/gateway/internal/numeric"
	"github.com/vox-guard/voxguard/gateway/internal/policy"
)

// These are the invariant tests ported from backend/tests/test_pipeline.py. They differ from
// golden_test.go in what they prove: golden_test proves the Go port equals the Python
// implementation, these prove the spec is satisfied regardless of what either implementation
// happens to do. Both are needed — an equivalent port of a wrong implementation is still wrong.

var P = policy.Default

func det(p, confidence, voiced float64) *Detection {
	return &Detection{PSynthetic: p, Confidence: confidence, VoicedSeconds: voiced}
}

func ctx(pairs map[string]any) *Context {
	c := &Context{}
	for k, v := range pairs {
		switch k {
		case "caller_attestation":
			s := v.(string)
			c.CallerAttestation = &s
		case "attestation_source":
			s := v.(string)
			c.AttestationSource = &s
		case "transaction_value":
			f := v.(float64)
			c.TransactionValue = &f
		case "beneficiary_is_new":
			b := v.(bool)
			c.BeneficiaryIsNew = &b
		case "request_urgency":
			s := v.(string)
			c.RequestUrgency = &s
		case "urgency_source":
			s := v.(string)
			c.UrgencySource = &s
		case "confirmed_fraud_flags_90d":
			n := v.(int)
			c.ConfirmedFraudFlags90d = &n
		default:
			panic("unknown context key in test helper: " + k)
		}
	}
	c.SetKeys(len(pairs))
	return c
}

func mustScore(t *testing.T, d *Detection, c *Context, v *Verification) *WindowResult {
	t.Helper()
	r, err := ScoreWindow(d, c, v, P)
	if err != nil {
		t.Fatalf("ScoreWindow: %v", err)
	}
	return r
}

// CLAUDE.md invariant 4 / DEF-1 / DR-003. This is the defect that made the v1 spec
// non-functional: an inactive signal's weight left in the denominator caps the reachable score
// so a HIGH alert can never fire. With only the AI signal active its weight must renormalise to
// 1.0, not stay at 0.60.
func TestActiveSignalRenormalisationMakesHighReachable(t *testing.T) {
	r := mustScore(t, det(0.95, 0.95, 3), nil, nil)
	if r.Band != "HIGH" {
		t.Fatalf("band = %s, want HIGH (DEF-1 regression)", r.Band)
	}
	if got := *r.WindowScore; got != 92.75 {
		t.Errorf("window_score = %v, want 92.75", got)
	}
	eqStrings(t, "active_signals", r.ActiveSignals, []string{"ai"})
	if w := r.ContributingFactors[0].Weight; w != 1 {
		t.Errorf("the sole active signal renormalised to %v, want 1.0 — inactive weight left in "+
			"the denominator is DEF-1", w)
	}
}

// CLAUDE.md invariant 2: a check that could not run yields UNKNOWN or a degraded floor, never a
// passing score.
func TestAbsenceOfEvidenceIsNeverLow(t *testing.T) {
	c := ctx(map[string]any{"caller_attestation": "UNKNOWN"})

	failed := mustScore(t, nil, c, nil)
	if failed.Band == "LOW" || *failed.WindowScore < 40 {
		t.Errorf("detector unavailable produced %v/%s, want at least the 40 floor",
			*failed.WindowScore, failed.Band)
	}
	if !failed.Degraded {
		t.Error("detector unavailable must mark the window degraded")
	}

	unsupported := det(0.1, 1, 3)
	no := false
	unsupported.LanguageSupported = &no
	r := mustScore(t, unsupported, c, nil)
	if r.Band == "LOW" || *r.WindowScore < 40 {
		t.Errorf("unsupported language produced %v/%s, want at least the 40 floor",
			*r.WindowScore, r.Band)
	}

	// Too little voiced audio is not evidence either way: UNKNOWN, with no numeric score.
	if short := mustScore(t, det(0.9, 1, 1), nil, nil); short.Band != "UNKNOWN" || short.WindowScore != nil {
		t.Errorf("insufficient voiced audio = %v/%s, want UNKNOWN with no score",
			derefF(short.WindowScore), short.Band)
	}
	if none := mustScore(t, nil, nil, nil); none.Band != "UNKNOWN" {
		t.Errorf("no signals at all = %s, want UNKNOWN", none.Band)
	}
}

// CLAUDE.md invariant 3: floors combine with max(), never as an additive penalty, and they are
// reported separately from the evidence so the operator can see the score was floored.
func TestFloorsAreAppliedLastViaMax(t *testing.T) {
	d := det(0, 1, 3)
	d.AdversarialFlag = true
	r := mustScore(t, d, nil, nil)

	if *r.WindowScore != 55 {
		t.Errorf("window_score = %v, want exactly 55 — the highest floor, not a sum of floors",
			*r.WindowScore)
	}
	if r.Band != "MEDIUM" {
		t.Errorf("band = %s, want MEDIUM", r.Band)
	}
	want := []Floor{{"context_unavailable", 40}, {"adversarial_input", 55}}
	if len(r.AppliedFloors) != 2 || r.AppliedFloors[0] != want[0] || r.AppliedFloors[1] != want[1] {
		t.Errorf("applied_floors = %+v, want %+v ascending by value", r.AppliedFloors, want)
	}
	if *r.BaseScore != 0 {
		t.Errorf("base_score = %v, want 0 — the floor must not be folded into the evidence",
			*r.BaseScore)
	}
}

// CLAUDE.md invariant 6, asserted in code as well as here.
func TestContributingFactorsSumToBaseScore(t *testing.T) {
	full := ctx(map[string]any{
		"caller_attestation": "KNOWN_UNVERIFIED", "attestation_source": "CALLER_ID_ONLY",
		"transaction_value": 275000.0, "beneficiary_is_new": true,
		"request_urgency": "high", "urgency_source": "AGENT_ASSERTED",
		"confirmed_fraud_flags_90d": 2,
	})
	match := 0.41
	r := mustScore(t, det(0.72, 0.88, 3), full,
		&Verification{ReferenceAvailable: true, MatchScore: &match, VoicedSecondsUsed: 3})

	if len(r.ContributingFactors) != 3 {
		t.Fatalf("expected all three signals active, got %v", r.ActiveSignals)
	}
	sum := 0.0
	for _, f := range r.ContributingFactors {
		sum += f.Points
	}
	if roundedSum := round6(sum); roundedSum != *r.BaseScore {
		t.Errorf("contributing_factors sum to %v, base_score is %v", roundedSum, *r.BaseScore)
	}
}

// CLAUDE.md invariant 5 / DR-007: nothing an adversary controls may lower a score. A caller who
// says "this is not urgent" must not be able to buy a discount with that claim.
func TestAdversaryControlledContextCannotLowerRisk(t *testing.T) {
	base := map[string]any{
		"caller_attestation": "KNOWN_UNVERIFIED", "attestation_source": "CALLER_ID_ONLY",
		"transaction_value": 800000.0, "beneficiary_is_new": true,
		"confirmed_fraud_flags_90d": 0, "urgency_source": "CALLER_CLAIMED",
	}
	with := func(urgency string) float64 {
		fields := map[string]any{"request_urgency": urgency}
		for k, v := range base {
			fields[k] = v
		}
		return *mustScore(t, det(0.5, 1, 3), ctx(fields), nil).WindowScore
	}
	if low, normal := with("low"), with("normal"); low != normal {
		t.Errorf("claiming low urgency scored %v vs %v for normal — an adversary-controlled "+
			"input lowered the score", low, normal)
	}
	if high, normal := with("high"), with("normal"); high < normal {
		t.Errorf("claiming high urgency scored %v, below normal's %v — the claim may raise "+
			"risk or be ignored, never lower it", high, normal)
	}
}

// 03 section 4: the trust discount is the only downward path, so every gate is independent and
// all of them must hold.
func TestTrustDiscountRequiresEveryIndependentGate(t *testing.T) {
	trusted := map[string]any{
		"caller_attestation": "VERIFIED", "attestation_source": "STIR_SHAKEN_A",
		"transaction_value": 1000.0, "request_urgency": "normal",
	}
	match := 0.9
	verified := &Verification{ReferenceAvailable: true, MatchScore: &match, VoicedSecondsUsed: 3}

	if got := mustScore(t, det(0.5, 1, 3), ctx(trusted), verified).TrustDiscount; got != 10 {
		t.Fatalf("all gates satisfied but trust_discount = %v, want 10", got)
	}

	weaker := map[string]any{}
	for k, v := range trusted {
		weaker[k] = v
	}
	weaker["caller_attestation"] = "KNOWN_UNVERIFIED"
	weaker["attestation_source"] = "CALLER_ID_ONLY"
	if got := mustScore(t, det(0.5, 1, 3), ctx(weaker), verified).TrustDiscount; got != 0 {
		t.Errorf("caller-ID-only attestation earned a discount of %v, want 0", got)
	}

	adversarial := det(0.5, 1, 3)
	adversarial.AdversarialFlag = true
	if got := mustScore(t, adversarial, ctx(trusted), verified).TrustDiscount; got != 0 {
		t.Errorf("adversarial input earned a discount of %v, want 0", got)
	}

	weak := 0.84
	if got := mustScore(t, det(0.5, 1, 3), ctx(trusted),
		&Verification{ReferenceAvailable: true, MatchScore: &weak, VoicedSecondsUsed: 3}).TrustDiscount; got != 0 {
		t.Errorf("match_score below the 0.85 gate earned a discount of %v, want 0", got)
	}

	large := map[string]any{}
	for k, v := range trusted {
		large[k] = v
	}
	large["transaction_value"] = 50001.0
	if got := mustScore(t, det(0.5, 1, 3), ctx(large), verified).TrustDiscount; got != 0 {
		t.Errorf("transaction above the routine threshold earned a discount of %v, want 0", got)
	}
}

// CLAUDE.md invariant 7 / DR-012: never read match_score without reference_available, and fail
// loudly rather than coercing.
func TestMatchScoreContractFailsLoudly(t *testing.T) {
	absent := mustScore(t, det(0.5, 1, 3), nil, &Verification{ReferenceAvailable: false})
	for _, r := range absent.DegradedReasons {
		if r == "verifier_failed" {
			t.Error("having no enrolled reference is not a verifier failure")
		}
	}
	if absent.InactiveReasons["speaker"] != "not_enrolled" {
		t.Errorf("speaker inactive reason = %q, want not_enrolled", absent.InactiveReasons["speaker"])
	}

	stray := 0.5
	_, err := ScoreWindow(det(0.5, 1, 3), nil, &Verification{ReferenceAvailable: false, MatchScore: &stray}, P)
	if !errors.Is(err, ErrMatchScoreContract) {
		t.Errorf("a match_score with no reference returned %v, want ErrMatchScoreContract", err)
	}

	_, err = ScoreWindow(det(0.5, 1, 3), nil, &Verification{ReferenceAvailable: true, VoicedSecondsUsed: 3}, P)
	if !errors.Is(err, ErrMatchScoreContract) {
		t.Errorf("a reference with no match_score returned %v, want ErrMatchScoreContract", err)
	}
}

// 03 section 6: losing the Context Service lowers the HIGH threshold rather than the score.
func TestContextFailureLowersTheHighThreshold(t *testing.T) {
	r := mustScore(t, det(0.6421, 1, 3), nil, nil)
	if !r.ContextDegraded {
		t.Fatal("context_degraded not set with no context supplied")
	}
	if *r.WindowScore >= P.Bands.HighMin {
		t.Fatalf("test fixture invalid: score %v is HIGH at the normal threshold", *r.WindowScore)
	}
	if r.Band != "HIGH" {
		t.Errorf("band = %s at score %v, want HIGH under the degraded threshold of %v",
			r.Band, *r.WindowScore, P.Bands.HighMin-P.DegradedThresholdDelta)
	}
}

// 04 sections 3-5: EWMA plus decaying peak, two-stage hysteresis, and one alert per escalation.
func TestSessionUsesPeakMemoryAndTwoStageHysteresis(t *testing.T) {
	s := NewSessionRisk("session")
	w := func(v float64) *WindowResult {
		return &WindowResult{WindowScore: &v, AppliedFloors: []Floor{}}
	}

	if got := s.Update(w(90), P); got.Band != "UNKNOWN" {
		t.Errorf("first window band = %s, want UNKNOWN — one window is not yet a pattern", got.Band)
	}
	medium := s.Update(w(90), P)
	if medium.Band != "MEDIUM" || !medium.BandChanged {
		t.Errorf("second window = %s changed=%v, want MEDIUM changed=true", medium.Band, medium.BandChanged)
	}
	high := s.Update(w(90), P)
	if high.Band != "HIGH" || !high.BandChanged {
		t.Errorf("third window = %s changed=%v, want HIGH changed=true — LOW must not jump "+
			"straight to HIGH, but MEDIUM to HIGH is one step", high.Band, high.BandChanged)
	}
	if after := s.Update(w(5), P); *after.SessionScore < 80 {
		t.Errorf("session score fell to %v after one quiet window; the decaying peak must "+
			"resist single-window dilution", *after.SessionScore)
	}
	if s.EscalationSeq() != 2 {
		t.Errorf("escalation_seq = %d, want 2", s.EscalationSeq())
	}
}

// CLAUDE.md invariant 11: one alert per band escalation, not per window.
func TestAlertStormIsBounded(t *testing.T) {
	s := NewSessionRisk("storm")
	v := 95.0
	keys := 0
	seen := map[string]bool{}
	for i := 0; i < 600; i++ {
		got := s.Update(&WindowResult{WindowScore: &v, AppliedFloors: []Floor{}}, P)
		if got.AlertKey != nil {
			keys++
			if seen[*got.AlertKey] {
				t.Fatalf("alert key %s reissued at window %d", *got.AlertKey, i)
			}
			seen[*got.AlertKey] = true
		}
	}
	if keys != 2 {
		t.Errorf("600 windows at HIGH produced %d alerts, want 2 (one per escalation)", keys)
	}
}

func TestPolicyPackDoesNotClaimToBeTheBankingPack(t *testing.T) {
	// DEV-1: the Phase 0 pack must be named honestly. 03 section 7's banking pack is
	// ai .45 / speaker .30 / context .25 with bands 35/65 and mandatory speaker verification.
	if P.Version == "banking-default@1.0.0" {
		t.Fatal("policy version claims to be the banking pack")
	}
	if P.Weights.AI == 0.45 && P.Weights.Speaker == 0.30 && P.MandatorySpeakerVerification {
		t.Fatal("weights match the banking pack but the version string does not say so")
	}
}

func round6(x float64) float64 { return numeric.Round(x, 6) }
