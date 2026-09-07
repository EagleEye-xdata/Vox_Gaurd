package numeric

import (
	"math"
	"testing"
)

// The values below were produced by CPython 3.11: `round(x, places)`.
func TestRoundMatchesCPython(t *testing.T) {
	cases := []struct {
		in     float64
		places int
		want   float64
	}{
		{92.75, 2, 92.75},
		{92.5885, 2, 92.59},
		{69.427484, 2, 69.43},
		{2.675, 2, 2.67},   // math.Round would give 2.68 here
		{0.125, 2, 0.12},   // exact binary midpoint: half-to-even rounds down
		{0.375, 2, 0.38},   // exact binary midpoint: half-to-even rounds up
		{-2.675, 2, -2.67}, // sign must not flip the tie rule
		{33.207583, 6, 33.207583},
		{0.6, 6, 0.6},
		{100.0, 2, 100.0},
		{0.0, 2, 0.0},
	}
	for _, c := range cases {
		if got := Round(c.in, c.places); got != c.want {
			t.Errorf("Round(%v, %d) = %v, want %v", c.in, c.places, got, c.want)
		}
	}
}

func TestRoundPassesThroughNonFinite(t *testing.T) {
	if got := Round(math.NaN(), 2); !math.IsNaN(got) {
		t.Errorf("Round(NaN) = %v, want NaN", got)
	}
	if got := Round(math.Inf(1), 2); !math.IsInf(got, 1) {
		t.Errorf("Round(+Inf) = %v, want +Inf", got)
	}
}
