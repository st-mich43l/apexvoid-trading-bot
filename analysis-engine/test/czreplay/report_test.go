package czreplay_test

import (
	"math"
	"sort"
	"time"
)

// Period is a chronological slice of the captures. Development and held-out are
// disjoint and ordered in time; nothing was tuned on the held-out slices.
type Period struct {
	Key      string
	Label    string
	Role     string // development | held-out
	Captures []string
	From, To int64 // [From, To) over the evaluated candle's open time
}

func utc(s string) int64 {
	t, err := time.Parse("2006-01-02 15:04", s)
	if err != nil {
		panic(err)
	}
	return t.UTC().Unix()
}

var fxFiles = []string{"EURUSD", "GBPUSD", "GBPJPY", "USDJPY"}

var periods = []Period{
	{"xau_sep", "XAU 14-21 Sep (Python-origin detector era)", "development", []string{"XAU Sep"}, utc("2026-09-01 00:00"), utc("2026-10-01 00:00")},
	{"xau_oct_legacy_period", "XAU 1 Oct - 6 Oct 09:33 (legacy running-intersection detector in production)", "development", []string{"XAU Oct"}, utc("2026-10-01 00:00"), 1791279180},
	{"xau_oct_frozen_period", "XAU 6 Oct 09:33 - 13 Oct (frozen technique publishers in production)", "held-out", []string{"XAU Oct"}, 1791279180, utc("2026-10-14 00:00")},
	{"fx_dev", "FX (4 pairs) 2 - 8 Oct", "development", fxFiles, utc("2026-10-02 00:00"), utc("2026-10-08 00:00")},
	{"fx_heldout", "FX (4 pairs) 8 - 13 Oct", "held-out", fxFiles, utc("2026-10-08 00:00"), utc("2026-10-14 00:00")},
	// Pooled views (development and held-out together): for scale, never for choosing anything.
	{"xau_all", "XAU, every window pooled (14-21 Sep and 1-13 Oct)", "pooled", []string{"XAU Sep", "XAU Oct"}, utc("2026-09-01 00:00"), utc("2026-10-14 00:00")},
	{"fx_all", "FX (4 pairs), 2 - 13 Oct pooled", "pooled", fxFiles, utc("2026-10-02 00:00"), utc("2026-10-14 00:00")},
}

// ModelSummary is one model over one period.
type ModelSummary struct {
	Model string `json:"model"`
	// Decisions published in the period, by the model's own rules.
	Raw int `json:"decisions"`
	// Thinned keeps the first decision per symbol, direction and hour: overlapping
	// re-confirmations are not independent trades.
	Thinned    int `json:"thinned_one_per_hour_direction"`
	Executable int `json:"executable_within_stop_cap"`
	Filled     int `json:"filled_executable"`

	Single Stat `json:"single_target_r"`
	Ladder Stat `json:"ladder_r"`
	// All filled setups as published, ignoring the execution stop cap.
	SingleAsPublished Stat `json:"single_target_r_as_published"`

	MedianStopPips   float64 `json:"median_stop_pips"`
	MedianStopATR    float64 `json:"median_stop_atr"`
	MedianRewardRisk float64 `json:"median_reward_risk"`
	MedianMFE        float64 `json:"median_mfe_r"`
	MedianMAE        float64 `json:"median_mae_r"`
	FixedRRTargets   int     `json:"fixed_reward_risk_targets"`

	Patterns map[string]int `json:"patterns,omitempty"`
	Stars    map[int]int    `json:"stars,omitempty"`

	// Structural defect counts among the model's own published decisions.
	MembersConfirmableAfterTouch int     `json:"decisions_with_a_member_not_confirmable_before_the_touch,omitempty"`
	SingleIndependentSource      int     `json:"decisions_with_fewer_than_two_independent_sources,omitempty"`
	OppositeColourConfirmation   int     `json:"sweep_reclaim_confirmations_by_an_opposite_colour_candle,omitempty"`
	MedianUnionOverOverlap       float64 `json:"median_union_width_over_overlap_width,omitempty"`
	FailedAtPublication          int     `json:"decisions_whose_reaction_had_already_failed_at_publication,omitempty"`
}

func inPeriod(p Period, d *Decision) bool { return d.EvalTime >= p.From && d.EvalTime < p.To }

type thinKey struct {
	symbol, direction string
	hour              int64
}

// thin keeps the first decision per symbol, direction and hour.
func thin(ds []*Decision) []*Decision {
	sort.SliceStable(ds, func(i, j int) bool { return ds[i].EvalTime < ds[j].EvalTime })
	seen := map[thinKey]bool{}
	var out []*Decision
	for _, d := range ds {
		k := thinKey{d.Symbol, d.Direction, d.EvalTime / 3600}
		if seen[k] {
			continue
		}
		seen[k] = true
		out = append(out, d)
	}
	return out
}

func decisionsOf(rs []*Run, model string, p Period) []*Decision {
	var out []*Decision
	for _, r := range rs {
		for _, d := range r.Decisions[model] {
			if inPeriod(p, d) {
				out = append(out, d)
			}
		}
	}
	return out
}

func runsFor(all map[string]*Run, p Period) []*Run {
	var out []*Run
	for _, name := range p.Captures {
		if r, ok := all[name]; ok {
			out = append(out, r)
		}
	}
	return out
}

func summarize(model string, ds []*Decision) ModelSummary {
	s := ModelSummary{Model: model, Raw: len(ds), Patterns: map[string]int{}, Stars: map[int]int{}}
	thinned := thin(append([]*Decision(nil), ds...))
	s.Thinned = len(thinned)
	var single, ladder, published []float64
	var stopPips, stopATR, rr, mfe, mae, overlap []float64
	for _, d := range ds {
		s.Patterns[d.Pattern]++
		if d.Stars > 0 {
			s.Stars[d.Stars]++
		}
		if d.LateMember {
			s.MembersConfirmableAfterTouch++
		}
		if d.SingleSource {
			s.SingleIndependentSource++
		}
		if d.OppositeColourSet {
			s.OppositeColourConfirmation++
		}
		if d.FailedAtPublication {
			s.FailedAtPublication++
		}
		if d.UnionOverOverlap > 0 {
			overlap = append(overlap, d.UnionOverOverlap)
		}
		if d.TargetKind == "fixed_reward_risk" {
			s.FixedRRTargets++
		}
	}
	for _, d := range thinned {
		if d.Executable {
			s.Executable++
		}
		if d.Single.Result == "unfilled" || d.Single.Result == "invalid" || d.Single.Result == "" {
			continue
		}
		published = append(published, d.Single.R)
		if !d.Executable {
			continue
		}
		s.Filled++
		single = append(single, d.Single.R)
		ladder = append(ladder, d.Ladder.R)
		stopPips = append(stopPips, d.RiskPips)
		if d.StopATR > 0 {
			stopATR = append(stopATR, d.StopATR)
		}
		rr = append(rr, d.RewardRisk)
		mfe = append(mfe, d.Single.MFE)
		mae = append(mae, d.Single.MAE)
	}
	s.Single, s.Ladder, s.SingleAsPublished = stat(single), stat(ladder), stat(published)
	for _, st := range []*Stat{&s.Single, &s.Ladder, &s.SingleAsPublished} {
		st.Mean, st.Lo, st.Hi = round(st.Mean, 3), round(st.Lo, 3), round(st.Hi, 3)
	}
	s.MedianStopPips, s.MedianStopATR = round(median(stopPips), 1), round(median(stopATR), 2)
	s.MedianRewardRisk, s.MedianMFE, s.MedianMAE = round(median(rr), 2), round(median(mfe), 2), round(median(mae), 2)
	s.MedianUnionOverOverlap = round(median(overlap), 2)
	return s
}

// Comparison is how v3 (model D) changes the frozen v2 (model B) baseline over
// the same period: which of v2's setups it still publishes, which it removes
// (winners and losers by v2's own simulated single-target outcome) and which are
// new.
type Comparison struct {
	Baseline       string  `json:"baseline"`
	Candidate      string  `json:"candidate"`
	BaselineFilled int     `json:"baseline_filled"`
	LosersRemoved  int     `json:"baseline_losers_removed"`
	LosersOf       int     `json:"baseline_losers"`
	WinnersRemoved int     `json:"baseline_winners_removed"`
	WinnersOf      int     `json:"baseline_winners"`
	CandidateNew   int     `json:"candidate_decisions_with_no_baseline_counterpart"`
	CandidateTotal int     `json:"candidate_thinned"`
	RetainedMeanR  float64 `json:"retained_baseline_mean_r"`
	RemovedMeanR   float64 `json:"removed_baseline_mean_r"`
}

func overlapPositive(aLow, aHigh, bLow, bHigh float64) bool {
	return math.Min(aHigh, bHigh)-math.Max(aLow, bLow) > 0
}

// counterpart reports whether the candidate model published the same reaction as
// the baseline decision: the same direction, a confirmation within two candles and
// an entry that overlaps the baseline's band.
func counterpart(base *Decision, others []*Decision) bool {
	for _, o := range others {
		if o.Direction == base.Direction && abs64(o.ConfirmTime-base.ConfirmTime) <= 600 && overlapPositive(o.EntryLow, o.EntryHigh, base.EntryLow, base.EntryHigh) {
			return true
		}
	}
	return false
}

func abs64(v int64) int64 {
	if v < 0 {
		return -v
	}
	return v
}

func compare(baseline, candidate string, rs []*Run, p Period) Comparison {
	c := Comparison{Baseline: baseline, Candidate: candidate}
	baseDecisions := thin(decisionsOf(rs, baseline, p))
	candidateAll := decisionsOf(rs, candidate, p)
	candThin := thin(append([]*Decision(nil), candidateAll...))
	c.CandidateTotal = len(candThin)
	var kept, removed []float64
	for _, b := range baseDecisions {
		if b.Single.Result == "unfilled" || b.Single.Result == "invalid" || b.Single.Result == "" {
			continue
		}
		c.BaselineFilled++
		retained := counterpart(b, candidateAll)
		if b.Single.R > 0 {
			c.WinnersOf++
			if !retained {
				c.WinnersRemoved++
			}
		} else {
			c.LosersOf++
			if !retained {
				c.LosersRemoved++
			}
		}
		if retained {
			kept = append(kept, b.Single.R)
		} else {
			removed = append(removed, b.Single.R)
		}
	}
	for _, d := range candThin {
		if !counterpart(d, decisionsOf(rs, baseline, p)) {
			c.CandidateNew++
		}
	}
	mean := func(xs []float64) float64 {
		if len(xs) == 0 {
			return 0
		}
		t := 0.0
		for _, x := range xs {
			t += x
		}
		return round(t/float64(len(xs)), 3)
	}
	c.RetainedMeanR, c.RemovedMeanR = mean(kept), mean(removed)
	return c
}
