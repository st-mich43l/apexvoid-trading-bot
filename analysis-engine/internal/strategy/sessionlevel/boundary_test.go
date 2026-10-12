package sessionlevel

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// A session within its end tolerance of finishing is still forming: the level
// exists only when the period has ended.
func TestASessionStillFormingIsNeverALevelEvenWhenNearlyComplete(t *testing.T) {
	cfg := testConfig()
	full := flatM5(utc(2025, 10, 14, 22, 0), utc(2025, 10, 15, 13, 0), 4000)
	for _, tc := range []struct {
		name string
		now  int64
	}{{"one hour before the end", utc(2025, 10, 15, 12, 0)}, {"five minutes before the end", utc(2025, 10, 15, 12, 55)}} {
		t.Run(tc.name, func(t *testing.T) {
			bars := cut(full, tc.now, utc(2025, 10, 15, 14, 0))
			levels, _ := buildLevels(cfg, bars, nil, tc.now)
			if levelCount(levels, "LONDON_H")+levelCount(levels, "LONDON_L") != 0 {
				t.Fatalf("a forming London session produced a level: %+v", levels)
			}
		})
	}
}

// With a continuous feed (FX) the trading day turns over at the rollover hour: at
// 21:30 the day that ended at 21:00 is already "the previous day".
func TestTheTradingDayTurnsOverAtTheRolloverHour(t *testing.T) {
	cfg := testConfig()
	h1 := hourly(utc(2025, 10, 12, 21, 0), 24*4, 1000, nil)
	h1 = withSpike(h1, utc(2025, 10, 14, 20, 0), 1100, 0) // the last hour of Tuesday's trading day
	h1 = withSpike(h1, utc(2025, 10, 14, 21, 0), 1900, 0) // the first hour of Wednesday's
	h1 = withSpike(h1, utc(2025, 10, 13, 20, 0), 1500, 0) // Monday's
	for _, tc := range []struct {
		name  string
		now   int64
		price float64
		end   int64
	}{
		{"just before the rollover", utc(2025, 10, 14, 20, 30), 1500, utc(2025, 10, 13, 21, 0)},
		{"after the rollover", utc(2025, 10, 14, 21, 30), 1100, utc(2025, 10, 14, 21, 0)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var closed []market.Candle
			for _, c := range h1 {
				if c.Time+3600 <= tc.now {
					closed = append(closed, c)
				}
			}
			levels, rejected := buildLevels(cfg, nil, closed, tc.now)
			h, ok := levelNamed(levels, "PDH")
			if !ok || h.Price != tc.price || h.PeriodEnd != tc.end {
				t.Fatalf("PDH = %+v rejected %+v, want %v ending %d", h, rejected, tc.price, tc.end)
			}
		})
	}
}

func TestTheConfirmationWindowBoundaryIsExact(t *testing.T) {
	// window 3: the candles at touch, touch+1 and touch+2 may confirm; touch+3 may not.
	bars, _ := firstTouchDay(opts{waitBars: 2})
	refused(t, analyse(bars), "LONDON_L", ModelFirstTouch, StateExpired, ReasonConfirmExpired)
}

func TestARejectionNeedsTheCloseInTheUpperPartAndAWickOrABody(t *testing.T) {
	set := func(o, h, l, c float64) []market.Candle {
		bars, _ := firstTouchDay(opts{})
		last := &bars[len(bars)-1]
		last.Open, last.High, last.Low, last.Close = o, h, l, c
		return bars
	}
	// A real lower wick but the close sits mid-range: not a rejection.
	if a := analyse(set(4105.0, 4106.6, 4104.1, 4105.4)); len(a.Setups) != 0 {
		t.Fatalf("a mid-range close confirmed a reaction: %+v", a.Setups[0])
	}
	// The close is high enough but there is neither a wick nor a body.
	if a := analyse(set(4104.5, 4106.1, 4104.1, 4105.5)); len(a.Setups) != 0 {
		t.Fatalf("a candle without wick or body confirmed a reaction: %+v", a.Setups[0])
	}
}

// With the fail buffer inside the sweep threshold, a close through the level that
// has not swept it fails the touch.
func TestAClosedThroughTouchFailsWithoutBeingASweep(t *testing.T) {
	cfg := testConfig()
	cfg.SweepThresholdPips, cfg.SweepThresholdATR = 50, 5
	bars, _ := firstTouchDay(opts{rejectClose: 4103.0, rejectOpen: 4104.4, rejectLow: 4102.9})
	a := analyseWith(cfg, bars)
	refused(t, a, "LONDON_L", ModelFirstTouch, StateReactionFailed, ReasonClosedThrough)
}

func TestAnOldTouchIsNotEvaluatedAndTheLookbackBoundaryIsExact(t *testing.T) {
	bars, _ := firstTouchDay(opts{afterBars: 47})
	if eps := episodesOf(analyse(bars), "LONDON_L", ModelFirstTouch); len(eps) != 1 {
		t.Fatalf("a touch 48 periods old must still be evaluated: %+v", eps)
	}
	bars, _ = firstTouchDay(opts{afterBars: 48})
	if eps := episodesOf(analyse(bars), "LONDON_L", ModelFirstTouch); len(eps) != 0 {
		t.Fatalf("a touch 49 periods old must not be evaluated: %+v", eps)
	}
}

func TestTheApproachLookbackBoundaryIsExact(t *testing.T) {
	cfg := testConfig()
	var bars []market.Candle
	at := dayAnchor
	for i := 0; i < 60; i++ {
		bars = append(bars, market.Candle{Time: at + int64(i)*300, Open: 4100.2, High: 4100.4, Low: 4100.0, Close: 4100.2})
	}
	l := frameLevel{Level: Level{Price: 4100.0}, price: 4100.0, known: true}
	d := newDetector(cfg, bars, nil, market.Buy)
	const touch = 50
	bars[touch-24].Close, bars[touch-24].High = 4103.0, 4103.1
	if !d.approached(l, touch, 1.0) {
		t.Fatal("a close 24 periods before the touch is inside the lookback")
	}
	bars[touch-24].Close, bars[touch-24].High = 4100.2, 4100.4
	bars[touch-25].Close, bars[touch-25].High = 4103.0, 4103.1
	if d.approached(l, touch, 1.0) {
		t.Fatal("a close 25 periods before the touch is outside the lookback")
	}
}

// A level price has already reached is no longer liquidity to aim at, one it has
// not reached yet is, and the nearest decides.
func TestOpposingLevelsAreCredibleTargetsOnlyWhileUnreached(t *testing.T) {
	cfg := testConfig()
	var bars []market.Candle
	for i := 0; i < 30; i++ {
		bars = append(bars, market.Candle{Time: dayAnchor + int64(i)*300, Open: 4104.5, High: 4105.0, Low: 4104.0, Close: 4104.5})
	}
	d := newDetector(cfg, bars, nil, market.Buy)
	d.targets = []frameTarget{{price: 4110, reachedAt: bars[5].Time, id: "reached"}, {price: 4115, reachedAt: 0, id: "open"}, {price: 4112, reachedAt: bars[28].Time, id: "reached later"}}
	price, kind, found, _ := d.target(10, 20, 4105.2, 1.0)
	if !found || kind != "level" || price != 4112 {
		t.Fatalf("target = %v (%s) found=%v, want the 4112 level that price had not reached when the reaction confirmed", price, kind, found)
	}
	d.targets = d.targets[:1]
	if _, _, found, any := d.target(10, 20, 4105.2, 1.0); found || any {
		t.Fatalf("a level price had already reached was used as a target (found=%v any=%v)", found, any)
	}
}

// The episode limit counts touches, and only when the history can show them.
func TestTheEpisodeLimitNeedsAKnownTouchHistory(t *testing.T) {
	bars, _ := sweepDay(opts{prior: 3})
	cfg := testConfig()
	cfg.MaximumPriorTouches = 5
	if a := analyseWith(cfg, bars); len(episodesOf(a, "LONDON_L", ModelSweepReclaim)) == 0 {
		t.Fatal("expected sweep episodes")
	} else {
		limited := false
		for _, ep := range episodesOf(a, "LONDON_L", ModelSweepReclaim) {
			if ep.Reason == ReasonEpisodeLimit {
				limited = true
			}
		}
		if !limited || len(a.Setups) != 0 {
			t.Fatalf("a level touched three times already must hit the episode limit: %+v", a.Episodes)
		}
	}
	// The same touches on a level whose history is unknown are not counted.
	l := Level{ID: "level:PDL:1", Name: "PDL", Kind: KindDay, Side: SideSupport, Price: 4104, AvailableAt: bars[0].Time - 3600, PeriodEnd: bars[0].Time - 3600}
	d := newDetector(cfg, bars, []Level{l}, market.Buy)
	for _, ep := range d.run() {
		if ep.Model == ModelSweepReclaim && ep.Reason == ReasonEpisodeLimit {
			t.Fatalf("the episode limit was applied to an unknown touch history: %+v", ep)
		}
	}
}

// Candidates are ranked by evidence: the freshest confirmation, then the room to
// the target, then the rejection, then the significance of the level.
func TestCandidatesAreRankedByEvidenceNotByListOrder(t *testing.T) {
	base := Setup{ConfirmedAt: 100, RewardRisk: 2, Reaction: Reaction{RejectionStrength: 0.8}, Level: Level{Kind: KindSession, ID: "b"}}
	fresher, roomier, stronger, weekly := base, base, base, base
	fresher.ConfirmedAt = 200
	roomier.RewardRisk = 3
	stronger.Reaction.RejectionStrength = 0.9
	weekly.Level.Kind = KindWeek
	for name, tc := range map[string][2]Setup{"fresher": {fresher, roomier}, "roomier": {roomier, stronger}, "stronger": {stronger, weekly}, "weekly": {weekly, base}} {
		if !better(tc[0], tc[1]) || better(tc[1], tc[0]) {
			t.Errorf("%s must outrank the other", name)
		}
	}
	other := base
	other.Level.ID = "a"
	if !better(other, base) || better(base, other) {
		t.Error("ties break by level id, so the order is total")
	}
}
