// Package reactionstop_test measures where the legacy-family invalidation sits against the bars the reaction itself traded.
//
// The seven strategies that kept the frozen publisher's geometry (key_level, supply, demand, order_block, fvg, flip_zone, trendline) published
// "far edge of the structure minus a buffer". A sweep or strong reclaim confirmed by a wick that pierces the band puts that price inside the
// bars between the touch and the confirmation. This replays every committed M5 capture and, for each confirmed candidate, compares the structural-only
// invalidation with the one anchored beyond the reaction's extreme.
//
//	REACTION_STOP_OUT=/tmp/rs go test ./test/reactionstop -run TestMeasureReactionAnchoredStop -timeout 30m
//
// The structural-only price is the published invalidation on a tree without the anchor (the base commit); on a tree with it the test instead asserts that
// every published invalidation lies beyond the reaction's extreme by the buffer and that the evidence code is present exactly when the extreme set it.
// The output is a deterministic function of the captures and the committed configuration. It carries published facts and bars only: no outcome and no
// tuning input.
package reactionstop_test

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

var legacyFamily = map[string]bool{"key_level": true, "supply": true, "demand": true, "order_block": true, "fvg": true, "flip_zone": true, "trendline": true}

const anchoredCode = "invalidation_beyond_reaction_extreme"

// xauPipValuePerLot is USD per pip per lot: pip 0.1 x contract_units_per_lot 100 (config/instruments.yml, XAU contract).
const xauPipValuePerLot = 0.1 * 100.0

// xauLots is the equity-table lot the instrument trades (the brief's figure); the lot does not depend on the stop.
const xauLots = 0.12

type record struct {
	ID, Strategy, Symbol, Class, Direction, Pattern, Capture string
	ConfirmTime                                              int64
	Old, New, Adverse, Buffer, Reference, ATR, Pip           float64
	Anchored                                                 bool // the evidence code is present (only on a tree with the anchor)
	OldInside, NewInside                                     bool
	OldRiskPips, NewRiskPips                                 float64
	MinPips, MaxPips                                         float64
	OldTradedWithin24                                        bool // the structural-only invalidation was traded by one of the next 24 M5 bars (descriptive)
	Resting                                                  bool
}

func evidenceCodes(c opportunity.Candidate) []string {
	var out []string
	for _, e := range c.Evidence {
		out = append(out, e.Code)
	}
	return out
}

func class(symbol string) string {
	if symbol == "XAU" {
		return "XAU"
	}
	return "FX"
}

func TestMeasureReactionAnchoredStop(t *testing.T) {
	dir := os.Getenv("REACTION_STOP_OUT")
	if dir == "" {
		t.Skip("REACTION_STOP_OUT not set")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	matches, _ := filepath.Glob(filepath.Join("..", "..", "testdata", "replay-*-production-capture-*.json"))
	for _, m := range matches {
		if !strings.Contains(m, "-m1-") {
			names = append(names, filepath.Base(m))
		}
	}
	sort.Strings(names)
	var mu sync.Mutex
	var all []record
	var wg sync.WaitGroup
	for _, name := range names {
		wg.Add(1)
		go func(name string) {
			defer wg.Done()
			capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
			if err != nil {
				t.Error(err)
				return
			}
			settings, err := engine.LoadSettings(doc, market.M5, false)
			if err != nil {
				t.Error(err)
				return
			}
			if err := engine.ApplyInstrument(&settings, doc, string(capture.Symbol)); err != nil {
				t.Error(err)
				return
			}
			m5, err := capture.Candles(market.M5)
			if err != nil {
				t.Error(err)
				return
			}
			index := make(map[int64]int, len(m5))
			for i, b := range m5 {
				index[b.Time] = i
			}
			var local []record
			_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{WithoutOBDefinition: true, OnEvaluation: func(e engine.Evaluation) {
				frame := e.Context.Timeframes[market.M5]
				if frame == nil || frame.Legacy == nil {
					return
				}
				for _, c := range e.Candidates {
					strategy := string(c.Strategy)
					if !legacyFamily[strategy] || c.Reaction == nil {
						continue
					}
					touch, ok1 := index[c.Reaction.TouchBarTime]
					confirm, ok2 := index[c.Reaction.ConfirmationBarTime]
					if !ok1 || !ok2 || touch > confirm {
						continue
					}
					buy := c.Direction == market.Buy
					adverse := m5[touch].Low
					if !buy {
						adverse = m5[touch].High
					}
					for _, b := range m5[touch : confirm+1] {
						if buy {
							adverse = math.Min(adverse, b.Low)
						} else {
							adverse = math.Max(adverse, b.High)
						}
					}
					bufferATR, _ := doc.Get("analysis.strategies." + strategy + ".invalidation_buffer_atr")
					bufferATRf, _ := bufferATR.(float64)
					buffer := bufferATRf * frame.Legacy.DetectorATR
					published := float64(c.Invalidation.Price)
					anchored := false
					for _, code := range evidenceCodes(c) {
						anchored = anchored || code == anchoredCode
					}
					pip := settings.Geometry.PipSize
					reference := c.Entry.High
					if !buy {
						reference = c.Entry.Low
					}
					r := record{ID: c.ID, Strategy: strategy, Symbol: string(c.Symbol), Class: class(string(c.Symbol)), Direction: string(c.Direction), Pattern: c.Reaction.Pattern,
						Capture: name, ConfirmTime: c.Reaction.ConfirmationBarTime, Adverse: adverse, Buffer: buffer, Reference: reference, ATR: frame.Legacy.DetectorATR, Pip: pip,
						Anchored: anchored, MinPips: settings.InstrumentStopMinPips, MaxPips: settings.InstrumentStopMaxPips}
					if buy {
						r.Old = published
						r.New = math.Min(published, adverse-buffer)
						r.OldInside, r.NewInside = published >= adverse, r.New >= adverse
					} else {
						r.Old = published
						r.New = math.Max(published, adverse+buffer)
						r.OldInside, r.NewInside = published <= adverse, r.New <= adverse
					}
					r.OldRiskPips, r.NewRiskPips = math.Abs(reference-r.Old)/pip, math.Abs(reference-r.New)/pip
					// Forward path, descriptive only: was the structural-only price traded by one of the next 24 bars?
					for k := confirm + 1; k <= confirm+24 && k < len(m5); k++ {
						if (buy && m5[k].Low <= r.Old) || (!buy && m5[k].High >= r.Old) {
							r.OldTradedWithin24 = true
							break
						}
					}
					local = append(local, r)
				}
			}})
			if err != nil {
				t.Error(err)
				return
			}
			mu.Lock()
			all = append(all, local...)
			mu.Unlock()
		}(name)
	}
	wg.Wait()
	sort.SliceStable(all, func(i, j int) bool {
		if all[i].Capture != all[j].Capture {
			return all[i].Capture < all[j].Capture
		}
		if all[i].ConfirmTime != all[j].ConfirmTime {
			return all[i].ConfirmTime < all[j].ConfirmTime
		}
		return all[i].ID < all[j].ID
	})
	seen := map[string]bool{}
	var records []record
	for _, r := range all {
		if !seen[r.ID] {
			seen[r.ID] = true
			records = append(records, r)
		}
	}
	postFix := false
	for _, r := range records {
		postFix = postFix || r.Anchored
	}
	if postFix {
		// On a tree with the anchor: every published invalidation lies beyond the reaction extreme by the buffer, and the code marks the ones the extreme set.
		for _, r := range records {
			// Published prices are rounded to the instrument's tick (a tenth of a pip), so every comparison allows one tick.
			tick := r.Pip / 10
			beyond := r.Old <= r.Adverse-r.Buffer+tick
			if r.Direction == string(market.Sell) {
				beyond = r.Old >= r.Adverse+r.Buffer-tick
			}
			if !beyond {
				t.Errorf("%s %s %s: published invalidation %.5f is not beyond the reaction extreme %.5f by the buffer %.5f", r.Strategy, r.Symbol, r.ID, r.Old, r.Adverse, r.Buffer)
			}
			if r.Anchored && math.Abs(math.Abs(r.Old-r.Adverse)-r.Buffer) > tick {
				t.Errorf("%s %s %s: flagged anchored but the price is not extreme -/+ buffer", r.Strategy, r.Symbol, r.ID)
			}
		}
	}
	out, err := os.Create(filepath.Join(dir, "records.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	enc := json.NewEncoder(out)
	for _, r := range records {
		if err := enc.Encode(r); err != nil {
			t.Fatal(err)
		}
	}
	out.Close()
	if !postFix {
		raw, err := json.MarshalIndent(aggregate(records), "", " ")
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "results.json"), append(raw, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if postFix {
		raw, err := json.MarshalIndent(afterAggregate(records), "", " ")
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "after.json"), append(raw, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	t.Logf("%d confirmed legacy-family candidates across %d captures (post-fix tree: %v)", len(records), len(names), postFix)
}

type cell struct {
	N                      int     `json:"n"`
	Inside                 int     `json:"stop_inside_reaction"`
	InsideShare            float64 `json:"stop_inside_share"`
	Anchored               int     `json:"anchored"`
	AnchoredShare          float64 `json:"anchored_share"`
	AddedRiskPipsMedian    float64 `json:"added_risk_pips_median"`
	AddedRiskPipsP90       float64 `json:"added_risk_pips_p90"`
	AddedRiskATRMedian     float64 `json:"added_risk_atr_median"`
	AddedRiskATRP90        float64 `json:"added_risk_atr_p90"`
	OldRiskPipsMedian      float64 `json:"old_risk_pips_median"`
	OldRiskATRMedian       float64 `json:"old_risk_atr_median"`
	AnchoredOverMax        int     `json:"anchored_over_envelope_max"`
	NewlyOverMax           int     `json:"anchored_newly_over_envelope_max"`
	AnchoredBelowMin       int     `json:"anchored_below_envelope_min"`
	XAUWidened             int     `json:"xau_anchored_executed_stop_widened"`
	XAUWidenedDollarMedian float64 `json:"xau_widened_added_dollar_risk_median_at_0_12_lots"`
	OldInsideTraded24      float64 `json:"old_inside_traded_within_24_bars_share"`
	OldOutsideTraded24     float64 `json:"old_outside_traded_within_24_bars_share"`
	OldInsideN             int     `json:"old_inside_n"`
	OldOutsideN            int     `json:"old_outside_n"`
}

func quantile(v []float64, q float64) float64 {
	if len(v) == 0 {
		return 0
	}
	sort.Float64s(v)
	return v[int(math.Min(float64(len(v)-1), math.Floor(q*float64(len(v)))))]
}

func summarize(rs []record) cell {
	c := cell{N: len(rs)}
	var addedPips, addedATR, oldPips, oldATR, dollars []float64
	var inTr, inN, outTr, outN int
	for _, r := range rs {
		oldPips = append(oldPips, r.OldRiskPips)
		if r.ATR > 0 {
			oldATR = append(oldATR, math.Abs(r.Reference-r.Old)/r.ATR)
		}
		if r.OldInside {
			c.Inside++
			inN++
			if r.OldTradedWithin24 {
				inTr++
			}
		} else {
			outN++
			if r.OldTradedWithin24 {
				outTr++
			}
		}
		if math.Abs(r.New-r.Old) < r.Pip/10*0.999 { // the extreme moves the price by less than one tick: not anchored in effect
			continue
		}
		c.Anchored++
		added := r.NewRiskPips - r.OldRiskPips
		addedPips = append(addedPips, added)
		if r.ATR > 0 {
			addedATR = append(addedATR, added*r.Pip/r.ATR)
		}
		if r.MaxPips > 0 && r.NewRiskPips > r.MaxPips+1e-9 {
			c.AnchoredOverMax++
			if r.OldRiskPips <= r.MaxPips+1e-9 {
				c.NewlyOverMax++
			}
		}
		if r.Class == "FX" && r.MinPips > 0 && r.NewRiskPips < r.MinPips-1e-9 {
			c.AnchoredBelowMin++
		}
		if r.Class == "XAU" && r.OldRiskPips >= r.MinPips-1e-9 && r.OldRiskPips <= r.MaxPips+1e-9 && r.NewRiskPips <= r.MaxPips+1e-9 && added > 1e-9 {
			c.XAUWidened++
			dollars = append(dollars, added*xauLots*xauPipValuePerLot)
		}
	}
	if c.N > 0 {
		c.InsideShare = round(float64(c.Inside) / float64(c.N))
		c.AnchoredShare = round(float64(c.Anchored) / float64(c.N))
	}
	c.AddedRiskPipsMedian, c.AddedRiskPipsP90 = round(quantile(addedPips, 0.5)), round(quantile(addedPips, 0.9))
	c.AddedRiskATRMedian, c.AddedRiskATRP90 = round(quantile(addedATR, 0.5)), round(quantile(addedATR, 0.9))
	c.OldRiskPipsMedian, c.OldRiskATRMedian = round(quantile(oldPips, 0.5)), round(quantile(oldATR, 0.5))
	c.XAUWidenedDollarMedian = round(quantile(dollars, 0.5))
	c.OldInsideN, c.OldOutsideN = inN, outN
	if inN > 0 {
		c.OldInsideTraded24 = round(float64(inTr) / float64(inN))
	}
	if outN > 0 {
		c.OldOutsideTraded24 = round(float64(outTr) / float64(outN))
	}
	return c
}

func round(v float64) float64 { return math.Round(v*1000) / 1000 }

func aggregate(records []record) map[string]any {
	groups := map[string][]record{}
	patterns := map[string][]record{}
	for _, r := range records {
		groups[r.Strategy+"|"+r.Class] = append(groups[r.Strategy+"|"+r.Class], r)
		patterns[r.Pattern] = append(patterns[r.Pattern], r)
	}
	out := map[string]any{"description": "confirmed legacy-family candidates over the committed M5 captures, de-duplicated by opportunity id; old = structural-only invalidation, new = anchored beyond the reaction extreme",
		"total": summarize(records)}
	byGroup := map[string]cell{}
	for k, v := range groups {
		byGroup[k] = summarize(v)
	}
	byPattern := map[string]cell{}
	for k, v := range patterns {
		byPattern[k] = summarize(v)
	}
	out["by_strategy_class"], out["by_pattern"] = byGroup, byPattern
	return out
}

// afterAggregate is the published state on a tree with the anchor: how many candidates are published, how many carry the evidence code, how many
// still have a stop inside the reaction (none is allowed) and how many are wider than the instrument's envelope maximum, from the proximal entry.
func afterAggregate(records []record) map[string]any {
	type counts struct {
		N               int `json:"n"`
		Flagged         int `json:"flagged_with_evidence_code"`
		StopInside      int `json:"stop_inside_reaction"`
		OverEnvelopeMax int `json:"over_envelope_max"`
	}
	cells := map[string]*counts{}
	total := &counts{}
	for _, r := range records {
		key := r.Strategy + "|" + r.Class
		if cells[key] == nil {
			cells[key] = &counts{}
		}
		for _, c := range []*counts{cells[key], total} {
			c.N++
			if r.Anchored {
				c.Flagged++
			}
			inside := r.Old >= r.Adverse-r.Pip/10
			if r.Direction == string(market.Sell) {
				inside = r.Old <= r.Adverse+r.Pip/10
			}
			if inside {
				c.StopInside++
			}
			if r.MaxPips > 0 && math.Abs(r.Reference-r.Old)/r.Pip > r.MaxPips {
				c.OverEnvelopeMax++
			}
		}
	}
	return map[string]any{"description": "published state on the tree with the anchor; over_envelope_max is measured from the proximal entry edge", "total": total, "by_strategy_class": cells}
}
