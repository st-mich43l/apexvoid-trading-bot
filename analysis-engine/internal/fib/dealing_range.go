package fib

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// Config aggregates every fib-domain tunable — same "one coherent
// struct" convention structure.Settings/zone.Config already establish.
// This domain has no config.analysis.yml precedent to speak of before
// Phase S4 — all four leaves are newly surfaced at their real Python
// function-default values (see config/analysis.yml's own comments).
type Config struct {
	// EpsilonATR is nearest_fib's epsilon_atr (default 0.15): how close
	// (in ATR) price must be to a level for NearestLevel to report it.
	EpsilonATR float64

	// DeepDiscount/DeepPremium are fib_zone_label's own thresholds
	// (0.382/0.618 — the same ratios as the 38.2%/61.8% retracement
	// levels themselves, not a coincidence in the source).
	DeepDiscount float64
	DeepPremium  float64

	// EqHalfBand is already the HALVED band dealing_range() passes to
	// fib_zone_label as eq_half_band (Python's own eq_band=0.10 default,
	// halved to 0.05) — stored pre-halved here so Resolve never needs to
	// know dealing_range()'s own un-halved eq_band parameter existed.
	EqHalfBand float64
}

// DealingRange is the premium/discount read for one bracketing swing
// pair — dealing_range.py's DealingRange dataclass.
type DealingRange struct {
	High, Low, Equilibrium market.Price
	Position               float64
	Zone                   string // "discount" | "eq" | "premium"
	FibZone                string // "deep_discount" | "discount" | "eq" | "premium" | "deep_premium"
}

// State is one timeframe's complete fib read — Ladder and Range are
// always built from the SAME bracketing swing pair (see doc.go). Range
// is nil when no bracketing/opposing swing pair exists yet (too little
// structure), matching Python's own `dealing_range() -> DealingRange |
// None`.
type State struct {
	Ladder []Level
	Range  *DealingRange
}

// Book is the fib-domain slice of a symbol's canonical state — same
// shape as zone.Book/session.Book.
type Book struct {
	ByTimeframe map[market.Timeframe]State
}

// NewBook returns an empty Book ready for Set/Get.
func NewBook() *Book {
	return &Book{ByTimeframe: make(map[market.Timeframe]State)}
}

// Set stores tf's latest State, replacing whatever was there.
func (b *Book) Set(tf market.Timeframe, state State) {
	b.ByTimeframe[tf] = state
}

// Get returns tf's latest State, or the zero value and false if none has
// been computed yet.
func (b *Book) Get(tf market.Timeframe) (State, bool) {
	state, ok := b.ByTimeframe[tf]
	return state, ok
}

// Update is this domain's single entrypoint: the current price's
// bracketing (or, failing that, most recent opposing) swing pair
// resolves both the fib ladder and the dealing range in one pass. Pure
// and causal, same contract as structure.Update/zone.Update/
// session.Update: only ever reads candles/swings given to it. Price is
// the latest candle's own Close — the same "current price" every
// production caller (fib_from_swings/dealing_range) is ultimately
// evaluated against for a live read.
func Update(candles []market.Candle, swings []structure.Swing, cfg Config) State {
	if len(candles) == 0 {
		return State{}
	}
	price := candles[len(candles)-1].Close
	low, high, ok := swingRangePair(swings, price)
	if !ok {
		return State{}
	}
	state := State{Ladder: Ladder(market.Price(low), market.Price(high), true)}
	if dr, ok := resolveFromPair(low, high, price, cfg); ok {
		state.Range = &dr
	}
	return state
}

// Resolve ports dealing_range(): the premium/discount read for price
// against swings' own bracketing (or most recent opposing) pair. Exposed
// separately from Update for a caller (a future strategy) that wants a
// dealing-range read against an arbitrary price, not just the latest
// close.
func Resolve(swings []structure.Swing, price market.Price, cfg Config) (DealingRange, bool) {
	low, high, ok := swingRangePair(swings, float64(price))
	if !ok {
		return DealingRange{}, false
	}
	return resolveFromPair(low, high, float64(price), cfg)
}

func resolveFromPair(low, high, price float64, cfg Config) (DealingRange, bool) {
	if high <= low {
		return DealingRange{}, false
	}
	position := clamp01((price - low) / (high - low))
	eq := (high + low) / 2

	halfBand := cfg.EqHalfBand
	if halfBand < 0 {
		halfBand = 0
	}
	var zone string
	switch {
	case absF(position-0.5) <= halfBand:
		zone = "eq"
	case position < 0.5:
		zone = "discount"
	default:
		zone = "premium"
	}

	return DealingRange{
		High: market.Price(high), Low: market.Price(low), Equilibrium: market.Price(eq),
		Position: position, Zone: zone, FibZone: fibZoneLabel(position, cfg),
	}, true
}

// fibZoneLabel ports fib_zone_label: the finer 5-way premium/discount
// band. cfg.EqHalfBand is already the halved band (see Config's own
// comment), used directly here exactly as dealing_range() passes its own
// pre-halved value through.
func fibZoneLabel(position float64, cfg Config) string {
	pos := clamp01(position)
	halfBand := cfg.EqHalfBand
	if halfBand < 0 {
		halfBand = 0
	}
	switch {
	case absF(pos-0.5) <= halfBand:
		return "eq"
	case pos <= cfg.DeepDiscount:
		return "deep_discount"
	case pos < 0.5:
		return "discount"
	case pos >= cfg.DeepPremium:
		return "deep_premium"
	default:
		return "premium"
	}
}

// swingRangePair ports swing_range_pair: a bracketing pair (price falls
// between the two) if one exists, else the most recent opposing-kind
// pair regardless of whether price falls inside it.
func swingRangePair(swings []structure.Swing, price float64) (low, high float64, ok bool) {
	if l, h, found := bracketingPair(swings, price); found {
		return l, h, true
	}
	return lastOpposingPair(swings)
}

// bracketingPair ports _bracketing_pair: scanning backward from the most
// recent swing, the first opposite-kind pair (in either scan order) whose
// [low,high] contains price.
func bracketingPair(swings []structure.Swing, price float64) (low, high float64, found bool) {
	for i := len(swings) - 1; i >= 0; i-- {
		first := swings[i]
		for j := i - 1; j >= 0; j-- {
			second := swings[j]
			if first.Kind == second.Kind {
				continue
			}
			l := minF(float64(first.Price), float64(second.Price))
			h := maxF(float64(first.Price), float64(second.Price))
			if l <= price && price <= h {
				return l, h, true
			}
		}
	}
	return 0, 0, false
}

// lastOpposingPair ports _last_opposing_pair: the most recent swing
// paired with the closest earlier swing of the OPPOSITE kind, regardless
// of whether price falls inside that pair.
func lastOpposingPair(swings []structure.Swing) (low, high float64, found bool) {
	if len(swings) < 2 {
		return 0, 0, false
	}
	last := swings[len(swings)-1]
	for i := len(swings) - 2; i >= 0; i-- {
		item := swings[i]
		if item.Kind == last.Kind {
			continue
		}
		return minF(float64(last.Price), float64(item.Price)), maxF(float64(last.Price), float64(item.Price)), true
	}
	return 0, 0, false
}

func clamp01(v float64) float64 {
	if v < 0 {
		return 0
	}
	if v > 1 {
		return 1
	}
	return v
}

func absF(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}

func minF(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}

func maxF(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}
