package htfzones_test

import (
	"math"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// Real confirmed setups found on M15/H1 structure and confirmed on M5, one per case the change distinguishes. newlyOffered marks the cases the higher frame's
// own last close would NOT have offered (the latent proximity defect): before the change they had no instance and no candidate.
type realCase struct {
	capture, strategy string
	direction         market.Direction
	confirmTime       int64
	structure         market.Timeframe
	riskPips          float64
	class             string // structural | floor | refused (the stop budget of the previous PR)
	newlyOffered      bool
}

var realCases = []realCase{
	// XAU
	{"replay-xau-production-capture-20261012.json", "fvg", market.Sell, 1791549600, market.H1, 64.9, "structural", true},          // 10-09 12:40 H1 FVG, wick rejection
	{"replay-xau-production-capture-20260921.json", "order_block", market.Sell, 1789458600, market.M15, 61.6, "structural", true}, // 09-15 07:50 M15 OB, engulfing
	{"replay-xau-production-capture-20260921.json", "fvg", market.Buy, 1789434000, market.M15, 58.5, "structural", true},          // 09-15 01:00 M15 FVG, strong reclaim
	{"replay-xau-production-capture-20261012.json", "order_block", market.Buy, 1791782100, market.H1, 77.5, "refused", true},      // 10-12 05:15 H1 OB: honest stop over the 70 pip cap
	// EURUSD
	{"replay-eurusd-production-capture-20261006.json", "fvg", market.Buy, 1790754900, market.M15, 7.1, "floor", true},          // 09-30 07:55 M15 FVG
	{"replay-eurusd-production-capture-20261006.json", "order_block", market.Buy, 1790733600, market.M15, 5.7, "floor", false}, // 09-30 02:00 M15 OB
	{"replay-eurusd-production-capture-20261006.json", "fvg", market.Sell, 1791180300, market.H1, 44.8, "refused", false},      // 10-05 06:05 H1 FVG: 44.8 pips against the 20 pip cap
}

type found struct {
	record
	instance                techniquezone.Instance
	hasInst                 bool
	absentUnderOldProximity bool
	m5Extreme, m5ATR        float64
	pip                     float64
}

// replayCases replays one capture and returns, per fixture, the candidate with its higher-timeframe instance and the M5 facts the stop is built from.
func replayCases(t *testing.T, doc *config.Document, name string, cases []realCase, withBudget bool) map[int64]map[string]*found {
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, string(capture.Symbol)); err != nil {
		t.Fatal(err)
	}
	m5, err := capture.Candles(market.M5)
	if err != nil {
		t.Fatal(err)
	}
	index := map[int64]int{}
	for i, b := range m5 {
		index[b.Time] = i
	}
	out := map[int64]map[string]*found{}
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{WithoutStopBudget: !withBudget, WithoutFVGDefinition: true, OnEvaluation: func(e engine.Evaluation) {
		for _, c := range e.Candidates {
			if c.Reaction == nil {
				continue
			}
			for _, want := range cases {
				if string(c.Strategy) != want.strategy || c.Direction != want.direction || c.Reaction.ConfirmationBarTime != want.confirmTime || c.StructureTimeframe != want.structure {
					continue
				}
				key := want.strategy + string(want.direction)
				if out[want.confirmTime] == nil {
					out[want.confirmTime] = map[string]*found{}
				}
				if out[want.confirmTime][key] != nil {
					continue
				}
				f := &found{pip: settings.Geometry.PipSize}
				f.record = record{Strategy: want.strategy, Direction: string(want.direction), Structure: string(want.structure), EntryLow: float64(c.Entry.Low), EntryHigh: float64(c.Entry.High),
					Invalidation: float64(c.Invalidation.Price), Evidence: evidenceCodes(c)}
				reference := c.Entry.High
				if want.direction == market.Sell {
					reference = c.Entry.Low
				}
				f.RiskPips = math.Abs(float64(reference)-float64(c.Invalidation.Price)) / settings.Geometry.PipSize
				frame := e.Context.Timeframes[want.structure].Legacy
				side := "sell"
				if want.direction == market.Buy {
					side = "buy"
				}
				for _, in := range frame.Techniques() {
					if in.Side == side && math.Abs(in.Low-f.EntryLow) < 1e-9 && math.Abs(in.High-f.EntryHigh) < 1e-9 && in.Technique != "" {
						f.instance, f.hasInst = in, true
					}
				}
				f.absentUnderOldProximity = true
				for _, in := range techniquezone.InstancesFromScoredZones(frame.TechniqueZones, frame.Bars, techniquezone.ATRScalar(frame.ATR, 1), settings.TechniqueZones.Technique) {
					if in.Side == side && math.Abs(in.Low-f.EntryLow) < 1e-9 && math.Abs(in.High-f.EntryHigh) < 1e-9 {
						f.absentUnderOldProximity = false
					}
				}
				touch, confirm := index[c.Reaction.TouchBarTime], index[c.Reaction.ConfirmationBarTime]
				f.m5Extreme = m5[touch].Low
				if want.direction == market.Sell {
					f.m5Extreme = m5[touch].High
				}
				for _, b := range m5[touch : confirm+1] {
					if want.direction == market.Buy {
						f.m5Extreme = math.Min(f.m5Extreme, b.Low)
					} else {
						f.m5Extreme = math.Max(f.m5Extreme, b.High)
					}
				}
				f.m5ATR = e.Context.Timeframes[market.M5].Legacy.DetectorATR
				out[want.confirmTime][key] = f
			}
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func TestRealHigherTimeframeSetupsAreFoundOnTheStructureAndStopBeyondTheWholeZone(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	byCapture := map[string][]realCase{}
	for _, c := range realCases {
		byCapture[c.capture] = append(byCapture[c.capture], c)
	}
	var wg sync.WaitGroup
	for name, cases := range byCapture {
		wg.Add(1)
		go func(name string, cases []realCase) {
			defer wg.Done()
			unbudgeted := replayCases(t, doc, name, cases, false)
			budgeted := replayCases(t, doc, name, cases, true)
			for _, want := range cases {
				label := want.strategy + " " + string(want.direction) + " " + string(want.structure)
				f := unbudgeted[want.confirmTime][want.strategy+string(want.direction)]
				if f == nil {
					t.Errorf("%s at %d was not published", label, want.confirmTime)
					continue
				}
				if !has(f.Evidence, "htf_zone_"+strings.ToLower(string(want.structure))) {
					t.Errorf("%s: evidence %v lacks htf_zone_%s", label, f.Evidence, strings.ToLower(string(want.structure)))
				}
				if math.Abs(f.RiskPips-want.riskPips) > 0.15 {
					t.Errorf("%s: stop %.1f pips from the proximal edge, fixture pins %.1f", label, f.RiskPips, want.riskPips)
				}
				if want.newlyOffered && !f.absentUnderOldProximity {
					t.Errorf("%s: the higher frame's own last close would already have offered this zone", label)
				}
				if !f.hasInst {
					t.Errorf("%s: no higher-timeframe instance matches the candidate's entry band", label)
					continue
				}
				// The stop sits beyond the WHOLE higher-timeframe structure (not its clipped entry slice) and the M5 reaction extreme, plus the buffer of the
				// M5 detector ATR.
				buffer, _ := doc.Get("analysis.strategies." + want.strategy + ".invalidation_buffer_atr")
				span := buffer.(float64) * f.m5ATR
				tick := f.pip / 10
				structLow, structHigh := f.instance.Low, f.instance.High
				if f.instance.EntryClipped {
					structLow, structHigh = f.instance.StructuralLow, f.instance.StructuralHigh
				}
				wantStop := math.Min(structLow, f.m5Extreme) - span
				if want.direction == market.Sell {
					wantStop = math.Max(structHigh, f.m5Extreme) + span
				}
				if math.Abs(f.Invalidation-wantStop) > tick*1.01 {
					t.Errorf("%s: invalidation %v, want beyond the whole structure and the M5 extreme: %v (structure %v-%v, extreme %v, buffer %v)", label, f.Invalidation, wantStop, structLow, structHigh, f.m5Extreme, span)
				}
				if f.instance.EntryClipped && (f.EntryHigh-f.EntryLow) > (f.instance.StructuralHigh-f.instance.StructuralLow)+1e-9 {
					t.Errorf("%s: the entry band is wider than the structure", label)
				}
				after := budgeted[want.confirmTime][want.strategy+string(want.direction)]
				if want.class == "refused" && after != nil {
					t.Errorf("%s: %.1f pips is over the cap yet the budgeted engine published it", label, f.RiskPips)
				}
				if want.class != "refused" && after == nil {
					t.Errorf("%s: inside the budget (%s) yet the budgeted engine did not publish it", label, want.class)
				}
			}
		}(name, cases)
	}
	wg.Wait()
}

func has(codes []string, want string) bool {
	for _, c := range codes {
		if c == want {
			return true
		}
	}
	return false
}
