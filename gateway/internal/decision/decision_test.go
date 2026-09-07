package decision

import (
	"errors"
	"strings"
	"testing"

	"github.com/vox-guard/voxguard/gateway/internal/policy"
	"github.com/vox-guard/voxguard/gateway/internal/scoring"
)

var P = policy.Default

func ctx(txn float64, newBeneficiary bool, attestation string) *scoring.Context {
	c := &scoring.Context{
		TransactionValue:  &txn,
		BeneficiaryIsNew:  &newBeneficiary,
		CallerAttestation: &attestation,
	}
	c.SetKeys(3)
	return c
}

func TestBandMapsToDecision(t *testing.T) {
	cases := []struct {
		name       string
		band       string
		context    *scoring.Context
		wantAction string
	}{
		{"high value high band blocks", "HIGH", ctx(250000, false, "UNKNOWN"), "BLOCK"},
		{"new beneficiary high band blocks", "HIGH", ctx(1000, true, "UNKNOWN"), "BLOCK"},
		{"routine high band steps up", "HIGH", ctx(1000, false, "UNKNOWN"), "STEP_UP"},
		{"unverified medium steps up", "MEDIUM", ctx(1000, false, "KNOWN_UNVERIFIED"), "STEP_UP"},
		{"verified medium warns", "MEDIUM", ctx(1000, false, "VERIFIED"), "WARN"},
		{"low band allows", "LOW", ctx(1000, false, "VERIFIED"), "ALLOW"},
	}
	s := New([]byte("k"))
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := s.Decide(Verdict{SessionID: c.name, Band: c.band}, c.context, P)
			if got.Decision != c.wantAction {
				t.Errorf("decision = %s, want %s", got.Decision, c.wantAction)
			}
			if got.PolicyVersion != P.Version {
				t.Errorf("policy_version = %q, want %q — CLAUDE.md invariant 8",
					got.PolicyVersion, P.Version)
			}
			if got.OriginSignature == "" {
				t.Error("decision carries no origin signature")
			}
		})
	}
}

// CLAUDE.md invariant 2: UNKNOWN must not decide ALLOW.
func TestUnknownBandNeverAllows(t *testing.T) {
	s := New([]byte("k"))
	got := s.Decide(Verdict{SessionID: "u", Band: "UNKNOWN"}, nil, P)
	if got.Decision == "ALLOW" {
		t.Fatal("an unassessed session decided ALLOW; absence of evidence is not a pass")
	}
	if got.Decision != P.DefaultDegradedDecision {
		t.Errorf("decision = %s, want the pack's degraded default %s",
			got.Decision, P.DefaultDegradedDecision)
	}
	degraded := s.Decide(Verdict{SessionID: "d", Band: "UNKNOWN", Degraded: true}, nil, P)
	if degraded.ReasonCode != "INSUFFICIENT_EVIDENCE_DEGRADED_DEFAULT" {
		t.Errorf("reason_code = %s, want INSUFFICIENT_EVIDENCE_DEGRADED_DEFAULT", degraded.ReasonCode)
	}
}

// CLAUDE.md invariant 12: never state a conclusion about a person.
func TestExplanationsDoNotAccuseAPerson(t *testing.T) {
	s := New([]byte("k"))
	banned := []string{"fraud detected", "is a fraudster", "the caller is lying", "criminal",
		"impersonator", "you are"}
	for _, band := range []string{"HIGH", "MEDIUM", "LOW", "UNKNOWN"} {
		got := s.Decide(Verdict{SessionID: band, Band: band}, ctx(1000, false, "UNKNOWN"), P)
		lower := strings.ToLower(got.ExplanationForHuman)
		for _, phrase := range banned {
			if strings.Contains(lower, phrase) {
				t.Errorf("band %s explanation states a conclusion about a person (%q): %q",
					band, phrase, got.ExplanationForHuman)
			}
		}
	}
}

func TestWALSequenceIsMonotonic(t *testing.T) {
	s := New([]byte("k"))
	previous := int64(0)
	for i := 0; i < 20; i++ {
		got := s.Decide(Verdict{SessionID: "seq", Band: "LOW"}, nil, P)
		if got.WALSeq <= previous {
			t.Fatalf("wal_seq went %d then %d; it must be monotonic", previous, got.WALSeq)
		}
		previous = got.WALSeq
	}
}

func TestOverrideRequiresRoleAndReason(t *testing.T) {
	s := New([]byte("k"))

	if _, err := s.Override("s", "ALLOW", "ok", "sup1", "SUPERVISOR"); !errors.Is(err, ErrReasonRequired) {
		t.Errorf("a two-character reason returned %v, want ErrReasonRequired", err)
	}
	if _, err := s.Override("s", "ALLOW", "verified out of band", "a1", "AGENT"); !errors.Is(err, ErrRoleNotAuthorised) {
		t.Errorf("an unauthorised role returned %v, want ErrRoleNotAuthorised", err)
	}
	if _, err := s.Override("s", "RELEASE", "verified out of band", "sup1", "SUPERVISOR"); !errors.Is(err, ErrInvalidDecision) {
		t.Errorf("a decision outside the enum returned %v, want ErrInvalidDecision", err)
	}

	o, err := s.Override("s", "ALLOW", "verified out of band on registered number", "sup1", "SUPERVISOR")
	if err != nil {
		t.Fatalf("valid override rejected: %v", err)
	}
	if o.OriginSignature == "" || o.Timestamp == "" {
		t.Error("override is not attributable: missing signature or timestamp")
	}
}

func TestOverrideTakesEffectAndIsAttributed(t *testing.T) {
	s := New([]byte("k"))
	if _, err := s.Override("call-1", "ALLOW", "verified out of band", "sup1", "SUPERVISOR"); err != nil {
		t.Fatalf("override: %v", err)
	}
	got := s.Decide(Verdict{SessionID: "call-1", Band: "HIGH"}, ctx(500000, true, "UNKNOWN"), P)
	if got.Decision != "ALLOW" {
		t.Errorf("decision = %s, want the overridden ALLOW", got.Decision)
	}
	if !got.Overridden || got.OverrideDetails == nil {
		t.Fatal("the override is not visible on the decision record")
	}
	if !strings.Contains(got.ExplanationForHuman, "sup1") {
		t.Errorf("explanation %q does not name who authorised the override", got.ExplanationForHuman)
	}
	if got.OverrideDetails.Role != "SUPERVISOR" {
		t.Errorf("override role = %q, want SUPERVISOR", got.OverrideDetails.Role)
	}

	if !s.ClearOverride("call-1") {
		t.Fatal("ClearOverride reported no override to clear")
	}
	if after := s.Decide(Verdict{SessionID: "call-1", Band: "HIGH"}, ctx(500000, true, "UNKNOWN"), P); after.Decision != "BLOCK" {
		t.Errorf("after clearing the override the decision is %s, want the automated BLOCK", after.Decision)
	}
}
