package czreplay_test

import (
	"fmt"
	"math"
	"sort"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structuralconfluence"
)

type FactInfo struct {
	ID               string   `json:"id"`
	Source           string   `json:"source"`
	Low              float64  `json:"low"`
	High             float64  `json:"high"`
	Formed           string   `json:"formed_at"`
	Confirmable      string   `json:"confirmable_from"`
	Atoms            []string `json:"provenance_atoms"`
	Verified         bool     `json:"verified"`
	UnverifiedReason string   `json:"unverified_reason,omitempty"`
	Touches          int      `json:"touches"`
}

type RegionInfo struct {
	Low        float64  `json:"low"`
	High       float64  `json:"high"`
	WidthPips  float64  `json:"width_pips"`
	Sources    int      `json:"independent_sources"`
	MinSources int      `json:"min_independent_sources"`
	Support    []string `json:"support"`
	Episode    string   `json:"episode"`
}

// Case is one production trade rebuilt: what v2 (or the legacy detector) saw,
// what the v3 structural engine sees from the same candles, and the v3 decision.
type Case struct {
	Trade     string  `json:"trade"`
	Symbol    string  `json:"symbol"`
	Direction string  `json:"direction"`
	Regime    string  `json:"production_detector_regime"`
	Commit    string  `json:"deployed_commit_at_fill"`
	RealizedR float64 `json:"realized_r"`
	ExitPath  string  `json:"exit_path"`

	Original struct {
		Constituents []string `json:"constituents"`
		EntryLow     float64  `json:"entry_low"`
		EntryHigh    float64  `json:"entry_high"`
		BandPips     float64  `json:"band_width_pips"`
		Stop         float64  `json:"invalidation"`
		Target       float64  `json:"target"`
		Pattern      string   `json:"confirmation_pattern"`
		Stars        int      `json:"stars"`
		TouchTime    string   `json:"touch"`
		ConfirmTime  string   `json:"confirmation"`
	} `json:"original"`

	Reproduced                string       `json:"reproduced_by_replay"`
	EvaluatedAt               string       `json:"evaluated_at"`
	Facts                     []FactInfo   `json:"v3_structures_at_evaluation"`
	Independent               int          `json:"v3_independent_sources_among_the_structures"`
	Dependent                 [][]string   `json:"v3_dependent_groups"`
	Regions                   []RegionInfo `json:"v3_supported_regions_on_the_trade_side"`
	TrueOverlap               float64      `json:"v3_true_overlap_pips_inside_the_original_band"`
	UnionOverlap              float64      `json:"original_band_over_true_overlap,omitempty"`
	V3Decision                string       `json:"v3_decision"`
	V3EntryInsideOriginalPips float64      `json:"v3_entry_pips_inside_the_original_band,omitempty"`
	V3Setup                   *Decision    `json:"v3_setup,omitempty"`
	Reasons                   []string     `json:"v3_reasons"`
	WouldV3Change             string       `json:"summary"`
}

func stamp(u int64) string { return time.Unix(u, 0).UTC().Format("2006-01-02 15:04") }

func lowestFirst(rs []structuralconfluence.Region) {
	sort.SliceStable(rs, func(i, j int) bool { return rs[i].Low < rs[j].Low })
}

func buildCases(t *testing.T, rs map[string]*Run) []Case {
	run := rs["XAU Oct"]
	var out []Case
	for _, tr := range loadLedger(t) {
		if tr.Symbol != "XAU" || tr.AEConfirm < "2026-10-05" {
			continue
		}
		c := Case{
			Trade: tr.CloseTime[5:16] + " " + tr.Direction, Symbol: tr.Symbol, Direction: tr.Direction, Regime: tr.Regime,
			Commit: tr.Commit, RealizedR: tr.R, ExitPath: tr.ExitPath,
		}
		c.Original.EntryLow, c.Original.EntryHigh = tr.Entry[0], tr.Entry[1]
		c.Original.BandPips = (tr.Entry[1] - tr.Entry[0]) / run.Pip
		c.Original.Stop, c.Original.Target, c.Original.Pattern, c.Original.Stars = tr.Invalidation, tr.Target, tr.Pattern, tr.Stars
		c.Original.TouchTime, c.Original.ConfirmTime = tr.AETouch[:16], tr.AEConfirm[:16]
		for _, k := range tr.Constituents {
			c.Original.Constituents = append(c.Original.Constituents, fmt.Sprintf("%s [%.2f, %.2f] origin %s, %d touches", k.Technique, k.Low, k.High, stamp(k.Origin), k.Touches))
		}
		confirm := utc(tr.AEConfirm[:16])
		same := func(d *Decision) bool {
			return d.Direction == tr.Direction && d.ConfirmTime == confirm && math.Abs(d.EntryLow-tr.Entry[0]) < 0.006 && math.Abs(d.EntryHigh-tr.Entry[1]) < 0.006
		}
		var evalTime int64
		for _, d := range run.Decisions["B"] {
			if same(d) {
				c.Reproduced, evalTime = "Model B (frozen v2): same band, stop and target as production", d.EvalTime
				if math.Abs(d.Stop-tr.Invalidation) > reproductionTolerance || math.Abs(d.Target-tr.Target) > reproductionTolerance {
					c.Reproduced = "Model B: same band, stop or target differs"
				}
				break
			}
		}
		if evalTime == 0 {
			for _, d := range run.Decisions["A"] {
				if same(d) {
					c.Reproduced, evalTime = "Model A (legacy running intersection): same band, stop and target as production", d.EvalTime
					if math.Abs(d.Stop-tr.Invalidation) > reproductionTolerance || math.Abs(d.Target-tr.Target) > reproductionTolerance {
						c.Reproduced = "Model A: same band, stop or target differs"
					}
					break
				}
			}
		}
		if evalTime == 0 {
			c.Reproduced = "not reproduced"
			evalTime = confirm + 300
		}
		c.EvaluatedAt = stamp(evalTime)
		snap, ok := run.Snapshots[evalTime]
		if !ok {
			t.Fatalf("no snapshot at %s", c.EvaluatedAt)
		}
		side := structuralconfluence.Buy
		if tr.Direction == "SELL" {
			side = structuralconfluence.Sell
		}
		for _, f := range snap.Analysis.Facts {
			c.Facts = append(c.Facts, FactInfo{
				ID: f.ID, Source: f.Source, Low: f.Low, High: f.High, Formed: stamp(f.FormedAt), Confirmable: stamp(f.AvailableAt),
				Atoms: f.Atoms, Verified: f.Verified, UnverifiedReason: f.UnverifiedReason, Touches: f.Touches,
			})
		}
		var sameSide []structuralconfluence.Fact
		for _, f := range snap.Analysis.Facts {
			if f.Side == side && f.Verified && f.High > tr.Entry[0] && f.Low < tr.Entry[1] {
				sameSide = append(sameSide, f)
			}
		}
		groups := structuralconfluence.Groups(sameSide)
		c.Independent = len(groups)
		for _, g := range groups {
			if len(g.Members) > 1 {
				c.Dependent = append(c.Dependent, g.Members)
			}
		}
		widest := 0.0
		regions := append([]structuralconfluence.Region(nil), snap.Analysis.Regions...)
		lowestFirst(regions)
		for _, r := range regions {
			if r.Side != side {
				continue
			}
			c.Regions = append(c.Regions, RegionInfo{Low: r.Low, High: r.High, WidthPips: round(r.Width()/run.Pip, 1), Sources: len(r.Groups), MinSources: r.MinGroups, Support: r.Support, Episode: r.EpisodeID})
			if overlapPositive(r.Low, r.High, tr.Entry[0], tr.Entry[1]) {
				w := math.Min(r.High, tr.Entry[1]) - math.Max(r.Low, tr.Entry[0])
				widest = math.Max(widest, w)
			}
		}
		c.TrueOverlap = round(widest/run.Pip, 1)
		if widest > 0 {
			c.UnionOverlap = round((tr.Entry[1]-tr.Entry[0])/widest, 2)
		}
		// v3's decision: a published setup for the same reaction, else the reasons
		// recorded for the structures at this price around the evaluation.
		reasons := map[string]bool{}
		var published *Decision
		for _, d := range run.Decisions["D"] {
			if d.Direction == tr.Direction && abs64(d.ConfirmTime-confirm) <= 900 && overlapPositive(d.EntryLow, d.EntryHigh, tr.Entry[0]-run.Pip*50, tr.Entry[1]+run.Pip*50) {
				published = d
				break
			}
		}
		sameRegion := false
		if published != nil {
			w := math.Min(published.EntryHigh, tr.Entry[1]) - math.Max(published.EntryLow, tr.Entry[0])
			sameRegion = w >= 0.5*(published.EntryHigh-published.EntryLow)
			c.V3EntryInsideOriginalPips = round(math.Max(w, 0)/run.Pip, 1)
		}
		for k := int64(0); k <= 3; k++ {
			s, ok := run.Snapshots[confirm+300*k]
			if !ok {
				continue
			}
			for _, rej := range s.Analysis.Rejections {
				if string(rej.Direction) != tr.Direction {
					continue
				}
				if overlapPositive(rej.Low, rej.High, tr.Entry[0], tr.Entry[1]) || rej.ConfirmTime == confirm {
					reasons[rej.Reason] = true
				}
			}
		}
		if published != nil {
			c.V3Setup = published
			c.V3Decision = "published at a different price region from the same confirming candle"
			if sameRegion {
				c.V3Decision = "published at the same price region"
			}
		} else {
			c.V3Decision = "not published"
		}
		for r := range reasons {
			c.Reasons = append(c.Reasons, r)
		}
		sort.Strings(c.Reasons)
		out = append(out, c)
	}
	return out
}
