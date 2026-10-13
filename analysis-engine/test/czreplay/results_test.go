package czreplay_test

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"testing"
)

// PeriodResult is every model over one chronological period.
type PeriodResult struct {
	Label  string                  `json:"label"`
	Role   string                  `json:"role"`
	From   string                  `json:"from_utc"`
	To     string                  `json:"to_utc"`
	Models map[string]ModelSummary `json:"models"`
	// V3 (model D) against the frozen v2 (model B), and the structure-only
	// variant (model C) against it.
	V3AgainstV2        Comparison     `json:"v3_against_v2"`
	StructureAgainstV2 Comparison     `json:"structure_only_against_v2"`
	V3Rejections       map[string]int `json:"v3_rejections_by_reason"`
}

// Funnel is what the v3 structural engine and strategy made of one capture.
type Funnel struct {
	Evaluations          int            `json:"evaluated_candles"`
	DistinctStructures   int            `json:"distinct_structures_observed"`
	VerifiedStructures   int            `json:"structures_with_reproducible_provenance"`
	DistinctRegions      int            `json:"distinct_supported_regions_observed"`
	ConfirmedSetups      int            `json:"confirmed_setups_before_the_confluence_floor"`
	Published            int            `json:"published_confirmed_opportunities"`
	Rejections           map[string]int `json:"rejections_by_reason"`
	V2Published          int            `json:"v2_published_decisions"`
	StructureOnlyPublish int            `json:"structure_only_published_decisions"`
	CausalityRejectedC   int            `json:"structure_only_decisions_rejected_for_a_structure_created_after_the_touch"`
	OtherCandidates      int            `json:"other_strategies_candidates"`
	OthersSHA256         string         `json:"other_strategies_candidates_sha256"`
	StrategyAnalyzeAgree bool           `json:"strategy_analysis_agrees_with_the_published_candidates"`
}

// reproductionTolerance is how far a reproduced price may differ from production's:
// the wire rounds prices to two decimals and the capture holds a little less
// warm-up than production had, which moves the Wilder ATR in the third decimal.
const reproductionTolerance = 0.01

// Fidelity is whether the frozen v2 re-implementation reproduces what production
// published.
type Fidelity struct {
	Trade      string `json:"trade"`
	Symbol     string `json:"symbol"`
	Regime     string `json:"production_regime"`
	Reproduced bool   `json:"reproduced"`
	Detail     string `json:"detail"`
}

// Results is the machine-readable certification of the replay.
type Results struct {
	Description string                  `json:"description"`
	Method      []string                `json:"method"`
	Config      map[string]any          `json:"confluence_zone_v3_xau_parameters"`
	Captures    map[string]string       `json:"captures_sha256"`
	Periods     map[string]PeriodResult `json:"periods"`
	Funnel      map[string]Funnel       `json:"funnel_by_capture"`
	Fidelity    []Fidelity              `json:"v2_reproduction_of_production_trades"`
	SevenXAU    []Case                  `json:"seven_xau_trades"`
	Limits      []string                `json:"limits"`
}

func fileSHA(t testing.TB, name string) string {
	raw, err := os.ReadFile(testdata(name))
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func buildResults(t *testing.T, rs map[string]*Run) Results {
	doc := loadDoc(t)
	res := Results{
		Description: "Confluence Zone v3 replay certification over the committed real production captures. Hypothetical, causal fills (a fill needs a later candle that trades to the proximal edge; stop wins a same-candle tie); no spread, slippage, commission, M1 trigger or broker behaviour. Model A is the legacy running-intersection detector (frozen baseline data), B the Python-parity union detector (v2, re-implemented in this package), C the v3 structural engine with v2's confirmation, stop and target, D the full v3 strategy as the engine publishes it.",
		Method: []string{
			"every capture is replayed through the production engine path (replaycapture.Replay) candle by candle; the models are evaluated on the same closed candles",
			"decisions are thinned to one per symbol, direction and hour (overlapping re-confirmations are not independent trades)",
			"a decision is executable only if its risk from the proximal edge is within the instrument's execution stop cap (the Algo Bot refuses a plan beyond it); statistics are on executable filled decisions, 'as published' ignores the cap",
			"single_target_r: the published stop and target; ladder_r: 1R-4R closing 40/20/20/20 with break-even after the first, the stop widened to the instrument's floor when tighter",
			"intervals are seeded 95 % bootstrap over the thinned decisions, which treats them as independent and is optimistic",
			"development and held-out periods are chronological and disjoint; no coefficient was chosen on a held-out period",
			"winners/losers removed are judged by the baseline's own simulated single-target outcome; a v3 decision counterparts a v2 one when direction matches, the confirmation is within two candles and the entries overlap",
		},
		Config: map[string]any{}, Captures: map[string]string{}, Periods: map[string]PeriodResult{}, Funnel: map[string]Funnel{},
		Limits: []string{
			"7 XAU trades and 10 FX trades are the only live outcomes; the replay is hypothetical and does not model the executor",
			"a capture holds 11-12 days of XAU and 11 days of FX; one September XAU window (14-21 Sep) is the only older sample",
			"Model A is replayed from a frozen data file produced by the legacy engine revision; it is a what-if outside its production period (before 6 Oct 09:33 UTC)",
			"USDJPY production trades of 4, 7 and 12 Oct could not be reproduced from the captures (the audit found the same)",
			"correlated, overlapping setups are thinned but the bootstrap still treats the remainder as independent",
		},
	}
	settings := symbolSettings(t, doc, "XAU")
	for k, v := range strategyParams(t, settings, "confluence_zone") {
		res.Config[k] = v
	}
	for _, c := range captures {
		res.Captures[c.File] = fileSHA(t, c.File)
	}
	for _, p := range periods {
		runList := runsFor(rs, p)
		pr := PeriodResult{Label: p.Label, Role: p.Role, From: stamp(p.From), To: stamp(p.To), Models: map[string]ModelSummary{}, V3Rejections: map[string]int{}}
		for _, m := range []string{"A", "B", "C", "D"} {
			pr.Models[m] = summarize(m, decisionsOf(runList, m, p))
		}
		pr.V3AgainstV2 = compare("B", "D", runList, p)
		pr.StructureAgainstV2 = compare("B", "C", runList, p)
		for _, r := range runList {
			for _, rej := range r.Rejections {
				if rej.FirstSeen >= p.From && rej.FirstSeen < p.To {
					pr.V3Rejections[rej.Reason]++
				}
			}
		}
		res.Periods[p.Key] = pr
	}
	for _, c := range captures {
		r := rs[c.Name]
		f := Funnel{
			Evaluations: r.Evaluations, DistinctStructures: len(r.FactIDs), VerifiedStructures: len(r.VerifiedFactIDs),
			DistinctRegions: len(r.RegionEpisodes), ConfirmedSetups: len(r.ConfirmedSetups), Published: len(r.Decisions["D"]),
			Rejections: map[string]int{}, V2Published: len(r.Decisions["B"]), StructureOnlyPublish: len(r.Decisions["C"]),
			CausalityRejectedC: r.CausalityRejectedC, OtherCandidates: r.OtherCandidates, OthersSHA256: r.OthersSHA, StrategyAnalyzeAgree: r.Inconsistent == 0,
		}
		for _, rej := range r.Rejections {
			f.Rejections[rej.Reason]++
		}
		res.Funnel[c.Name] = f
	}
	res.Fidelity = fidelity(t, rs)
	res.SevenXAU = buildCases(t, rs)
	return res
}

// fidelity checks, against the production ledger, that the frozen v2 re-implementation
// publishes exactly what production published (band, stop and target).
func fidelity(t *testing.T, rs map[string]*Run) []Fidelity {
	byCapture := map[string]*Run{}
	for _, c := range captures {
		if c.Name != "XAU Sep" {
			byCapture[c.Symbol] = rs[c.Name]
		}
	}
	var out []Fidelity
	for _, tr := range loadLedger(t) {
		f := Fidelity{Trade: tr.CloseTime[5:16] + " " + tr.Direction, Symbol: tr.Symbol, Regime: tr.Regime}
		r, ok := byCapture[tr.Symbol]
		if !ok || tr.Regime != "frozen_python_publisher_union" {
			f.Detail = "not applicable: " + map[bool]string{true: "legacy-regime trade (model A)", false: "symbol not reproducible from the captures"}[tr.Regime != "frozen_python_publisher_union"]
			out = append(out, f)
			continue
		}
		confirm := utc(tr.AEConfirm[:16])
		for _, d := range r.Decisions["B"] {
			if d.Direction == tr.Direction && d.ConfirmTime == confirm && abs(d.EntryLow-tr.Entry[0]) < 0.006 && abs(d.EntryHigh-tr.Entry[1]) < 0.006 {
				f.Reproduced = abs(d.Stop-tr.Invalidation) < reproductionTolerance && abs(d.Target-tr.Target) < reproductionTolerance
				f.Detail = fmt.Sprintf("band [%.5g, %.5g] stop %.5g target %.5g", d.EntryLow, d.EntryHigh, d.Stop, d.Target)
				break
			}
		}
		if f.Detail == "" {
			f.Detail = "no frozen v2 decision with the production band and confirmation"
		}
		out = append(out, f)
	}
	return out
}

func abs(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}

func marshal(res Results) []byte {
	raw, err := json.MarshalIndent(res, "", " ")
	if err != nil {
		panic(err)
	}
	return append(raw, '\n')
}

// TestCertification replays every capture, checks the invariants the contract
// promises, and (with CZ_RESULTS_OUT) writes the machine-readable results.
func TestCertification(t *testing.T) {
	rs := runAll(t)
	res := buildResults(t, rs)

	// The strategy's own analysis and the engine's published candidates agree.
	for name, r := range rs {
		if r.Inconsistent != 0 {
			t.Errorf("%s: the strategy's analysis and the engine's candidates disagreed %d times", name, r.Inconsistent)
		}
	}
	// v3 publishes no invented target, only executable setups, every setup valid.
	for name, r := range rs {
		for _, d := range r.Decisions["D"] {
			if d.TargetKind != "nearest_opposing_liquidity" {
				t.Errorf("%s: v3 published target kind %q", name, d.TargetKind)
			}
			if !d.Executable {
				t.Errorf("%s: v3 published a setup beyond the execution cap: %+v", name, d)
			}
			if d.Single.Result == "invalid" {
				t.Errorf("%s: a v3 setup could not be simulated: %+v", name, d)
			}
		}
	}
	// Other strategies are untouched: their candidates equal what master published.
	raw, err := os.ReadFile(testdata("confluence-zone-other-strategies-baseline-master-f18179f.json"))
	if err != nil {
		t.Fatal(err)
	}
	var master map[string]struct {
		Candidates int    `json:"candidates"`
		SHA256     string `json:"sha256"`
	}
	if err := json.Unmarshal(raw, &master); err != nil {
		t.Fatal(err)
	}
	for _, c := range captures {
		want, ok := master[c.File]
		if !ok {
			t.Fatalf("no master baseline for %s", c.File)
		}
		got := rs[c.Name]
		if got.OtherCandidates != want.Candidates || got.OthersSHA != want.SHA256 {
			t.Errorf("%s: the other strategies' candidates changed: %d %s, master %d %s", c.File, got.OtherCandidates, got.OthersSHA, want.Candidates, want.SHA256)
		}
	}
	// v2 is reproduced exactly wherever production ran it.
	reproduced := 0
	for _, f := range res.Fidelity {
		if f.Regime == "frozen_python_publisher_union" && f.Symbol != "USDJPY" {
			if !f.Reproduced {
				t.Errorf("frozen v2 re-implementation does not reproduce %s %s: %s", f.Symbol, f.Trade, f.Detail)
			}
			reproduced++
		}
	}
	if reproduced < 8 {
		t.Errorf("only %d production trades checked for fidelity", reproduced)
	}

	got := marshal(res)
	if out := os.Getenv("CZ_RESULTS_OUT"); out != "" {
		if err := os.WriteFile(out, got, 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	want, err := os.ReadFile(repoFile("docs", "strategies", "confluence-zone-v3-replay-results.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(want, got) {
		t.Fatal("the committed replay results drifted: regenerate with CZ_RESULTS_OUT=docs/strategies/confluence-zone-v3-replay-results.json go test ./test/czreplay -run TestCertification and review the diff")
	}
}
