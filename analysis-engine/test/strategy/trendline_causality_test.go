package strategy_test

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	strategytrendline "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/trendline"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

// A flat support line at 99.6, confirmed validations, a last candle that dips into its band and closes back above it
// with a hammer: a textbook reaction. confirmedAt is the candle whose close confirmed the second anchor.
func trendlineScenario(confirmedAt int) *analysiscontext.MarketContext {
	bars := legacyfixture.Bars(10, 100)
	bars[9] = market.Candle{Time: 3000, Open: 99.9, High: 100.3, Low: 99.5, Close: 100.2}
	line := trendline.Trendline{
		Kind: trendline.KindSupport, AnchorA: "a", AnchorB: "b", AnchorAIndex: 1, AnchorBIndex: confirmedAt - 2, AnchorBConfirmedIndex: confirmedAt,
		Slope: 0, Intercept: 99.6, State: trendline.StateConfirmed,
		ValidationTouches: []trendline.ValidationTouch{{BarIndex: 6}},
	}
	return legacyfixture.Context(bars, 1, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Trendlines = []trendline.Trendline{line}
		f.Pools = []techniquezone.Pool{{Side: "buy", Level: 103, Band: .1, Touches: 1}}
	})
}

func newTrendline(t *testing.T) strategy.Strategy {
	t.Helper()
	s, err := strategytrendline.New(cfg(strategytrendline.ID, trendlineParams()))
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// The bars that formed and confirmed a line's second anchor are the line's formation. A touch or a rejection on them is
// not a reaction to a line that did not exist yet.
func TestATrendlineIsNotConfirmedByTheBarsThatConfirmedItsAnchor(t *testing.T) {
	s := newTrendline(t)
	if got := s.Evaluate(trendlineScenario(6)); len(got) != 1 {
		t.Fatalf("a rejection three bars after the line was knowable must publish: %+v", got)
	}
	if got := s.Evaluate(trendlineScenario(8)); len(got) != 1 {
		t.Fatalf("a rejection on the bar after the line became knowable still publishes: %+v", got)
	}
	if got := s.Evaluate(trendlineScenario(9)); len(got) != 0 {
		t.Fatalf("the bar that confirmed the anchor cannot also be the reaction to the line: %+v", got)
	}
}
