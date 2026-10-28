package keylevel

import (
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

func hasEvidence(c opportunity.Candidate, code string) bool {
	for _, e := range c.Evidence {
		if e.Code == code {
			return true
		}
	}
	return false
}

// The level 99 has the reaction band 98.5-99.5 (the level band 0.3 is thinner than the 0.5·ATR proximal floor).
// buyRejection's low (98.0) is beyond the band's far edge: it swept the level. A wick whose low stays inside the band did not.
var (
	sweptBuy   = buyRejection
	unsweptBuy = market.Candle{Open: 99.6, High: 100.3, Low: 98.7, Close: 100.2}
	touchedBuy = market.Candle{Open: 99.6, High: 100.3, Low: 98.5, Close: 100.2} // exactly the far edge: not strictly beyond
	sweptSell  = sellRejection                                                   // high 102.0 beyond the band 100.5-101.5 of level 101
	unswSell   = market.Candle{Open: 100.3, High: 101.2, Low: 99.8, Close: 99.9}
)

func reactionLevel() []techniquezone.Level { return []techniquezone.Level{level(99, "reaction", 3)} }

func TestWithTheRuleAnUnsweptConfirmationPublishesNothingAndASweptOneDoes(t *testing.T) {
	rule := map[string]any{"require_reaction_sweep": true}

	if got := newStrategy(t, rule).Evaluate(contextWith(unsweptBuy, reactionLevel(), nil)); len(got) != 0 {
		t.Fatalf("a reaction whose low stayed inside the band did not sweep the level: %+v", got)
	}
	got := newStrategy(t, rule).Evaluate(contextWith(sweptBuy, reactionLevel(), nil))
	if len(got) != 1 {
		t.Fatalf("a swept reaction must publish: %+v", got)
	}
	if !hasEvidence(got[0], ReactionSweptEvidence) || !hasEvidence(got[0], strategyutil.ReactionExtremeEvidence) {
		t.Fatalf("the candidate must say it swept the level and that its invalidation is the reaction's extreme: %+v", got[0].Evidence)
	}
	if got[0].StrategyVersion != "v6" {
		t.Fatalf("version %q, want v6", got[0].StrategyVersion)
	}
}

func TestTheSweepMustBeStrictlyBeyondTheFarEdge(t *testing.T) {
	rule := map[string]any{"require_reaction_sweep": true}
	if got := newStrategy(t, rule).Evaluate(contextWith(touchedBuy, reactionLevel(), nil)); len(got) != 0 {
		t.Fatalf("a low exactly on the band's far edge is not beyond it: %+v", got)
	}
}

func TestTheRuleIsTheSameForASell(t *testing.T) {
	rule := map[string]any{"require_reaction_sweep": true}
	sellLevel := []techniquezone.Level{level(101, "reaction", 3)}
	if got := newStrategy(t, rule).Evaluate(contextWith(unswSell, sellLevel, nil)); len(got) != 0 {
		t.Fatalf("a SELL whose high stayed inside the band did not sweep it: %+v", got)
	}
	got := newStrategy(t, rule).Evaluate(contextWith(sweptSell, sellLevel, nil))
	if len(got) != 1 || got[0].Direction != market.Sell || !hasEvidence(got[0], ReactionSweptEvidence) {
		t.Fatalf("a swept SELL must publish with the evidence: %+v", got)
	}
}

func TestWithoutTheRuleKeyLevelIsAsBeforeApartFromTheVersion(t *testing.T) {
	// Flag absent and flag false both publish the unswept reaction, with no sweep evidence.
	for _, own := range []map[string]any{nil, {"require_reaction_sweep": false}} {
		got := newStrategy(t, own).Evaluate(contextWith(unsweptBuy, reactionLevel(), nil))
		if len(got) != 1 || hasEvidence(got[0], ReactionSweptEvidence) {
			t.Fatalf("without the rule an unswept reaction publishes as it always did: %+v", got)
		}
	}
	// A swept reaction is the same candidate either way: the rule adds only its evidence code.
	off := newStrategy(t, nil).Evaluate(contextWith(sweptBuy, reactionLevel(), nil))
	on := newStrategy(t, map[string]any{"require_reaction_sweep": true}).Evaluate(contextWith(sweptBuy, reactionLevel(), nil))
	if len(off) != 1 || len(on) != 1 {
		t.Fatalf("both must publish: %d %d", len(off), len(on))
	}
	if off[0].Entry != on[0].Entry || off[0].Invalidation != on[0].Invalidation || off[0].Quality.Overall != on[0].Quality.Overall {
		t.Fatalf("the geometry and quality of a swept reaction do not depend on the rule: %+v vs %+v", off[0], on[0])
	}
	if len(on[0].Evidence) != len(off[0].Evidence)+1 || !hasEvidence(on[0], ReactionSweptEvidence) || hasEvidence(off[0], ReactionSweptEvidence) {
		t.Fatalf("only the evidence differs: %+v vs %+v", off[0].Evidence, on[0].Evidence)
	}
}

func TestTheFlagMustBeABoolean(t *testing.T) {
	_, err := parseConfig(map[string]any{
		"minimum_touches": 2.0, "invalidation_buffer_atr": .5, "minimum_target_distance_atr": 1.0, "fallback_target_r": 2.0, "expiry_hours": 24.0,
		"breakout_accept_bars": 2.0, "price_digits": 2.0, "detector_contract": "profit_week", "require_reaction_sweep": "yes",
	})
	if err == nil || !strings.Contains(err.Error(), "require_reaction_sweep") {
		t.Fatalf("a non-boolean flag must be an error, got %v", err)
	}
}

// Two levels confirm on the same bar. 98.4 (five touches, three stars) is NOT swept: its band is 97.9-98.9 and the low is 98.0. 99 (two touches, two stars) IS
// swept. The previous close is set so that both confirm.
func twoLevels() []techniquezone.Level {
	return []techniquezone.Level{level(99, "reaction", 2), level(98.4, "reaction", 5)}
}

func TestAnUnsweptBestStarsAttemptDoesNotSuppressASweptLowerStarsAttempt(t *testing.T) {
	off := newStrategy(t, nil).Evaluate(contextWithPrev(98.6, sweptBuy, twoLevels(), nil))
	if len(off) != 1 || off[0].StructuralID != "keylevel:reaction:98.40" {
		t.Fatalf("without the rule the higher-stars level wins: %+v", off)
	}
	s := newStrategy(t, map[string]any{"require_reaction_sweep": true})
	on := s.Evaluate(contextWithPrev(98.6, sweptBuy, twoLevels(), nil))
	if len(on) != 1 || on[0].StructuralID != "keylevel:reaction:99.00" || !hasEvidence(on[0], ReactionSweptEvidence) {
		t.Fatalf("with the rule the swept level publishes although another level had more stars (v5 chose first and refused after, publishing nothing): %+v", on)
	}
	if n := s.(*Strategy).TakeUnsweptRefusals(); n != 1 {
		t.Fatalf("one unswept refusal, got %d", n)
	}
}

func TestAStopBudgetRefusalOfTheBestAttemptDoesNotSuppressAnInBudgetAttempt(t *testing.T) {
	// 99 (five touches, three stars, swept) has a risk of 2.0 = 20 pips at a 0.1 pip; 98.4 (two touches, two stars, unswept) has 1.5 = 15 pips. Cap 18 pips.
	levels := []techniquezone.Level{level(99, "reaction", 5), level(98.4, "reaction", 2)}
	budget := map[string]any{"execution_stop_max_pips": 18.0, "execution_pip_size": 0.1}
	uncapped := newStrategy(t, nil).Evaluate(contextWithPrev(98.6, sweptBuy, levels, nil))
	if len(uncapped) != 1 || uncapped[0].StructuralID != "keylevel:reaction:99.00" {
		t.Fatalf("without a budget the higher-stars level wins: %+v", uncapped)
	}
	s := newStrategy(t, budget)
	got := s.Evaluate(contextWithPrev(98.6, sweptBuy, levels, nil))
	if len(got) != 1 || got[0].StructuralID != "keylevel:reaction:98.40" {
		t.Fatalf("the best attempt is over the cap: the in-budget one must publish instead of nothing: %+v", got)
	}
	if n := s.(*Strategy).TakeStopBudgetRefusals(); n != 1 {
		t.Fatalf("one stop-budget refusal, got %d", n)
	}
	if n := s.(*Strategy).TakeUnsweptRefusals(); n != 0 {
		t.Fatalf("a stop-budget refusal is not an unswept refusal: %d", n)
	}
}

func TestTheTwoRefusalCountersAreSeparate(t *testing.T) {
	s := newStrategy(t, map[string]any{"require_reaction_sweep": true, "execution_stop_max_pips": 18.0, "execution_pip_size": 0.1})
	// 99 is swept but over the cap (20 pips): a stop-budget refusal. 98.4 is unswept (15 pips): an unswept refusal.
	levels := []techniquezone.Level{level(99, "reaction", 5), level(98.4, "reaction", 2)}
	if got := s.Evaluate(contextWithPrev(98.6, sweptBuy, levels, nil)); len(got) != 0 {
		t.Fatalf("both are refused: %+v", got)
	}
	if budget, unswept := s.(*Strategy).TakeStopBudgetRefusals(), s.(*Strategy).TakeUnsweptRefusals(); budget != 1 || unswept != 1 {
		t.Fatalf("budget %d unswept %d, want 1 and 1", budget, unswept)
	}
	if s.(*Strategy).TakeUnsweptRefusals() != 0 {
		t.Fatal("the count resets once read")
	}
}
