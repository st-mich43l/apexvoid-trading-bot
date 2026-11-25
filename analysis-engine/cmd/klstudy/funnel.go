package main

// klstudy funnel (PR-KL14 Part C, read-only): why v13 is near-silent. For each capture it replays Key Level alone twice in this process, as v12
// (level_model swing, the rollback) and as v13 (the shipped XAU configuration) with refusal telemetry on, and
//
//   - reports v13's funnel: major levels in the book by source (kl_major_levels_in_book, per evaluation), refusals by reason (minor_level, no_m5_reaction,
//     both_sides_confirmed, duplicate_thesis, entry_zone_below_minimum_width), reactions refused for want of a sweep, stop-budget refusals, published;
//   - classifies every v12 setup on the bar it was published: kept (v13 published the same direction on that bar), major (the level is major in the v13 book but
//     a later step refused it), minor (with the rule the level failed) or not_in_book (no book level overlaps the v12 band);
//   - writes the v12 and v13 setups as labeller input, split by class, so the setups that only minor_level refused can be labelled against v13's.
//
//	go run ./cmd/klstudy funnel -config ../config/apexvoid.yml -out DIR -capture nov=xau.json [-capture ...]
//
// The book is the strategy's own (keylevel.StudyBook, the same code v13 runs); nothing in the strategy or the configuration is changed.

import (
	"bytes"
	"flag"
	"fmt"
	"path/filepath"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/telemetry"
)

const (
	classKept      = "kept"
	classMajor     = "major"
	classMinor     = "minor"
	classNotInBook = "not_in_book"
)

type majorThresholds struct {
	swingMinReactions, roundMinReactions float64
	swingMinSpanHours, swingMinDeparture float64
}

func thresholdsOf(params map[string]any) (majorThresholds, error) {
	major, ok := params["major"].(map[string]any)
	if !ok {
		return majorThresholds{}, fmt.Errorf("the key_level parameters carry no major block")
	}
	get := func(key string) (float64, error) {
		switch v := major[key].(type) {
		case int:
			return float64(v), nil
		case int64:
			return float64(v), nil
		case float64:
			return v, nil
		}
		return 0, fmt.Errorf("key_level.major.%s is missing or not a number", key)
	}
	var t majorThresholds
	var err error
	for _, item := range []struct {
		key string
		dst *float64
	}{
		{"swing_min_reactions", &t.swingMinReactions}, {"round_min_reactions", &t.roundMinReactions},
		{"swing_min_span_hours", &t.swingMinSpanHours}, {"swing_min_max_departure_ref_atr", &t.swingMinDeparture},
	} {
		if *item.dst, err = get(item.key); err != nil {
			return t, err
		}
	}
	return t, nil
}

// minorReason names the first rule a minor level fails. A level that carries a listed session, or a $50/$100 handle with enough reactions, would be major
// if intact, so it is minor because it was broken; otherwise the swing rule W decides (reactions, then span, then departure).
func minorReason(l keylevel.BookLevel, t majorThresholds) string {
	round := l.RoundClass == 50 || l.RoundClass == 100
	switch {
	case len(l.Sessions) > 0:
		return "session_level_broken"
	case round && float64(l.Reactions) >= t.roundMinReactions:
		return "round_level_broken"
	case round:
		return "round_without_reaction"
	case float64(l.Reactions) < t.swingMinReactions:
		return "reactions"
	case l.SpanHours < t.swingMinSpanHours:
		return "span"
	case l.MaxDepartureRefATR < t.swingMinDeparture:
		return "departure"
	}
	return "broken"
}

type bookLevelView struct {
	Price              float64  `json:"price"`
	Band               float64  `json:"band"`
	Source             string   `json:"source"`
	Timeframe          string   `json:"timeframe"`
	Rules              []string `json:"rules"`
	Reactions          int      `json:"reactions"`
	SpanHours          float64  `json:"span_hours"`
	MaxDepartureRefATR float64  `json:"max_departure_ref_atr"`
	Sessions           []string `json:"sessions"`
	RoundClass         int      `json:"round_class"`
}

type funnelSetup struct {
	Key       string         `json:"key"`
	Direction string         `json:"direction"`
	CreatedAt int64          `json:"created_at"`
	Class     string         `json:"class"`
	Rules     []string       `json:"rules,omitempty"`
	Reason    string         `json:"reason,omitempty"`
	Level     *bookLevelView `json:"level,omitempty"`
}

type funnelReport struct {
	Capture             string             `json:"capture"`
	Evaluations         int                `json:"evaluations"`
	V12Setups           int                `json:"v12_setups"`
	V13Setups           int                `json:"v13_setups"`
	BookPerEvaluation   map[string]float64 `json:"major_levels_in_book_per_evaluation"`
	Refused             map[string]int64   `json:"refused"`
	RefusedByTimeframe  map[string]int64   `json:"refused_by_timeframe"`
	UnsweptRefused      int64              `json:"reaction_without_sweep"`
	StopBudgetRefused   int64              `json:"stop_budget_refused"`
	Published           int64              `json:"published"`
	V12Classes          map[string]int     `json:"v12_classes"`
	MinorReasons        map[string]int     `json:"minor_reasons"`
	MajorDroppedRuleSet map[string]int     `json:"major_dropped_rules"`
	Setups              []funnelSetup      `json:"v12_setup_classes"`
}

func runFunnel(args []string) error {
	fs := flag.NewFlagSet("funnel", flag.ContinueOnError)
	var captures multiFlag
	fs.Var(&captures, "capture", "name=capture.json (repeat)")
	configPath := fs.String("config", "../config/apexvoid.yml", "configuration root (the shipped one: XAU level_model major)")
	out := fs.String("out", "", "output directory")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *out == "" || len(captures) == 0 {
		return fmt.Errorf("usage: klstudy funnel -config CFG -out DIR -capture name=path [...]")
	}
	doc, err := config.ResolveDocument(*configPath)
	if err != nil {
		return err
	}
	params, err := xauKeyLevelParams(doc)
	if err != nil {
		return err
	}
	thresholds, err := thresholdsOf(params)
	if err != nil {
		return err
	}
	var reports []funnelReport
	for _, raw := range captures {
		c, err := parseCapture(raw)
		if err != nil {
			return err
		}
		capture, err := replaycapture.Load(c.Path)
		if err != nil {
			return err
		}
		report, files, err := funnelCapture(doc, capture, c.Name, params, thresholds)
		if err != nil {
			return fmt.Errorf("%s: %w", c.Name, err)
		}
		for name, setups := range files {
			if err := writeSetups(filepath.Join(*out, c.Name, name+".jsonl"), setups); err != nil {
				return err
			}
		}
		reports = append(reports, report)
	}
	return writeJSON(filepath.Join(*out, "funnel.json"), reports)
}

func xauKeyLevelParams(doc *config.Document) (map[string]any, error) {
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		return nil, err
	}
	if err := engine.ApplyInstrument(&settings, doc, "XAU"); err != nil {
		return nil, err
	}
	for _, s := range settings.Strategies {
		if s.ID == "key_level" {
			return s.Parameters, nil
		}
	}
	return nil, fmt.Errorf("key_level is not configured")
}

func keyLevelOnly() []string {
	var off []string
	for _, id := range strategy.KnownIDs() {
		if id != "key_level" {
			off = append(off, string(id))
		}
	}
	return off
}

func funnelCapture(doc *config.Document, capture *replaycapture.Capture, name string, params map[string]any, t majorThresholds) (funnelReport, map[string][]attrSetup, error) {
	report := funnelReport{
		Capture: name, BookPerEvaluation: map[string]float64{}, Refused: map[string]int64{}, RefusedByTimeframe: map[string]int64{},
		V12Classes: map[string]int{}, MinorReasons: map[string]int{}, MajorDroppedRuleSet: map[string]int{},
	}
	v12Result, err := replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{
		DisabledStrategies: keyLevelOnly(), StrategyParameters: map[string]map[string]any{"key_level": {"level_model": "swing"}},
	})
	if err != nil {
		return report, nil, err
	}
	v12 := keyLevelCandidates(v12Result)
	atBar := map[int64][]opportunity.Candidate{}
	for _, c := range v12 {
		atBar[c.CreatedAt] = append(atBar[c.CreatedAt], c)
	}
	classes := map[string]funnelSetup{}
	rec := telemetry.NewRecorder()
	hook := func(ev engine.Evaluation) {
		if ev.Timeframe != market.M5 {
			return
		}
		report.Evaluations++
		pending := atBar[ev.BarTime]
		if len(pending) == 0 {
			return
		}
		book, ok := studyBookAt(ev, params)
		for _, c := range pending {
			classes[c.ID] = classifySetup(c, book, ok, t)
		}
	}
	v13Result, err := replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{DisabledStrategies: keyLevelOnly(), Telemetry: rec, OnEvaluation: hook})
	if err != nil {
		return report, nil, err
	}
	v13 := keyLevelCandidates(v13Result)
	report.V12Setups, report.V13Setups = len(v12), len(v13)
	published := map[string]bool{}
	for _, c := range v13 {
		published[fmt.Sprintf("%s|%d", c.Direction, c.CreatedAt)] = true
	}
	readTelemetry(rec, &report)

	v12Env, err := envelopes(v12, v12Result.Options)
	if err != nil {
		return report, nil, err
	}
	v13Env, err := envelopes(v13, v13Result.Options)
	if err != nil {
		return report, nil, err
	}
	v12Setups, err := setupsFromEnvelopes(v12Env, "v12", name, "key_level")
	if err != nil {
		return report, nil, err
	}
	v13Setups, err := setupsFromEnvelopes(v13Env, "v13", name, "key_level")
	if err != nil {
		return report, nil, err
	}
	idOf := map[string]string{} // key -> candidate id (the envelope order is the candidate order)
	for i, c := range v12 {
		idOf[setupKeyOf(v12Env[i])] = c.ID
	}
	files := map[string][]attrSetup{"v12": v12Setups, "v13": v13Setups}
	for _, s := range v12Setups {
		fs := classes[idOf[setupKeyOf(s.Creation)]]
		fs.Key, fs.Direction, fs.CreatedAt = s.Key, s.direction, s.createdAt
		if published[fmt.Sprintf("%s|%d", s.direction, s.createdAt)] {
			fs.Class, fs.Rules, fs.Reason = classKept, nil, ""
		}
		if fs.Class == "" {
			fs.Class, fs.Reason = classNotInBook, "no_book_at_bar"
		}
		report.V12Classes[fs.Class]++
		switch fs.Class {
		case classMinor:
			report.MinorReasons[fs.Reason]++
			files["v12_minor_only"] = append(files["v12_minor_only"], s)
		case classMajor:
			report.MajorDroppedRuleSet[joinRules(fs.Rules)]++
			files["v12_major_dropped"] = append(files["v12_major_dropped"], s)
		case classNotInBook:
			files["v12_not_in_book"] = append(files["v12_not_in_book"], s)
		}
		report.Setups = append(report.Setups, fs)
	}
	return report, files, nil
}

func keyLevelCandidates(r *replaycapture.Result) []opportunity.Candidate {
	var out []opportunity.Candidate
	for _, c := range r.Discovered {
		if c.Strategy == "key_level" {
			out = append(out, c)
		}
	}
	return out
}

func envelopes(cs []opportunity.Candidate, opts replaycapture.EnvelopeOptions) ([][]byte, error) {
	var out [][]byte
	for _, c := range cs {
		line, err := replaycapture.CreationEnvelope(c, opts)
		if err != nil {
			return nil, err
		}
		out = append(out, line)
	}
	return out, nil
}

// setupKeyOf identifies an envelope by its bytes (ids are unique per replay), used only to join a candidate to its keyed setup.
func setupKeyOf(env []byte) string { return string(bytes.TrimSpace(env)) }

func studyBookAt(ev engine.Evaluation, params map[string]any) ([]keylevel.BookLevel, bool) {
	frames := map[market.Timeframe]*strategyutil.LegacyDetector{}
	for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
		if d, ok := strategyutil.NewLegacyDetectorForFrame(ev.Context, tf, strategyutil.LegacyDetectorSettings{}); ok {
			frames[tf] = d
		}
	}
	m5 := frames[market.M5]
	if m5 == nil {
		return nil, false
	}
	book, err := keylevel.StudyBook(params, frames, m5.Price, ev.BarTime+300)
	if err != nil {
		return nil, false
	}
	return book, true
}

func classifySetup(c opportunity.Candidate, book []keylevel.BookLevel, haveBook bool, t majorThresholds) funnelSetup {
	fs := funnelSetup{Direction: string(c.Direction), CreatedAt: c.CreatedAt}
	if !haveBook {
		fs.Class, fs.Reason = classNotInBook, "no_book_at_bar"
		return fs
	}
	var overlapping []keylevel.BookLevel
	for _, l := range book {
		if l.Price+l.Band < c.Structure.Low || l.Price-l.Band > c.Structure.High {
			continue
		}
		overlapping = append(overlapping, l)
	}
	if len(overlapping) == 0 {
		fs.Class, fs.Reason = classNotInBook, "no_overlapping_level"
		return fs
	}
	mid := (c.Structure.Low + c.Structure.High) / 2
	sort.SliceStable(overlapping, func(i, j int) bool {
		a, b := overlapping[i], overlapping[j]
		if a.Major != b.Major {
			return a.Major
		}
		if a.Reactions != b.Reactions {
			return a.Reactions > b.Reactions
		}
		if a.Score != b.Score {
			return a.Score > b.Score
		}
		return abs(a.Price-mid) < abs(b.Price-mid)
	})
	best := overlapping[0]
	view := &bookLevelView{
		Price: best.Price, Band: best.Band, Source: best.Source, Timeframe: string(best.Timeframe), Rules: best.Rules, Reactions: best.Reactions,
		SpanHours: best.SpanHours, MaxDepartureRefATR: best.MaxDepartureRefATR, Sessions: best.Sessions, RoundClass: best.RoundClass,
	}
	fs.Level = view
	if best.Major {
		rules := map[string]bool{}
		for _, l := range overlapping {
			if l.Major {
				for _, r := range l.Rules {
					rules[r] = true
				}
			}
		}
		for r := range rules {
			fs.Rules = append(fs.Rules, r)
		}
		sort.Strings(fs.Rules)
		fs.Class = classMajor
		return fs
	}
	fs.Class, fs.Reason = classMinor, minorReason(best, t)
	return fs
}

func readTelemetry(rec *telemetry.Recorder, report *funnelReport) {
	_, counts := rec.Snapshot()
	evals := float64(report.Evaluations)
	for _, c := range counts {
		switch telemetry.Counter(c.Counter) {
		case telemetry.CounterStrategyRefused:
			if c.Strategy == "key_level" {
				report.Refused[c.Reason] += c.Count
				report.RefusedByTimeframe[c.Reason+"|"+c.Timeframe] += c.Count
			}
		case telemetry.CounterStrategyPublished:
			if c.Strategy == "key_level" {
				report.Published += c.Count
			}
		case telemetry.CounterStrategyFeature:
			if c.Strategy == "key_level" && c.Reason == "kl_major_levels_in_book" && evals > 0 {
				report.BookPerEvaluation[c.Value+"|"+c.Timeframe] += float64(c.Count) / evals
			}
		case telemetry.CounterStrategyKeyLevelUnsweptRefused:
			report.UnsweptRefused += c.Count
		case telemetry.CounterStrategyStopBudgetRefused:
			report.StopBudgetRefused += c.Count
		}
	}
}

func joinRules(rules []string) string {
	if len(rules) == 0 {
		return "-"
	}
	out := rules[0]
	for _, r := range rules[1:] {
		out += "+" + r
	}
	return out
}

func abs(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}
