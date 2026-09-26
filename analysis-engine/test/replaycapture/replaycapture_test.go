package replaycapture_test

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

func repoPath(t *testing.T, parts ...string) string {
	t.Helper()
	_, file, _, _ := runtime.Caller(0)
	root := filepath.Join(filepath.Dir(file), "..", "..", "..")
	return filepath.Join(append([]string{root}, parts...)...)
}

func capturePath(t *testing.T) string {
	return repoPath(t, "contracts", "analysis", "replay", "xau-production-capture-20260921.json")
}

func writeCapture(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "capture.json")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestCommittedCaptureLoadsAndIsInternallyConsistent(t *testing.T) {
	c, err := replaycapture.Load(capturePath(t))
	if err != nil {
		t.Fatal(err)
	}
	want := map[market.Timeframe]int{market.M5: 1500, market.M15: 600, market.H1: 300}
	for tf, n := range want {
		candles, err := c.Candles(tf)
		if err != nil || len(candles) != n {
			t.Fatalf("%s: got %d bars, err=%v, want %d", tf, len(candles), err, n)
		}
	}
	var prov map[string]any
	if err := json.Unmarshal(c.Provenance, &prov); err != nil || prov["source"] == nil || prov["independent_corroboration"] == nil {
		t.Fatalf("provenance must state its source and corroboration: %v %v", prov, err)
	}
}

func TestLoadRejectsMalformedInputInsteadOfRepairingIt(t *testing.T) {
	cols := `"columns":["t","open","high","low","close","volume"]`
	cases := map[string]string{
		"wrong version":           `{"version":2,"symbol":"XAU",` + cols + `,"timeframes":{"M5":[[0,1,2,1,2,1]]}}`,
		"missing symbol":          `{"version":1,` + cols + `,"timeframes":{"M5":[[0,1,2,1,2,1]]}}`,
		"wrong columns":           `{"version":1,"symbol":"XAU","columns":["t","o"],"timeframes":{"M5":[[0,1]]}}`,
		"unordered":               `{"version":1,"symbol":"XAU",` + cols + `,"timeframes":{"M5":[[600,2,3,1,2,1],[300,2,3,1,2,1]]}}`,
		"duplicate":               `{"version":1,"symbol":"XAU",` + cols + `,"timeframes":{"M5":[[300,2,3,1,2,1],[300,2,3,1,2,1]]}}`,
		"misaligned to timeframe": `{"version":1,"symbol":"XAU",` + cols + `,"timeframes":{"M5":[[301,2,3,1,2,1]]}}`,
		"high below close":        `{"version":1,"symbol":"XAU",` + cols + `,"timeframes":{"M5":[[300,2,2,1,3,1]]}}`,
		"non-positive price":      `{"version":1,"symbol":"XAU",` + cols + `,"timeframes":{"M5":[[300,0,1,0,1,1]]}}`,
		"unknown timeframe":       `{"version":1,"symbol":"XAU",` + cols + `,"timeframes":{"M7":[[420,2,3,1,2,1]]}}`,
	}
	for name, body := range cases {
		if _, err := replaycapture.Load(writeCapture(t, body)); err == nil {
			t.Errorf("%s: expected an error", name)
		}
	}
}

func h1(t int64, o, h, l, c float64) market.Candle {
	return market.Candle{Time: t, Open: o, High: h, Low: l, Close: c, Volume: 10}
}

func TestDeriveH4KeepsOnlyCompleteUTCAlignedBuckets(t *testing.T) {
	// 00:00-04:00 complete; 04:00 bucket missing its 06:00 bar; 08:00 complete.
	const hr = int64(3600)
	in := []market.Candle{
		h1(0, 10, 12, 9, 11), h1(hr, 11, 15, 10, 14), h1(2*hr, 14, 14, 8, 9), h1(3*hr, 9, 11, 9, 10),
		h1(4*hr, 10, 11, 10, 11), h1(5*hr, 11, 12, 11, 12), h1(7*hr, 12, 13, 12, 13),
		h1(8*hr, 20, 21, 19, 20), h1(9*hr, 20, 25, 20, 24), h1(10*hr, 24, 24, 22, 23), h1(11*hr, 23, 23, 21, 22),
		h1(12*hr, 22, 22, 21, 21), // a lone trailing bar: incomplete bucket
	}
	got := replaycapture.DeriveH4(in)
	if len(got) != 2 {
		t.Fatalf("want 2 complete buckets, got %d: %+v", len(got), got)
	}
	first, second := got[0], got[1]
	if first.Time != 0 || first.Open != 10 || first.High != 15 || first.Low != 8 || first.Close != 10 || first.Volume != 40 {
		t.Fatalf("bucket 1 wrong: %+v", first)
	}
	if second.Time != 8*hr || second.Open != 20 || second.High != 25 || second.Low != 19 || second.Close != 22 {
		t.Fatalf("bucket 2 wrong: %+v", second)
	}
}

func TestEventsAreCausalOrderedAndHigherTimeframeFirst(t *testing.T) {
	c, err := replaycapture.Load(capturePath(t))
	if err != nil {
		t.Fatal(err)
	}
	byTF := map[market.Timeframe][]market.Candle{}
	for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
		byTF[tf], _ = c.Candles(tf)
	}
	byTF[market.H4] = replaycapture.DeriveH4(byTF[market.H1])
	events, err := replaycapture.Events("XAU", byTF, marketdata.EventOriginReplay)
	if err != nil {
		t.Fatal(err)
	}
	total := 0
	for _, candles := range byTF {
		total += len(candles)
	}
	if len(events) != total {
		t.Fatalf("every bar delivered exactly once: %d vs %d", len(events), total)
	}
	closeAt := func(e marketdata.BarEvent) int64 {
		m, _ := e.Timeframe.Minutes()
		return e.Candle.Time + int64(m)*60
	}
	for i := 1; i < len(events); i++ {
		prev, cur := events[i-1], events[i]
		if closeAt(cur) < closeAt(prev) {
			t.Fatalf("event %d delivered before an earlier close", i)
		}
		if closeAt(cur) == closeAt(prev) {
			pm, _ := prev.Timeframe.Minutes()
			cm, _ := cur.Timeframe.Minutes()
			if cm > pm {
				t.Fatalf("at close %d a lower timeframe (%s) preceded a higher one (%s)", closeAt(cur), prev.Timeframe, cur.Timeframe)
			}
		}
	}
	again, _ := replaycapture.Events("XAU", byTF, marketdata.EventOriginReplay)
	for i := range events {
		if events[i] != again[i] {
			t.Fatalf("event order is not deterministic at %d", i)
		}
	}
}

func goldenCandidate() opportunity.Candidate {
	return opportunity.Candidate{
		ID: "opp_x", Strategy: "supply", StrategyVersion: "v2", Symbol: "XAU", ObservedTimeframe: market.M5, Direction: market.Sell,
		Entry:        opportunity.EntryZone{Low: 4352.5, High: 4356},
		Invalidation: market.PriceLevel{Price: 4358.75, Label: "supply_zone_invalidated"},
		Targets:      []opportunity.Target{{Price: market.PriceLevel{Price: 4344, Label: "t"}}},
		Evidence:     []opportunity.Evidence{{Code: "e1"}},
		Quality:      opportunity.StrategyQuality{Overall: 0.8},
		FormedAt:     900, CreatedAt: 1000, ExpiresAt: 2000,
		Technical: &opportunity.TechnicalContext{ATR: 3.6, ReferencePrice: 4351.9, ReferenceTime: 1000},
	}
}

func TestCreationEnvelopeIsDeterministicValidAndReplayStamped(t *testing.T) {
	opts := replaycapture.EnvelopeOptions{ConfigVersion: 3, ConfigFingerprint: "fp"}
	a, err := replaycapture.CreationEnvelope(goldenCandidate(), opts)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := replaycapture.CreationEnvelope(goldenCandidate(), opts)
	if !bytes.Equal(a, b) {
		t.Fatal("same candidate must encode to identical bytes")
	}
	var env map[string]any
	if err := json.Unmarshal(a, &env); err != nil {
		t.Fatal(err)
	}
	if env["event_id"] != "replay-opp_x" || env["producer"] != "apexvoid-analysis-engine" || env["event_type"] != "analysis.opportunity.v1" ||
		env["occurred_at"].(float64) != 1000 || env["produced_at"].(float64) != 1000 {
		t.Fatalf("unexpected envelope: %v", env)
	}
	payload := env["payload"].(map[string]any)
	if payload["id"] != "opp_x" || payload["technical_context"] == nil {
		t.Fatalf("payload must be the producer's own adapter output incl. technical_context: %v", payload)
	}
}

// ---- the real replay --------------------------------------------------------------

type replayMeta struct {
	Description       string `json:"description"`
	Capture           string `json:"capture"`
	TotalDiscovered   int    `json:"total_discovered"`
	TotalEnvelopesSHA string `json:"total_envelopes_sha256"`
	GoldenCount       int    `json:"golden_supply_demand_confirmed_count"`
	DerivedH4         bool   `json:"h4_derived_from_h1_utc_aligned"`
	Timeframes        int    `json:"timeframes_dispatched"`
	Events            int    `json:"events_dispatched"`
}

func replay(t *testing.T) (*replaycapture.Result, [][]byte) {
	t.Helper()
	doc, err := config.ResolveDocument(repoPath(t, "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	c, err := replaycapture.Load(capturePath(t))
	if err != nil {
		t.Fatal(err)
	}
	result, err := replaycapture.Replay(doc, c, market.M5, replaycapture.Options{})
	if err != nil {
		t.Fatal(err)
	}
	lines := make([][]byte, len(result.Discovered))
	for i, candidate := range result.Discovered {
		if lines[i], err = replaycapture.CreationEnvelope(candidate, result.Options); err != nil {
			t.Fatal(err)
		}
	}
	return result, lines
}

// normalizeEnvelope pins the one field that legitimately varies with unrelated
// configuration edits: config_fingerprint hashes the whole resolved config, so a
// change to any leaf (e.g. a new technical_authority setting) must not churn the
// golden. Numbers keep their exact text (UseNumber); keys come out sorted.
func normalizeEnvelope(t *testing.T, line []byte) []byte {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(line))
	decoder.UseNumber()
	var envelope map[string]any
	if err := decoder.Decode(&envelope); err != nil {
		t.Fatal(err)
	}
	envelope["config_fingerprint"] = "<config-fingerprint>"
	out, err := json.Marshal(envelope)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func isConfirmedZoneReaction(c opportunity.Candidate) bool {
	return (c.Strategy == "supply" || c.Strategy == "demand") && c.ObservedTimeframe == market.M5 &&
		c.Technical != nil && c.Technical.Confirmation != nil
}

func TestRealCaptureReplayIsDeterministicAndMatchesTheCommittedGolden(t *testing.T) {
	result, lines := replay(t)
	_, again := replay(t)
	if len(lines) != len(again) {
		t.Fatalf("two replays discovered %d vs %d opportunities", len(lines), len(again))
	}
	for i := range lines {
		if !bytes.Equal(lines[i], again[i]) {
			t.Fatalf("replay is not deterministic at opportunity %d", i)
		}
	}
	var golden bytes.Buffer
	all := sha256.New()
	confirmed := 0
	for i, candidate := range result.Discovered {
		normalized := normalizeEnvelope(t, lines[i])
		all.Write(normalized)
		all.Write([]byte("\n"))
		if isConfirmedZoneReaction(candidate) {
			golden.Write(normalized)
			golden.WriteByte('\n')
			confirmed++
		}
	}
	meta := replayMeta{
		Description:       "S14C Go replay of the committed real XAU capture (regenerate with UPDATE_GOLDEN=1 go test ./test/replaycapture)",
		Capture:           "xau-production-capture-20260921.json",
		TotalDiscovered:   len(result.Discovered),
		TotalEnvelopesSHA: hex.EncodeToString(all.Sum(nil)),
		GoldenCount:       confirmed,
		DerivedH4:         result.DerivedH4,
		Timeframes:        result.Timeframes,
		Events:            result.Events,
	}
	goldenPath := repoPath(t, "contracts", "analysis", "replay", "go-supply-demand-confirmed-envelopes-xau-20260921.jsonl")
	metaPath := repoPath(t, "contracts", "analysis", "replay", "go-replay-xau-20260921.meta.json")
	if os.Getenv("UPDATE_GOLDEN") == "1" {
		if err := os.WriteFile(goldenPath, golden.Bytes(), 0o644); err != nil {
			t.Fatal(err)
		}
		raw, _ := json.MarshalIndent(meta, "", "  ")
		if err := os.WriteFile(metaPath, append(raw, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	wantGolden, err := os.ReadFile(goldenPath)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(wantGolden, golden.Bytes()) {
		t.Fatal("confirmed supply/demand envelopes drifted from the committed golden (rerun with UPDATE_GOLDEN=1 and review the diff)")
	}
	var wantMeta replayMeta
	raw, err := os.ReadFile(metaPath)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &wantMeta); err != nil {
		t.Fatal(err)
	}
	if wantMeta != meta {
		t.Fatalf("replay totals drifted: got %+v want %+v", meta, wantMeta)
	}
}

func TestH4IsNotSynthesisedUnlessAskedBecauseTheLiveFeedHasNone(t *testing.T) {
	doc, err := config.ResolveDocument(repoPath(t, "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	c, err := replaycapture.Load(capturePath(t))
	if err != nil {
		t.Fatal(err)
	}
	faithful, err := replaycapture.Replay(doc, c, market.M5, replaycapture.Options{})
	if err != nil {
		t.Fatal(err)
	}
	explored, err := replaycapture.Replay(doc, c, market.M5, replaycapture.Options{DeriveH4: true})
	if err != nil {
		t.Fatal(err)
	}
	if faithful.DerivedH4 || faithful.Timeframes != 3 {
		t.Fatalf("default replay must use only the captured timeframes: %+v", faithful)
	}
	if !explored.DerivedH4 || explored.Timeframes != 4 || explored.Events <= faithful.Events {
		t.Fatalf("derive-h4 must add H4 events: %+v", explored)
	}
	for _, candidate := range faithful.Discovered {
		if candidate.Technical == nil {
			continue
		}
		for _, h := range candidate.Technical.HigherTimeframes {
			if h.Timeframe == market.H4 {
				t.Fatalf("%s: an H4 bias appeared although no H4 was delivered", candidate.ID)
			}
		}
	}
}

func TestEveryConfirmedReactionCarriesItsCausalFacts(t *testing.T) {
	result, _ := replay(t)
	byStrategy := map[string]int{}
	for _, c := range result.Discovered {
		if !isConfirmedZoneReaction(c) {
			continue
		}
		byStrategy[string(c.Strategy)]++
		tech := c.Technical
		conf := tech.Confirmation
		if conf.ConfirmationBarTime < conf.TouchBarTime || conf.ConfirmationBarTime > tech.ReferenceTime || tech.ReferenceTime > c.CreatedAt {
			t.Fatalf("%s: reaction timing not causal: touch=%d confirm=%d ref=%d created=%d", c.ID, conf.TouchBarTime, conf.ConfirmationBarTime, tech.ReferenceTime, c.CreatedAt)
		}
		if !(tech.ATR > 0) || !(tech.ReferencePrice > 0) {
			t.Fatalf("%s: missing ATR/reference price", c.ID)
		}
		for _, h := range tech.HigherTimeframes {
			m, _ := h.Timeframe.Minutes()
			if h.ReferenceTime+int64(m)*60 > tech.ReferenceTime+5*60 {
				t.Fatalf("%s: higher-timeframe bar %s@%d had not closed at the observation", c.ID, h.Timeframe, h.ReferenceTime)
			}
		}
	}
	keys := make([]string, 0)
	for k := range byStrategy {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	if len(keys) != 2 || keys[0] != "demand" || keys[1] != "supply" {
		t.Fatalf("the real capture must yield confirmed reactions for both reviewed scopes, got %v", byStrategy)
	}
}
