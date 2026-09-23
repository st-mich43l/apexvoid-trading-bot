package session_test

import (
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
)

// sessionTestConfig mirrors config/analysis.yml's real production values
// (analysis.sessions.{asia,london,ny}_start / .daily_rollover_utc_hour),
// not invented numbers.
func sessionTestConfig() session.Config {
	return session.Config{AsiaStartHour: 22, LondonStartHour: 7, NYStartHour: 13, DailyRolloverUTCHour: 21}
}

// ts builds a UTC timestamp `hour` hours into day `day` after 2024-01-01
// (a Monday) — a fixed, readable anchor so every fixture's weekday/hour
// is easy to reason about without a separate epoch-math comment per case.
func ts(day, hour int) int64 {
	return time.Date(2024, 1, 1+day, hour, 0, 0, 0, time.UTC).Unix()
}

// candle builds a fixture bar — only Time/High/Low matter to this
// package, so Open/Close just sit at the midpoint.
func candle(day, hour int, high, low float64) market.Candle {
	mid := (high + low) / 2
	return market.Candle{Time: ts(day, hour), Open: mid, High: high, Low: low, Close: mid, Volume: 1}
}

func levelsNamed(levels []session.Level, name string) []session.Level {
	var out []session.Level
	for _, l := range levels {
		if l.Name == name {
			out = append(out, l)
		}
	}
	return out
}

// TestSessionExtremesReportsTwoMostRecentClosedOccurrencesAndCrossSessionSweeps
// ports session_liquidity.py::_session_extremes' two central rules: only
// the two most recently CLOSED occurrences of a window are reported (an
// older third occurrence is dropped), and porting _swept_ts's own scan —
// sweep detection runs over ALL later candles, not just the same
// session's own future occurrences, so a later session's own extreme can
// sweep an earlier one's level.
func TestSessionExtremesReportsTwoMostRecentClosedOccurrencesAndCrossSessionSweeps(t *testing.T) {
	candles := []market.Candle{
		candle(-1, 22, 200, 198), // oldest Asia occurrence — must be dropped by the two-most-recent cap
		candle(0, 22, 100, 98),   // run1 start
		candle(1, 1, 103, 95),    // run1 ASIA_L candidate (low=95)
		candle(1, 3, 105, 99),    // run1 ASIA_H candidate (high=105)
		candle(1, 10, 110, 105),  // London-hour candle: sweeps run1's ASIA_H (110 > 105)
		candle(1, 22, 108, 100),  // run2 start
		candle(2, 1, 130, 110),   // run2 ASIA_H candidate (high=130)
		candle(2, 2, 115, 80),    // run2 ASIA_L candidate (low=80) — also sweeps run1's ASIA_L (80 < 95)
		candle(2, 7, 100, 95),    // closes run2 (its close time is exactly day2 07:00 UTC)
	}

	state := session.Update(candles, sessionTestConfig())

	highs := levelsNamed(state.Levels, "ASIA_H")
	lows := levelsNamed(state.Levels, "ASIA_L")
	if len(highs) != 2 || len(lows) != 2 {
		t.Fatalf("expected exactly 2 ASIA_H and 2 ASIA_L (two most recent closed occurrences only), got %d/%d: %+v", len(highs), len(lows), state.Levels)
	}
	for _, h := range highs {
		if h.Price == 200 {
			t.Fatal("the oldest (third) Asia occurrence must be dropped by the two-most-recent-closed cap")
		}
	}

	run1High := findByPrice(t, highs, 105)
	if run1High.Time != ts(1, 3) {
		t.Errorf("run1 ASIA_H.Time = %d, want the hour-3 candle at %d", run1High.Time, ts(1, 3))
	}
	if !run1High.Swept || run1High.SweptAt == nil || *run1High.SweptAt != ts(1, 10) {
		t.Errorf("run1 ASIA_H should be swept by the London-hour 110 candle at %d, got swept=%v at %+v", ts(1, 10), run1High.Swept, run1High.SweptAt)
	}

	run1Low := findByPrice(t, lows, 95)
	if !run1Low.Swept || run1Low.SweptAt == nil || *run1Low.SweptAt != ts(2, 2) {
		t.Errorf("run1 ASIA_L should be swept by run2's own 80-low candle at %d (sweep scans ALL later candles, not just the same window), got swept=%v at %+v", ts(2, 2), run1Low.Swept, run1Low.SweptAt)
	}

	run2High := findByPrice(t, highs, 130)
	if run2High.Swept {
		t.Errorf("run2 ASIA_H has nothing later that exceeds it, must not be swept: %+v", run2High)
	}

	run2Low := findByPrice(t, lows, 80)
	if run2Low.Swept {
		t.Errorf("run2 ASIA_L has nothing later that undercuts it, must not be swept: %+v", run2Low)
	}
}

func findByPrice(t *testing.T, levels []session.Level, price float64) session.Level {
	t.Helper()
	for _, l := range levels {
		if float64(l.Price) == price {
			return l
		}
	}
	t.Fatalf("no level with price %v found in %+v", price, levels)
	return session.Level{}
}

// TestSessionExtremesExcludesAStillFormingOccurrence: an occurrence whose
// window has not yet closed as of the latest candle must not be reported
// at all — session_liquidity.py's `if close_ts > last_ts: continue`.
func TestSessionExtremesExcludesAStillFormingOccurrence(t *testing.T) {
	candles := []market.Candle{
		candle(0, 22, 100, 98),
		candle(1, 3, 105, 95), // last candle — well before this Asia occurrence's 07:00 close
	}
	state := session.Update(candles, sessionTestConfig())
	if len(levelsNamed(state.Levels, "ASIA_H")) != 0 || len(levelsNamed(state.Levels, "ASIA_L")) != 0 {
		t.Fatalf("a still-forming session occurrence must not be reported, got %+v", state.Levels)
	}
}

// TestPreviousDayLevelsRequireTwoDistinctTradingDays ports
// _previous_day_levels: PDH/PDL are only emitted once a SECOND distinct
// trading day has begun (the current one), and describe the day before
// it — never the still-open current trading day.
func TestPreviousDayLevelsRequireTwoDistinctTradingDays(t *testing.T) {
	day0Low := candle(0, 10, 50, 35)  // trading day 0 (hour 10 < rollover 21) — PDL candidate
	day0High := candle(0, 15, 60, 40) // trading day 0 — PDH candidate

	t.Run("single trading day yields nothing", func(t *testing.T) {
		state := session.Update([]market.Candle{day0Low, day0High}, sessionTestConfig())
		if len(levelsNamed(state.Levels, "PDH")) != 0 || len(levelsNamed(state.Levels, "PDL")) != 0 {
			t.Fatalf("only one trading day exists, PDH/PDL must be empty, got %+v", state.Levels)
		}
	})

	t.Run("a second trading day reveals the previous day's levels", func(t *testing.T) {
		day1 := candle(1, 5, 10, 5) // begins trading day 1 (hour 5 < rollover 21, so trading day 1 not 2)
		state := session.Update([]market.Candle{day0Low, day0High, day1}, sessionTestConfig())

		pdh := levelsNamed(state.Levels, "PDH")
		pdl := levelsNamed(state.Levels, "PDL")
		if len(pdh) != 1 || len(pdl) != 1 {
			t.Fatalf("expected exactly one PDH and one PDL, got %+v", state.Levels)
		}
		if pdh[0].Price != 60 || pdh[0].Time != ts(0, 15) {
			t.Errorf("PDH = %+v, want price=60 time=%d", pdh[0], ts(0, 15))
		}
		if pdl[0].Price != 35 || pdl[0].Time != ts(0, 10) {
			t.Errorf("PDL = %+v, want price=35 time=%d", pdl[0], ts(0, 10))
		}
	})
}

// TestPreviousWeekLevelsRequireHistoryReachingTheWeekStart ports
// previous_week_levels' own guard (`frame.index[0] > previous_week:
// return []`) — data existing INSIDE the previous week's date range is
// not enough; the earliest candle in the whole series must reach back to
// (or before) that week's own Monday 00:00 UTC start, or PWH/PWL are
// withheld entirely rather than computed from a partial week.
func TestPreviousWeekLevelsRequireHistoryReachingTheWeekStart(t *testing.T) {
	t.Run("history starting mid-week withholds PWH/PWL even though in-window data exists", func(t *testing.T) {
		candles := []market.Candle{
			candle(10, 1, 10, 5), // inside what would be the previous week, but not at its start
			candle(15, 5, 20, 1), // current week (weekStart(day15) = day14, a Monday)
		}
		state := session.Update(candles, sessionTestConfig())
		if len(levelsNamed(state.Levels, "PWH")) != 0 || len(levelsNamed(state.Levels, "PWL")) != 0 {
			t.Fatalf("history must reach the previous week's own start to report PWH/PWL, got %+v", state.Levels)
		}
	})

	t.Run("history reaching the week start reports PWH/PWL with cross-window sweep", func(t *testing.T) {
		candles := []market.Candle{
			candle(6, 20, 1, 1),    // day6 (Sunday) — before day7 (the previous week's Monday start): satisfies the guard, itself outside the window
			candle(8, 3, 70, 50),   // inside the previous week — PWL candidate (low=50)
			candle(12, 10, 90, 55), // inside the previous week — PWH candidate (high=90), and the week's LAST candle (closedAt)
			candle(15, 5, 20, 1),   // current week — sweeps PWL (1 < 50) but not PWH (20 < 90)
		}
		state := session.Update(candles, sessionTestConfig())

		pwh := levelsNamed(state.Levels, "PWH")
		pwl := levelsNamed(state.Levels, "PWL")
		if len(pwh) != 1 || len(pwl) != 1 {
			t.Fatalf("expected exactly one PWH and one PWL, got %+v", state.Levels)
		}
		if pwh[0].Price != 90 || pwh[0].Time != ts(12, 10) || pwh[0].Swept {
			t.Errorf("PWH = %+v, want price=90 time=%d swept=false", pwh[0], ts(12, 10))
		}
		if pwl[0].Price != 50 || pwl[0].Time != ts(8, 3) || !pwl[0].Swept || pwl[0].SweptAt == nil || *pwl[0].SweptAt != ts(15, 5) {
			t.Errorf("PWL = %+v, want price=50 time=%d swept=true sweptAt=%d", pwl[0], ts(8, 3), ts(15, 5))
		}
	})
}

// TestActiveSessionClassifiesLatestBarByUTCHour: the one piece of this
// package with no Python equivalent — see doc.go. windows() always
// partitions the full 24h UTC day, so every hour resolves to exactly one
// of Asia/London/NY.
func TestActiveSessionClassifiesLatestBarByUTCHour(t *testing.T) {
	cases := []struct {
		hour int
		want string
	}{
		{23, session.Asia},   // 22:00-07:00 wraps past midnight
		{10, session.London}, // 07:00-13:00
		{15, session.NY},     // 13:00-22:00
	}
	for _, tc := range cases {
		state := session.Update([]market.Candle{candle(0, tc.hour, 100, 99)}, sessionTestConfig())
		if state.Active != tc.want {
			t.Errorf("hour %d: Active = %q, want %q", tc.hour, state.Active, tc.want)
		}
	}
}

func TestUpdateWithNoCandlesReturnsEmptyState(t *testing.T) {
	state := session.Update(nil, sessionTestConfig())
	if len(state.Levels) != 0 || state.Active != "" {
		t.Fatalf("expected a zero-value State for no candles, got %+v", state)
	}
}
