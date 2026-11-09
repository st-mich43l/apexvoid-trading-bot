package strategyutil

import (
	"fmt"
	"math"
	"sync/atomic"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Evidence codes a legacy-family candidate carries when the instrument stop budget is applied.
const (
	// StopBudgetStructuralEvidence: the structural stop lies inside the instrument's [floor, cap].
	StopBudgetStructuralEvidence = "stop_budget_structural"
	// StopBudgetFloorEvidence: the structural stop is tighter than the instrument floor; execution widens it to the floor.
	StopBudgetFloorEvidence = "stop_budget_floor"
	// StopBudgetDeepEntryEvidence: the structural stop is wider than the cap from the proximal edge of the entry band but within it deeper in the band;
	// the entry band was cut to the part from which the stop (never moved) risks at most the cap.
	StopBudgetDeepEntryEvidence = "stop_budget_deep_entry"
)

// StopBudget is the instrument's stop envelope as the detector sees it: the same "refused, never tightened" rule CRT, Break & Retest, iFVG and Confluence
// Zone apply. The engine injects it per instrument (internal/engine applyLegacyStopBudget); absent keys mean no budget and the strategy behaves as before.
type StopBudget struct {
	// MaxPips is the instrument's stop cap; a structural stop wider than this is refused (0 = no cap).
	MaxPips float64
	// MinPips is the instrument's stop floor; a tighter stop is published as it is and widened by execution (0 = no floor).
	MinPips float64
	// PipSize converts price distance to pips. The budget is inactive without it.
	PipSize float64
	// DeepEntryMinimumWidthPips is the narrowest entry band Fit may return when it cuts a band to the part within the cap (0 = deep entry off: a stop wider
	// than the cap from the proximal edge is refused, as Apply does). Read from deep_entry_minimum_width_pips.
	DeepEntryMinimumWidthPips float64
	// Refusals, when set, counts every refused candidate (read by the engine's telemetry, never by the strategy).
	Refusals *RefusalCounter
}

// RefusalCounter counts the candidates a strategy refused for exceeding the stop cap. A strategy embeds it; the engine drains it after each
// evaluation through TakeStopBudgetRefusals (strategy.StopBudgetReporter) and records the count in telemetry.
type RefusalCounter struct{ n atomic.Int64 }

// TakeStopBudgetRefusals returns the refusals since the last call and resets the count.
func (c *RefusalCounter) TakeStopBudgetRefusals() int64 { return c.n.Swap(0) }

// SweepRefusalCounter counts the candidates a strategy refused because the confirming reaction did not sweep the level (Key Level with
// require_reaction_sweep). It is kept apart from RefusalCounter: a stop-budget refusal and an unswept refusal are different decisions and must
// not read as one. The engine drains it after each evaluation through TakeUnsweptRefusals (strategy.UnsweptReporter).
type SweepRefusalCounter struct{ n atomic.Int64 }

// CountUnswept records one candidate refused for want of a sweep.
func (c *SweepRefusalCounter) CountUnswept() { c.n.Add(1) }

// TakeUnsweptRefusals returns the refusals since the last call and resets the count.
func (c *SweepRefusalCounter) TakeUnsweptRefusals() int64 { return c.n.Swap(0) }

// ParseStopBudget reads the optional per-instrument keys execution_stop_max_pips, execution_stop_min_pips and execution_pip_size. The pip key is separate
// from pip_size on purpose: pip_size feeds the technique geometry, and a budget must not change which zones a strategy decides on.
func ParseStopBudget(params map[string]any) (StopBudget, error) {
	var b StopBudget
	for key, dst := range map[string]*float64{
		"execution_stop_max_pips": &b.MaxPips,
		"execution_stop_min_pips": &b.MinPips,
		"execution_pip_size":      &b.PipSize,
	} {
		if _, present := params[key]; !present {
			continue
		}
		v, err := Float(params, key)
		if err != nil {
			return StopBudget{}, err
		}
		if !(v > 0) || math.IsInf(v, 0) {
			return StopBudget{}, fmt.Errorf("%s must be > 0 when set (omit it to disable the stop budget)", key)
		}
		*dst = v
	}
	if _, present := params["deep_entry_minimum_width_pips"]; present {
		v, err := Float(params, "deep_entry_minimum_width_pips")
		if err != nil {
			return StopBudget{}, err
		}
		if !(v > 0) || math.IsInf(v, 0) {
			return StopBudget{}, fmt.Errorf("deep_entry_minimum_width_pips must be > 0 when set (omit it to turn deep entry off)")
		}
		b.DeepEntryMinimumWidthPips = v
	}
	if b.MaxPips > 0 && b.MinPips > b.MaxPips {
		return StopBudget{}, fmt.Errorf("execution_stop_min_pips %v exceeds execution_stop_max_pips %v", b.MinPips, b.MaxPips)
	}
	if (b.MaxPips > 0 || b.MinPips > 0) && !(b.PipSize > 0) {
		return StopBudget{}, fmt.Errorf("a stop budget needs execution_pip_size")
	}
	return b, nil
}

// Active reports whether any bound is configured and the pip size is known.
func (b StopBudget) Active() bool {
	return b.PipSize > 0 && (b.MaxPips > 0 || b.MinPips > 0)
}

// EffectiveRisk is the distance from the proximal entry edge (reference) to the stop the trade is actually exposed to: the structural distance to the
// invalidation, or the instrument floor when that is tighter (the invalidation is published as it is and execution widens the stop to the floor, which is
// what an FX Key Level trade does when its structural stop is a few pips). Without a floor it is the structural distance. It never moves either price.
func (b StopBudget) EffectiveRisk(reference, invalidation float64) float64 {
	risk := math.Abs(reference - invalidation)
	if b.Active() && b.MinPips > 0 {
		if floor := b.MinPips * b.PipSize; risk < floor {
			return floor
		}
	}
	return risk
}

// Apply classifies the structural stop measured from the proximal entry edge (reference) to the invalidation. It never moves either price.
//
//	no budget                    -> ("", true): today's behaviour, no evidence code
//	risk > MaxPips               -> ("", false): refused (counted)
//	risk < MinPips               -> (StopBudgetFloorEvidence, true)
//	otherwise                    -> (StopBudgetStructuralEvidence, true)
//
// A risk exactly equal to the cap is published (the same 1e-9 pip tolerance Break & Retest uses).
func (b StopBudget) Apply(reference, invalidation float64) (code string, publish bool) {
	if !b.Active() {
		return "", true
	}
	risk := math.Abs(reference-invalidation) / b.PipSize
	if b.MaxPips > 0 && risk > b.MaxPips+1e-9 {
		if b.Refusals != nil {
			b.Refusals.n.Add(1)
		}
		return "", false
	}
	if b.MinPips > 0 && risk < b.MinPips {
		return StopBudgetFloorEvidence, true
	}
	return StopBudgetStructuralEvidence, true
}

// Fit is Apply for a strategy that owns an entry band. The invalidation is never moved; when the stop from the band's proximal edge is wider than the cap,
// the band is cut to the part from which the stop is within the cap (the entry goes deeper into the structure instead of the stop moving closer).
//
//	BUY (proximal edge = high):  deep = invalidation + cap; the band becomes [low, min(high, deep)].
//	SELL (proximal edge = low):  deep = invalidation - cap; the band becomes [max(low, deep), high].
//
//	no budget, or a band that fits as it is   -> Apply's result, the band unchanged
//	fits only deeper in the band              -> the cut band, StopBudgetDeepEntryEvidence (the new proximal edge risks exactly the cap)
//	the far edge is beyond the cap, or the cut band is narrower than DeepEntryMinimumWidthPips (or deep entry is off) -> refused and counted
//
// The floor never applies to a cut band: its proximal edge risks the cap, which is at or above the floor.
func (b StopBudget) Fit(direction market.Direction, low, high, invalidation float64) (newLow, newHigh float64, code string, ok bool) {
	proximal := high
	if direction == market.Sell {
		proximal = low
	}
	whole, fits := b.Apply(proximal, invalidation)
	if fits {
		return low, high, whole, true
	}
	// Apply counted a refusal; Fit gives the band a second chance and takes the count back when it publishes.
	if b.Refusals != nil {
		b.Refusals.n.Add(-1)
	}
	refuse := func() (float64, float64, string, bool) {
		if b.Refusals != nil {
			b.Refusals.n.Add(1)
		}
		return 0, 0, "", false
	}
	if !(b.DeepEntryMinimumWidthPips > 0) || !(b.MaxPips > 0) || !(b.PipSize > 0) {
		return refuse()
	}
	cap := b.MaxPips * b.PipSize
	minWidth := b.DeepEntryMinimumWidthPips * b.PipSize
	// The cut edge is rounded to the instrument tick (a tenth of a pip: 0.01 on gold, 0.00001 on EURUSD) toward the inside of the cap, so an order placed on it and
	// rounded to the broker's digits never risks more than the cap.
	tick := b.PipSize / 10
	if direction == market.Sell {
		deep := math.Ceil((invalidation-cap)/tick-1e-6) * tick
		if high < deep {
			return refuse()
		}
		newLow, newHigh = math.Max(low, deep), high
	} else {
		deep := math.Floor((invalidation+cap)/tick+1e-6) * tick
		if low > deep {
			return refuse()
		}
		newLow, newHigh = low, math.Min(high, deep)
	}
	if newHigh-newLow < minWidth-1e-9 {
		return refuse()
	}
	return newLow, newHigh, StopBudgetDeepEntryEvidence, true
}
