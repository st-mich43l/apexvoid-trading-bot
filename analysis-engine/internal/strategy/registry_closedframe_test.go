package strategy

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// perFrame is a strategy that decides on M5 and on M15, each frame alone.
type perFrame struct {
	id      StrategyID
	closed  []market.Timeframe
	evalAll int
}

func (p *perFrame) ID() StrategyID { return p.id }
func (p *perFrame) RequiredTimeframes() []market.Timeframe {
	return []market.Timeframe{market.M5, market.M15}
}
func (p *perFrame) Evaluate(*analysiscontext.MarketContext) []opportunity.Candidate {
	p.evalAll++
	return nil
}
func (p *perFrame) EvaluateClosed(_ *analysiscontext.MarketContext, closed market.Timeframe) []opportunity.Candidate {
	p.closed = append(p.closed, closed)
	return nil
}

// allFrames is an ordinary strategy that needs both of its frames present.
type allFrames struct {
	id    StrategyID
	evals int
}

func (a *allFrames) ID() StrategyID { return a.id }
func (a *allFrames) RequiredTimeframes() []market.Timeframe {
	return []market.Timeframe{market.M5, market.M15}
}
func (a *allFrames) Evaluate(*analysiscontext.MarketContext) []opportunity.Candidate {
	a.evals++
	return nil
}

func registryOf(t *testing.T, per *perFrame, plain *allFrames) *Registry {
	t.Helper()
	var configs []Config
	for _, id := range knownIDs {
		configs = append(configs, Config{ID: id, Version: "v1", Enabled: id == per.id || id == plain.id})
	}
	registry, err := NewRegistry(configs, map[StrategyID]Factory{
		per.id:   func(Config) (Strategy, error) { return per, nil },
		plain.id: func(Config) (Strategy, error) { return plain, nil },
	})
	if err != nil {
		t.Fatal(err)
	}
	return registry
}

func contextWith(frames ...market.Timeframe) *analysiscontext.MarketContext {
	ctx := &analysiscontext.MarketContext{Symbol: "EURUSD", Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{}}
	for _, tf := range frames {
		ctx.Timeframes[tf] = &analysiscontext.TimeframeContext{Timeframe: tf}
	}
	return ctx
}

// A per-frame strategy is evaluated for the frame that closed, only when that frame is a setup frame of its own, and a frame that is not warm yet never holds the
// other back; an ordinary strategy keeps the rule that every frame it declared must be present.
func TestClosedFrameStrategiesAreEvaluatedPerFrame(t *testing.T) {
	per, plain := &perFrame{id: "trendline"}, &allFrames{id: "key_level"}
	registry := registryOf(t, per, plain)

	// M5 closes while M15 has no candles yet: the per-frame strategy runs on M5, the ordinary one is deferred.
	result, err := registry.Evaluate(contextWith(market.M5), market.M5)
	if err != nil {
		t.Fatal(err)
	}
	if len(per.closed) != 1 || per.closed[0] != market.M5 || per.evalAll != 0 {
		t.Errorf("per-frame calls = %v (Evaluate %d), want one EvaluateClosed(M5)", per.closed, per.evalAll)
	}
	if len(result.Deferred) != 1 || result.Deferred[0] != "key_level" || plain.evals != 0 {
		t.Errorf("deferred = %v, key_level evaluated %d times; want key_level deferred while M15 is missing", result.Deferred, plain.evals)
	}

	// M15 closes with both frames present: each strategy runs once, the per-frame one for M15 only.
	if _, err := registry.Evaluate(contextWith(market.M5, market.M15), market.M15); err != nil {
		t.Fatal(err)
	}
	if len(per.closed) != 2 || per.closed[1] != market.M15 || plain.evals != 1 {
		t.Errorf("per-frame calls = %v, key_level evals %d; want EvaluateClosed(M5), EvaluateClosed(M15) and one key_level evaluation", per.closed, plain.evals)
	}

	// M15 closes but its frame is absent from the context: the per-frame strategy is deferred for it, never run on the wrong frame.
	if result, err = registry.Evaluate(contextWith(market.M5), market.M15); err != nil {
		t.Fatal(err)
	}
	if len(per.closed) != 2 {
		t.Errorf("per-frame strategy ran for a frame the context does not hold: %v", per.closed)
	}

	// A timeframe it does not decide on (H1) triggers neither.
	if _, err = registry.Evaluate(contextWith(market.M5, market.M15, market.H1), market.H1); err != nil {
		t.Fatal(err)
	}
	if len(per.closed) != 2 || plain.evals != 1 {
		t.Errorf("H1 closing evaluated a strategy that does not declare it: %v / %d", per.closed, plain.evals)
	}
}
