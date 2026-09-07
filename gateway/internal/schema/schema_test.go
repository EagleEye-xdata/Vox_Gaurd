package schema

import (
	"strings"
	"testing"
)

func decodeStart(t *testing.T, body string) (*Start, error) {
	t.Helper()
	var s Start
	if err := Decode(strings.NewReader(body), &s); err != nil {
		return nil, err
	}
	return &s, s.Validate()
}

// Pydantic's extra="forbid". A typo in a security-relevant field must fail loudly rather than
// silently taking the default: "simulate_detector_failre: true" that quietly means "false" is
// how a demo ends up proving the wrong thing.
func TestUnknownFieldsAreRejected(t *testing.T) {
	_, err := decodeStart(t, `{"filename":"a.wav","simulate_detector_failre":true}`)
	if err == nil {
		t.Fatal("a misspelled field was accepted")
	}
	if !strings.Contains(err.Error(), "simulate_detector_failre") {
		t.Errorf("error %q does not name the offending field", err)
	}
}

// CLAUDE.md invariant 5 / 03 section 4, enforced at the boundary.
func TestVerifiedAttestationRequiresATrustedSource(t *testing.T) {
	cases := []struct {
		name   string
		body   string
		reject bool
	}{
		{"caller id cannot verify", `{"caller_attestation":"VERIFIED","attestation_source":"CALLER_ID_ONLY"}`, true},
		{"no source cannot verify", `{"caller_attestation":"VERIFIED","attestation_source":"NONE"}`, true},
		{"omitted source cannot verify", `{"caller_attestation":"VERIFIED"}`, true},
		{"stir shaken can verify", `{"caller_attestation":"VERIFIED","attestation_source":"STIR_SHAKEN_A"}`, false},
		{"app session can verify", `{"caller_attestation":"VERIFIED","attestation_source":"AUTHENTICATED_APP_SESSION"}`, false},
		{"caller id may claim unverified", `{"caller_attestation":"KNOWN_UNVERIFIED","attestation_source":"CALLER_ID_ONLY"}`, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			var ctx Context
			if err := Decode(strings.NewReader(c.body), &ctx); err != nil {
				t.Fatalf("decode: %v", err)
			}
			err := ctx.Validate()
			if c.reject && err == nil {
				t.Error("an untrusted source produced a VERIFIED attestation")
			}
			if !c.reject && err != nil {
				t.Errorf("a trusted source was rejected: %v", err)
			}
		})
	}
}

func TestContextDefaultsMatchThePythonModel(t *testing.T) {
	var ctx Context
	if err := Decode(strings.NewReader(`{}`), &ctx); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if err := ctx.Validate(); err != nil {
		t.Fatalf("validate: %v", err)
	}
	if *ctx.CallerAttestation != "UNKNOWN" {
		t.Errorf("caller_attestation default = %q, want UNKNOWN", *ctx.CallerAttestation)
	}
	if *ctx.RequestUrgency != "normal" {
		t.Errorf("request_urgency default = %q, want normal", *ctx.RequestUrgency)
	}
	if *ctx.UrgencySource != "POLICY_DERIVED" {
		t.Errorf("urgency_source default = %q, want POLICY_DERIVED", *ctx.UrgencySource)
	}
	if *ctx.ConfirmedFraudFlags90d != 0 {
		t.Errorf("confirmed_fraud_flags_90d default = %d, want 0", *ctx.ConfirmedFraudFlags90d)
	}
	// transaction_value has no default: an unknown transaction value must renormalise out of the
	// context term, not be scored as a zero-value transaction.
	if ctx.TransactionValue != nil {
		t.Errorf("transaction_value defaulted to %v; it must stay absent", *ctx.TransactionValue)
	}

	s := ctx.ToScoring()
	if s.Empty() {
		t.Error("a defaulted context converted to an empty scoring context")
	}
}

func TestStartValidatesBounds(t *testing.T) {
	if _, err := decodeStart(t, `{"filename":""}`); err == nil {
		t.Error("an empty filename was accepted")
	}
	if _, err := decodeStart(t, `{"filename":"a.wav","interval":10}`); err == nil {
		t.Error("an interval of 10 seconds was accepted; the bound is 4")
	}
	if _, err := decodeStart(t, `{"filename":"a.wav","language":"Hindi"}`); err == nil {
		t.Error("a free-text language was accepted")
	}

	s, err := decodeStart(t, `{"filename":"fixture-steady.wav","language":"hi-en"}`)
	if err != nil {
		t.Fatalf("a valid start was rejected: %v", err)
	}
	if *s.Label != "Simulated call" || *s.Interval != 1 {
		t.Errorf("defaults not applied: label=%q interval=%v", *s.Label, *s.Interval)
	}
}

// CLAUDE.md invariant 8: no audit record without the versions that produced it.
func TestLedgerEventRequiresModelVersions(t *testing.T) {
	body := `{"call_id":"c1","event_type":"observation","risk_score":50}`
	var e LedgerEvent
	if err := Decode(strings.NewReader(body), &e); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if err := e.Validate(); err == nil {
		t.Fatal("an audit record with no model_versions was accepted")
	}

	e.ModelVersions = map[string]string{"detector": "heuristic-acoustic@1.2.0"}
	if err := e.Validate(); err != nil {
		t.Fatalf("a complete record was rejected: %v", err)
	}
	m := e.Map()
	if m["policy_version"] != "demo-detector-first@2.1.0" {
		t.Errorf("policy_version = %v, want the default", m["policy_version"])
	}
	if m["band"] != "UNKNOWN" {
		t.Errorf("band = %v, want UNKNOWN", m["band"])
	}
}

func TestOverrideAndResolutionEnumsAreClosed(t *testing.T) {
	d := DecisionOverrideRequest{Decision: "RELEASE", Reason: "looks fine", SupervisorID: "s1"}
	if err := d.Validate(); err == nil {
		t.Error("a decision outside the enum was accepted")
	}
	d = DecisionOverrideRequest{Decision: "ALLOW", Reason: "ok", SupervisorID: "s1"}
	if err := d.Validate(); err == nil {
		t.Error("a two-character override reason was accepted")
	}
	d = DecisionOverrideRequest{Decision: "ALLOW", Reason: "verified out of band", SupervisorID: "s1"}
	if err := d.Validate(); err != nil {
		t.Errorf("a valid override was rejected: %v", err)
	}
	if *d.Role != "SUPERVISOR" {
		t.Errorf("role default = %q, want SUPERVISOR", *d.Role)
	}

	r := AlertResolveRequest{Outcome: "PROBABLY_FINE", Notes: "hmm"}
	if err := r.Validate(); err == nil {
		t.Error("a resolution outcome outside the enum was accepted")
	}
}
