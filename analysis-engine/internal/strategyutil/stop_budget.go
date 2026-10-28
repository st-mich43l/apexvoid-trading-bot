package strategyutil

import (
	"fmt"
	"math"
	"sync/atomic"
)

// Evidence codes a legacy-family candidate carries when the instrument stop budget is applied.
const (
	// StopBudgetStructuralEvidence: the structural stop lies inside the instrument's [floor, cap].
	StopBudgetStructuralEvidence = "stop_budget_structural"
	// StopBudgetFloorEvidence: the structural stop is tighter than the instrument floor; execution widens it to the floor.
	StopBudgetFloorEvidence = "stop_budget_floor"
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
