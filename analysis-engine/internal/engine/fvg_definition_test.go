package engine

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

func bar(i int, open, high, low, close float64) market.Candle {
	return market.Candle{Time: int64(i) * 900, Open: open, High: high, Low: low, Close: close}
}

// gapFrame is a higher frame with twelve quiet bars and one bullish fair value gap, 100.5-101.5 (1.0 wide), a strong displacement candle and the given ATR.
func gapFrame(atr float64) *analysiscontext.LegacyFrame {
	bars := make([]market.Candle, 0, 18)
	for i := 0; i < 12; i++ {
		bars = append(bars, bar(i, 100, 100.4, 99.6, 100))
	}
	bars = append(bars, bar(12, 100.2, 100.5, 99.9, 100.3), bar(13, 100.4, 102.8, 100.3, 102.6), bar(14, 102.0, 102.8, 101.5, 102.6))
	for i := 15; i < 18; i++ {
		bars = append(bars, bar(i, 102.4, 102.9, 101.9, 102.5))
	}
	series := make([]float64, len(bars))
	for i := range series {
		series[i] = atr
	}
	zone := techniquezone.Zone{Bottom: 100.5, Top: 101.5, Side: "demand", OriginIndex: 14, Source: "bullish_fvg", Sources: []string{"bullish_fvg"}, BreakIndex: -1}
	return &analysiscontext.LegacyFrame{Bars: bars, ATR: series, TechniqueZones: []techniquezone.Zone{zone}}
}

func fvgSettings(params map[string]any) Settings {
	s := Settings{TechniqueZones: productionTechniqueZoneSettings()}
	s.Strategies = []strategy.Config{{ID: "fvg", Enabled: true, Parameters: params}}
	return s
}

var definitionOn = map[string]any{"confirmed_minimum_gap_atr": 0.5, "confirmed_minimum_displacement_body_atr": 0.5, "confirmed_minimum_displacement_body_ratio": 0.5}

func TestAHigherFrameGapIsJudgedOnThatFramesOwnATRAndTheRefusalIsLedgered(t *testing.T) {
	ledger := &zoneRefusalLedger{}
	m15 := higherTimeframeInstanceSource(gapFrame(3.0), market.M15, fvgSettings(definitionOn), ledger)() // 1.0 is 0.33 of an ATR of 3.0
	h1 := higherTimeframeInstanceSource(gapFrame(1.5), market.H1, fvgSettings(definitionOn), ledger)()   // 1.0 is 0.67 of an ATR of 1.5
	if len(m15) != 0 {
		t.Fatalf("the M15 gap is 0.33 of its own ATR and must be refused: %+v", m15)
	}
	if len(h1) != 1 || h1[0].Timeframe != "H1" {
		t.Fatalf("the same gap on a frame whose ATR makes it 0.67 passes and is stamped with its frame: %+v", h1)
	}
	got := ledger.drain()
	if len(got) != 1 || got[zoneRefusalKey{"fvg", market.M15, techniquezone.FVGRefusedGapSmall}] != 1 {
		t.Fatalf("one gap_small refusal on M15, none for H1: %v", got)
	}
	if ledger.drain() != nil {
		t.Fatal("the ledger resets once it is read")
	}
}

func TestWithoutTheKeysTheDefinitionIsOffAndNothingIsLedgered(t *testing.T) {
	ledger := &zoneRefusalLedger{}
	got := higherTimeframeInstanceSource(gapFrame(3.0), market.M15, fvgSettings(map[string]any{"minimum_gap_atr": 0.5}), ledger)()
	if len(got) != 1 || ledger.drain() != nil {
		t.Fatalf("with no definition keys the gap is offered as before: %+v", got)
	}
}

func TestTheDefinitionIsInjectedForTheStrategySourcesOnlyAndNotIntoTheSharedSettings(t *testing.T) {
	settings := fvgSettings(definitionOn)
	got := zoneDefinitionTechnique(settings)
	if got.FVGMinGapATR != 0.5 || got.FVGMinDisplacementBodyATR != 0.5 || got.FVGMinDisplacementBodyRatio != 0.5 {
		t.Fatalf("the strategy sources must judge with the definition: %+v", got)
	}
	shared := settings.TechniqueZones.Technique
	if shared.FVGMinGapATR != 0 || shared.FVGMinDisplacementBodyATR != 0 || shared.FVGMinDisplacementBodyRatio != 0 {
		t.Fatalf("the shared settings (Confluence Zone facts, the zone book) must not change: %+v", shared)
	}
}

func TestWithoutFVGDefinitionRemovesOnlyThoseKeysAndNeverMutatesTheCallersMap(t *testing.T) {
	params := map[string]any{"minimum_gap_atr": 0.5, "expiry_hours": 24.0, "confirmed_minimum_gap_atr": 0.5, "confirmed_minimum_displacement_body_atr": 0.5, "confirmed_minimum_displacement_body_ratio": 0.5}
	s := fvgSettings(params)
	WithoutFVGDefinition(&s)
	got := s.Strategies[0].Parameters
	if len(got) != 2 || got["minimum_gap_atr"] != 0.5 || got["expiry_hours"] != 24.0 {
		t.Fatalf("only the definition keys go: %v", got)
	}
	if len(params) != 5 {
		t.Fatal("the original parameter map was mutated")
	}
}

func TestANilLedgerRecordsNothing(t *testing.T) {
	var ledger *zoneRefusalLedger
	if ledger.recorder(market.M15) != nil || ledger.drain() != nil {
		t.Fatal("a nil ledger must be inert")
	}
	if got := higherTimeframeInstanceSource(gapFrame(3.0), market.M15, fvgSettings(definitionOn), nil)(); len(got) != 0 {
		t.Fatalf("the gap is still refused without a ledger: %+v", got)
	}
}
