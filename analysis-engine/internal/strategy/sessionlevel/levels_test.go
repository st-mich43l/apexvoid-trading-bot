package sessionlevel

import (
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func utc(y int, mo time.Month, d, h, mi int) int64 {
	return time.Date(y, mo, d, h, mi, 0, 0, time.UTC).Unix()
}

// flatM5 writes dense M5 candles in [from, to) around price with a 2.0 range, then
// lets the caller punch holes in it.
func flatM5(from, to int64, price float64) []market.Candle {
	var out []market.Candle
	for t := from; t < to; t += 300 {
		out = append(out, market.Candle{Time: t, Open: price, High: price + 1, Low: price - 1, Close: price})
	}
	return out
}

// cut removes the candles opening in [from, to).
func cut(bars []market.Candle, from, to int64) []market.Candle {
	var out []market.Candle
	for _, c := range bars {
		if c.Time < from || c.Time >= to {
			out = append(out, c)
		}
	}
	return out
}

func levelNamed(levels []Level, name string) (Level, bool) {
	for _, l := range levels {
		if l.Name == name {
			return l, true
		}
	}
	return Level{}, false
}

func rejectedReason(rejected []Rejected, name string) string {
	for _, r := range rejected {
		if r.Name == name {
			return r.Reason
		}
	}
	return ""
}

func TestACompleteSessionBuildsLevelsWithTheExactPeriod(t *testing.T) {
	cfg := testConfig()
	m5 := flatM5(utc(2025, 10, 14, 22, 0), utc(2025, 10, 15, 13, 0), 4000)
	// Distinct extremes inside London.
	for i := range m5 {
		switch m5[i].Time {
		case utc(2025, 10, 15, 9, 15):
			m5[i].High = 4030
		case utc(2025, 10, 15, 11, 40):
			m5[i].Low = 3970
		}
	}
	levels, rejected := buildLevels(cfg, m5, nil, utc(2025, 10, 15, 13, 0))
	h, ok := levelNamed(levels, "LONDON_H")
	l, _ := levelNamed(levels, "LONDON_L")
	if !ok || h.Price != 4030 || l.Price != 3970 {
		t.Fatalf("levels = %+v rejected %+v", levels, rejected)
	}
	if h.PeriodStart != utc(2025, 10, 15, 7, 0) || h.PeriodEnd != utc(2025, 10, 15, 13, 0) || h.AvailableAt != h.PeriodEnd {
		t.Errorf("london period = %d..%d available %d", h.PeriodStart, h.PeriodEnd, h.AvailableAt)
	}
	if h.FormedAt != utc(2025, 10, 15, 9, 15) || l.FormedAt != utc(2025, 10, 15, 11, 40) || h.Candles != 72 || h.Coverage != 1 {
		t.Errorf("provenance high %+v low %+v", h, l)
	}
	asiaH, _ := levelNamed(levels, "ASIA_H")
	if asiaH.PeriodStart != utc(2025, 10, 14, 22, 0) || asiaH.PeriodEnd != utc(2025, 10, 15, 7, 0) {
		t.Errorf("asia crosses UTC midnight: %d..%d", asiaH.PeriodStart, asiaH.PeriodEnd)
	}
	if h.ID == l.ID || h.ID == asiaH.ID {
		t.Errorf("ids must be distinct: %q %q %q", h.ID, l.ID, asiaH.ID)
	}
}

func TestAPeriodTheCandlesDoNotCoverIsNotALevel(t *testing.T) {
	cfg := testConfig()
	now := utc(2025, 10, 15, 13, 0)
	full := flatM5(utc(2025, 10, 14, 22, 0), now, 4000)
	for _, tc := range []struct {
		name   string
		bars   []market.Candle
		reason string
	}{
		{"the session start is missing", cut(full, utc(2025, 10, 15, 7, 0), utc(2025, 10, 15, 9, 0)), ReasonStartIncomplete},
		{"the session end is missing", cut(full, utc(2025, 10, 15, 11, 0), utc(2025, 10, 15, 13, 0)), ReasonEndIncomplete},
		{"a hole inside the session", cut(full, utc(2025, 10, 15, 9, 0), utc(2025, 10, 15, 10, 30)), ReasonFeedGap},
		{"holes that each pass the gap rule but leave too little", cut(cut(cut(full, utc(2025, 10, 15, 8, 0), utc(2025, 10, 15, 9, 0)), utc(2025, 10, 15, 10, 0), utc(2025, 10, 15, 11, 0)), utc(2025, 10, 15, 11, 30), utc(2025, 10, 15, 12, 30)), ReasonLowCoverage},
	} {
		t.Run(tc.name, func(t *testing.T) {
			levels, rejected := buildLevels(cfg, tc.bars, nil, now)
			if _, ok := levelNamed(levels, "LONDON_H"); ok {
				t.Fatal("an incomplete period produced a level")
			}
			if got := rejectedReason(rejected, "LONDON"); got != tc.reason {
				t.Fatalf("reason = %q, want %q (%+v)", got, tc.reason, rejected)
			}
			if _, ok := levelNamed(levels, "ASIA_H"); !ok {
				t.Fatal("an unrelated complete session must still build")
			}
		})
	}
}

// The tolerances exist for real feeds: the daily 21:00-22:00 UTC break and a late
// first candle on a Sunday open do not make a session incomplete.
func TestTheDailyBreakAndALateOpenDoNotMakeASessionIncomplete(t *testing.T) {
	cfg := testConfig()
	now := utc(2025, 10, 15, 22, 0)
	ny := cut(flatM5(utc(2025, 10, 15, 13, 0), now, 4000), utc(2025, 10, 15, 21, 0), now)
	levels, rejected := buildLevels(cfg, ny, nil, now)
	if l, ok := levelNamed(levels, "NY_H"); !ok || l.Candles != 96 {
		t.Fatalf("NY with the 21:00 break: %+v %+v", levels, rejected)
	}
	late := cut(flatM5(utc(2025, 10, 14, 22, 0), utc(2025, 10, 15, 7, 0), 4000), utc(2025, 10, 14, 22, 0), utc(2025, 10, 14, 23, 0))
	levels, rejected = buildLevels(cfg, late, nil, utc(2025, 10, 15, 7, 0))
	if _, ok := levelNamed(levels, "ASIA_H"); !ok {
		t.Fatalf("Asia opening one hour late: %+v", rejected)
	}
}

func TestOnlyTheLatestCompletedOccurrenceIsALevelAndNeverAFallback(t *testing.T) {
	cfg := testConfig()
	now := utc(2025, 10, 16, 14, 0)
	m5 := flatM5(utc(2025, 10, 14, 22, 0), now, 4000)
	for i := range m5 {
		if m5[i].Time == utc(2025, 10, 15, 9, 0) { // yesterday's London: a different, older extreme
			m5[i].High = 4100
		}
		if m5[i].Time == utc(2025, 10, 16, 9, 0) {
			m5[i].High = 4050
		}
	}
	levels, _ := buildLevels(cfg, m5, nil, now)
	if l, ok := levelNamed(levels, "LONDON_H"); !ok || l.Price != 4050 || l.PeriodStart != utc(2025, 10, 16, 7, 0) {
		t.Fatalf("london high = %+v, want today's 4050", l)
	}
	// Today's London is incomplete: yesterday's (26 h old, inside the age limit)
	// must NOT take its place.
	broken := cut(m5, utc(2025, 10, 16, 9, 0), utc(2025, 10, 16, 11, 0))
	levels, rejected := buildLevels(cfg, broken, nil, now)
	if _, ok := levelNamed(levels, "LONDON_H"); ok {
		t.Fatalf("an older London level stood in for the incomplete newer one: %+v", levels)
	}
	if got := rejectedReason(rejected, "LONDON"); got != ReasonFeedGap {
		t.Fatalf("reason = %q", got)
	}
}

func TestAStaleLevelExpires(t *testing.T) {
	cfg := testConfig()
	cfg.SessionMaxAgeHours = 10
	m5 := flatM5(utc(2025, 10, 14, 22, 0), utc(2025, 10, 16, 0, 0), 4000)
	// London of the 15th completed at 13:00; ten hours later is the limit.
	if levels, _ := buildLevels(cfg, m5, nil, utc(2025, 10, 15, 22, 55)); levelCount(levels, "LONDON_H") != 1 {
		t.Fatal("the London level must still be current 9h55 after it completed")
	}
	levels, rejected := buildLevels(cfg, m5, nil, utc(2025, 10, 15, 23, 5))
	if levelCount(levels, "LONDON_H") != 0 || rejectedReason(rejected, "LONDON") != ReasonTooOld {
		t.Fatalf("a level older than session_max_age_hours was kept: %+v %+v", levels, rejected)
	}
}

func levelCount(levels []Level, name string) int {
	n := 0
	for _, l := range levels {
		if l.Name == name {
			n++
		}
	}
	return n
}

func TestThePreviousTradingDayIsDefinedByTheRolloverHour(t *testing.T) {
	cfg := testConfig()
	now := utc(2025, 10, 15, 3, 0) // Wednesday, 03:00 UTC
	h1 := hourly(utc(2025, 10, 13, 22, 0), 53, 4000, breakHour)
	h1 = withSpike(h1, utc(2025, 10, 14, 15, 0), 4200, 3900) // inside Tuesday's trading day
	h1 = withSpike(h1, utc(2025, 10, 14, 22, 0), 4300, 3800) // the first hour of WEDNESDAY's trading day
	h1 = withSpike(h1, utc(2025, 10, 14, 20, 0), 0, 3950)    // the last hour of Tuesday's
	h1 = withSpike(h1, utc(2025, 10, 13, 20, 0), 4250, 0)    // MONDAY's trading day
	levels, rejected := buildLevels(cfg, nil, h1, now)
	h, okH := levelNamed(levels, "PDH")
	l, okL := levelNamed(levels, "PDL")
	if !okH || !okL {
		t.Fatalf("PD levels missing: %+v %+v", levels, rejected)
	}
	if h.Price != 4200 || l.Price != 3900 {
		t.Fatalf("PDH %v PDL %v, want Tuesday's trading-day extremes 4200 / 3900", h.Price, l.Price)
	}
	if h.PeriodStart != utc(2025, 10, 13, 21, 0) || h.PeriodEnd != utc(2025, 10, 14, 21, 0) || h.AvailableAt != h.PeriodEnd || h.SourceTF != market.H1 {
		t.Fatalf("PD period = %d..%d tf %v", h.PeriodStart, h.PeriodEnd, h.SourceTF)
	}
	if h.FormedAt != utc(2025, 10, 14, 15, 0) || h.Kind != KindDay {
		t.Fatalf("PDH provenance = %+v", h)
	}
}

func TestOnAMondayThePreviousTradingDayIsFridayAndSundayEveningIsMondays(t *testing.T) {
	cfg := testConfig()
	now := utc(2025, 10, 20, 5, 0)                              // Monday
	h1 := hourly(utc(2025, 10, 16, 21, 0), 24, 4000, breakHour) // Thursday 21:00 -> Friday 21:00
	h1 = append(h1, hourly(utc(2025, 10, 19, 22, 0), 7, 4000, breakHour)...)
	h1 = withSpike(h1, utc(2025, 10, 17, 3, 0), 4150, 3920)  // Friday
	h1 = withSpike(h1, utc(2025, 10, 19, 23, 0), 4400, 3500) // Sunday evening: Monday's trading day
	levels, rejected := buildLevels(cfg, nil, h1, now)
	h, ok := levelNamed(levels, "PDH")
	if !ok || h.Price != 4150 {
		t.Fatalf("PDH = %+v rejected %+v, want Friday's 4150", h, rejected)
	}
	if h.PeriodEnd != utc(2025, 10, 17, 21, 0) {
		t.Fatalf("the previous trading day ended %d", h.PeriodEnd)
	}
}

func TestADayOrWeekTheHourlyCandlesDoNotCoverIsNotALevel(t *testing.T) {
	cfg := testConfig()
	now := utc(2025, 10, 15, 3, 0)
	full := hourly(utc(2025, 10, 13, 22, 0), 53, 4000, breakHour)
	for _, tc := range []struct {
		name         string
		from, to     int64
		wantFeedGap  bool
		wantStartEnd string
	}{
		{"ten hours missing in the middle of Tuesday", utc(2025, 10, 14, 6, 0), utc(2025, 10, 14, 16, 0), true, ""},
		{"the first half of Tuesday is missing", utc(2025, 10, 13, 22, 0), utc(2025, 10, 14, 12, 0), false, ReasonStartIncomplete},
	} {
		t.Run(tc.name, func(t *testing.T) {
			bars := cutH1(full, tc.from, tc.to)
			levels, rejected := buildLevels(cfg, nil, bars, now)
			if levelCount(levels, "PDH") != 0 {
				t.Fatalf("an incomplete day produced PDH: %+v", levels)
			}
			got := rejectedReason(rejected, "PD")
			want := tc.wantStartEnd
			if tc.wantFeedGap {
				want = ReasonFeedGap
			}
			if got != want {
				t.Fatalf("reason = %q, want %q", got, want)
			}
		})
	}
	if levels, rejected := buildLevels(cfg, nil, nil, now); levelCount(levels, "PDH") != 0 || rejectedReason(rejected, "PD") != ReasonNoHigherTF {
		t.Fatalf("without hourly candles: %+v %+v", levels, rejected)
	}
}

// weekendOrBreak is the XAU feed: closed from Friday 21:00 to Sunday 22:00 UTC and
// for the daily 21:00-22:00 break.
func weekendOrBreak(t int64) bool {
	u := time.Unix(t, 0).UTC()
	switch {
	case u.Hour() == 21:
		return true
	case u.Weekday() == time.Saturday, u.Weekday() == time.Friday && u.Hour() >= 21, u.Weekday() == time.Sunday && u.Hour() < 22:
		return true
	}
	return false
}

func cutH1(bars []market.Candle, from, to int64) []market.Candle { return cut(bars, from, to) }

func TestThePreviousTradingWeekIsTheLatestCompletedISOWeek(t *testing.T) {
	cfg := testConfig()
	now := utc(2025, 10, 15, 3, 0) // Wednesday of ISO week 42
	h1 := hourly(utc(2025, 10, 5, 22, 0), 24*10, 4000, weekendOrBreak)
	h1 = withSpike(h1, utc(2025, 10, 8, 12, 0), 4500, 3600)  // Wednesday of the previous week
	h1 = withSpike(h1, utc(2025, 10, 12, 22, 0), 4800, 3200) // Sunday evening: THIS week's trading week
	levels, rejected := buildLevels(cfg, nil, h1, now)
	h, ok := levelNamed(levels, "PWH")
	l, _ := levelNamed(levels, "PWL")
	if !ok || h.Price != 4500 || l.Price != 3600 {
		t.Fatalf("PWH %v PWL %v rejected %+v", h.Price, l.Price, rejected)
	}
	if h.PeriodStart != utc(2025, 10, 5, 21, 0) || h.PeriodEnd != utc(2025, 10, 10, 21, 0) || h.Kind != KindWeek {
		t.Fatalf("PW period = %d..%d", h.PeriodStart, h.PeriodEnd)
	}
}

// An hourly candle that has not closed at the evaluated M5 candle's close is not
// known yet, however much the capture holds.
func TestAnHourlyCandleThatHasNotClosedIsNeverRead(t *testing.T) {
	cfg := testConfig()
	m5 := flatM5(utc(2025, 10, 15, 6, 0), utc(2025, 10, 15, 8, 5), 4000) // evaluated at 08:00, closing 08:05
	h1 := []market.Candle{
		{Time: utc(2025, 10, 15, 6, 0), Open: 1, High: 2, Low: 0.5, Close: 1},
		{Time: utc(2025, 10, 15, 7, 0), Open: 1, High: 2, Low: 0.5, Close: 1},
		{Time: utc(2025, 10, 15, 8, 0), Open: 1, High: 2, Low: 0.5, Close: 1}, // closes 09:00: still forming
	}
	_, got, _ := prepare(cfg, Input{M5: m5, H1: h1}, len(m5)-1)
	if len(got) != 2 || got[len(got)-1].Time != utc(2025, 10, 15, 7, 0) {
		t.Fatalf("closed hourly candles = %+v", got)
	}
}
