package alerts

import (
	"errors"
	"strings"
	"testing"
	"time"
)

func newAlert(t *testing.T, s *Store, key, band string) *Alert {
	t.Helper()
	a, _ := s.Ensure(key, "call-1", 82.5, band, "demo-detector-first@2.1.0",
		map[string]string{"detector": "heuristic-acoustic@1.2.0"})
	return a
}

// CLAUDE.md invariant 11: one alert per band escalation, not per window. The store must be
// idempotent on the escalation key.
func TestEnsureIsIdempotentOnTheEscalationKey(t *testing.T) {
	s := NewStore()
	first, created := s.Ensure("key-1", "call-1", 80, "HIGH", "p", nil)
	if !created {
		t.Fatal("the first Ensure did not report creating the alert")
	}
	for i := 0; i < 500; i++ {
		again, created := s.Ensure("key-1", "call-1", float64(80+i%5), "HIGH", "p", nil)
		if created {
			t.Fatalf("Ensure raised a duplicate alert at repeat %d", i)
		}
		if again != first {
			t.Fatal("Ensure returned a different alert object for the same key")
		}
	}
	if len(s.List()) != 1 {
		t.Errorf("store holds %d alerts after 501 Ensure calls, want 1", len(s.List()))
	}
	if first.RiskScore == 80 {
		t.Error("the score was not refreshed on repeat sightings")
	}
}

// docs/02 §10.1: the SLA tightens with the band, because a HIGH alert is worthless if nobody
// looks at it while the call is still connected.
func TestSLADeadlinesTightenWithTheBand(t *testing.T) {
	base := time.Date(2026, 9, 7, 12, 0, 0, 0, time.UTC)
	cases := []struct {
		band    string
		ackMins float64
		resMins float64
	}{
		{"HIGH", 2, 30},
		{"MEDIUM", 15, 240},
		{"LOW", 60, 1440},
		{"UNKNOWN", 60, 1440},
	}
	for _, c := range cases {
		ack, resolve := SLADeadlines(c.band, base)
		ackT, err := time.Parse(time.RFC3339Nano, ack)
		if err != nil {
			t.Fatalf("%s ack deadline unparseable: %v", c.band, err)
		}
		resT, err := time.Parse(time.RFC3339Nano, resolve)
		if err != nil {
			t.Fatalf("%s resolve deadline unparseable: %v", c.band, err)
		}
		if got := ackT.Sub(base).Minutes(); got != c.ackMins {
			t.Errorf("%s ack SLA = %v min, want %v", c.band, got, c.ackMins)
		}
		if got := resT.Sub(base).Minutes(); got != c.resMins {
			t.Errorf("%s resolve SLA = %v min, want %v", c.band, got, c.resMins)
		}
	}
}

func TestAlertLifecycle(t *testing.T) {
	s := NewStore()
	a := newAlert(t, s, "key-1", "HIGH")
	if a.Status != "active" || a.AssignedTo != nil {
		t.Fatalf("new alert status = %s assigned = %v, want active and unassigned", a.Status, a.AssignedTo)
	}
	if !a.AutoBlock {
		t.Error("a HIGH alert did not set auto_block")
	}

	assigned, err := s.Assign("key-1", "analyst_07")
	if err != nil {
		t.Fatalf("assign: %v", err)
	}
	if assigned.Status != "assigned" || *assigned.AssignedTo != "analyst_07" {
		t.Errorf("after assign: status=%s assignee=%v", assigned.Status, assigned.AssignedTo)
	}

	escalated, changed, err := s.Escalate("key-1")
	if err != nil || !changed || escalated.Status != "escalated" {
		t.Fatalf("escalate = %+v changed=%v err=%v", escalated.Status, changed, err)
	}
	if _, changed, _ := s.Escalate("key-1"); changed {
		t.Error("escalating twice reported a second transition; that would double-write the audit log")
	}

	resolved, err := s.Resolve("key-1", "FALSE_POSITIVE", "Callback confirmed the customer's identity.", "analyst_07")
	if err != nil {
		t.Fatalf("resolve: %v", err)
	}
	if resolved.Status != "resolved" || resolved.Resolution == nil {
		t.Fatalf("after resolve: status=%s resolution=%v", resolved.Status, resolved.Resolution)
	}
	if resolved.Resolution.Outcome != "FALSE_POSITIVE" {
		t.Errorf("outcome = %s, want FALSE_POSITIVE", resolved.Resolution.Outcome)
	}
}

func TestResolutionOutcomeEnumIsClosed(t *testing.T) {
	s := NewStore()
	newAlert(t, s, "key-1", "MEDIUM")

	if _, err := s.Resolve("key-1", "PROBABLY_FINE", "notes here", "a1"); !errors.Is(err, ErrInvalidOutcome) {
		t.Errorf("an outcome outside the enum returned %v, want ErrInvalidOutcome", err)
	}
	if _, err := s.Resolve("key-1", "CONFIRMED_FRAUD", "  ", "a1"); !errors.Is(err, ErrNotesRequired) {
		t.Errorf("blank notes returned %v, want ErrNotesRequired", err)
	}
	if _, err := s.Resolve("missing", "CONFIRMED_FRAUD", "notes here", "a1"); !errors.Is(err, ErrNotFound) {
		t.Errorf("an unknown alert returned %v, want ErrNotFound", err)
	}
}

// docs/09 §5: an alert a person cannot contest is not an acceptable alert.
func TestAppealsAreRecordedAgainstTheAlert(t *testing.T) {
	s := NewStore()
	newAlert(t, s, "key-1", "HIGH")

	if _, err := s.LodgeAppeal("key-1", "no", "CUSTOMER", ""); !errors.Is(err, ErrReasonRequired) {
		t.Errorf("a two-character reason returned %v, want ErrReasonRequired", err)
	}

	appeal, err := s.LodgeAppeal("key-1", "I made this call myself from my registered handset.", "CUSTOMER", "")
	if err != nil {
		t.Fatalf("lodge appeal: %v", err)
	}
	if appeal.Status != "PENDING_REVIEW" {
		t.Errorf("appeal status = %s, want PENDING_REVIEW", appeal.Status)
	}
	if appeal.ContactInfo != "Not provided" {
		t.Errorf("missing contact info = %q, want the explicit \"Not provided\"", appeal.ContactInfo)
	}
	a, _ := s.Get("key-1")
	if len(a.Appeals) != 1 || a.Appeals[0].AppealID != appeal.AppealID {
		t.Errorf("the appeal is not attached to the alert: %+v", a.Appeals)
	}
}

// CLAUDE.md invariant 12: the agent-facing copy must not state a conclusion about a person.
func TestAlertCopyDoesNotAccuseAPerson(t *testing.T) {
	s := NewStore()
	a := newAlert(t, s, "key-1", "HIGH")
	banned := []string{"fraud detected", "fraudster", "the caller is lying", "criminal", "scammer"}
	text := strings.ToLower(a.Message + " " + strings.Join(a.Recommendations, " "))
	for _, phrase := range banned {
		if strings.Contains(text, phrase) {
			t.Errorf("alert copy contains %q: %s", phrase, text)
		}
	}
}

func TestListReturnsNewestFirst(t *testing.T) {
	s := NewStore()
	for _, key := range []string{"a", "b", "c"} {
		newAlert(t, s, key, "MEDIUM")
	}
	got := s.List()
	if len(got) != 3 || got[0].ID != "c" || got[2].ID != "a" {
		t.Errorf("List order = %s %s %s, want c b a", got[0].ID, got[1].ID, got[2].ID)
	}
}
