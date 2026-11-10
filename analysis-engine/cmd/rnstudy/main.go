// Command rnstudy is the read-only evaluation tool of RN-1 (Round Number validation, docs/strategies/round_number.md). It replays a real closed-bar capture
// (tools.capture_bars) through the production engine and writes JSON lines, one per record:
//
//	setup / setup_conf            what Round Number publishes with candle confirmation optional (production) / required (the rule before AE-CF1)
//	setup_open / setup_open_conf  the same with the confluence floor at 1 star: also the setups the floor refuses (measurement only, never published)
//	rejection                     every real episode a rule refused, with its reason
//	neighbour                     CRT and Break & Retest at 1 star on the same candles, for the same-event comparison
//	raw / rawrej                  the same detector on price grids shifted by 0, 2, 3, 7 and 8 dollars (control levels), no confluence floor, no liquidity
//
// Outcomes are simulated by the scripts in analysis/ (RN_DIR names the directory with cap-xau.json and study.jsonl). Nothing here is published or written to
// any service.
//
//	go run ./cmd/rnstudy -capture cap-xau.json -config ../config/apexvoid.yml -out study.jsonl
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/breakretest"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/crt"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/roundnumber"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

func main() {
	capturePath := flag.String("capture", "", "capture json")
	configPath := flag.String("config", "../config/apexvoid.yml", "config")
	out := flag.String("out", "", "jsonl out")
	flag.Parse()
	if err := run(*capturePath, *configPath, *out); err != nil {
		fmt.Fprintln(os.Stderr, "rnstudy:", err)
		os.Exit(1)
	}
}

func run(capturePath, configPath, outPath string) error {
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
	var sc strategy.Config
	for _, s := range settings.Strategies {
		if string(s.ID) == "round_number" {
			sc = s
		}
	}
	if sc.ID == "" {
		return fmt.Errorf("round_number not configured")
	}
	cfg, err := roundnumber.ParseConfig(sc.Parameters)
	if err != nil {
		return err
	}
	st, err := roundnumber.New(sc)
	if err != nil {
		return err
	}
	rn := st.(*roundnumber.Strategy)
	// The same strategy with the confluence floor at 1 star: the population the floor refuses (never published; for measurement only).
	open := sc
	open.Parameters = map[string]any{}
	for k, v := range sc.Parameters {
		open.Parameters[k] = v
	}
	open.Parameters["confluence_floor"] = 1.0
	stOpen, err := roundnumber.New(open)
	if err != nil {
		return err
	}
	rnOpen := stOpen.(*roundnumber.Strategy)
	variant := func(floor float64, confirm bool) *roundnumber.Strategy {
		v := sc
		v.Parameters = map[string]any{}
		for k, val := range sc.Parameters {
			v.Parameters[k] = val
		}
		v.Parameters["confluence_floor"] = floor
		v.Parameters["require_candle_confirmation"] = confirm
		x, err := roundnumber.New(v)
		if err != nil {
			panic(err)
		}
		return x.(*roundnumber.Strategy)
	}
	rnConf, rnOpenConf := variant(2, true), variant(1, true)
	// The neighbours at 1 star, for the same-event comparison (never published).
	neighbours := map[string]strategy.Strategy{}
	for _, s := range settings.Strategies {
		var mk func(strategy.Config) (strategy.Strategy, error)
		switch string(s.ID) {
		case "crt":
			mk = crt.New
		case "break_retest":
			mk = breakretest.New
		default:
			continue
		}
		v := s
		v.Parameters = map[string]any{}
		for k, val := range s.Parameters {
			v.Parameters[k] = val
		}
		v.Parameters["confluence_floor"] = 1.0
		x, err := mk(v)
		if err != nil {
			return err
		}
		neighbours[string(s.ID)] = x
	}
	seenN := map[string]bool{}
	seenConf, seenOpenConf := map[string]bool{}, map[string]bool{}
	capture, err := replaycapture.Load(capturePath)
	if err != nil {
		return err
	}
	f, err := os.Create(outPath)
	if err != nil {
		return err
	}
	defer f.Close()
	enc := json.NewEncoder(f)
	emit := func(v map[string]any) { _ = enc.Encode(v) }

	detSettings, err := strategyutil.ParseLegacyDetectorSettings(sc.Parameters)
	if err != nil {
		return err
	}
	chochTimes := func(ctx *analysiscontext.MarketContext, dir market.Direction) []int64 {
		d, ok := strategyutil.NewLegacyDetector(ctx, market.M5, dir, detSettings)
		if !ok {
			return nil
		}
		wanted := "down"
		if dir == market.Buy {
			wanted = "up"
		}
		var out []int64
		for _, b := range d.Frame.Breaks {
			if b.Kind == "CHoCH" && b.Direction == wanted && b.Index >= 0 && b.Index < len(d.Frame.Bars) && b.Index >= len(d.Frame.Bars)-12 {
				out = append(out, d.Frame.Bars[b.Index].Time)
			}
		}
		return out
	}
	seenSetup, seenRej, seenOpen := map[string]bool{}, map[string]bool{}, map[string]bool{}
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(ev engine.Evaluation) {
		if ev.Timeframe != market.M5 || ev.Context == nil {
			return
		}
		analysis, cands := rn.Analyze(ev.Context)
		for i, s := range analysis.Setups {
			c := cands[i]
			key := c.ID
			if seenSetup[key] {
				continue
			}
			seenSetup[key] = true
			evid := make([]string, 0, len(c.Evidence))
			for _, e := range c.Evidence {
				evid = append(evid, e.Code)
			}
			emit(map[string]any{
				"rec": "setup", "group": "real", "id": c.ID, "kind": s.Kind, "dir": string(s.Direction), "handle": s.Handle, "major": s.Major,
				"confirmed_at": s.ConfirmedAt, "created_at": c.CreatedAt, "expires_at": c.ExpiresAt,
				"entry_low": s.EntryLow, "entry_high": s.EntryHigh, "stop": s.Stop, "target": s.Target, "target_kind": s.TargetKind,
				"atr": s.M5ATR, "risk_pips": s.RiskPips, "reward_pips": s.RewardPips, "rr": s.TechnicalRR,
				"wick": s.WickRejection, "engulf": s.Engulfing, "force_atr": s.ForceATR, "sweep_depth_atr": s.SweepDepthATR,
				"structural": s.Structural, "retest_bars": s.RetestBars,
				"quality": c.Quality.Overall, "components": c.Quality.Components, "evidence": strings.Join(evid, ","),
				"choch_times": chochTimes(ev.Context, s.Direction), "local_trend": ev.Context.Timeframes[market.M5].Legacy.Structure, "htf": c.DetectorConfluence.Factors.HTFAligned,
				"stars": c.DetectorConfluence.SelectedStars,
			})
		}
		openAnalysis, openCands := rnOpen.Analyze(ev.Context)
		for i, s := range openAnalysis.Setups {
			c := openCands[i]
			if seenOpen[c.ID] {
				continue
			}
			seenOpen[c.ID] = true
			f := c.DetectorConfluence.Factors
			emit(map[string]any{
				"rec": "setup_open", "group": "real", "id": c.ID, "kind": s.Kind, "dir": string(s.Direction), "handle": s.Handle, "major": s.Major,
				"confirmed_at": s.ConfirmedAt, "created_at": c.CreatedAt, "expires_at": c.ExpiresAt,
				"entry_low": s.EntryLow, "entry_high": s.EntryHigh, "stop": s.Stop, "target": s.Target, "target_kind": s.TargetKind,
				"atr": s.M5ATR, "risk_pips": s.RiskPips, "rr": s.TechnicalRR, "wick": s.WickRejection, "engulf": s.Engulfing, "force_atr": s.ForceATR,
				"stars": c.DetectorConfluence.SelectedStars, "htf": f.HTFAligned, "disp": f.DisplacementGrade, "struct": f.StructuralAgreement, "session": f.SessionContext,
				"quality": c.Quality.Overall, "components": c.Quality.Components,
				"choch_times": chochTimes(ev.Context, s.Direction), "local_trend": ev.Context.Timeframes[market.M5].Legacy.Structure,
			})
		}
		for _, vv := range []struct {
			name string
			st   *roundnumber.Strategy
			seen map[string]bool
		}{{"setup_conf", rnConf, seenConf}, {"setup_open_conf", rnOpenConf, seenOpenConf}} {
			a, cs := vv.st.Analyze(ev.Context)
			for i, s := range a.Setups {
				c := cs[i]
				if vv.seen[c.ID] {
					continue
				}
				vv.seen[c.ID] = true
				f := c.DetectorConfluence.Factors
				emit(map[string]any{
					"rec": vv.name, "group": "real", "id": c.ID, "kind": s.Kind, "dir": string(s.Direction), "handle": s.Handle, "major": s.Major,
					"confirmed_at": s.ConfirmedAt, "created_at": c.CreatedAt, "expires_at": c.ExpiresAt,
					"entry_low": s.EntryLow, "entry_high": s.EntryHigh, "stop": s.Stop, "target": s.Target, "target_kind": s.TargetKind,
					"atr": s.M5ATR, "risk_pips": s.RiskPips, "rr": s.TechnicalRR, "wick": s.WickRejection, "engulf": s.Engulfing, "force_atr": s.ForceATR,
					"stars": c.DetectorConfluence.SelectedStars, "htf": f.HTFAligned, "disp": f.DisplacementGrade, "struct": f.StructuralAgreement,
					"quality": c.Quality.Overall, "components": c.Quality.Components,
				})
			}
		}
		for name, st := range neighbours {
			for _, c := range st.Evaluate(ev.Context) {
				if seenN[c.ID] {
					continue
				}
				seenN[c.ID] = true
				emit(map[string]any{
					"rec": "neighbour", "strategy": name, "id": c.ID, "dir": string(c.Direction), "created_at": c.CreatedAt, "expires_at": c.ExpiresAt, "formed_at": c.FormedAt,
					"entry_low": float64(c.Entry.Low), "entry_high": float64(c.Entry.High), "stop": float64(c.Invalidation.Price), "target": float64(c.Targets[0].Price.Price),
					"stars": c.DetectorConfluence.SelectedStars,
				})
			}
		}
		for _, r := range analysis.Rejections {
			key := fmt.Sprintf("%s|%s|%s|%.2f|%d", r.Reason, r.Kind, r.Direction, r.Handle, r.At)
			if seenRej[key] {
				continue
			}
			seenRej[key] = true
			emit(map[string]any{"rec": "rejection", "reason": r.Reason, "kind": r.Kind, "dir": string(r.Direction), "handle": r.Handle, "at": r.At})
		}
	}})
	if err != nil {
		return err
	}

	// Control levels: the same detector on the M5 series shifted so that the grid falls at 10k+offset. Liquidity is nil (the next grid level is the
	// target) for every group, the round one included, so the groups differ only in where the level is.
	m5, err := capture.Candles(market.M5)
	if err != nil {
		return err
	}
	for _, offset := range []float64{0, 2, 3, 7, 8} {
		for _, optional := range []bool{true, false} {
			c := cfg
			c.CandleConfirmationOptional = optional
			shifted := make([]market.Candle, len(m5))
			for i, k := range m5 {
				k.Open, k.High, k.Low, k.Close = k.Open-offset, k.High-offset, k.Low-offset, k.Close-offset
				shifted[i] = k
			}
			seen := map[string]bool{}
			reasons := map[string]bool{}
			for i := 150; i < len(shifted); i++ {
				a := roundnumber.DetectAsOf(c, roundnumber.Input{M5: shifted}, i)
				for _, s := range a.Setups {
					key := fmt.Sprintf("%s|%s|%.2f|%d", s.Kind, s.Direction, s.Handle, s.ConfirmedAt)
					if seen[key] {
						continue
					}
					seen[key] = true
					emit(map[string]any{
						"rec": "raw", "group": fmt.Sprintf("off%.0f", offset), "optional": optional, "kind": s.Kind, "dir": string(s.Direction),
						"handle": s.Handle + offset, "major": s.Major && offset == 0, "confirmed_at": s.ConfirmedAt, "created_at": shifted[i].Time + 300,
						"entry_low": s.EntryLow + offset, "entry_high": s.EntryHigh + offset, "stop": s.Stop + offset, "target": s.Target + offset, "target_kind": s.TargetKind,
						"atr": s.M5ATR, "risk_pips": s.RiskPips, "rr": s.TechnicalRR, "wick": s.WickRejection, "engulf": s.Engulfing, "force_atr": s.ForceATR,
					})
				}
				for _, r := range a.Rejections {
					key := fmt.Sprintf("%s|%s|%s|%.2f|%d", r.Reason, r.Kind, r.Direction, r.Handle, r.At)
					if reasons[key] {
						continue
					}
					reasons[key] = true
					emit(map[string]any{"rec": "rawrej", "group": fmt.Sprintf("off%.0f", offset), "optional": optional, "reason": r.Reason, "kind": r.Kind, "dir": string(r.Direction), "handle": r.Handle + offset, "at": r.At})
				}
			}
		}
	}
	return nil
}
