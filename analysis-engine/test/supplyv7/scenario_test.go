package supplyv7_test

import (
	"math"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/supply"
)

// A hand-built market with a supply zone on M15. Everything is derived from the M5 path: the M15 frame is the aggregate of complete M15 candles, exactly as
// the engine builds it, so cutting the path at any M5 bar gives the context of that moment.
//
//	bars 0-59   a flat chop around 210.00
//	bars 60-68  M15 candles 20-22: the base, range 209.98-210.15
//	bars 69-74  M15 candles 23-24: the departure, a strong leg down 210.08 -> 209.10
//	bars 75-77  M15 candle 25: the rebound
//	bar 78      a touch of the zone from below
//	bar 79      the wick rejection: closes lower with an upper wick
type tick struct{ o, h, l, c float64 }

const (
	m5Seconds = 300
	startTime = 1_792_999_800 // a multiple of 900
	// The path is drawn in "ticks" of a wider market than GBPJPY's 25-pip stop cap allows; px scales it down around 210.00 (every ratio is kept).
	scale  = 0.55
	m5ATR  = 0.10 * scale
	m15ATR = 0.30 * scale
)

func px(x float64) float64 { return 210.00 + (x-210.00)*scale }

func chop() tick { return tick{210.00, 210.04, 209.96, 210.00} }

func basePath() []tick {
	var path []tick
	for i := 0; i < 60; i++ {
		path = append(path, chop())
	}
	return append(path,
		tick{210.00, 210.03, 209.98, 210.02}, tick{210.02, 210.10, 210.01, 210.08}, tick{210.08, 210.09, 210.03, 210.05}, // 20: base
		tick{210.05, 210.12, 210.04, 210.10}, tick{210.10, 210.11, 210.00, 210.01}, tick{210.01, 210.03, 209.99, 210.02}, // 21: base
		tick{210.02, 210.06, 210.00, 210.05}, tick{210.05, 210.15, 210.04, 210.12}, tick{210.12, 210.13, 210.06, 210.08}, // 22: base
		tick{210.08, 210.09, 209.80, 209.82}, tick{209.82, 209.84, 209.64, 209.66}, tick{209.66, 209.68, 209.58, 209.60}, // 23: the departure
		tick{209.60, 209.62, 209.40, 209.42}, tick{209.42, 209.44, 209.24, 209.26}, tick{209.26, 209.28, 209.08, 209.10}, // 24: the departure
		tick{209.10, 209.40, 209.08, 209.38}, tick{209.38, 209.66, 209.36, 209.64}, tick{209.64, 209.90, 209.62, 209.88}, // 25: the rebound
		tick{209.88, 210.00, 209.86, 209.96}, // 78: the touch
		tick{209.97, 210.14, 209.90, 209.92}, // 79: the wick rejection
	)
}

const decisionBar = 79

func candles(path []tick) []market.Candle {
	out := make([]market.Candle, len(path))
	for i, p := range path {
		out[i] = market.Candle{Time: startTime + int64(i)*m5Seconds, Open: px(p.o), High: px(p.h), Low: px(p.l), Close: px(p.c), Volume: 1}
	}
	return out
}

func aggregate(m5 []market.Candle) []market.Candle {
	var out []market.Candle
	for i := 0; i+3 <= len(m5); i += 3 {
		c := market.Candle{Time: m5[i].Time, Open: m5[i].Open, High: m5[i].High, Low: m5[i].Low, Close: m5[i+2].Close, Volume: 3}
		for _, b := range m5[i : i+3] {
			c.High, c.Low = math.Max(c.High, b.High), math.Min(c.Low, b.Low)
		}
		out = append(out, c)
	}
	return out
}

func frame(bars []market.Candle, atr float64) *analysiscontext.LegacyFrame {
	series := make([]float64, len(bars))
	for i := range series {
		series[i] = atr
	}
	return &analysiscontext.LegacyFrame{Bars: bars, ATR: series, DetectorATR: atr}
}

// contextAt is the market after the closed M5 bar `last` of path.
func contextAt(path []tick, last int, htf string) *analysiscontext.MarketContext {
	m5 := candles(path[:last+1])
	return &analysiscontext.MarketContext{
		Symbol: "GBPJPY",
		Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{
			market.M5:  {Timeframe: market.M5, Legacy: frame(m5, m5ATR)},
			market.M15: {Timeframe: market.M15, Legacy: frame(aggregate(m5), m15ATR)},
		},
		Legacy: &analysiscontext.LegacyRead{LocalStructure: "down", HTFBias: htf},
	}
}

// newStrategy builds the strategy with the engine-resolved GBPJPY parameters, over which override changes single keys.
func newStrategy(t testing.TB, override map[string]any) *supply.Strategy {
	t.Helper()
	doc := resolve(t)
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, "GBPJPY"); err != nil {
		t.Fatal(err)
	}
	var cfg strategy.Config
	for _, c := range settings.Strategies {
		if c.ID == "supply" {
			cfg = c
		}
	}
	params := map[string]any{}
	for k, v := range cfg.Parameters {
		params[k] = v
	}
	params["structure_timeframes"] = []any{"M15"}
	for k, v := range override {
		params[k] = v
	}
	cfg.Parameters = params
	s, err := supply.New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	return s.(*supply.Strategy)
}

func decide(t testing.TB, s *supply.Strategy, path []tick, last int, htf string) ([]opportunity.Candidate, map[string]int64) {
	t.Helper()
	got := s.Evaluate(contextAt(path, last, htf))
	reasons := map[string]int64{}
	for r, n := range s.TakeRefusals() {
		reasons[r.Reason] += n
	}
	return got, reasons
}

func itoa(n int64) string {
	if n == 0 {
		return "0"
	}
	var digits []byte
	for ; n > 0; n /= 10 {
		digits = append([]byte{byte('0' + n%10)}, digits...)
	}
	return string(digits)
}

func TestDetectsTheZoneAndPublishesTheConfirmedSell(t *testing.T) {
	got, reasons := decide(t, newStrategy(t, map[string]any{"confluence_floor": 1}), basePath(), decisionBar, "down")
	if len(got) != 1 {
		t.Fatalf("want one candidate, got %d (refusals %v)", len(got), reasons)
	}
	c := got[0]
	if c.Direction != market.Sell || c.Structure.Timeframe != market.M15 || math.Abs(c.Structure.Low-px(209.98)) > 1e-9 || math.Abs(c.Structure.High-px(210.15)) > 1e-9 {
		t.Fatalf("wrong structure: %s", describe(c))
	}
	if c.Structure.FormedAt != startTime+66*m5Seconds {
		t.Errorf("formed at %d, want the zone's last base candle", c.Structure.FormedAt)
	}
	if math.Abs(c.Entry.Low-px(209.98)) > 1e-9 || math.Abs(c.Entry.High-px(210.15)) > 1e-9 {
		t.Errorf("entry band %v-%v, want the whole zone", c.Entry.Low, c.Entry.High)
	}
	if c.Reaction == nil || c.Reaction.Pattern != "wick_rejection" || c.Reaction.ReactionType != "rejection" || c.Reaction.ConfirmationBarTime != startTime+decisionBar*m5Seconds {
		t.Errorf("reaction %+v, want a wick_rejection on bar %d", c.Reaction, decisionBar)
	}
	if want := px(210.15) + 0.5*m5ATR; math.Abs(float64(c.Invalidation.Price)-want) > 1e-9 {
		t.Errorf("stop %v, want %v (above the whole zone, plus the buffer)", c.Invalidation.Price, want)
	}
	target := c.Targets[0].Price
	if target.Label != "nearest_opposing_structure" || math.Abs(float64(target.Price)-px(209.08)) > 1e-9 {
		t.Errorf("target %v %q, want the 209.08 departure low", target.Price, target.Label)
	}
	if c.StrategyVersion != "v9" || c.StructuralID != "technique:supply_demand:sell:"+itoa(startTime+66*m5Seconds)+"@M15" {
		t.Errorf("version %q id %q", c.StrategyVersion, c.StructuralID)
	}
}

func TestADemandZoneIsNeverASupplyCandidate(t *testing.T) {
	path := basePath()
	for i := range path {
		path[i] = tick{o: 420 - path[i].o, h: 420 - path[i].l, l: 420 - path[i].h, c: 420 - path[i].c}
	}
	got, _ := decide(t, newStrategy(t, map[string]any{"confluence_floor": 1}), path, decisionBar, "up")
	if len(got) != 0 {
		t.Fatalf("the mirrored (demand) zone published %s", describe(got[0]))
	}
}

func TestAnEntryWithNoRoomIsRefusedAtTheBoundary(t *testing.T) {
	published, _ := decide(t, newStrategy(t, map[string]any{"confluence_floor": 1}), basePath(), decisionBar, "down")
	if len(published) != 1 {
		t.Fatal("the base scenario must publish")
	}
	roomR := published[0].Quality.Components["room_r"]
	if !(roomR > 1) {
		t.Fatalf("room_r %v", roomR)
	}
	just := func(r float64) (int, map[string]int64) {
		got, reasons := decide(t, newStrategy(t, map[string]any{"confluence_floor": 1, "minimum_target_r": r, "fallback_target_r": r + 1}), basePath(), decisionBar, "down")
		return len(got), reasons
	}
	if n, reasons := just(roomR - 0.01); n != 1 {
		t.Errorf("an obstacle %.2f R away publishes at a %.2f R minimum: refusals %v", roomR, roomR-0.01, reasons)
	}
	if n, reasons := just(roomR + 0.01); n != 0 || reasons["no_target_room"] == 0 {
		t.Errorf("an obstacle %.2f R away is refused at a %.2f R minimum: published %d refusals %v", roomR, roomR+0.01, n, reasons)
	}
}

func TestAZonePriceClosedAboveIsDeadAndNotTraded(t *testing.T) {
	path := basePath()
	path[75], path[76], path[77] = tick{209.10, 209.60, 209.08, 209.58}, tick{209.58, 210.20, 209.56, 210.18}, tick{210.18, 210.40, 210.16, 210.38} // closes 0.23 above the high
	path[78], path[79] = tick{210.38, 210.40, 210.10, 210.12}, tick{210.12, 210.40, 210.05, 210.06}
	got, _ := decide(t, newStrategy(t, map[string]any{"confluence_floor": 1}), path, decisionBar, "down")
	if len(got) != 0 {
		t.Fatalf("a zone price closed above and stayed above is dead, published %s", describe(got[0]))
	}
}

func TestOnlyStructureFramesDefineAZone(t *testing.T) {
	m5Only := contextAt(basePath(), decisionBar, "down")
	delete(m5Only.Timeframes, market.M15)
	s := newStrategy(t, map[string]any{"confluence_floor": 1})
	if got := s.Evaluate(m5Only); len(got) != 0 {
		t.Fatalf("with no structure frame the M5 bars alone published %s", describe(got[0]))
	}
	h1Only := newStrategy(t, map[string]any{"confluence_floor": 1, "structure_timeframes": []any{"H1"}})
	if got, _ := decide(t, h1Only, basePath(), decisionBar, "down"); len(got) != 0 {
		t.Fatalf("a strategy that reads H1 only must not trade an M15 zone")
	}
}

func TestTheZoneIsNotAReactionBeforeItsDepartureHasClosed(t *testing.T) {
	s := newStrategy(t, map[string]any{"confluence_floor": 1})
	for last := 0; last < 75; last++ {
		if got, _ := decide(t, s, basePath(), last, "down"); len(got) != 0 {
			t.Fatalf("bar %d: published before the M15 candle that ended the departure had closed: %s", last, describe(got[0]))
		}
	}
}

func TestTheStopBudgetRefusesAnOverCapStopAndFlagsAnUnderFloorOne(t *testing.T) {
	under, _ := decide(t, newStrategy(t, map[string]any{"confluence_floor": 1}), basePath(), decisionBar, "down")
	if len(under) != 1 {
		t.Fatal("the base scenario must publish")
	}
	ev := map[string]bool{}
	for _, e := range under[0].Evidence {
		ev[e.Code] = true
	}
	if !ev["m5_supply_zone_confirmed"] || !ev["m5_supply_zone_rejection_confirmed"] || !ev["htf_zone_m15"] {
		t.Errorf("evidence %v", ev)
	}
	if !ev["stop_budget_floor"] {
		t.Errorf("a stop under the 22 pip GBPJPY floor carries stop_budget_floor: %v", ev)
	}
	capped := newStrategy(t, map[string]any{"confluence_floor": 1, "execution_stop_max_pips": 5.0, "execution_stop_min_pips": 2.0})
	got, _ := decide(t, capped, basePath(), decisionBar, "down")
	if len(got) != 0 {
		t.Fatalf("a stop over the cap must be refused, never tightened: %s", describe(got[0]))
	}
	if n := capped.TakeStopBudgetRefusals(); n == 0 {
		t.Error("the refusal must be counted")
	}
}

func TestConfluenceFactorsAreMeasuredNotAssumed(t *testing.T) {
	s := newStrategy(t, map[string]any{"confluence_floor": 1})
	with, _ := decide(t, s, basePath(), decisionBar, "down")
	without, _ := decide(t, s, basePath(), decisionBar, "up")
	if len(with) != 1 || len(without) != 1 {
		t.Fatalf("published %d / %d", len(with), len(without))
	}
	f, g := with[0].DetectorConfluence.Factors, without[0].DetectorConfluence.Factors
	if !f.HTFAligned || g.HTFAligned {
		t.Errorf("HTFAligned must follow the higher-timeframe bias: down=%v up=%v", f.HTFAligned, g.HTFAligned)
	}
	if !f.WickRejection || f.DisplacementGrade || f.StructuralAgreement || f.CHoCH || f.FibTouch {
		t.Errorf("a plain wick rejection shows the wick and nothing else: %+v", f)
	}
}

// The M5 window holds 150 bars (12.5 hours); the M15 frame holds zone for days. A zone whose origin has left the M5 window must stay tradable for as long
// as its own frame holds it, with the identity and the geometry it had inside the window.
func contextFrom(path []tick, last, from int, htf string) *analysiscontext.MarketContext {
	ctx := contextAt(path, last, htf)
	m5 := ctx.Timeframes[market.M5].Legacy
	ctx.Timeframes[market.M5].Legacy = frame(m5.Bars[from:], m5ATR)
	return ctx
}

func TestAZoneOlderThanTheM5WindowStaysTradableWithTheSameIdentity(t *testing.T) {
	s := newStrategy(t, map[string]any{"confluence_floor": 1})
	whole := s.Evaluate(contextAt(basePath(), decisionBar, "down"))
	s.TakeRefusals()
	// the window holds only the last bars (at least 5): the structure's origin, and the start of the departure, are before the first M5 bar
	windowed := s.Evaluate(contextFrom(basePath(), decisionBar, 75, "down"))
	s.TakeRefusals()
	if len(whole) != 1 || len(windowed) != 1 {
		t.Fatalf("published %d inside the window, %d with the origin before it", len(whole), len(windowed))
	}
	a, b := whole[0], windowed[0]
	if a.ID != b.ID || a.StructuralID != b.StructuralID || a.Entry != b.Entry || a.Invalidation != b.Invalidation || a.Targets[0] != b.Targets[0] || *a.Reaction != *b.Reaction {
		t.Fatalf("a zone leaving the M5 window changed the decision:\n inside: %+v\n outside: %+v", a, b)
	}
	if a.Structure.FormedAt != b.Structure.FormedAt || a.Structure.FormedAt == 0 {
		t.Errorf("formed at %d vs %d", a.Structure.FormedAt, b.Structure.FormedAt)
	}
	// the frozen analysis dropped it; the switch reproduces that
	bounded := newStrategy(t, map[string]any{"confluence_floor": 1, "bind_within_execution_window": true})
	if got := bounded.Evaluate(contextFrom(basePath(), decisionBar, 75, "down")); len(got) != 0 {
		t.Fatalf("with bind_within_execution_window the origin before the window drops the structure, published %s", describe(got[0]))
	}
	if got := bounded.Evaluate(contextAt(basePath(), decisionBar, "down")); len(got) != 1 {
		t.Fatalf("with bind_within_execution_window a structure inside the window is unchanged, published %d", len(got))
	}
}
