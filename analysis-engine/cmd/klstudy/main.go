// Command klstudy is the read-only diagnostic of PR-KL13 (Key Level v13, major levels on XAU): for every closed XAU algo_auto Key Level trade it builds the
// major-level book as of the trade's creation, with the bars available then, and classifies the traded level as major (with its rules), minor, or
// unclassifiable. It prints month x class: n, net R, expectancy, win rate and the stop rate before 1R. Nothing is published or written to any service.
//
// Inputs (both read-only exports from production):
//
//  1. The trades, one JSON object per line (psql COPY ... TO STDOUT of row_to_json):
//
//     COPY (SELECT row_to_json(t) FROM (
//     SELECT r.group_id, r.closed_at, r.net_r, r.mfe_r, r.result_pips, r.risk_pips, r.stop_pips, r.exit_path, r.booked_tp_count,
//     s.opportunity_id AS snapshot_opportunity_id, s.technical_entry_low, s.technical_entry_high, s.direction, s.opportunity_created_at,
//     o.opportunity_id, o.created_at AS opp_created_at, o.creation_envelope->'payload'->'entry' AS entry,
//     o.creation_envelope->'payload'->>'direction' AS opp_direction
//     FROM auto_trade_results r
//     LEFT JOIN auto_trade_plan_snapshots s ON s.plan_id = r.group_id
//     LEFT JOIN analysis_opportunities o ON o.opportunity_id = COALESCE(s.opportunity_id, regexp_replace(r.group_id, '^v8:go_', ”))
//     WHERE r.trade_stream = 'algo_auto' AND r.symbol IN ('XAU','XAUUSD') AND r.setup_type = 'Key Level' AND r.closed_at >= 1788220800) t) TO STDOUT;
//
//     (The specification's query joins auto_trade_plan_snapshots only; snapshots exist from 22 Oct 2026, so the opportunity is also found from the group id,
//     v8:go_opp_<hash> -> opp_<hash>. auto_trade_results starts on 30 Sep 2026 for XAU algo_auto: September is not in the table.)
//
//  2. A Redis bar dump (tools.capture_bars), as deep as Redis holds: H1 ~2,000 bars, M15 back to late October, M5 ~7 days:
//
//     docker exec apexvoid-trading-bot python -m tools.capture_bars --symbol XAU --m5 3000 --m15 3000 --h1 2000 --out /tmp/xau.json
//
// A frame whose bars do not reach far enough back at a trade's creation is not read and the trade is flagged (before about 22 Oct only H1 and the session
// levels are read). net_r is used where the trade carries it; trades closed before net R accounting carry none and count in n and the outcome rates only. The stop
// rate before 1R is the share of trades that ended at a full stop with no target booked (TP1 is 1R on XAU).
//
//	go run ./cmd/klstudy -capture xau.json -trades kl_trades.jsonl -config ../config/apexvoid.yml [-out per_trade.jsonl]
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/legacyread"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

type trade struct {
	GroupID              string   `json:"group_id"`
	ClosedAt             int64    `json:"closed_at"`
	NetR                 *float64 `json:"net_r"`
	ExitPath             string   `json:"exit_path"`
	BookedTPCount        *int     `json:"booked_tp_count"`
	TechnicalEntryLow    *float64 `json:"technical_entry_low"`
	TechnicalEntryHigh   *float64 `json:"technical_entry_high"`
	Direction            string   `json:"direction"`
	OpportunityCreatedAt *int64   `json:"opportunity_created_at"`
	OppCreatedAt         *int64   `json:"opp_created_at"`
	OppDirection         string   `json:"opp_direction"`
	Entry                *struct {
		Low  float64 `json:"low"`
		High float64 `json:"high"`
	} `json:"entry"`
}

type verdict struct {
	GroupID  string   `json:"group_id"`
	Created  int64    `json:"created_at"`
	Month    string   `json:"month"`
	Class    string   `json:"class"` // major | minor | unclassifiable
	Rules    []string `json:"rules,omitempty"`
	Sources  []string `json:"sources,omitempty"`
	Frames   []string `json:"frames_read"`
	Partial  bool     `json:"partial_frames"`
	NetR     *float64 `json:"net_r"`
	ExitPath string   `json:"exit_path"`
	Reason   string   `json:"reason,omitempty"`
}

func main() {
	capturePath := flag.String("capture", "", "XAU bar capture (tools.capture_bars)")
	tradesPath := flag.String("trades", "", "trades jsonl (see the command's documentation for the query)")
	configPath := flag.String("config", "../config/apexvoid.yml", "config")
	out := flag.String("out", "", "optional per-trade jsonl")
	flag.Parse()
	if err := run(*capturePath, *tradesPath, *configPath, *out); err != nil {
		fmt.Fprintln(os.Stderr, "klstudy:", err)
		os.Exit(1)
	}
}

func run(capturePath, tradesPath, configPath, outPath string) error {
	doc, err := config.ResolveDocument(configPath)
	if err != nil {
		return err
	}
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		return err
	}
	if err := engine.ApplyInstrument(&settings, doc, "XAU"); err != nil {
		return err
	}
	var params map[string]any
	for _, s := range settings.Strategies {
		if s.ID == "key_level" {
			params = s.Parameters
		}
	}
	if params == nil {
		return fmt.Errorf("key_level is not configured")
	}
	capture, err := replaycapture.Load(capturePath)
	if err != nil {
		return err
	}
	bars := map[market.Timeframe][]market.Candle{}
	for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
		if c, err := capture.Candles(tf); err == nil {
			bars[tf] = c
		}
	}
	trades, err := readTrades(tradesPath)
	if err != nil {
		return err
	}
	var verdicts []verdict
	for _, t := range trades {
		verdicts = append(verdicts, classifyTrade(t, bars, params, settings.LegacyRead))
	}
	if outPath != "" {
		f, err := os.Create(outPath)
		if err != nil {
			return err
		}
		enc := json.NewEncoder(f)
		for _, v := range verdicts {
			if err := enc.Encode(v); err != nil {
				return err
			}
		}
		if err := f.Close(); err != nil {
			return err
		}
	}
	printTable(verdicts, trades)
	return nil
}

func readTrades(path string) ([]trade, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var out []trade
	scanner := bufio.NewScanner(f)
	scanner.Buffer(make([]byte, 1<<20), 1<<24)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var t trade
		if err := json.Unmarshal([]byte(line), &t); err != nil {
			return nil, fmt.Errorf("trade line: %w", err)
		}
		out = append(out, t)
	}
	return out, scanner.Err()
}

func seconds(tf market.Timeframe) int64 {
	m, _ := tf.Minutes()
	return int64(m) * 60
}

func classifyTrade(t trade, all map[market.Timeframe][]market.Candle, params map[string]any, cfg legacyread.Config) verdict {
	v := verdict{GroupID: t.GroupID, NetR: t.NetR, ExitPath: t.ExitPath, Class: "unclassifiable"}
	created := int64(0)
	switch {
	case t.OpportunityCreatedAt != nil:
		created = *t.OpportunityCreatedAt
	case t.OppCreatedAt != nil:
		created = *t.OppCreatedAt
	}
	low, high := math.NaN(), math.NaN()
	switch {
	case t.TechnicalEntryLow != nil && t.TechnicalEntryHigh != nil:
		low, high = *t.TechnicalEntryLow, *t.TechnicalEntryHigh
	case t.Entry != nil:
		low, high = t.Entry.Low, t.Entry.High
	}
	v.Created = created
	when := created
	if when == 0 {
		when = t.ClosedAt
	}
	v.Month = time.Unix(when, 0).UTC().Format("2006-01")
	if created == 0 || math.IsNaN(low) {
		v.Reason = "no creation time or entry band"
		return v
	}
	// Each frame cut at the trade's creation (closed bars only); a frame whose cut is shorter than its analysis window is not read.
	cuts := map[market.Timeframe][]market.Candle{}
	for _, tf := range []market.Timeframe{market.H1, market.M15, market.M5} {
		var cut []market.Candle
		for _, c := range all[tf] {
			if c.Time+seconds(tf) <= created {
				cut = append(cut, c)
			}
		}
		if window := cfg.WindowBars[tf]; len(cut) < 50 || (window > 0 && len(cut) < window) {
			v.Partial = v.Partial || tf != market.H1
			continue
		}
		cuts[tf] = cut
		v.Frames = append(v.Frames, string(tf))
	}
	if cuts[market.H1] == nil {
		v.Reason = "no H1 history at the trade's creation"
		return v
	}
	weekly := legacyread.Weekly(cuts[market.H1], market.H1, cfg)
	staged := map[market.Timeframe]legacyread.Staged{}
	for tf, cut := range cuts {
		staged[tf] = legacyread.Stage(cut, tf, weekly, cfg)
	}
	frames := legacyread.Complete(staged)
	detectors := map[market.Timeframe]*strategyutil.LegacyDetector{}
	for tf, f := range frames {
		if len(f.Bars) == 0 {
			continue
		}
		atr := f.DetectorATR
		if !(atr > 0) {
			continue
		}
		detectors[tf] = &strategyutil.LegacyDetector{Frame: f, ATR: atr, Price: f.Bars[len(f.Bars)-1].Close}
	}
	book, err := keylevel.StudyBook(params, detectors, (low+high)/2, created)
	if err != nil {
		v.Reason = err.Error()
		return v
	}
	v.Class = "minor"
	for _, l := range book {
		if l.Price+l.Band < low || l.Price-l.Band > high {
			continue
		}
		if l.Major {
			v.Class = "major"
			for _, r := range l.Rules {
				if !contains(v.Rules, r) {
					v.Rules = append(v.Rules, r)
				}
			}
			if !contains(v.Sources, l.Source) {
				v.Sources = append(v.Sources, l.Source)
			}
		}
	}
	sort.Strings(v.Rules)
	return v
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

type cell struct {
	n, withR, wins, stopsBefore1R int
	sumR                          float64
}

func printTable(verdicts []verdict, trades []trade) {
	byKey := map[string]*cell{}
	tradeOf := map[string]trade{}
	for _, t := range trades {
		tradeOf[t.GroupID] = t
	}
	var months []string
	for _, v := range verdicts {
		for _, key := range []string{v.Month + " " + v.Class, "all " + v.Class} {
			c := byKey[key]
			if c == nil {
				c = &cell{}
				byKey[key] = c
			}
			t := tradeOf[v.GroupID]
			c.n++
			if v.NetR != nil {
				c.withR++
				c.sumR += *v.NetR
			}
			won := v.NetR != nil && *v.NetR > 0 || v.NetR == nil && (v.ExitPath == "tp2_full" || v.ExitPath == "tp1_be")
			if won {
				c.wins++
			}
			if v.ExitPath == "full_stop" && (t.BookedTPCount == nil || *t.BookedTPCount == 0) {
				c.stopsBefore1R++
			}
		}
		if !contains(months, v.Month) {
			months = append(months, v.Month)
		}
	}
	sort.Strings(months)
	months = append(months, "all")
	fmt.Println("| month | class | n | n with net R | net R | expectancy (R) | win rate | stop before 1R |")
	fmt.Println("|---|---|---|---|---|---|---|---|")
	for _, m := range months {
		for _, class := range []string{"major", "minor", "unclassifiable"} {
			c := byKey[m+" "+class]
			if c == nil {
				continue
			}
			exp := "-"
			if c.withR > 0 {
				exp = fmt.Sprintf("%+.2f", c.sumR/float64(c.withR))
			}
			fmt.Printf("| %s | %s | %d | %d | %+.2f | %s | %.0f%% | %.0f%% |\n", m, class, c.n, c.withR, c.sumR, exp, 100*float64(c.wins)/float64(c.n), 100*float64(c.stopsBefore1R)/float64(c.n))
		}
	}
}
