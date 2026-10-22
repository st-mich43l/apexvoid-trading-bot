// Package quality is the scoring rule every strategy's Quality.Overall is built with.
//
// Technical validity is decided before this package is reached: a setup that violates its strategy's thesis never gets a score.
// Technical quality is how strong a valid setup is by the methodology of its own strategy, in [0, 1]. It is not a probability
// of winning, and the package says nothing about performance (that is measured separately, against matched random entries).
//
// The rule is deliberately plain and identical for every strategy, so that a score can be explained from its parts:
//
//   - A strategy defines a few dimensions. Each is a bounded, continuous function of facts available when the opportunity is
//     created (closed bars only), normalised by the instrument's own volatility or structure, with a documented range.
//   - A dimension for which the evidence is missing scores 0. Absence never earns credit.
//   - Overall is the unweighted arithmetic mean of the dimensions, published exactly: Overall == mean(score_* components).
//     There are no statistical weights; a weight would be a tuning knob.
//   - Components named "score_*" are the dimensions. Every other component is a raw measurement published for audit
//     (its unit is in its name); measurements never enter Overall.
//
// The model name of each strategy ("key_level.quality.v1") is its scoring rule's version. A change to a dimension's definition
// or range is a new version, frozen before it is evaluated against outcomes.
package quality

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Prefix marks a component as a scored dimension.
const Prefix = "score_"

// ModelVersionKey is the component that names the scoring rule's version on the wire. It is provenance, not a measurement and not a
// dimension: it never enters Overall, and a consumer that joins an opportunity to its outcome reads it to know which rule produced the
// score ("<strategy>.quality.v1" is published as 1). A rule whose name carries no ".v<N>" suffix publishes none.
const ModelVersionKey = "quality_model_version"

// Model accumulates the dimensions and measurements of one opportunity.
type Model struct {
	name         string
	dimensions   map[string]float64
	order        []string
	measurements map[string]float64
}

// New starts the scoring of one opportunity under the named model (for example "key_level.quality.v1").
func New(name string) *Model {
	return &Model{name: name, dimensions: map[string]float64{}, measurements: map[string]float64{}}
}

// Name is the scoring rule's version.
func (m *Model) Name() string { return m.name }

// Score records one dimension. The name is published as "score_<name>". A non-finite value is missing evidence and scores 0;
// everything else is clamped to [0, 1]. A dimension is recorded once: scoring the same one twice is a defect in the strategy.
func (m *Model) Score(name string, value float64) {
	key := Prefix + name
	if _, dup := m.dimensions[key]; dup {
		panic(fmt.Sprintf("quality: dimension %q scored twice", key))
	}
	m.dimensions[key] = Clamp01(value)
	m.order = append(m.order, key)
}

// Measure records a raw measurement (not scored, never part of Overall). Non-finite values are dropped, not zeroed: an
// unavailable measurement is left out rather than published as a number.
func (m *Model) Measure(name string, value float64) {
	if name == ModelVersionKey {
		panic(fmt.Sprintf("quality: %q is published by the model itself", name))
	}
	if strings.HasPrefix(name, Prefix) {
		panic(fmt.Sprintf("quality: measurement %q uses the dimension prefix", name))
	}
	if finite(value) {
		m.measurements[name] = value
	}
}

// Overall is the mean of the dimensions; 0 when there are none.
func (m *Model) Overall() float64 {
	if len(m.order) == 0 {
		return 0
	}
	sum := 0.0
	for _, key := range m.order {
		sum += m.dimensions[key]
	}
	return sum / float64(len(m.order))
}

// Version is the number in the model name's ".v<N>" suffix, and whether the name has one.
func (m *Model) Version() (int, bool) {
	i := strings.LastIndex(m.name, ".v")
	if i < 0 {
		return 0, false
	}
	n, err := strconv.Atoi(m.name[i+2:])
	if err != nil || n < 1 {
		return 0, false
	}
	return n, true
}

// Components is the published map: every dimension (score_*), every measurement and the model version (provenance).
func (m *Model) Components() map[string]float64 {
	out := make(map[string]float64, len(m.dimensions)+len(m.measurements)+1)
	for k, v := range m.measurements {
		out[k] = v
	}
	if version, ok := m.Version(); ok {
		out[ModelVersionKey] = float64(version)
	}
	for k, v := range m.dimensions {
		out[k] = v
	}
	return out
}

// Dimensions lists the scored dimension names in the order they were scored.
func (m *Model) Dimensions() []string { return append([]string(nil), m.order...) }

// Verify reports whether a published component map agrees with its Overall: Overall must equal the mean of the score_* components
// (within floating point), and every score_* component lies in [0, 1].
func Verify(overall float64, components map[string]float64) error {
	var names []string
	for name := range components {
		if strings.HasPrefix(name, Prefix) {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	if len(names) == 0 {
		return fmt.Errorf("quality: no %s* dimension is published", Prefix)
	}
	sum := 0.0
	for _, name := range names {
		v := components[name]
		if !finite(v) || v < 0 || v > 1 {
			return fmt.Errorf("quality: dimension %s = %v is outside [0, 1]", name, v)
		}
		sum += v
	}
	if mean := sum / float64(len(names)); math.Abs(mean-overall) > 1e-9 {
		return fmt.Errorf("quality: overall %v is not the mean %v of its dimensions", overall, mean)
	}
	return nil
}

func finite(x float64) bool { return !math.IsNaN(x) && !math.IsInf(x, 0) }

// Clamp01 bounds x to [0, 1]; a non-finite x is 0.
func Clamp01(x float64) float64 {
	if !finite(x) || x < 0 {
		return 0
	}
	if x > 1 {
		return 1
	}
	return x
}

// Ramp rises linearly from 0 at lo to 1 at hi. lo must be below hi.
func Ramp(x, lo, hi float64) float64 {
	if !finite(x) || !(hi > lo) {
		return 0
	}
	return Clamp01((x - lo) / (hi - lo))
}

// Fall is 1 at lo, falling linearly to 0 at hi.
func Fall(x, lo, hi float64) float64 {
	if !finite(x) || !(hi > lo) {
		return 0
	}
	return 1 - Clamp01((x-lo)/(hi-lo))
}

// Peak is a trapezoid: 0 up to a, rising to 1 at b, 1 until c, falling to 0 at d. It is how "more is better, until it is not"
// is expressed (an extended candle, a level tested too often, a late retest). Requires a < b <= c < d.
func Peak(x, a, b, c, d float64) float64 {
	if !finite(x) || !(a < b && b <= c && c < d) {
		return 0
	}
	switch {
	case x <= a || x >= d:
		return 0
	case x < b:
		return (x - a) / (b - a)
	case x <= c:
		return 1
	default:
		return (d - x) / (d - c)
	}
}

// Room scores the room to a legitimate structural objective as a technical reward:risk, 1R scoring 0 and 3R scoring 1.
// A target that is a fixed reward:risk beyond the entry (no structure there) is not room: structural is false and it scores 0.
func Room(rewardDistance, riskDistance float64, structural bool) float64 {
	if !structural || !finite(rewardDistance) || !finite(riskDistance) || !(riskDistance > 0) || rewardDistance <= 0 {
		return 0
	}
	return Ramp(rewardDistance/riskDistance, 1, 3)
}

// CloseStrength is where the candle closed inside its own range in the trade's favour: a buy that closes on its high is 1, on
// its low 0. A zero-range candle is missing evidence.
func CloseStrength(c market.Candle, buy bool) float64 {
	r := c.Range()
	if !(r > 0) {
		return 0
	}
	if buy {
		return Clamp01((c.Close - c.Low) / r)
	}
	return Clamp01((c.High - c.Close) / r)
}

// RejectionWick is the share of the candle's range that is the wick against the trade (the lower wick of a buy): the part of the
// bar that price visited and refused.
func RejectionWick(c market.Candle, buy bool) float64 {
	r := c.Range()
	if !(r > 0) {
		return 0
	}
	if buy {
		return Clamp01((math.Min(c.Open, c.Close) - c.Low) / r)
	}
	return Clamp01((c.High - math.Max(c.Open, c.Close)) / r)
}

// Episodes counts the distinct approaches of price to a level band: a run of consecutive bars whose range overlaps the band is one
// episode, and a bar that opens on one side and closes beyond the far edge is a break, not a touch. Several bars grinding inside the
// band are one episode, so the same visit is never counted twice.
func Episodes(bars []market.Candle, price, band float64) int {
	if len(bars) == 0 || !finite(price) || !finite(band) || band < 0 {
		return 0
	}
	lo, hi := price-band, price+band
	episodes, inEpisode := 0, false
	for _, b := range bars {
		touched := b.Low <= hi && b.High >= lo
		if touched && (b.Open > hi && b.Close < lo || b.Open < lo && b.Close > hi) {
			touched = false
		}
		if touched && !inEpisode {
			episodes++
		}
		inEpisode = touched
	}
	return episodes
}

// FirstVisit is the index of the first bar that visits the level band (an overlapping range that did not open on one side and close
// beyond the other), or -1. It is where a level's life begins for the purposes of its integrity: what price did before it ever
// reached the level says nothing about whether the level held.
func FirstVisit(bars []market.Candle, price, band float64) int {
	if !finite(price) || !finite(band) || band < 0 {
		return -1
	}
	lo, hi := price-band, price+band
	for i, b := range bars {
		if b.Low <= hi && b.High >= lo && !(b.Open > hi && b.Close < lo || b.Open < lo && b.Close > hi) {
			return i
		}
	}
	return -1
}

// ClosesThrough counts the bars that closed beyond the defended side of a level band by more than the band itself: below a support
// (buy), above a resistance (sell). It is how many times the level failed to hold a close.
func ClosesThrough(bars []market.Candle, price, band float64, buy bool) int {
	n := 0
	for _, b := range bars {
		if buy && b.Close < price-2*band || !buy && b.Close > price+2*band {
			n++
		}
	}
	return n
}
