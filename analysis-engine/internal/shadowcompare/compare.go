// Package shadowcompare performs semantic, not byte-for-byte, comparison of
// Python and Go strategy observations exported from identical bar streams.
package shadowcompare

import (
	"fmt"
	"math"
	"sort"
)

type PriceBand struct {
	Low  float64 `json:"low"`
	High float64 `json:"high"`
}
type Observation struct {
	Engine               string             `json:"engine"`
	ID                   string             `json:"id"`
	Symbol               string             `json:"symbol"`
	Timeframe            string             `json:"timeframe"`
	Strategy             string             `json:"strategy"`
	Direction            string             `json:"direction"`
	InputFingerprint     string             `json:"input_fingerprint"`
	ConfigFingerprint    string             `json:"config_fingerprint"`
	FormedAt             int64              `json:"formed_at"`
	ConfirmedAt          int64              `json:"confirmed_at"`
	Entry                PriceBand          `json:"entry"`
	Invalidation         float64            `json:"invalidation"`
	Targets              []float64          `json:"targets"`
	StructureFingerprint string             `json:"structure_fingerprint"`
	ZoneFingerprint      string             `json:"zone_fingerprint"`
	Quality              map[string]float64 `json:"quality"`
	MFE                  float64            `json:"mfe"`
	MAE                  float64            `json:"mae"`
	Outcome              string             `json:"outcome"`
}
type Difference struct {
	PythonID string `json:"python_id,omitempty"`
	GoID     string `json:"go_id,omitempty"`
	Category string `json:"category"`
	Detail   string `json:"detail"`
}
type Report struct {
	PythonCount int          `json:"python_count"`
	GoCount     int          `json:"go_count"`
	Matched     int          `json:"matched"`
	PythonOnly  int          `json:"python_only"`
	GoOnly      int          `json:"go_only"`
	Differences []Difference `json:"differences"`
}

func (o Observation) Validate() error {
	if o.Engine == "" || o.ID == "" || o.Symbol == "" || o.Timeframe == "" || o.Strategy == "" || (o.Direction != "BUY" && o.Direction != "SELL") || o.InputFingerprint == "" || o.ConfigFingerprint == "" || o.ConfirmedAt < 0 || o.Entry.Low > o.Entry.High || len(o.Targets) == 0 {
		return fmt.Errorf("incomplete comparison observation %q", o.ID)
	}
	return nil
}

func ValidCategory(category string) bool {
	switch category {
	case "intended_redesign", "confirmed_regression", "configuration_difference", "data_mismatch", "unresolved_discrepancy":
		return true
	default:
		return false
	}
}

// Compare greedily matches the nearest same-thesis observation inside the
// confirmation tolerance. A dispositions map may classify a pair as
// intended_redesign, confirmed_regression, configuration_difference,
// data_mismatch, or unresolved_discrepancy; otherwise evidence determines
// config/data mismatches and remaining differences stay unresolved.
func Compare(python, goSide []Observation, toleranceSeconds int64, dispositions map[string]string) Report {
	r := Report{PythonCount: len(python), GoCount: len(goSide)}
	used := make([]bool, len(goSide))
	for _, p := range python {
		best := -1
		bestDelta := int64(math.MaxInt64)
		for i, g := range goSide {
			if used[i] || p.Symbol != g.Symbol || p.Timeframe != g.Timeframe || p.Strategy != g.Strategy || p.Direction != g.Direction {
				continue
			}
			d := abs64(p.ConfirmedAt - g.ConfirmedAt)
			if d <= toleranceSeconds && d < bestDelta {
				best, bestDelta = i, d
			}
		}
		if best < 0 {
			r.PythonOnly++
			r.Differences = append(r.Differences, Difference{PythonID: p.ID, Category: "unresolved_discrepancy", Detail: "no Go semantic match"})
			continue
		}
		used[best] = true
		r.Matched++
		g := goSide[best]
		if detail := differenceDetail(p, g); detail != "" {
			category := categoryFor(p, g, dispositions)
			r.Differences = append(r.Differences, Difference{PythonID: p.ID, GoID: g.ID, Category: category, Detail: detail})
		}
	}
	for i, g := range goSide {
		if !used[i] {
			r.GoOnly++
			r.Differences = append(r.Differences, Difference{GoID: g.ID, Category: "unresolved_discrepancy", Detail: "no Python semantic match"})
		}
	}
	sort.Slice(r.Differences, func(i, j int) bool {
		return r.Differences[i].PythonID+r.Differences[i].GoID < r.Differences[j].PythonID+r.Differences[j].GoID
	})
	return r
}
func categoryFor(p, g Observation, d map[string]string) string {
	if v := d[p.ID+"|"+g.ID]; v != "" {
		return v
	}
	if p.InputFingerprint != g.InputFingerprint {
		return "data_mismatch"
	}
	if p.ConfigFingerprint != g.ConfigFingerprint {
		return "configuration_difference"
	}
	return "unresolved_discrepancy"
}
func differenceDetail(p, g Observation) string {
	parts := ""
	add := func(v string) {
		if parts != "" {
			parts += "; "
		}
		parts += v
	}
	if p.StructureFingerprint != g.StructureFingerprint {
		add("structure")
	}
	if p.ZoneFingerprint != g.ZoneFingerprint {
		add("zone")
	}
	if p.FormedAt != g.FormedAt || p.ConfirmedAt != g.ConfirmedAt {
		add("formation_confirmation_timing")
	}
	if !overlap(p.Entry, g.Entry) {
		add("entry")
	}
	if p.Invalidation != g.Invalidation {
		add("invalidation")
	}
	if !floatsEqual(p.Targets, g.Targets) {
		add("targets")
	}
	if p.Outcome != g.Outcome {
		add("outcome")
	}
	if p.MFE != g.MFE || p.MAE != g.MAE {
		add("mfe_mae")
	}
	if !qualityEqual(p.Quality, g.Quality) {
		add("quality")
	}
	return parts
}
func overlap(a, b PriceBand) bool { return a.Low <= b.High && b.Low <= a.High }
func abs64(v int64) int64 {
	if v < 0 {
		return -v
	}
	return v
}
func floatsEqual(a, b []float64) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func qualityEqual(a, b map[string]float64) bool {
	if len(a) != len(b) {
		return false
	}
	for key, value := range a {
		if b[key] != value {
			return false
		}
	}
	return true
}
