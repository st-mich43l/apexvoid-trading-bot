package manuallevels_test

import (
	"sort"
	"strings"
)

// aggregate summarises attributions with counts, shares and quantiles only: no signal row, price or note.
func aggregate(all []attribution, used map[string][]string) map[string]any {
	coverage := map[string]any{"signals": len(all)}
	bySymbol, byState, frames := map[string]int{}, map[string]int{}, map[string]int{}
	covered, maxAge := 0, int64(0)
	for _, a := range all {
		bySymbol[a.Symbol]++
		if !a.Covered {
			byState["uncovered"]++
			continue
		}
		covered++
		byState[a.StateTF]++
		for _, f := range a.Frames {
			frames[f]++
		}
		if a.AgeSeconds > maxAge {
			maxAge = a.AgeSeconds
		}
	}
	coverage["by_symbol"], coverage["covered"], coverage["state_timeframe"] = bySymbol, covered, byState
	coverage["frames_available"], coverage["state_age_seconds_max"] = frames, maxAge
	sections := map[string]any{"all": section(all)}
	byType, byStream := map[string][]attribution{}, map[string][]attribution{}
	for _, a := range all {
		byType[a.SetupType] = append(byType[a.SetupType], a)
		byStream[a.Stream] = append(byStream[a.Stream], a)
	}
	types, streams := map[string]any{}, map[string]any{}
	for k, v := range byType {
		types[k] = section(v)
	}
	for k, v := range byStream {
		streams[k] = section(v)
	}
	sections["by_setup_type"], sections["by_stream"] = types, streams
	return map[string]any{
		"description":   "Manual-book level attribution: which structures the owner's entries and stops sit on and whether the analysis engine publishes a matching candidate. Aggregates only. 'explains' = the proximal entry is inside the structure or within 0.25 M5 ATR of it; distances are to the nearest structure of the family on that timeframe, in M5 ATR at the state bar.",
		"captures_used": used, "coverage": coverage, "results": sections,
	}
}

func dist(values []float64) map[string]float64 {
	return map[string]float64{"p25": round(quantile(values, .25)), "median": round(quantile(values, .5)), "p75": round(quantile(values, .75)), "p90": round(quantile(values, .9))}
}

func section(all []attribution) map[string]any {
	var covered []attribution
	for _, a := range all {
		if a.Covered {
			covered = append(covered, a)
		}
	}
	out := map[string]any{"signals": len(all), "covered": len(covered)}
	symbols := map[string]bool{"all": true}
	for _, a := range covered {
		symbols[a.Symbol] = true
	}

	// Explained share and the full distance distribution per family x timeframe x symbol.
	type acc struct {
		n, explained, nullN, nullExplained int
		atr, pips                          []float64
	}
	cells := map[string]*acc{}
	explainedAny := map[string][2]int{}
	for _, a := range covered {
		for key, row := range a.Entry {
			for _, sym := range []string{a.Symbol, "all"} {
				id := key + "|" + sym
				if cells[id] == nil {
					cells[id] = &acc{}
				}
				c := cells[id]
				c.n++
				c.atr, c.pips = append(c.atr, row.ATR), append(c.pips, row.Pips)
				if row.Explains {
					c.explained++
				}
				c.nullN += a.NullN
				c.nullExplained += a.NullExplained[key]
			}
		}
		for _, sym := range []string{a.Symbol, "all"} {
			v := explainedAny[sym]
			v[0]++
			if len(a.Explained) > 0 {
				v[1]++
			}
			explainedAny[sym] = v
		}
	}
	explained := map[string]any{}
	for id, c := range cells {
		explained[id] = map[string]any{"signals_with_the_family": c.n, "explained": c.explained, "share": share(c.explained, c.n), "chance_share": share(c.nullExplained, c.nullN), "distance_atr": dist(c.atr), "distance_pips_median": round(quantile(c.pips, .5))}
	}
	out["entry_explained"] = explained
	anyFamily := map[string]any{}
	for sym, v := range explainedAny {
		anyFamily[sym] = map[string]any{"n": v[0], "explained_by_at_least_one_family": v[1], "share": share(v[1], v[0])}
	}
	out["entry_explained_by_any_family"] = anyFamily

	// Stop distance and the structure the stop protects.
	stops := map[string]any{}
	stopSymbols := map[string][]attribution{"all": covered}
	for _, a := range covered {
		stopSymbols[a.Symbol] = append(stopSymbols[a.Symbol], a)
	}
	for sym, list := range stopSymbols {
		var pips, atr []float64
		in5070 := 0
		for _, a := range list {
			if !a.HasStop {
				continue
			}
			pips, atr = append(pips, a.StopPips), append(atr, a.StopATR)
			if a.StopPips >= 50 && a.StopPips <= 70 {
				in5070++
			}
		}
		stops[sym] = map[string]any{"n": len(pips), "pips": dist(pips), "atr": dist(atr), "within_50_70_pips": in5070, "share_within_50_70": share(in5070, len(pips))}
	}
	out["stop_distance"] = stops
	anchors, nearestAnchor, beyond := map[string]any{}, map[string]int{}, map[string]any{}
	gapATR, within, beyondATR := map[string][]float64{}, map[string]int{}, map[string][]float64{}
	for _, a := range covered {
		best, bestGap := "", 1e18
		for key, row := range a.Protected {
			gapATR[key] = append(gapATR[key], row.GapATR)
			if row.GapATR <= 0.5 {
				within[key]++
			}
			if row.GapATR < bestGap {
				best, bestGap = key, row.GapATR
			}
		}
		if best != "" {
			nearestAnchor[best]++
		}
		for key, row := range a.Beyond {
			beyondATR[key] = append(beyondATR[key], row.GapATR)
		}
	}
	for key, gaps := range gapATR {
		anchors[key] = map[string]any{"signals_with_a_protected_structure": len(gaps), "gap_atr": dist(gaps), "gap_within_0_5_atr": within[key]}
	}
	for key, gaps := range beyondATR {
		beyond[key] = map[string]any{"signals": len(gaps), "gap_atr": dist(gaps)}
	}
	out["stop_protects"], out["stop_anchor_nearest_family"], out["stop_nearest_beyond"] = anchors, nearestAnchor, beyond

	// Side and acceptance.
	side := map[string]any{}
	for _, action := range []string{"BUY", "SELL"} {
		n, levels, crossed := 0, 0, 0
		for _, a := range covered {
			if a.Action != action {
				continue
			}
			n++
			if a.HasLevel {
				levels++
				if a.LevelCrossed {
					crossed++
				}
			}
		}
		side[action] = map[string]any{"signals": n, "sit_on_a_horizontal_level": levels, "that_level_was_crossed_with_acceptance_in_the_prior_48_h1_bars": crossed, "first_touch": levels - crossed}
	}
	out["side"] = side
	pdh := 0
	for _, a := range covered {
		if a.PDHInM5Frame {
			pdh++
		}
	}
	out["pdh_pdl_present_in_the_m5_frame"] = map[string]any{"signals": len(covered), "with_pdh_pdl_in_the_150_bar_m5_frame": pdh}

	// AE recall.
	matched, keyLevel, nullMatched, nullN := 0, 0, 0, 0
	byStrategy, byPrimary := map[string]int{}, map[string][2]int{}
	misses, missPrimary := map[string]int{}, map[string]int{}
	missCount := 0
	for _, a := range covered {
		fam := a.Primary
		if i := strings.Index(fam, "|"); i >= 0 {
			fam = fam[:i]
		}
		if !a.HasPrimary {
			fam = "(none)"
		}
		nullMatched += a.NullAEMatched
		nullN += a.NullN
		v := byPrimary[fam]
		v[0]++
		if a.AEMatched {
			matched++
			v[1]++
			for _, s := range a.AEStrategies {
				byStrategy[s]++
			}
			if a.AEKeyLevel {
				keyLevel++
			}
		} else {
			missCount++
			missPrimary[fam]++
			seen := map[string]bool{}
			for _, key := range a.Explained {
				f := key
				if i := strings.Index(f, "|"); i >= 0 {
					f = f[:i]
				}
				if !seen[f] {
					seen[f] = true
					misses[f]++
				}
			}
		}
		byPrimary[fam] = v
	}
	families := map[string]any{}
	for fam, v := range byPrimary {
		families[fam] = map[string]any{"signals": v[0], "ae_matched": v[1]}
	}
	out["ae_recall"] = map[string]any{"signals": len(covered), "matched": matched, "share": share(matched, len(covered)), "chance_share": share(nullMatched, nullN), "key_level_matched": keyLevel,
		"matching_strategies": byStrategy, "by_primary_explaining_family": families}
	out["ae_misses"] = map[string]any{"signals": missCount, "explained_by_family_any": misses, "primary_family": missPrimary, "no_explaining_family": missPrimary["(none)"]}
	return out
}

func share(a, n int) float64 {
	if n == 0 {
		return 0
	}
	return round(float64(a) / float64(n))
}

var _ = sort.Strings
