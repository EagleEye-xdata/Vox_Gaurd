package scoring

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"math"

	"github.com/vox-guard/voxguard/gateway/internal/numeric"
	"github.com/vox-guard/voxguard/gateway/internal/policy"
)

// SessionRisk is the Session Aggregator (docs/04 sections 3-5, docs/01 section 2 [Go]).
//
// It combines an EWMA with a decaying peak so that a single high window is neither ignored nor
// allowed to pin the session forever, then gates band changes behind N-of-M hysteresis so the
// operator's screen does not flicker. De-escalation is deliberately harder than escalation.
type SessionRisk struct {
	SessionID string

	ewma  *float64
	peak  float64
	score *float64
	band  string

	history       []float64
	bandTimeline  []BandPoint
	escalationSeq int
}

// NewSessionRisk starts a session in the UNKNOWN band. Not LOW — nothing has been assessed yet,
// and CLAUDE.md invariant 2 forbids treating that as a pass.
func NewSessionRisk(sessionID string) *SessionRisk {
	return &SessionRisk{
		SessionID:    sessionID,
		band:         "UNKNOWN",
		history:      []float64{},
		bandTimeline: []BandPoint{{Window: 0, Band: "UNKNOWN"}},
	}
}

// Band returns the current session band.
func (s *SessionRisk) Band() string { return s.band }

// Score returns the current session score, nil while nothing has been assessed.
func (s *SessionRisk) Score() *float64 { return s.score }

// EscalationSeq returns how many band escalations have occurred.
func (s *SessionRisk) EscalationSeq() int { return s.escalationSeq }

// Update folds one window result into the session verdict.
func (s *SessionRisk) Update(w *WindowResult, p policy.Pack) SessionSnapshot {
	if w == nil || w.WindowScore == nil {
		// An unassessed window carries no evidence, so it must not move the session at all.
		return s.snapshot(false, nil)
	}
	value := *w.WindowScore

	// 03 section 6: the window's threshold delta must follow through to the session band,
	// otherwise the window says HIGH and the session quietly disagrees.
	contextDegraded := w.ContextDegraded
	bandOf := func(v float64) string { return BandFor(&v, p, contextDegraded) }

	if s.ewma == nil {
		s.ewma = f64(value)
	} else {
		s.ewma = f64(p.EWMAAlpha*value + (1-p.EWMAAlpha)*(*s.ewma))
	}
	s.peak = math.Max(s.peak*p.PeakDecay, value)
	s.score = f64(numeric.Round(math.Max(*s.ewma, s.peak-p.PeakDiscount), 2))
	s.history = append(s.history, *s.score)

	candidate, previous := bandOf(*s.score), s.band

	// An adversarial or replay floor is evidence of an active attack, not of drift. It escalates
	// on the first window rather than waiting for the N-of-M window to fill.
	immediate := false
	for _, f := range w.AppliedFloors {
		if f.Reason == "adversarial_input" || f.Reason == "replay_suspected" {
			immediate = true
			break
		}
	}

	switch {
	case s.band == "UNKNOWN":
		if candidate == "LOW" {
			s.band = "LOW"
		} else if immediate || s.countAtLeast(bandOf, "MEDIUM", 3) >= 2 {
			s.band = "MEDIUM"
		}
	case BandRank[candidate] > BandRank[s.band]:
		// One step at a time: LOW cannot jump straight to HIGH.
		next := "HIGH"
		if s.band == "LOW" {
			next = "MEDIUM"
		}
		if immediate || s.countAtLeast(bandOf, next, 3) >= 2 {
			s.band = next
		}
	case BandRank[candidate] < BandRank[s.band] && len(s.history) >= 6:
		// Asymmetric de-escalation (04 section 4): 5 of the last 6 windows must sit a clear
		// 5 points below the threshold before risk is allowed to come down.
		lower := "MEDIUM"
		ceiling := p.Bands.HighMin
		if contextDegraded {
			ceiling -= p.DegradedThresholdDelta
		}
		if s.band == "MEDIUM" {
			lower = "LOW"
			ceiling = p.Bands.MediumMin
		}
		quiet := 0
		for _, v := range s.history[len(s.history)-6:] {
			if v < ceiling-5 {
				quiet++
			}
		}
		if quiet >= 5 {
			s.band = lower
		}
	}

	changed := s.band != previous
	if changed {
		s.bandTimeline = append(s.bandTimeline, BandPoint{Window: len(s.history), Band: s.band})
	}

	// CLAUDE.md invariant 11: one alert per band escalation, not per window. The key is derived
	// from the escalation counter, so a session that sits at HIGH for 600 windows still produces
	// exactly the alerts its escalations earned.
	var alertKey *string
	if changed && (s.band == "MEDIUM" || s.band == "HIGH") {
		s.escalationSeq++
		sum := sha256.Sum256([]byte(fmt.Sprintf("%s:main:%s:%d", s.SessionID, s.band, s.escalationSeq)))
		key := hex.EncodeToString(sum[:])
		alertKey = &key
	}
	return s.snapshot(changed, alertKey)
}

// countAtLeast counts how many of the last n scores reach at least the given band.
func (s *SessionRisk) countAtLeast(bandOf func(float64) string, band string, n int) int {
	start := len(s.history) - n
	if start < 0 {
		start = 0
	}
	count := 0
	for _, v := range s.history[start:] {
		if BandRank[bandOf(v)] >= BandRank[band] {
			count++
		}
	}
	return count
}

// Snapshot returns the current verdict without folding in a new window.
func (s *SessionRisk) Snapshot() SessionSnapshot { return s.snapshot(false, nil) }

func (s *SessionRisk) snapshot(changed bool, alertKey *string) SessionSnapshot {
	var authenticity, ewma *float64
	if s.score != nil {
		authenticity = f64(numeric.Round(100-*s.score, 2))
	}
	if s.ewma != nil {
		ewma = f64(numeric.Round(*s.ewma, 2))
	}
	history := make([]float64, len(s.history))
	copy(history, s.history)
	timeline := make([]BandPoint, len(s.bandTimeline))
	copy(timeline, s.bandTimeline)

	return SessionSnapshot{
		SessionScore:      s.score,
		AuthenticityScore: authenticity,
		Band:              s.band,
		BandChanged:       changed,
		History:           history,
		PeakScore:         numeric.Round(s.peak, 2),
		EWMAScore:         ewma,
		BandTimeline:      timeline,
		EscalationSeq:     s.escalationSeq,
		AlertKey:          alertKey,
	}
}
