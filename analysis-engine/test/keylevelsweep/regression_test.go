// Package keylevelsweep_test holds the evidence for the XAU Key Level sweep rule (docs/strategies/keylevel-sweep-first.md): a regression over five real
// setups from the committed XAU capture, and a measurement over every committed M5 capture (KEYLEVEL_SWEEP_OUT).
package keylevelsweep_test

import (
	"path/filepath"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

func hasCode(c opportunity.Candidate, code string) bool {
	for _, e := range c.Evidence {
		if e.Code == code {
			return true
		}
	}
	return false
}

type caseKey struct {
	direction market.Direction
	bar       string // the evaluated M5 bar, "2006-01-02T15:04" UTC
}

type published struct {
	entryLow, entryHigh, invalidation float64
	candidate                         opportunity.Candidate
}

// replayKeyLevel replays one capture as the engine is configured and returns the XAU Key Level candidates by (direction, evaluated bar), the first one
// when a bar publishes the same setup twice. withoutRule is the v5 behaviour (the instrument's require_reaction_sweep removed).
func replayKeyLevel(t *testing.T, name string, withoutRule bool) map[caseKey]published {
	t.Helper()
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	out := map[caseKey]published{}
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{WithoutKeyLevelSweep: withoutRule, OnEvaluation: func(e engine.Evaluation) {
		for _, c := range e.Candidates {
			if string(c.Strategy) != "key_level" || c.Reaction == nil {
				continue
			}
			k := caseKey{c.Direction, time.Unix(e.BarTime, 0).UTC().Format("2006-01-02T15:04")}
			if _, seen := out[k]; !seen {
				out[k] = published{float64(c.Entry.Low), float64(c.Entry.High), float64(c.Invalidation.Price), c}
			}
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

// Five real XAU setups of 14-15 Oct 2026 (the first M5 bar after the confirmation, UTC). The first three did not sweep their level and stopped out in the
// replay; the last two swept it and reached at least 2R. v6 refuses the first three and publishes the last two exactly as v5 did.
func TestTheRuleRefusesTheThreeUnsweptSetupsAndKeepsTheTwoSweptOnes(t *testing.T) {
	const capture = "replay-xau-production-capture-20261022.json"
	refused := []caseKey{{market.Sell, "2026-10-14T20:40"}, {market.Sell, "2026-10-14T23:25"}, {market.Buy, "2026-10-15T05:25"}}
	kept := []caseKey{{market.Buy, "2026-10-15T17:35"}, {market.Buy, "2026-10-15T18:00"}}

	v5 := replayKeyLevel(t, capture, true)
	v6 := replayKeyLevel(t, capture, false)

	for _, k := range append(append([]caseKey{}, refused...), kept...) {
		if _, ok := v5[k]; !ok {
			t.Fatalf("v5 (rule off) must publish %v %s", k.direction, k.bar)
		}
	}
	for _, k := range refused {
		if hasCode(v5[k].candidate, strategyutil.ReactionExtremeEvidence) {
			t.Errorf("%v %s was meant to be an unswept setup, but v5 anchored its invalidation to the reaction extreme", k.direction, k.bar)
		}
		if _, ok := v6[k]; ok {
			t.Errorf("v6 must refuse the unswept %v %s", k.direction, k.bar)
		}
	}
	for _, k := range kept {
		before, after := v5[k], v6[k]
		if _, ok := v6[k]; !ok {
			t.Fatalf("v6 must publish the swept %v %s", k.direction, k.bar)
		}
		if after.entryLow != before.entryLow || after.entryHigh != before.entryHigh || after.invalidation != before.invalidation {
			t.Errorf("%v %s: v6 moved the geometry: entry %.2f-%.2f inv %.2f, v5 %.2f-%.2f inv %.2f", k.direction, k.bar, after.entryLow, after.entryHigh, after.invalidation, before.entryLow, before.entryHigh, before.invalidation)
		}
		if !hasCode(after.candidate, keylevel.ReactionSweptEvidence) || !hasCode(after.candidate, strategyutil.ReactionExtremeEvidence) {
			t.Errorf("%v %s: v6 must carry both the swept and the reaction-extreme evidence: %v", k.direction, k.bar, after.candidate.Evidence)
		}
		if hasCode(before.candidate, keylevel.ReactionSweptEvidence) {
			t.Errorf("%v %s: v5 must not carry the sweep evidence", k.direction, k.bar)
		}
		if after.candidate.StrategyVersion != "v6" {
			t.Errorf("version %q, want v6", after.candidate.StrategyVersion)
		}
	}
}
