package slreplay_test

import (
	"math"
	"strconv"
	"strings"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/sessionlevel"
)

// This file is an INDEPENDENT audit of the levels a frozen v3 setup traded, written
// from the definitions in docs/strategies/session_level.md and sharing no code with
// the production detector, so a bug in one cannot hide a bug in the other.

type levelRef struct {
	Name       string
	Price      float64
	Resistance bool
}

// parseV2 reads "session:NAME:price" off the frozen v3 candidate's structural id.
func parseV2(c opportunity.Candidate) (levelRef, bool) {
	parts := strings.Split(c.StructuralID, ":")
	if len(parts) < 3 || parts[0] != "session" {
		return levelRef{}, false
	}
	price, err := strconv.ParseFloat(parts[2], 64)
	if err != nil {
		return levelRef{}, false
	}
	name := parts[1]
	return levelRef{Name: name, Price: price, Resistance: strings.HasSuffix(name, "_H") || name == "PDH" || name == "PWH"}, true
}

// formedBy finds the candle that made the level's extreme: the latest candle at or
// before index i whose high (resistance) or low (support) is the level's price.
func formedBy(bars []market.Candle, i int, ref levelRef, upTo int64) (market.Candle, bool) {
	for k := i; k >= 0; k-- {
		if bars[k].Time > upTo {
			continue
		}
		v := bars[k].Low
		if ref.Resistance {
			v = bars[k].High
		}
		if math.Abs(v-ref.Price) < 1e-6 {
			return bars[k], true
		}
	}
	return market.Candle{}, false
}

func dayStart(t int64) time.Time { return time.Unix(t, 0).UTC().Truncate(24 * time.Hour) }

// periodOf is the defining period that contains the extreme formed at t.
func periodOf(cfg sessionlevel.Config, name string, t int64) (start, end int64, ok bool) {
	switch name {
	case "ASIA_H", "ASIA_L", "LONDON_H", "LONDON_L", "NY_H", "NY_L":
		var from, to int
		switch {
		case strings.HasPrefix(name, "ASIA"):
			from, to = cfg.AsiaStartHour, cfg.LondonStartHour
		case strings.HasPrefix(name, "LONDON"):
			from, to = cfg.LondonStartHour, cfg.NYStartHour
		default:
			from, to = cfg.NYStartHour, cfg.AsiaStartHour
		}
		length := int64((to-from+24)%24) * 3600
		for back := -1; back <= 2; back++ {
			s := dayStart(t).AddDate(0, 0, -back).Add(time.Duration(from) * time.Hour).Unix()
			if t >= s && t < s+length {
				return s, s + length, true
			}
		}
	case "PDH", "PDL":
		label := dayStart(t)
		if time.Unix(t, 0).UTC().Hour() >= cfg.RolloverHour {
			label = label.AddDate(0, 0, 1)
		}
		end = label.Add(time.Duration(cfg.RolloverHour) * time.Hour).Unix()
		return end - 24*3600, end, true
	case "PWH", "PWL":
		label := dayStart(t)
		if time.Unix(t, 0).UTC().Hour() >= cfg.RolloverHour {
			label = label.AddDate(0, 0, 1)
		}
		monday := label.AddDate(0, 0, -((int(label.Weekday()) + 6) % 7))
		start = monday.Add(time.Duration(cfg.RolloverHour)*time.Hour).Unix() - 24*3600
		end = monday.AddDate(0, 0, 4).Add(time.Duration(cfg.RolloverHour) * time.Hour).Unix()
		return start, end, true
	}
	return 0, 0, false
}

func coverage(bars []market.Candle, seconds, start, end int64) float64 {
	n := 0
	for _, c := range bars {
		if c.Time >= start && c.Time+seconds <= end {
			n++
		}
	}
	return float64(n) * float64(seconds) / float64(end-start)
}

// atrBefore is the Wilder ATR as of the candle before k, over the trailing 140.
func atrBefore(bars []market.Candle, k int, length, window int) float64 {
	end := k - 1
	if end < 0 {
		return 0
	}
	start := end - window + 1
	if start < 0 {
		start = 0
	}
	series, ok := indicator.WilderATR(bars[start:end+1], length)
	if !ok {
		return 0
	}
	return series[len(series)-1]
}

// auditV2 measures the level a frozen v3 setup traded against the v4 contract, at
// the moment it was published (M5 index i).
func auditV2(cfg sessionlevel.Config, m5, h1 []market.Candle, i int, c opportunity.Candidate, a sessionlevel.Analysis) v2Fact {
	ref, ok := parseV2(c)
	if !ok {
		return v2Fact{Status: "unparsed"}
	}
	f := v2Fact{Level: ref.Name, Price: ref.Price, Touches: -1}
	now := m5[i].Time + 300

	formed, ok := formedBy(m5, i, ref, now)
	source := m5
	if !ok {
		// A previous-day or previous-week extreme can predate the M5 window.
		formed, ok = formedBy(h1, len(h1)-1, ref, now)
		source = h1
	}
	if !ok {
		f.Status = statusOf(ref, a)
		return f
	}
	f.Found, f.FormedAt = true, formed.Time
	f.AgeHours = float64(m5[i].Time-formed.Time) / 3600
	f.Status = statusOf(ref, a)

	start, end, hasPeriod := periodOf(cfg, ref.Name, formed.Time)
	f.PeriodComplete = hasPeriod
	if hasPeriod {
		seconds, min := int64(300), cfg.SessionMinCoverage
		bars := m5
		if ref.Name == "PDH" || ref.Name == "PDL" || ref.Name == "PWH" || ref.Name == "PWL" {
			seconds, min, bars = 3600, cfg.PeriodMinCoverage, h1
		}
		f.PeriodComplete = end <= now && coverage(bars, seconds, start, end) >= min
	}
	if hasPeriod && source != nil {
		f.SweptBefore, f.SweepTied, f.Touches = touchHistory(cfg, m5, i, ref, end, c.Reaction)
	}
	// A level swept before the reaction is not a defect by itself (a sweep and reclaim
	// is a model of its own); it is one when the reclaim v3 accepted is not tied to a
	// sweep that happened within the reclaim window before it.
	f.SweptUntied = f.SweptBefore && !f.SweepTied
	f.AnyDefect = f.Status != "current" || f.SweptUntied || f.AgeHours > 24 || !f.PeriodComplete
	return f
}

func statusOf(ref levelRef, a sessionlevel.Analysis) string {
	for _, l := range a.Levels {
		if l.Name != ref.Name {
			continue
		}
		if math.Abs(l.Price-ref.Price) < 1e-6 {
			return "current"
		}
		return "superseded"
	}
	family := strings.TrimSuffix(strings.TrimSuffix(ref.Name, "_H"), "_L")
	if ref.Name == "PDH" || ref.Name == "PDL" {
		family = "PD"
	}
	if ref.Name == "PWH" || ref.Name == "PWL" {
		family = "PW"
	}
	for _, r := range a.Rejected {
		if r.Name == family {
			return "incomplete:" + r.Reason
		}
	}
	return "no_current_level"
}

// touchHistory counts the distinct touches of the level since its availability up to
// the observation, and whether it was swept before the reaction's own touch. A touch
// is a candle trading to within max(2 pips, 0.1 ATR) of the level; a new one counts
// only after a close at least half an ATR away and three periods apart.
func touchHistory(cfg sessionlevel.Config, m5 []market.Candle, i int, ref levelRef, available int64, reaction *opportunity.ReactionConfirmation) (swept, tied bool, touches int) {
	if len(m5) == 0 || m5[0].Time > available {
		return false, false, -1
	}
	sign := 1.0
	if ref.Resistance {
		sign = -1
	}
	p := sign * ref.Price
	armed, last := true, -1
	var lastSweep int64
	for k := 0; k <= i; k++ {
		if m5[k].Time < available {
			continue
		}
		atr := atrBefore(m5, k, cfg.ATRLength, cfg.ATRWindowBars)
		if atr <= 0 {
			continue
		}
		low, closeP := m5[k].Low, m5[k].Close
		if ref.Resistance {
			low, closeP = -m5[k].High, -m5[k].Close
		}
		tol := math.Max(cfg.TouchTolerancePips*cfg.PipSize, cfg.TouchToleranceATR*atr)
		sweep := math.Max(cfg.SweepThresholdPips*cfg.PipSize, cfg.SweepThresholdATR*atr)
		if armed && low <= p+tol && (last < 0 || k-last >= cfg.TouchSeparationBars) {
			touches++
			armed, last = false, k
		} else if !armed && closeP >= p+cfg.ApproachMinATR*atr {
			armed = true
		}
		if low < p-sweep && (reaction == nil || m5[k].Time < reaction.TouchBarTime) {
			swept = true
		}
		if low < p-sweep && reaction != nil && m5[k].Time <= reaction.ConfirmationBarTime {
			lastSweep = m5[k].Time
		}
	}
	if reaction != nil && lastSweep != 0 {
		tied = (reaction.ConfirmationBarTime-lastSweep)/300 <= int64(cfg.ReclaimMaxBars)
	}
	return swept, tied, touches
}

// auditV4 measures a published v4 setup the same independent way: its level must be
// current, its period complete, its reclaim tied to a sweep, and the touch count it
// publishes must equal the one counted here from the candles.
func auditV4(cfg sessionlevel.Config, m5, h1 []market.Candle, i int, c opportunity.Candidate, a sessionlevel.Analysis) v4Fact {
	var f v4Fact
	f.AuditedTouches = -1
	for _, s := range a.Setups {
		if s.ConfirmedAt != c.Reaction.ConfirmationBarTime || !strings.Contains(c.StructuralID, ":level:"+s.Level.Name+":") ||
			!strings.HasSuffix(c.StructuralID, ":"+s.Reaction.Model+":"+strconv.FormatInt(s.Reaction.EpisodeAt, 10)) {
			continue
		}
		ref := levelRef{Name: s.Level.Name, Price: s.Level.Price, Resistance: s.Level.Side == sessionlevel.SideResistance}
		f.Level, f.Kind, f.Model, f.PriorTouches, f.TouchesKnown = s.Level.Name, s.Level.Kind, s.Reaction.Model, s.PriorTouches, s.TouchesKnown
		f.AgeHours = float64(m5[i].Time-s.Level.AvailableAt) / 3600
		f.Status = statusOf(ref, a)
		f.PeriodComplete = s.Level.Coverage >= cfg.SessionMinCoverage
		confirmIdx := i
		for k := i; k >= 0; k-- {
			if m5[k].Time == s.ConfirmedAt {
				confirmIdx = k
				break
			}
		}
		swept, tied, touches := touchHistory(cfg, m5, confirmIdx, ref, s.Level.AvailableAt, c.Reaction)
		f.AuditedTouches = touches
		f.SweptUntied = swept && !tied && s.Reaction.Model == sessionlevel.ModelSweepReclaim
		return f
	}
	f.Status = "unmatched"
	return f
}
