// Package numeric provides the rounding primitive the Go port shares with the Python
// reference implementation.
package numeric

import (
	"math"
	"strconv"
)

// Round reproduces CPython's `round(float, places)` exactly.
//
// This is not a cosmetic detail. Every score in docs/03 and docs/04 is rounded before it is
// compared against a band threshold, and CLAUDE.md invariant 6 requires contributing_factors to
// sum to the base score. If Go rounded differently from Python, a window sitting on a threshold
// would land in a different band and the sum assertion would fail on inputs no unit test happens
// to cover.
//
// math.Round is wrong here: it rounds half away from zero, while CPython rounds half to even on
// the exact decimal value of the double (via David Gay's dtoa). strconv.FormatFloat applies the
// same round-half-to-even rule to the same exact value, so formatting and reparsing is the
// faithful port, not an approximation of one.
func Round(x float64, places int) float64 {
	if math.IsNaN(x) || math.IsInf(x, 0) {
		return x
	}
	v, err := strconv.ParseFloat(strconv.FormatFloat(x, 'f', places, 64), 64)
	if err != nil {
		return x
	}
	return v
}
