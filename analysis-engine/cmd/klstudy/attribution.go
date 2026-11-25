package main

// klstudy attribution (PR-KL14, read-only): replays the SAME bar captures through several builds of cmd/replay (one per pinned commit, each with its own
// configuration), keeps the setups one strategy published, and lists what each consecutive step adds and removes. It never modifies a build or a capture:
// old commits are built from git worktrees by the operator and passed in as binaries.
//
//	go run ./cmd/klstudy attribution -out DIR \
//	  -build v7=/bin/replay-v7,/wt/v7/config/apexvoid.yml -build v8=... -build v13=/bin/replay-master,../config/apexvoid.yml \
//	  -capture sep=xau_sep.json -capture nov13=internal/replaycapture/testdata/key_level_xau_2026-11-13.json
//
// Outputs, all deterministic for the same inputs:
//
//	DIR/<capture>/<build>.jsonl   one {"build","capture","key","creation","terminal":null} per published setup, in the outcome labeller's offline input shape
//	DIR/attribution.json          per capture: bar coverage, setups per build, and per step the keys removed and added
//
// A setup's key is direction|created_at|n (n orders setups of one direction on one bar by entry low), so the same trade idea matches across versions whose
// opportunity ids differ (the id hashes the strategy version). Labelling is done by algo-bot/scripts/kl_attribution.py label, from these files.

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
)

type buildSpec struct {
	Label, Replay, Config string
}

type captureSpec struct {
	Name, Path string
}

type multiFlag []string

func (m *multiFlag) String() string     { return strings.Join(*m, ";") }
func (m *multiFlag) Set(v string) error { *m = append(*m, v); return nil }

type attrSetup struct {
	Build     string          `json:"build"`
	Capture   string          `json:"capture"`
	Key       string          `json:"key"`
	Creation  json.RawMessage `json:"creation"`
	Terminal  json.RawMessage `json:"terminal"`
	direction string
	createdAt int64
	entryLow  float64
}

type attrStep struct {
	From    string   `json:"from"`
	To      string   `json:"to"`
	Removed []string `json:"removed"`
	Added   []string `json:"added"`
}

type attrCapture struct {
	Name    string           `json:"name"`
	Path    string           `json:"path"`
	Bars    map[string]int   `json:"bars"`
	First   map[string]int64 `json:"first_open"`
	Last    map[string]int64 `json:"last_open"`
	Setups  map[string]int   `json:"setups"`
	Builds  []string         `json:"builds"`
	Steps   []attrStep       `json:"steps"`
	Symbol  string           `json:"symbol"`
	Primary string           `json:"primary"`
}

type attrReport struct {
	Strategy string        `json:"strategy"`
	Builds   []buildSpec   `json:"builds"`
	Captures []attrCapture `json:"captures"`
}

func parseBuild(raw string) (buildSpec, error) {
	label, rest, ok := strings.Cut(raw, "=")
	replay, cfg, ok2 := strings.Cut(rest, ",")
	if !ok || !ok2 || label == "" || replay == "" || cfg == "" {
		return buildSpec{}, fmt.Errorf("-build wants label=replay_binary,config_root, got %q", raw)
	}
	return buildSpec{Label: label, Replay: replay, Config: cfg}, nil
}

func parseCapture(raw string) (captureSpec, error) {
	name, path, ok := strings.Cut(raw, "=")
	if !ok || name == "" || path == "" || strings.ContainsAny(name, "/\\") {
		return captureSpec{}, fmt.Errorf("-capture wants name=path, got %q", raw)
	}
	return captureSpec{Name: name, Path: path}, nil
}

func runAttribution(args []string) error {
	fs := flag.NewFlagSet("attribution", flag.ContinueOnError)
	var builds, captures multiFlag
	fs.Var(&builds, "build", "label=replay_binary,config_root (repeat, in step order)")
	fs.Var(&captures, "capture", "name=capture.json (repeat)")
	out := fs.String("out", "", "output directory")
	strategyID := fs.String("strategy", "key_level", "strategy whose setups are kept")
	symbol := fs.String("symbol", "XAU", "symbol")
	primary := fs.String("timeframe", "M5", "primary timeframe of the replay")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *out == "" || len(builds) < 2 || len(captures) == 0 {
		return fmt.Errorf("usage: klstudy attribution -out DIR -build l=bin,cfg -build l2=bin,cfg [...] -capture name=path [...]")
	}
	report := attrReport{Strategy: *strategyID}
	for _, raw := range builds {
		b, err := parseBuild(raw)
		if err != nil {
			return err
		}
		report.Builds = append(report.Builds, b)
	}
	for _, raw := range captures {
		c, err := parseCapture(raw)
		if err != nil {
			return err
		}
		entry, err := attributeCapture(c, report.Builds, *out, *strategyID, *symbol, *primary)
		if err != nil {
			return err
		}
		report.Captures = append(report.Captures, entry)
	}
	return writeJSON(filepath.Join(*out, "attribution.json"), report)
}

func attributeCapture(c captureSpec, builds []buildSpec, out, strategyID, symbol, primary string) (attrCapture, error) {
	entry := attrCapture{Name: c.Name, Path: c.Path, Bars: map[string]int{}, First: map[string]int64{}, Last: map[string]int64{}, Setups: map[string]int{}, Symbol: symbol, Primary: primary}
	if err := describeCapture(c.Path, &entry); err != nil {
		return entry, err
	}
	dir := filepath.Join(out, c.Name)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return entry, err
	}
	keysOf := map[string][]string{}
	for _, b := range builds {
		setups, err := replaySetups(b, c, dir, strategyID, symbol, primary)
		if err != nil {
			return entry, err
		}
		if err := writeSetups(filepath.Join(dir, b.Label+".jsonl"), setups); err != nil {
			return entry, err
		}
		var keys []string
		for _, s := range setups {
			keys = append(keys, s.Key)
		}
		keysOf[b.Label] = keys
		entry.Setups[b.Label] = len(setups)
		entry.Builds = append(entry.Builds, b.Label)
	}
	for i := 1; i < len(builds); i++ {
		from, to := builds[i-1].Label, builds[i].Label
		removed, added := diffKeys(keysOf[from], keysOf[to])
		entry.Steps = append(entry.Steps, attrStep{From: from, To: to, Removed: removed, Added: added})
	}
	return entry, nil
}

// describeCapture records the bar coverage of every timeframe (count, first and last open) without validating more than the replay itself will.
func describeCapture(path string, entry *attrCapture) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var c struct {
		Timeframes map[string][][]float64 `json:"timeframes"`
	}
	if err := json.Unmarshal(raw, &c); err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	for tf, rows := range c.Timeframes {
		entry.Bars[tf] = len(rows)
		if len(rows) > 0 {
			entry.First[tf] = int64(rows[0][0])
			entry.Last[tf] = int64(rows[len(rows)-1][0])
		}
	}
	return nil
}

// replaySetups runs one build on one capture and returns the strategy's setups with their stable keys, in key order.
func replaySetups(b buildSpec, c captureSpec, dir, strategyID, symbol, primary string) ([]attrSetup, error) {
	tmp := filepath.Join(dir, "."+b.Label+".envelopes.jsonl")
	defer os.Remove(tmp)
	cmd := exec.Command(b.Replay, "-capture", c.Path, "-config", b.Config, "-symbol", symbol, "-timeframe", primary, "-envelopes-out", tmp)
	var stderr bytes.Buffer
	cmd.Stdout = nil
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("build %s on %s: %w: %s", b.Label, c.Name, err, strings.TrimSpace(stderr.String()))
	}
	raw, err := os.ReadFile(tmp)
	if err != nil {
		return nil, err
	}
	setups, err := setupsFromEnvelopes(bytes.Split(raw, []byte("\n")), b.Label, c.Name, strategyID)
	if err != nil {
		return nil, fmt.Errorf("build %s on %s: %w", b.Label, c.Name, err)
	}
	return setups, nil
}

// setupsFromEnvelopes keeps one strategy's creation envelopes and gives them their stable keys.
func setupsFromEnvelopes(lines [][]byte, build, capture, strategyID string) ([]attrSetup, error) {
	var setups []attrSetup
	for _, line := range lines {
		line = bytes.TrimSpace(line)
		if len(line) == 0 {
			continue
		}
		var env struct {
			Payload struct {
				Strategy  string `json:"strategy"`
				Direction string `json:"direction"`
				CreatedAt int64  `json:"created_at"`
				Entry     struct {
					Low float64 `json:"low"`
				} `json:"entry"`
			} `json:"payload"`
		}
		if err := json.Unmarshal(line, &env); err != nil {
			return nil, fmt.Errorf("envelope: %w", err)
		}
		if env.Payload.Strategy != strategyID {
			continue
		}
		setups = append(setups, attrSetup{
			Build: build, Capture: capture, Creation: append(json.RawMessage(nil), line...), Terminal: json.RawMessage("null"),
			direction: env.Payload.Direction, createdAt: env.Payload.CreatedAt, entryLow: env.Payload.Entry.Low,
		})
	}
	assignKeys(setups)
	return setups, nil
}

// assignKeys gives each setup direction|created_at|n, n counting setups of one direction on one bar in entry-low order, and sorts by key.
func assignKeys(setups []attrSetup) {
	sort.SliceStable(setups, func(i, j int) bool {
		a, b := setups[i], setups[j]
		if a.createdAt != b.createdAt {
			return a.createdAt < b.createdAt
		}
		if a.direction != b.direction {
			return a.direction < b.direction
		}
		return a.entryLow < b.entryLow
	})
	seen := map[string]int{}
	for i := range setups {
		base := fmt.Sprintf("%s|%d", setups[i].direction, setups[i].createdAt)
		setups[i].Key = fmt.Sprintf("%s|%d", base, seen[base])
		seen[base]++
	}
}

func diffKeys(from, to []string) (removed, added []string) {
	inFrom, inTo := map[string]bool{}, map[string]bool{}
	for _, k := range from {
		inFrom[k] = true
	}
	for _, k := range to {
		inTo[k] = true
	}
	for _, k := range from {
		if !inTo[k] {
			removed = append(removed, k)
		}
	}
	for _, k := range to {
		if !inFrom[k] {
			added = append(added, k)
		}
	}
	if removed == nil {
		removed = []string{}
	}
	if added == nil {
		added = []string{}
	}
	return removed, added
}

func writeSetups(path string, setups []attrSetup) error {
	var buf bytes.Buffer
	for _, s := range setups {
		line, err := json.Marshal(s)
		if err != nil {
			return err
		}
		buf.Write(line)
		buf.WriteByte('\n')
	}
	return os.WriteFile(path, buf.Bytes(), 0o644)
}

func writeJSON(path string, v any) error {
	raw, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, append(raw, '\n'), 0o644)
}
