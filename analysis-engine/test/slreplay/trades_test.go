package slreplay_test

import (
	"fmt"
	"math"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/sessionlevel"
)

// losingTrade is one of the five XAU Session Level trades that lost on 2-6 Oct 2026,
// as the production plan and fill record them (read-only, 12 Oct 2026). Times are
// Unix seconds; Created is the OPEN time of the M5 candle the plan was built on.
type losingTrade struct {
	Label     string
	Direction market.Direction
	Level     string
	// FormedAt is the time the plan's structure id gives for the level's extreme.
	FormedAt   int64
	Created    int64
	FilledAt   int64
	ResultPips float64
	ExitPath   string
	EntryZone  [2]float64
	Stop       float64
	TP1        float64
}

var losingTrades = []losingTrade{
	{"2 Oct 05:20 SELL NY_H", market.Sell, "NY_H", 1790875800, 1790918400, 1790918886, -37, "manual_close", [2]float64{4184.57, 4187.79}, 4191.60, 4181.60},
	{"5 Oct 00:15 BUY NY_L", market.Buy, "NY_L", 1790867400, 1791159300, 1791168501, -36, "manual_close", [2]float64{0, 0}, 4146.64, 4156.64},
	{"6 Oct 00:55 BUY LONDON_L", market.Buy, "LONDON_L", 1791183600, 1791248100, 1791248469, -42, "full_stop", [2]float64{4147.46, 4151.42}, 4144.70, 4154.70},
	{"6 Oct 00:20 BUY ASIA_L", market.Buy, "ASIA_L", 1790903400, 1791246000, 1791250746, -43, "full_stop", [2]float64{4132.57, 4135.01}, 4129.31, 4139.31},
	{"6 Oct 05:00 BUY PDL", market.Buy, "PDL", 1791213600, 1791262800, 1791263164, -46, "full_stop", [2]float64{4121.65, 4124.65}, 4118.74, 4128.74},
}

type tradeVerdict struct {
	Trade       losingTrade
	Replayable  bool
	LevelPrice  float64
	LevelAgeH   float64
	Status      string // what the v4 level builder knew about this level then
	Complete    bool
	Swept       bool
	SweptUntied bool
	Touches     int
	V4Setup     bool
	V4Episodes  []string
	Notes       []string
	// NearestApproachATR is how close price came to the level in the 12 candles up to
	// the plan's candle, in ATR (negative: it traded through).
	NearestApproachATR float64
	// MFE and MAE are the best and worst excursion after the fill, until the stop was
	// reached or 8 hours passed, in R of the planned stop distance; AdverseBodyATR the largest candle body against
	// the position in the 6 candles after the fill.
	MFE, MAE, AdverseBodyATR float64
	FilledBeforeConfirm      bool
}

func (v tradeVerdict) String() string {
	return fmt.Sprintf("%s: replayable=%v level=%.2f age=%.1fh status=%s complete=%v swept=%v touches=%d nearest approach=%.2f ATR; after the fill MFE %.2fR MAE %.2fR largest adverse body %.2f ATR; filled before confirmation=%v; v4_setup=%v episodes=%v notes=%v",
		v.Trade.Label, v.Replayable, v.LevelPrice, v.LevelAgeH, v.Status, v.Complete, v.Swept, v.Touches, v.NearestApproachATR, v.MFE, v.MAE, v.AdverseBodyATR, v.FilledBeforeConfirm, v.V4Setup, v.V4Episodes, v.Notes)
}

func verdicts(t testing.TB) []tradeVerdict {
	t.Helper()
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", "replay-xau-production-capture-20261012.json"))
	if err != nil {
		t.Fatal(err)
	}
	m5, _ := capture.Candles(market.M5)
	h1, _ := capture.Candles(market.H1)
	params, err := sessionParamsFor(doc, "XAU")
	if err != nil {
		t.Fatal(err)
	}
	cfg, err := sessionlevel.ParseConfig(params)
	if err != nil {
		t.Fatal(err)
	}
	index := map[int64]int{}
	for i, b := range m5 {
		index[b.Time] = i
	}
	var out []tradeVerdict
	for _, tr := range losingTrades {
		v := tradeVerdict{Trade: tr, Touches: -1}
		i, ok := index[tr.Created]
		if !ok {
			v.Notes = append(v.Notes, fmt.Sprintf("the committed capture ends %s: the plan's candle is outside it", utc(m5[len(m5)-1].Time+300)))
			out = append(out, v)
			continue
		}
		v.Replayable = true
		ref := levelRef{Name: tr.Level, Resistance: strings.HasSuffix(tr.Level, "_H") || tr.Level == "PDH" || tr.Level == "PWH"}
		// The level's price is the extreme of the candle the plan's structure id names.
		var extreme market.Candle
		found := false
		for _, b := range m5 {
			if b.Time == tr.FormedAt {
				extreme, found = b, true
			}
		}
		if !found {
			for _, b := range h1 {
				if b.Time == tr.FormedAt {
					extreme, found = b, true
				}
			}
		}
		if !found {
			v.Notes = append(v.Notes, "the level's extreme candle is not in the capture")
			out = append(out, v)
			continue
		}
		ref.Price = extreme.Low
		if ref.Resistance {
			ref.Price = extreme.High
		}
		v.LevelPrice = ref.Price
		v.LevelAgeH = float64(tr.Created-tr.FormedAt) / 3600
		a := sessionlevel.DetectAsOf(cfg, sessionlevel.Input{M5: m5, H1: h1}, i)
		atr := atrBefore(m5, i+1, cfg.ATRLength, cfg.ATRWindowBars)
		v.NearestApproachATR = math.Inf(1)
		for k := i - 12; k <= i; k++ {
			d := m5[k].Low - ref.Price
			if ref.Resistance {
				d = ref.Price - m5[k].High
			}
			v.NearestApproachATR = math.Min(v.NearestApproachATR, d/atr)
		}
		v.FilledBeforeConfirm = tr.FilledAt < tr.Created+300
		entry := (tr.EntryZone[0] + tr.EntryZone[1]) / 2
		if tr.EntryZone[0] == 0 {
			entry = m5[index[tr.FilledAt-tr.FilledAt%300]].Open
		}
		risk := math.Abs(entry - tr.Stop)
		sign := 1.0
		if tr.Direction == market.Sell {
			sign = -1
		}
		fillIdx := index[tr.FilledAt-tr.FilledAt%300]
		for k := fillIdx; k < len(m5) && m5[k].Time <= tr.FilledAt+8*3600; k++ {
			fav, adv := sign*(m5[k].High-entry), sign*(entry-m5[k].Low)
			if tr.Direction == market.Sell {
				fav, adv = entry-m5[k].Low, m5[k].High-entry
			}
			stopped := adv >= risk
			v.MFE, v.MAE = math.Max(v.MFE, fav/risk), math.Max(v.MAE, math.Min(adv/risk, 1))
			if stopped {
				break
			}
			if k <= fillIdx+6 {
				body := sign * (m5[k].Open - m5[k].Close)
				v.AdverseBodyATR = math.Max(v.AdverseBodyATR, body/atr)
			}
		}
		v.Status = statusOf(ref, a)
		_, end, hasPeriod := periodOf(cfg, ref.Name, tr.FormedAt)
		if hasPeriod {
			seconds, min, bars := int64(300), cfg.SessionMinCoverage, m5
			if strings.HasPrefix(ref.Name, "PD") || strings.HasPrefix(ref.Name, "PW") {
				seconds, min, bars = 3600, cfg.PeriodMinCoverage, h1
			}
			start, _, _ := periodOf(cfg, ref.Name, tr.FormedAt)
			v.Complete = end <= tr.Created+300 && coverage(bars, seconds, start, end) >= min
			swept, tied, touches := touchHistory(cfg, m5, i, ref, end, nil)
			v.Swept, v.Touches = swept, touches
			// Without a reaction record, "tied" means: the most recent sweep is within the
			// reclaim window of the plan's confirmation.
			_ = tied
		}
		for _, ep := range a.Episodes {
			if ep.Level.Name == ref.Name && ep.Direction == tr.Direction && math.Abs(float64(ep.EpisodeAt-tr.Created)) <= 6*3600 {
				v.V4Episodes = append(v.V4Episodes, fmt.Sprintf("%s@%s %s %s", ep.Model, utc(ep.EpisodeAt), ep.State, ep.Reason))
			}
		}
		for _, s := range a.Setups {
			if s.Direction == tr.Direction && s.Level.Name == ref.Name {
				v.V4Setup = true
			}
		}
		out = append(out, v)
	}
	return out
}

func utc(t int64) string { return time.Unix(t, 0).UTC().Format("02 Jan 15:04") }
