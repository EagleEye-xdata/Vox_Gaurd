package scoring

import (
	"encoding/json"
	"math"
	"os"
	"reflect"
	"testing"

	"github.com/vox-guard/voxguard/gateway/internal/policy"
)

// The golden file is emitted by backend/tools/emit_golden.py from the Python implementation this
// package replaces. Replaying it is the only evidence that the port did not quietly change the
// arithmetic — and the arithmetic is precisely what the v2 spec exists to fix (DR-003 / DEF-1:
// v1 made a HIGH band unreachable; DR-007: adversary-controlled context could lower a score).
//
// Regenerate only when a policy or spec change is intended, and say so in the commit message.

type goldenFile struct {
	Policy   policy.Pack `json:"policy"`
	Windows  []goldenWindow
	Sessions []goldenSession
}

type goldenWindow struct {
	Name         string        `json:"name"`
	Detection    *Detection    `json:"detection"`
	Context      *Context      `json:"context"`
	Verification *Verification `json:"verification"`
	Expected     WindowResult  `json:"expected"`
}

type goldenSession struct {
	Name      string `json:"name"`
	SessionID string `json:"session_id"`
	Steps     []struct {
		Window   WindowResult    `json:"window"`
		Expected SessionSnapshot `json:"expected"`
	} `json:"steps"`
}

func loadGolden(t *testing.T) goldenFile {
	t.Helper()
	raw, err := os.ReadFile("testdata/golden_windows.json")
	if err != nil {
		t.Fatalf("read golden file: %v", err)
	}
	var g goldenFile
	if err := json.Unmarshal(raw, &g); err != nil {
		t.Fatalf("decode golden file: %v", err)
	}
	if len(g.Windows) == 0 || len(g.Sessions) == 0 {
		t.Fatal("golden file is empty; run: python backend/tools/emit_golden.py")
	}
	return g
}

// TestGoldenPolicyMatches guards against the port drifting from the emitted policy pack. A
// changed weight would silently invalidate every other golden comparison below.
func TestGoldenPolicyMatches(t *testing.T) {
	g := loadGolden(t)
	if !reflect.DeepEqual(g.Policy, policy.Default) {
		t.Fatalf("policy pack drifted from the golden file\n go:     %+v\n golden: %+v",
			policy.Default, g.Policy)
	}
}

func TestGoldenWindowsMatchPythonReference(t *testing.T) {
	g := loadGolden(t)
	for _, c := range g.Windows {
		t.Run(c.Name, func(t *testing.T) {
			got, err := ScoreWindow(c.Detection, c.Context, c.Verification, g.Policy)
			if err != nil {
				t.Fatalf("ScoreWindow returned %v", err)
			}
			compareWindow(t, got, &c.Expected)
		})
	}
}

func TestGoldenSessionsMatchPythonReference(t *testing.T) {
	g := loadGolden(t)
	for _, c := range g.Sessions {
		t.Run(c.Name, func(t *testing.T) {
			s := NewSessionRisk(c.SessionID)
			for i, step := range c.Steps {
				w := step.Window
				got := s.Update(&w, g.Policy)
				compareSnapshot(t, i, got, step.Expected)
			}
		})
	}
}

func compareWindow(t *testing.T, got, want *WindowResult) {
	t.Helper()
	eqPtr(t, "window_score", got.WindowScore, want.WindowScore)
	eqPtr(t, "base_score", got.BaseScore, want.BaseScore)
	eqF(t, "trust_discount", got.TrustDiscount, want.TrustDiscount)
	eqS(t, "band", got.Band, want.Band)
	eqB(t, "context_degraded", got.ContextDegraded, want.ContextDegraded)
	eqB(t, "degraded", got.Degraded, want.Degraded)
	eqS(t, "policy_version", got.PolicyVersion, want.PolicyVersion)
	eqStrings(t, "active_signals", got.ActiveSignals, want.ActiveSignals)
	eqStrings(t, "inactive_signals", got.InactiveSignals, want.InactiveSignals)
	eqStrings(t, "degraded_reasons", got.DegradedReasons, want.DegradedReasons)

	if len(got.InactiveReasons) != len(want.InactiveReasons) {
		t.Errorf("inactive_reasons: got %v, want %v", got.InactiveReasons, want.InactiveReasons)
	}
	for k, v := range want.InactiveReasons {
		if got.InactiveReasons[k] != v {
			t.Errorf("inactive_reasons[%s]: got %q, want %q", k, got.InactiveReasons[k], v)
		}
	}

	if len(got.AppliedFloors) != len(want.AppliedFloors) {
		t.Fatalf("applied_floors: got %v, want %v", got.AppliedFloors, want.AppliedFloors)
	}
	for i := range want.AppliedFloors {
		if got.AppliedFloors[i] != want.AppliedFloors[i] {
			t.Errorf("applied_floors[%d]: got %+v, want %+v", i, got.AppliedFloors[i], want.AppliedFloors[i])
		}
	}

	if len(got.ContributingFactors) != len(want.ContributingFactors) {
		t.Fatalf("contributing_factors: got %v, want %v", got.ContributingFactors, want.ContributingFactors)
	}
	for i := range want.ContributingFactors {
		if got.ContributingFactors[i] != want.ContributingFactors[i] {
			t.Errorf("contributing_factors[%d]: got %+v, want %+v",
				i, got.ContributingFactors[i], want.ContributingFactors[i])
		}
	}

	if len(got.ContextDetails) != len(want.ContextDetails) {
		t.Fatalf("context_details: got %v, want %v", got.ContextDetails, want.ContextDetails)
	}
	for i := range want.ContextDetails {
		if got.ContextDetails[i] != want.ContextDetails[i] {
			t.Errorf("context_details[%d]: got %+v, want %+v", i, got.ContextDetails[i], want.ContextDetails[i])
		}
	}
}

func compareSnapshot(t *testing.T, step int, got, want SessionSnapshot) {
	t.Helper()
	label := func(f string) string { return f + " at step " + itoa(step) }
	eqPtr(t, label("session_score"), got.SessionScore, want.SessionScore)
	eqPtr(t, label("authenticity_score"), got.AuthenticityScore, want.AuthenticityScore)
	eqPtr(t, label("ewma_score"), got.EWMAScore, want.EWMAScore)
	eqS(t, label("band"), got.Band, want.Band)
	eqB(t, label("band_changed"), got.BandChanged, want.BandChanged)
	eqF(t, label("peak_score"), got.PeakScore, want.PeakScore)
	if got.EscalationSeq != want.EscalationSeq {
		t.Errorf("%s: got %d, want %d", label("escalation_seq"), got.EscalationSeq, want.EscalationSeq)
	}
	if (got.AlertKey == nil) != (want.AlertKey == nil) {
		t.Errorf("%s: got %v, want %v", label("alert_key"), deref(got.AlertKey), deref(want.AlertKey))
	} else if got.AlertKey != nil && *got.AlertKey != *want.AlertKey {
		t.Errorf("%s: got %s, want %s", label("alert_key"), *got.AlertKey, *want.AlertKey)
	}
	if len(got.History) != len(want.History) {
		t.Fatalf("%s: got %v, want %v", label("history"), got.History, want.History)
	}
	for i := range want.History {
		if got.History[i] != want.History[i] {
			t.Errorf("%s[%d]: got %v, want %v", label("history"), i, got.History[i], want.History[i])
		}
	}
	if len(got.BandTimeline) != len(want.BandTimeline) {
		t.Fatalf("%s: got %v, want %v", label("band_timeline"), got.BandTimeline, want.BandTimeline)
	}
	for i := range want.BandTimeline {
		if got.BandTimeline[i] != want.BandTimeline[i] {
			t.Errorf("%s[%d]: got %+v, want %+v", label("band_timeline"), i,
				got.BandTimeline[i], want.BandTimeline[i])
		}
	}
}

func eqPtr(t *testing.T, field string, got, want *float64) {
	t.Helper()
	switch {
	case got == nil && want == nil:
	case got == nil || want == nil:
		t.Errorf("%s: got %v, want %v", field, derefF(got), derefF(want))
	case *got != *want && math.Abs(*got-*want) > 0:
		t.Errorf("%s: got %v, want %v", field, *got, *want)
	}
}

func eqF(t *testing.T, field string, got, want float64) {
	t.Helper()
	if got != want {
		t.Errorf("%s: got %v, want %v", field, got, want)
	}
}

func eqS(t *testing.T, field, got, want string) {
	t.Helper()
	if got != want {
		t.Errorf("%s: got %q, want %q", field, got, want)
	}
}

func eqB(t *testing.T, field string, got, want bool) {
	t.Helper()
	if got != want {
		t.Errorf("%s: got %v, want %v", field, got, want)
	}
}

func eqStrings(t *testing.T, field string, got, want []string) {
	t.Helper()
	if len(got) != len(want) {
		t.Errorf("%s: got %v, want %v", field, got, want)
		return
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("%s[%d]: got %q, want %q", field, i, got[i], want[i])
		}
	}
}

func deref(p *string) string {
	if p == nil {
		return "<nil>"
	}
	return *p
}

func derefF(p *float64) any {
	if p == nil {
		return "<nil>"
	}
	return *p
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}
