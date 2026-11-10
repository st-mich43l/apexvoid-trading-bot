package rangescalp

import (
	"math"
	"sort"
	"strings"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

const barTime = int64(1_700_000_000)

// ctxWith is a market context at barTime whose canonical zone book (M15) holds the given zones.
func ctxWith(zones ...zone.Zone) *analysiscontext.MarketContext {
	ctx := &analysiscontext.MarketContext{Symbol: "XAU", Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{}}
	for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
		ctx.Timeframes[tf] = &analysiscontext.TimeframeContext{Timeframe: tf, Zones: zone.ZoneState{ATR: 3}}
	}
	ctx.Timeframes[market.M15].Zones.Zones = zones
	ctx.Timeframes[market.M5].Candles = []market.Candle{{Time: barTime}}
	return ctx
}

func wallAt(kind zone.Kind, low, high float64) zone.Zone {
	return zone.Zone{ID: "w", Kind: kind, Low: market.Price(low), High: market.Price(high), State: zone.StateFresh, OriginTime: barTime - 5000, CreatedAt: barTime - 5000, TouchCount: 2, Strength: 6}
}

// probeWithRoom is a probe whose structural stop is stopPips and whose far range edge lies roomPips beyond the worst planned entry (sig.ATR is 1, as in
// signalFor, so the probe depth and the range are what they are in price units).
func probeWithRoom(direction market.Direction, stopPips, roomPips float64, cfg Config) signal {
	sig := signalFor(direction, stopPips, cfg)
	half := cfg.EntryWidthPips * cfg.PipSize / 2
	if direction == market.Sell {
		worst := sig.Close - half
		sig.Low = worst - roomPips*cfg.PipSize
	} else {
		worst := sig.Close + half
		sig.High = worst + roomPips*cfg.PipSize
	}
	sig.Width = sig.High - sig.Low
	sig.Efficiency, sig.ProbeDepthATR, sig.BarTime = 0.1, 0.3, barTime
	return sig
}

func build(t *testing.T, ctx *analysiscontext.MarketContext, sig signal) (opportunity.Candidate, string) {
	t.Helper()
	return testStrategy(t).candidate(ctx, []market.Candle{{Time: barTime}}, sig)
}

func targetPips(c opportunity.Candidate, direction market.Direction, pip float64) float64 {
	worst := float64(c.Entry.High)
	if direction == market.Sell {
		worst = float64(c.Entry.Low)
	}
	return math.Abs(float64(c.Targets[0].Price.Price)-worst) / pip
}

func TestTheTargetLadderIsBoundedByTheActualRoom(t *testing.T) {
	s := testStrategy(t)
	cases := []struct {
		name     string
		room     float64 // objective room from the worst entry, pips
		barrier  float64 // raw distance to an opposing barrier's near edge, pips; 0 = none
		want     float64 // selected target pips; 0 = refused
		wantWhy  string
		wantFrom string // the limiter recorded in the quality measurements
	}{
		{"plenty of valid opposing room: the 2R rung", 200, 0, 60, "", "objective"},
		{"a barrier closer than the 2R target: the 1R rung", 200, 50, 30, "", "barrier"},
		{"only 1R fits the range", 45, 0, 30, "", "objective"},
		{"no ladder target fits", 25, 0, 0, ReasonNoLadderTargetFits, ""},
		{"a barrier leaves less than 1R", 200, 33, 0, ReasonNoLadderTargetFits, ""},
		{"zero room", 0, 0, 0, ReasonNoOpposingRoom, ""},
		{"the far edge is behind the entry", -20, 0, 0, ReasonNoOpposingRoom, ""},
	}
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		for _, c := range cases {
			t.Run(string(direction)+" "+c.name, func(t *testing.T) {
				sig := probeWithRoom(direction, 30, c.room, s.cfg)
				var zones []zone.Zone
				if c.barrier > 0 {
					worst := sig.Close + s.cfg.EntryWidthPips*s.cfg.PipSize/2
					if direction == market.Sell {
						// The opposing wall of a SELL is demand below the entry; its near edge is its upper edge.
						worst = sig.Close - s.cfg.EntryWidthPips*s.cfg.PipSize/2
						zones = append(zones, wallAt(zone.KindDemand, worst-c.barrier*s.cfg.PipSize-1, worst-c.barrier*s.cfg.PipSize))
					} else {
						zones = append(zones, wallAt(zone.KindSupply, worst+c.barrier*s.cfg.PipSize, worst+c.barrier*s.cfg.PipSize+1))
					}
				}
				cand, why := build(t, ctxWith(zones...), sig)
				if why != c.wantWhy {
					t.Fatalf("refusal %q, want %q", why, c.wantWhy)
				}
				if c.want == 0 {
					return
				}
				if got := targetPips(cand, direction, s.cfg.PipSize); math.Abs(got-c.want) > 1e-6 {
					t.Errorf("target %.4f pips, want %.0f", got, c.want)
				}
				limited := cand.Quality.Components["room_limited_by_barrier"]
				if (c.wantFrom == "barrier") != (limited == 1) {
					t.Errorf("room_limited_by_barrier = %v, want limiter %q", limited, c.wantFrom)
				}
			})
		}
	}
}

func TestAnEntryInsideAnOpposingWallHasNoRoom(t *testing.T) {
	s := testStrategy(t)
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		sig := probeWithRoom(direction, 30, 200, s.cfg)
		kind := zone.KindSupply
		if direction == market.Sell {
			kind = zone.KindDemand
		}
		inside := wallAt(kind, sig.Close-1, sig.Close+1)
		if _, why := build(t, ctxWith(inside), sig); why != ReasonNoOpposingRoom {
			t.Errorf("%s inside a wall: refusal %q, want %q (no same-wall exemption)", direction, why, ReasonNoOpposingRoom)
		}
	}
}

func TestStaleAndFutureBarriersDoNotLimitTheRoom(t *testing.T) {
	s := testStrategy(t)
	sig := probeWithRoom(market.Buy, 30, 200, s.cfg)
	worst := sig.Close + s.cfg.EntryWidthPips*s.cfg.PipSize/2
	stale := wallAt(zone.KindSupply, worst+3, worst+4)
	stale.State = zone.StateMitigated
	future := wallAt(zone.KindSupply, worst+3, worst+4)
	future.OriginTime, future.CreatedAt = barTime+300, barTime+600
	cand, why := build(t, ctxWith(stale, future), sig)
	if why != "" {
		t.Fatalf("refusal %q: a mitigated zone and a zone from the future must not limit the room", why)
	}
	if got := targetPips(cand, market.Buy, s.cfg.PipSize); math.Abs(got-60) > 1e-6 {
		t.Errorf("target %.2f pips, want the 2R rung (60)", got)
	}
}

func TestTheStructuralStopCapIsUnchanged(t *testing.T) {
	s := testStrategy(t)
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		for _, c := range []struct {
			structural float64
			wantStop   float64
			wantWhy    string
		}{{24, 30, ""}, {39, 39, ""}, {40, 40, ""}, {40.5, 0, ReasonStopOverCap}, {48, 0, ReasonStopOverCap}} {
			sig := probeWithRoom(direction, c.structural, 400, s.cfg)
			cand, why := build(t, ctxWith(), sig)
			if why != c.wantWhy {
				t.Errorf("%s %.1f: refusal %q, want %q", direction, c.structural, why, c.wantWhy)
				continue
			}
			if c.wantStop == 0 {
				continue
			}
			worst := float64(cand.Entry.High)
			if direction == market.Sell {
				worst = float64(cand.Entry.Low)
			}
			if got := math.Abs(float64(cand.Invalidation.Price)-worst) / s.cfg.PipSize; math.Abs(got-c.wantStop) > 1e-6 {
				t.Errorf("%s %.1f: stop %.4f, want %.0f", direction, c.structural, got, c.wantStop)
			}
		}
	}
}

func TestQualityDimensionsAreFiniteBoundedAndTheOverallIsTheirMean(t *testing.T) {
	s := testStrategy(t)
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		sig := probeWithRoom(direction, 33, 200, s.cfg)
		for _, depth := range []float64{0, 0.05, 0.3, 0.6, 1.5, 5, 50} { // from a scrape to a breakout
			sig.ProbeDepthATR = depth
			cand, why := build(t, ctxWith(), sig)
			if why != "" {
				t.Fatalf("refused: %s", why)
			}
			q := cand.Quality
			if err := quality.Verify(q.Overall, q.Components); err != nil {
				t.Errorf("%s depth %v: %v", direction, depth, err)
			}
			sum, n := 0.0, 0
			for name, v := range q.Components {
				if math.IsNaN(v) || math.IsInf(v, 0) {
					t.Errorf("%s: %s = %v is not finite", direction, name, v)
				}
				if strings.HasPrefix(name, quality.Prefix) {
					if v < 0 || v > 1 {
						t.Errorf("%s: %s = %v outside [0, 1]", direction, name, v)
					}
					sum += v
					n++
				}
			}
			if n == 0 || math.Abs(q.Overall-sum/float64(n)) > 1e-9 {
				t.Errorf("%s: overall %v is not the mean %v of %d dimensions", direction, q.Overall, sum/float64(n), n)
			}
		}
	}
}

func TestProbeDepthIsNotRewardedIndefinitely(t *testing.T) {
	s := testStrategy(t)
	score := func(depth float64) float64 {
		sig := probeWithRoom(market.Buy, 33, 200, s.cfg)
		sig.ProbeDepthATR = depth
		cand, _ := build(t, ctxWith(), sig)
		return cand.Quality.Components["score_edge_probe"]
	}
	// 0 at no excursion, rising to a plateau, and back to 0 for a breakout-sized one. Inspect the boundaries.
	want := map[float64]float64{0: 0, 0.05: 0.5, 0.10: 1, 0.35: 1, 0.60: 1, 1.05: 0.5, 1.50: 0, 4: 0}
	for depth, w := range want {
		if got := score(depth); math.Abs(got-w) > 1e-9 {
			t.Errorf("edge_probe(%v ATR) = %v, want %v", depth, got, w)
		}
	}
}

func TestEntryLocationFavoursTheEdgeBeingFaded(t *testing.T) {
	s := testStrategy(t)
	at := func(widthPips float64) float64 {
		// A range widthPips wide whose low is the stop side and whose worst entry sits 12 pips above it.
		sig := probeWithRoom(market.Buy, 33, widthPips-12, s.cfg)
		cand, why := build(t, ctxWith(), sig)
		if why != "" {
			t.Fatalf("refused: %s", why)
		}
		return cand.Quality.Components["score_entry_location"]
	}
	if near, far := at(60), at(400); !(near < far) {
		t.Errorf("an entry 12 pips from the edge of a 60-pip range (%v) must score below one in a 400-pip range (%v)", near, far)
	}
}

// The selected ladder rung is a reward:risk the strategy chose. It is not structural room and must not score as room: the same 1R rung with
// 30 pips of room and with 45 pips of room scores differently, and 2R with exactly 2R of room scores half, not full.
func TestASyntheticRungDoesNotCreateRoom(t *testing.T) {
	s := testStrategy(t)
	room := func(roomPips float64) (target, score float64) {
		sig := probeWithRoom(market.Buy, 30, roomPips, s.cfg)
		cand, why := build(t, ctxWith(), sig)
		if why != "" {
			t.Fatalf("room %v refused: %s", roomPips, why)
		}
		return targetPips(cand, market.Buy, s.cfg.PipSize), cand.Quality.Components["score_room_reward_risk"]
	}
	t1, s1 := room(30)
	t2, s2 := room(45)
	if math.Abs(t1-30) > 1e-6 || math.Abs(t2-30) > 1e-6 {
		t.Fatalf("both should select the 1R rung, got %v and %v", t1, t2)
	}
	if !(s1 < s2) || s1 != 0 {
		t.Errorf("room scores for 31 and 45 pips = %v and %v, want 0 (1R) and above it", s1, s2)
	}
	if _, full := room(60); math.Abs(full-0.5) > 1e-9 {
		t.Errorf("2R of room scores %v, want 0.5 (3R scores 1)", full)
	}
	if _, plenty := room(90); plenty != 1 {
		t.Errorf("3R of room scores %v, want 1", plenty)
	}
}

// The schema is fixed: structural_support is on every opportunity, 1 with a supporting wall and 0 without, and the overall is always the exact mean of the
// same five dimensions.
func TestStructuralSupportIsAlwaysScoredWithAFixedSchema(t *testing.T) {
	s := testStrategy(t)
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		sig := probeWithRoom(direction, 33, 200, s.cfg)
		low, high := sig.SignalLow, sig.Close
		kind := zone.KindDemand
		if direction == market.Sell {
			low, high, kind = sig.Close, sig.SignalHigh, zone.KindSupply
		}
		opposite := zone.KindSupply
		if direction == market.Sell {
			opposite = zone.KindDemand
		}
		cases := []struct {
			name      string
			zones     []zone.Zone
			wantScore float64
		}{
			{"no wall at all", nil, 0},
			{"a supporting wall under the probe", []zone.Zone{wallAt(kind, low-0.1, high)}, 1},
			{"a wall of the other side is not support", []zone.Zone{wallAt(opposite, low-0.1, high)}, 0},
			{"a supporting wall away from the probe is not support", []zone.Zone{wallAt(kind, low-50, low-40)}, 0},
		}
		var schema []string
		for _, c := range cases {
			cand, why := build(t, ctxWith(c.zones...), sig)
			if why != "" {
				t.Fatalf("%s %s: refused %q", direction, c.name, why)
			}
			comps := cand.Quality.Components
			got, present := comps["score_structural_support"]
			if !present || got != c.wantScore {
				t.Errorf("%s %s: score_structural_support = %v (present %v), want %v", direction, c.name, got, present, c.wantScore)
			}
			if want := c.wantScore; comps["support_wall_present"] != want {
				t.Errorf("%s %s: support_wall_present = %v, want %v", direction, c.name, comps["support_wall_present"], want)
			}
			var dims []string
			sum := 0.0
			for name, v := range comps {
				if strings.HasPrefix(name, quality.Prefix) {
					dims = append(dims, name)
					sum += v
				}
			}
			sort.Strings(dims)
			if len(dims) != 5 || math.Abs(cand.Quality.Overall-sum/5) > 1e-12 {
				t.Errorf("%s %s: %d dimensions %v, overall %v vs mean %v: want the same five and the exact mean", direction, c.name, len(dims), dims, cand.Quality.Overall, sum/5)
			}
			if schema == nil {
				schema = dims
			} else if strings.Join(schema, ",") != strings.Join(dims, ",") {
				t.Errorf("%s %s: dimensions %v differ from %v", direction, c.name, dims, schema)
			}
			if err := quality.Verify(cand.Quality.Overall, comps); err != nil {
				t.Errorf("%s %s: %v", direction, c.name, err)
			}
		}
	}
}

func TestSupportMovesOnlyThatDimensionAndTheOverall(t *testing.T) {
	s := testStrategy(t)
	sig := probeWithRoom(market.Buy, 33, 200, s.cfg)
	without, _ := build(t, ctxWith(), sig)
	with, _ := build(t, ctxWith(wallAt(zone.KindDemand, sig.SignalLow-0.1, sig.Close)), sig)
	for name, v := range without.Quality.Components {
		if !strings.HasPrefix(name, quality.Prefix) || name == "score_structural_support" {
			continue
		}
		if with.Quality.Components[name] != v {
			t.Errorf("%s changed with a supporting wall: %v to %v", name, v, with.Quality.Components[name])
		}
	}
	if d := with.Quality.Overall - without.Quality.Overall; math.Abs(d-1.0/5) > 1e-12 {
		t.Errorf("overall moved by %v, want exactly 1/5", d)
	}
}

func TestCandleConfirmationConfigurationDoesNotChangeRoom(t *testing.T) {
	// analysis.require_candle_confirmation is injected into every strategy; Range Scalp has no confirmation candle and must neither refuse it nor change.
	params := map[string]any{
		"pip_size": 0.1, "stop_minimum_pips": 30.0, "stop_maximum_pips": 40.0, "minimum_net_target_pips": 10.0,
		"maximum_efficiency_ratio": 0.5, "minimum_range_atr": 1.0, "maximum_range_atr": 6.0, "edge_fraction": 0.25, "stop_buffer_atr": 0.0,
		"entry_width_pips": 4.0, "expiry_minutes": 30.0, "range_lookback_bars": 12, "cooldown_bars": 6, "maximum_signals_per_day": 3,
		"atr_length": 14, "atr_window_bars": 140, "trading_day_rollover_utc_hour": 22, "symbols": []any{"XAU"}, "reward_risk_ladder": []any{2.0, 1.0},
		"barrier_buffer_atr": 0.15, "barrier_max_width_atr": 2.0, "barrier_max_width_pips": 100.0,
	}
	var got [2]opportunity.Candidate
	for i, required := range []bool{true, false} {
		p := map[string]any{"require_candle_confirmation": required}
		for k, v := range params {
			p[k] = v
		}
		st, err := New(strategy.Config{ID: ID, Version: Version, Parameters: p})
		if err != nil {
			t.Fatal(err)
		}
		sig := probeWithRoom(market.Buy, 33, 200, st.(*Strategy).cfg)
		cand, why := st.(*Strategy).candidate(ctxWith(), []market.Candle{{Time: barTime}}, sig)
		if why != "" {
			t.Fatalf("require_candle_confirmation=%v refused: %s", required, why)
		}
		got[i] = cand
	}
	if got[0].Targets[0].Price != got[1].Targets[0].Price || got[0].Quality.Overall != got[1].Quality.Overall {
		t.Error("the candle confirmation switch changed Range Scalp's target or quality")
	}
}

func TestTheBarrierPolicyIsRequired(t *testing.T) {
	if _, err := parseConfig(map[string]any{"pip_size": 0.1}); err == nil {
		t.Error("a configuration without the barrier policy must fail closed")
	}
}
