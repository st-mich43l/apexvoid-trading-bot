package strategyutil

import (
	"reflect"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

func TestHigherTimeframesParameterIsOptionalAndStrict(t *testing.T) {
	got, err := ParseLegacyDetectorSettings(legacyfixture.Params(nil))
	if err != nil || len(got.HigherTimeframes) != 0 {
		t.Fatalf("absent parameter must mean none, got %v err %v", got.HigherTimeframes, err)
	}
	got, err = ParseLegacyDetectorSettings(legacyfixture.Params(map[string]any{"higher_timeframes": []any{"M15", "H1"}}))
	if err != nil || !reflect.DeepEqual(got.HigherTimeframes, []market.Timeframe{market.M15, market.H1}) {
		t.Fatalf("got %v err %v", got.HigherTimeframes, err)
	}
	for name, bad := range map[string]any{
		"execution timeframe": []any{"M5"},
		"lower timeframe":     []any{"M1"},
		"unknown":             []any{"M7"},
		"duplicate":           []any{"M15", "M15"},
		"not a string":        []any{15},
		"not a list":          "M15",
	} {
		if _, err := ParseLegacyDetectorSettings(legacyfixture.Params(map[string]any{"higher_timeframes": bad})); err == nil {
			t.Fatalf("%s must be rejected", name)
		}
	}
}

// m15Context is an M5 execution frame (bars every 300 s from t=300) with an M15
// frame whose bars sit on the same clock and whose instances are supplied.
func m15Context(m15Bars []market.Candle, instances []techniquezone.Instance) *analysiscontext.MarketContext {
	ctx := legacyfixture.Context(legacyfixture.Bars(60, 100), 1, nil)
	ctx.Timeframes[market.M15] = &analysiscontext.TimeframeContext{
		Timeframe: market.M15,
		Legacy: &analysiscontext.LegacyFrame{
			Bars:       m15Bars,
			Techniques: func() []techniquezone.Instance { return instances },
		},
	}
	return ctx
}

func TestHigherInstancesAreAnchoredToTheExecutionFrameByBarTime(t *testing.T) {
	// M5 bar i (0-based) opens at (i+1)*300. M15 bar times 3000 and 3900 are M5
	// bars 9 and 12; 60 is older than the whole execution window.
	m15Bars := []market.Candle{{Time: 60}, {Time: 3000}, {Time: 3900}}
	ctx := m15Context(m15Bars, []techniquezone.Instance{
		{Technique: "supply_demand", Side: "sell", Low: 110, High: 112, OriginIndex: 2, Timeframe: "M15"},
		{Technique: "supply_demand", Side: "buy", Low: 90, High: 92, OriginIndex: 1},
		{Technique: "supply_demand", Side: "buy", Low: 80, High: 82, OriginIndex: 0},
		{Technique: "supply_demand", Side: "buy", Low: 70, High: 72, OriginIndex: 9},
	})
	execBars := ctx.Timeframes[market.M5].Legacy.Bars
	got := higherInstances(ctx, execBars, []market.Timeframe{market.M15})
	if len(got) != 2 {
		t.Fatalf("want the two anchorable instances, got %+v", got)
	}
	if got[0].OriginIndex != 12 || got[0].Timeframe != "M15" || execBars[got[0].OriginIndex].Time != 3900 {
		t.Fatalf("sell origin not mapped to the M5 bar of the same time: %+v", got[0])
	}
	if got[1].OriginIndex != 9 || execBars[got[1].OriginIndex].Time != 3000 {
		t.Fatalf("buy origin not mapped: %+v", got[1])
	}
	if got := higherInstances(ctx, execBars, nil); got != nil {
		t.Fatalf("no configured timeframe means no instances, got %+v", got)
	}
	if got := higherInstances(ctx, execBars, []market.Timeframe{market.H1}); got != nil {
		t.Fatalf("an absent timeframe must be ignored, got %+v", got)
	}
}

func TestHigherInstancesStaySeparateFromTheExecutionDecisions(t *testing.T) {
	ctx := m15Context([]market.Candle{{Time: 3000}}, []techniquezone.Instance{
		{Technique: "supply_demand", Side: "sell", Low: 110, High: 112, OriginIndex: 0, Timeframe: "M15"},
	})
	ctx.Timeframes[market.M5].Legacy.Techniques = func() []techniquezone.Instance { return nil }
	legacy := settings(t)

	if _, ok := NewTechniqueSource(ctx, market.M5, legacy); ok {
		t.Fatal("without the parameter, an empty execution frame binds nothing")
	}
	legacy.HigherTimeframes = []market.Timeframe{market.M15}
	source, ok := NewTechniqueSource(ctx, market.M5, legacy)
	if !ok || len(source.Instances) != 0 || len(source.HigherInstances) != 1 {
		t.Fatalf("higher instances must bind alone, got ok=%v %+v", ok, source)
	}
	if bands := source.Bands(); len(bands) != 0 {
		t.Fatalf("higher instances must never form confluence bands, got %+v", bands)
	}
	if source.Technique("supply_demand") != nil {
		t.Fatal("the execution decision must not see higher instances")
	}
	if source.HigherTechnique("order_block") != nil {
		t.Fatal("only the requested technique is evaluated")
	}
}

func TestHigherInstanceIdentityAndEvidenceCarryTheTimeframe(t *testing.T) {
	d := &LegacyDetector{Frame: &analysiscontext.LegacyFrame{Bars: legacyfixture.Bars(10, 100)}}
	exec := techniquezone.Instance{Technique: TechniqueSupplyDemand, Side: "sell", OriginIndex: 3}
	higher := exec
	higher.Timeframe = "M15"
	if got := instanceID(exec, d); got != "technique:supply_demand:sell:1200" {
		t.Fatalf("execution identity changed: %q", got)
	}
	if got := instanceID(higher, d); got != "technique:supply_demand:sell:1200@M15" {
		t.Fatalf("higher identity must not collide with the execution one: %q", got)
	}
	spec := TechniqueSpec{ZoneEvidence: "z", ConfirmedEvidence: "c"}
	if got := techniqueEvidence(&TechniqueDecision{Instance: &exec}, spec); !reflect.DeepEqual(got, []string{"z", "c"}) {
		t.Fatalf("execution evidence changed: %v", got)
	}
	if got := techniqueEvidence(&TechniqueDecision{Instance: &higher}, spec); !reflect.DeepEqual(got, []string{"z", "c", "htf_zone_m15"}) {
		t.Fatalf("higher evidence must name the timeframe: %v", got)
	}
}
