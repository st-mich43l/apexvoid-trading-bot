package impulseretrace

import (
	"math"
	"strings"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

const barTime = int64(1_700_000_000) // the impulse bar's open time (impulseBar's)

// ctxWith is a market context at the impulse bar whose canonical zone book (M15) holds the given zones.
func ctxWith(zones ...zone.Zone) *analysiscontext.MarketContext {
	ctx := &analysiscontext.MarketContext{Symbol: "XAU", Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{}}
	for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
		ctx.Timeframes[tf] = &analysiscontext.TimeframeContext{Timeframe: tf, Zones: zone.ZoneState{ATR: 3}}
	}
	ctx.Timeframes[market.M15].Zones.Zones = zones
	ctx.Timeframes[market.M5].Candles = []market.Candle{{Time: barTime}}
	return ctx
}

func wallAt(kind zone.Kind, low, high float64, origin int64) zone.Zone {
	return zone.Zone{ID: "w", Kind: kind, Low: market.Price(low), High: market.Price(high), State: zone.StateFresh, OriginTime: origin, CreatedAt: origin, TouchCount: 2, Strength: 6}
}

// worstEntry is the planned entry that is worst for the trade: the high of a BUY band, the low of a SELL band.
func worstEntry(c opportunity.Candidate, direction market.Direction) float64 {
	if direction == market.Sell {
		return float64(c.Entry.Low)
	}
	return float64(c.Entry.High)
}

// opposingWall is a wall dist pips beyond the worst entry of an impulse of structuralPips (a supply wall above a BUY, a demand wall below a SELL).
func opposingWall(s *Strategy, direction market.Direction, structuralPips, dist float64, origin int64) zone.Zone {
	bar := impulseBar(direction, structuralPips, s.cfg)
	span := bar.High - bar.Low
	pip := s.cfg.PipSize
	if direction == market.Buy {
		worst := bar.High - s.cfg.RetraceFraction*span + s.cfg.EntryWidthPips*pip
		return wallAt(zone.KindSupply, worst+dist*pip, worst+dist*pip+1, origin)
	}
	worst := bar.Low + s.cfg.RetraceFraction*span - s.cfg.EntryWidthPips*pip
	return wallAt(zone.KindDemand, worst-dist*pip-1, worst-dist*pip, origin)
}

func buildFor(t *testing.T, s *Strategy, ctx *analysiscontext.MarketContext, direction market.Direction, structuralPips float64) (opportunity.Candidate, string) {
	t.Helper()
	return s.candidate(ctx, "", impulseBar(direction, structuralPips, s.cfg), direction, 1)
}

func TestTheTargetLadderIsBoundedByTheRoomFromThePlannedEntry(t *testing.T) {
	s := testStrategy(t)
	// A 30-pip structural stop: the impulse bar is 52 pips, the entry at its midpoint, so the impulse extreme is 22 pips ahead of the worst entry and the
	// equal-leg horizon 22 + 52 = 74 pips ahead.
	cases := []struct {
		name    string
		barrier float64 // raw distance to the opposing wall's near edge from the worst entry, pips; 0 = none
		want    float64
		wantWhy string
	}{
		{"plenty of room to the equal-leg horizon: the 2R rung", 0, 60, ""},
		{"a barrier closer than the 2R target: only 1R fits", 50, 30, ""},
		{"a barrier at 36 raw pips (31.5 after the buffer): 1R still fits", 36, 30, ""},
		{"a barrier at 33 raw pips (28.5 after the buffer): nothing fits", 33, 0, ReasonNoLadderTargetFits},
	}
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		for _, c := range cases {
			t.Run(string(direction)+" "+c.name, func(t *testing.T) {
				var zones []zone.Zone
				if c.barrier > 0 {
					zones = append(zones, opposingWall(s, direction, 30, c.barrier, barTime-5000))
				}
				cand, why := buildFor(t, s, ctxWith(zones...), direction, 30)
				if why != c.wantWhy {
					t.Fatalf("refusal %q, want %q", why, c.wantWhy)
				}
				if c.want == 0 {
					return
				}
				if got := math.Abs(float64(cand.Targets[0].Price.Price)-worstEntry(cand, direction)) / s.cfg.PipSize; math.Abs(got-c.want) > 1e-6 {
					t.Errorf("target %.4f pips from the worst entry, want %.0f", got, c.want)
				}
			})
		}
	}
}

func TestTheImpulseExtremeAloneNeverOffersOneR(t *testing.T) {
	// The reason the room runs beyond the extreme: with the entry at the retrace and the stop beyond the origin the extreme is always closer than the stop.
	s := testStrategy(t)
	for _, structural := range []float64{24, 30, 35, 40} {
		cand, why := buildFor(t, s, ctxWith(), market.Buy, structural)
		if why != "" {
			t.Fatalf("%v pips refused: %s", structural, why)
		}
		q := cand.Quality.Components
		stop := math.Abs(float64(cand.Invalidation.Price)-worstEntry(cand, market.Buy)) / s.cfg.PipSize
		if !(q["extreme_room_pips"] < stop) {
			t.Errorf("structural %.0f: room to the extreme %.1f pips is not below the %.1f-pip stop", structural, q["extreme_room_pips"], stop)
		}
		if !(q["available_room_pips"] > q["extreme_room_pips"]) {
			t.Errorf("structural %.0f: the available room %.1f must run beyond the extreme %.1f", structural, q["available_room_pips"], q["extreme_room_pips"])
		}
	}
}

func TestRoomIsFiniteAndSetByTheProjection(t *testing.T) {
	// The unlimited room is gone: with no barrier at all the horizon is the projection, and a short projection leaves too little room.
	params := validParams()
	params["room_projection"] = 0.1
	st, err := New(strategy.Config{ID: ID, Version: Version, Parameters: params})
	if err != nil {
		t.Fatal(err)
	}
	s := st.(*Strategy)
	if _, why := buildFor(t, s, ctxWith(), market.Buy, 30); why != ReasonNoLadderTargetFits {
		t.Errorf("a 0.1 projection leaves 27.2 pips of room for a 30-pip stop: refusal %q, want %q", why, ReasonNoLadderTargetFits)
	}
}

func TestAnEntryInsideAnOpposingWallHasNoRoom(t *testing.T) {
	s := testStrategy(t)
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		inside := opposingWall(s, direction, 30, -1, barTime-5000) // the wall's near edge is behind the worst entry: the entry sits in it
		inside.Low, inside.High = inside.Low-5*0.1, inside.High+5*0.1
		if _, why := buildFor(t, s, ctxWith(inside), direction, 30); why != ReasonNoOpposingRoom {
			t.Errorf("%s inside a wall: refusal %q, want %q (no same-wall exemption)", direction, why, ReasonNoOpposingRoom)
		}
	}
}

func TestTheImpulsesOwnStructureAndTheFutureAreNotBarriers(t *testing.T) {
	s := testStrategy(t)
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		older := opposingWall(s, direction, 30, 33, barTime-5000)
		born := opposingWall(s, direction, 30, 33, barTime) // born of the impulse bar itself
		future := opposingWall(s, direction, 30, 33, barTime+900)
		if _, why := buildFor(t, s, ctxWith(older), direction, 30); why != ReasonNoLadderTargetFits {
			t.Errorf("%s: an older wall 33 pips ahead must leave too little room, got %q", direction, why)
		}
		for name, z := range map[string]zone.Zone{"born of the impulse": born, "from the future": future} {
			if _, why := buildFor(t, s, ctxWith(z), direction, 30); why != "" {
				t.Errorf("%s: a wall %s limited the room (%q)", direction, name, why)
			}
		}
	}
}

func TestRepeatedAndStaleBarriers(t *testing.T) {
	s := testStrategy(t)
	wall := opposingWall(s, market.Buy, 30, 50, barTime-5000)
	ctx := ctxWith(wall)
	ctx.Timeframes[market.M5].Zones.Zones = []zone.Zone{wall}
	ctx.Timeframes[market.H1].Zones.Zones = []zone.Zone{wall}
	cand, why := buildFor(t, s, ctx, market.Buy, 30)
	if why != "" {
		t.Fatalf("three views of one wall: refusal %q", why)
	}
	if got := cand.Quality.Components["barrier_room_pips"]; math.Abs(got-45.5) > 1e-6 {
		t.Errorf("three views of one wall: barrier room %.3f, want one wall at 45.5 after the buffer", got)
	}
	stale := wall
	stale.State = zone.StateMitigated
	cand, why = buildFor(t, s, ctxWith(stale), market.Buy, 30)
	if why != "" || cand.Quality.Components["room_limited_by_barrier"] != 0 {
		t.Errorf("a mitigated wall must not limit the room (refusal %q)", why)
	}
}

func TestTheStructuralStopCapIsUnchanged(t *testing.T) {
	s := testStrategy(t)
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		for _, c := range []struct {
			structural, wantStop float64
			wantWhy              string
		}{{24, 30, ""}, {39, 39, ""}, {40, 40, ""}, {40.5, 0, ReasonStopOverCap}, {48, 0, ReasonStopOverCap}} {
			cand, why := buildFor(t, s, ctxWith(), direction, c.structural)
			if why != c.wantWhy {
				t.Errorf("%s %.1f: refusal %q, want %q", direction, c.structural, why, c.wantWhy)
				continue
			}
			if c.wantStop == 0 {
				continue
			}
			if got := math.Abs(float64(cand.Invalidation.Price)-worstEntry(cand, direction)) / s.cfg.PipSize; math.Abs(got-c.wantStop) > 1e-6 {
				t.Errorf("%s %.1f: stop %.4f, want %.0f", direction, c.structural, got, c.wantStop)
			}
		}
	}
}

func TestQualityDimensionsAreFiniteBoundedAndTheOverallIsTheirMean(t *testing.T) {
	s := testStrategy(t)
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		for _, barrier := range []float64{0, 50, 40} {
			var zones []zone.Zone
			if barrier > 0 {
				zones = append(zones, opposingWall(s, direction, 33, barrier, barTime-5000))
			}
			cand, why := buildFor(t, s, ctxWith(zones...), direction, 33)
			if why != "" {
				t.Fatalf("%s barrier %v refused: %s", direction, barrier, why)
			}
			q := cand.Quality
			if err := quality.Verify(q.Overall, q.Components); err != nil {
				t.Errorf("%s barrier %v: %v", direction, barrier, err)
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
			if n != 5 || math.Abs(q.Overall-sum/float64(n)) > 1e-9 {
				t.Errorf("%s: overall %v is not the mean %v of the five dimensions (%d found)", direction, q.Overall, sum/float64(n), n)
			}
		}
	}
}

func TestAStrongImpulseWithLittleRoomIsNotExcellent(t *testing.T) {
	s := testStrategy(t)
	plenty, _ := buildFor(t, s, ctxWith(), market.Buy, 33)
	tight, why := buildFor(t, s, ctxWith(opposingWall(s, market.Buy, 33, 38, barTime-5000)), market.Buy, 33)
	if why != "" {
		t.Fatalf("refused: %s", why)
	}
	if got := tight.Quality.Components["score_impulse_strength"]; got != 1 {
		t.Fatalf("the impulse must be strong for this test, impulse_strength = %v", got)
	}
	if tight.Quality.Components["score_room_reward_risk"] >= plenty.Quality.Components["score_room_reward_risk"] || !(tight.Quality.Overall < plenty.Quality.Overall) {
		t.Errorf("room scores %v vs %v, overall %v vs %v: the same impulse with less room must score lower",
			tight.Quality.Components["score_room_reward_risk"], plenty.Quality.Components["score_room_reward_risk"], tight.Quality.Overall, plenty.Quality.Overall)
	}
	if tight.Quality.Components["score_room_reward_risk"] > 0.1 || tight.Quality.Overall > 0.85 {
		t.Errorf("a strong impulse with barely 1R of room scored room %v overall %v: not an excellent setup", tight.Quality.Components["score_room_reward_risk"], tight.Quality.Overall)
	}
}

func TestASyntheticRungDoesNotCreateRoom(t *testing.T) {
	s := testStrategy(t)
	// Both select the 1R rung; the room score follows the room, not the rung.
	small, _ := buildFor(t, s, ctxWith(opposingWall(s, market.Buy, 33, 38, barTime-5000)), market.Buy, 33)
	larger, _ := buildFor(t, s, ctxWith(opposingWall(s, market.Buy, 33, 60, barTime-5000)), market.Buy, 33)
	if small.Quality.Components["target_r"] != larger.Quality.Components["target_r"] {
		t.Fatal("both cases must select the same rung")
	}
	if !(small.Quality.Components["score_room_reward_risk"] < larger.Quality.Components["score_room_reward_risk"]) {
		t.Error("the same rung with more room must score higher on room")
	}
}

func TestCandleConfirmationSwitchDoesNotChangeAnImpulse(t *testing.T) {
	var got [2]opportunity.Candidate
	for i, required := range []bool{true, false} {
		p := validParams()
		p["require_candle_confirmation"] = required
		st, err := New(strategy.Config{ID: ID, Version: Version, Parameters: p})
		if err != nil {
			t.Fatal(err)
		}
		cand, why := buildFor(t, st.(*Strategy), ctxWith(), market.Buy, 33)
		if why != "" {
			t.Fatalf("require_candle_confirmation=%v refused: %s", required, why)
		}
		got[i] = cand
	}
	if got[0].Targets[0].Price != got[1].Targets[0].Price || got[0].Quality.Overall != got[1].Quality.Overall || got[0].Entry != got[1].Entry {
		t.Error("the candle confirmation switch changed Impulse Retrace: it has no confirmation candle and a pending entry must not wait for one")
	}
}

func TestTheBarrierPolicyAndProjectionAreRequired(t *testing.T) {
	if _, err := parseConfig(map[string]any{"pip_size": 0.1}); err == nil {
		t.Error("a configuration without the room parameters must fail closed")
	}
}
