// Package scalpbreakoutretest implements the Breakout Retest Scalp: the
// profitable-week (14–18 Sep 2026) Python "Breakout Retest V2" engine, on the
// Go analysis authority.
//
// Thesis. The setup lives on M5 and is executed on M1:
//
//	level source → true cross → acceptance → retest → role flip → armed
//	             → M1 execution confirmation → entry zone around the level
//
// Level sources are the setup window's own swing highs/lows ("structure
// flip"), the window's M5 key levels, equal highs/lows (liquidity levels),
// and — as a separate legacy path — a compression box. Every source is run
// through the same state machine; each armed episode is scored with the
// frozen five-component quality rubric (displacement 30 %, acceptance 15 %,
// retest quality 25 %, retest speed 15 %, confirmation 15 %) and the single
// best episode per direction is kept, not the first match. An M5 setup is only
// actionable once a closed M1 bar confirms it at the level.
//
// Stateless: the whole decision is re-derived from the closed M5/M1 windows on
// every closed bar, exactly as the Python lane re-ran on every closed M1. The
// context Python cached between M5 boundaries (volatility, price used for the
// corridor room) is reproduced from the same closed bars, see context.go.
//
// Analysis vs execution: the opportunity describes the original scalp setup —
// structural stop, 1:2 (else 1:1) target limited by the available corridor.
// Spread-dependent guards (quote spread, spread-multiple stop floor) need a
// live quote the analysis engine does not have; they remain execution-owned.
package scalpbreakoutretest

import (
	"fmt"
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "scalp_breakout_retest"
const Version = "v3"

// Config is every tunable of the strategy; all values come from
// analysis.strategies.scalp_breakout_retest in config/analysis.yml (instrument
// scale is injected per instrument).
type Config struct {
	Symbols []string

	PipSize     float64
	PriceDigits int

	SetupWindowBars        int
	ConfirmationWindowBars int
	ConfirmationLookback   int
	ContextMaxAgeSeconds   int64
	ContextATRBars         int
	ActiveRangeBars        int
	ExpiryMinutes          float64

	// Compression box (legacy path).
	BoxMaxATR, MinBreakATR, TouchTolATR float64
	MinBoxBars, MaxBoxBars              int
	MinTouchesPerSide                   int
	RetestLookbackBars                  int
	RequireRetestRejection              bool

	// Breakout Retest V2.
	V2Enabled               bool
	CrossToleranceATR       float64
	BreakoutMarginATR       float64
	MaxBreakDelayBars       *int
	AcceptanceBars          int
	AcceptanceRequired      int
	MinRetestDelayBars      int
	MaxRetestDelayBars      int
	RetestFrontRunATR       float64
	MaxRetestPenetrationATR float64
	ConfirmationMode        string
	MinQualityScore         float64
	EnableStructureFlip     bool
	EnableM5KeyLevels       bool
	EnableLiquidityLevel    bool
	SwingMinAgeBars         int
	SwingMaxAgeBars         int
	SwingMinSpacingATR      float64
	M5StructureMinTouches   int

	// Setup-window structure.
	MicroSwingLookback    int
	EqualToleranceFracPip float64
	SwingFractalN         int
	LevelClusterATR       float64
	LevelRoundStep        float64
	LevelMinimumTouches   int
	LevelMaxClusterSpan   float64

	// Stop / target (the strategies.scalping book).
	BufferM1ATRMultiple     float64
	BufferMinSpreadMultiple float64
	MaximumSpreadPips       float64
	StopMinimumPips         float64
	StopMaximumPips         float64
	MinimumNetTargetPips    float64
	RewardRiskLadder        []float64
}

type Strategy struct {
	cfg         Config
	fingerprint string
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("scalpbreakoutretest: wrong ID")
	}
	cfg, err := parseConfig(c.Parameters)
	if err != nil {
		return nil, fmt.Errorf("scalpbreakoutretest: %w", err)
	}
	return &Strategy{cfg: cfg, fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

// RequiredTimeframes is M1 only: the Python lane ran once per closed M1 bar
// (M5 owns the setup and is read from the context), and evaluating again on
// the M5 close would observe the same candidate at an earlier bar time than the
// M1 observation already recorded.
func (s *Strategy) RequiredTimeframes() []market.Timeframe {
	return []market.Timeframe{market.M1}
}

func (s *Strategy) allowsSymbol(symbol market.Symbol) bool {
	if len(s.cfg.Symbols) == 0 {
		return true
	}
	for _, allowed := range s.cfg.Symbols {
		if allowed == string(symbol) {
			return true
		}
	}
	return false
}

// armed is one direction's best episode.
type armed struct {
	Level        float64
	Source       string
	Subtype      string
	Timeframe    string
	Entry        float64
	BreakTime    int64
	Score        float64
	Components   [5]float64
	Confirmation string
	Box          *compressionBox
}

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	if ctx == nil || !s.allowsSymbol(ctx.Symbol) {
		return nil
	}
	m5tf, m1tf := ctx.Timeframes[market.M5], ctx.Timeframes[market.M1]
	if m5tf == nil || m1tf == nil || len(m5tf.Candles) == 0 || len(m1tf.Candles) == 0 {
		return nil
	}
	cfg := s.cfg
	m1All := m1tf.Candles
	now := m1All[len(m1All)-1].Time
	// M5 bars closed by the time this M1 bar closed.
	closeAt := now + 60
	m5All := m5tf.Candles
	end := len(m5All)
	for end > 0 && m5All[end-1].Time+300 > closeAt {
		end--
	}
	m5 := tail(m5All[:end], cfg.SetupWindowBars)
	m1 := tail(m1All, cfg.ConfirmationWindowBars)
	if len(m5) == 0 || len(m1) == 0 {
		return nil
	}
	c, ok := buildContext(cfg, m5, m1All, now)
	if !ok {
		return nil
	}

	micro := buildMicroStructure(m5, cfg.MicroSwingLookback, cfg.EqualToleranceFracPip*cfg.PipSize, cfg.PriceDigits)
	box := findCompressionBox(m5, c.ATR, cfg.MinBoxBars, cfg.MaxBoxBars, cfg.BoxMaxATR, cfg.MinTouchesPerSide, cfg.TouchTolATR)
	if box == nil && !cfg.V2Enabled {
		return nil
	}
	minDisplacement := math.Max(cfg.PipSize*3, c.ATR*cfg.MinBreakATR)
	params := episodeParams{
		ATR:                  c.ATR,
		CrossTolerance:       cfg.CrossToleranceATR * c.ATR,
		BreakoutMargin:       cfg.BreakoutMarginATR * c.ATR,
		MaxBreakDelayBars:    cfg.MaxBreakDelayBars,
		AcceptanceBars:       cfg.AcceptanceBars,
		AcceptanceRequired:   cfg.AcceptanceRequired,
		MinRetestDelayBars:   cfg.MinRetestDelayBars,
		MaxRetestDelayBars:   cfg.MaxRetestDelayBars,
		RetestFrontRun:       cfg.RetestFrontRunATR * c.ATR,
		MaxRetestPenetration: cfg.MaxRetestPenetrationATR * c.ATR,
		ConfirmationMode:     cfg.ConfirmationMode,
	}
	currentIndex := len(m5) - 1
	currentPrice := m5[currentIndex].Close
	buffer := strategyutil.ScalpBuffer(c.M1ATR, cfg.BufferM1ATRMultiple, cfg.PipSize, cfg.MaximumSpreadPips, cfg.BufferMinSpreadMultiple)

	var out []opportunity.Candidate
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		var best *armed
		if box != nil {
			if ev := detectBoxBreakRetest(m5, direction, box.High, box.Low, minDisplacement, cfg.RetestLookbackBars, cfg.RequireRetestRejection, box.EndIndex); ev.Armed {
				a := armed{Level: ev.Level, Source: sourceCompressionBox, Subtype: subtypeRangeBreak, Timeframe: "M5", Entry: ev.Close, BreakTime: ev.BarTime, Box: box}
				a.Score, a.Components = qualityScore(&qualityInput{BreakDistanceATR: ptrIf(c.ATR > 0, ev.BreakDisplacement/c.ATR)})
				best = &a
			}
		}
		if cfg.V2Enabled {
			var candidates []levelCandidate
			if cfg.EnableStructureFlip {
				candidates = append(candidates, structureFlipCandidates(micro, direction, currentIndex, "M5", cfg.SwingMinAgeBars, cfg.SwingMaxAgeBars, c.ATR, cfg.SwingMinSpacingATR)...)
			}
			if cfg.EnableM5KeyLevels && len(c.KeyLevels) > 0 {
				candidates = append(candidates, m5StructureFlipCandidates(c.KeyLevels, direction, currentPrice, cfg.M5StructureMinTouches)...)
			}
			if cfg.EnableLiquidityLevel {
				candidates = append(candidates, liquidityLevelCandidates(micro, direction, "M5")...)
			}
			for _, cand := range candidates {
				ep := evaluateEpisode(m5, cand, params)
				if ep.State != stateArmed {
					continue
				}
				score, components := qualityScore(&qualityInput{
					BreakDistanceATR: ep.BreakDistanceAT, AcceptanceBars: ep.AcceptanceBars,
					RetestPenetrationATR: ep.RetestPenATR, BarsUntilRetest: ep.BarsUntilRetest, ConfirmationType: ep.ConfirmationType,
				})
				if best == nil || score > best.Score {
					best = &armed{
						Level: cand.Level, Source: cand.Source, Subtype: cand.Subtype, Timeframe: cand.Timeframe,
						Entry: m1[len(m1)-1].Close, BreakTime: ep.BreakTime, Score: score, Components: components, Confirmation: ep.ConfirmationType,
					}
				}
			}
		}
		if best == nil || best.Score < cfg.MinQualityScore {
			continue
		}

		// An M5 setup is only actionable once a closed M1 bar confirms it at the
		// level; the entry and the trigger time are that bar's.
		confirm, ok := confirmM1Execution(m1, direction, best.Level, buffer, cfg.ConfirmationLookback)
		if !ok {
			continue
		}
		best.Entry, _ = confirm.Close, confirm.Time

		zoneLow, zoneHigh := best.Level-buffer, best.Level+buffer
		worst := zoneHigh
		last := m1[len(m1)-1]
		stopPrice := math.Min(last.Low, best.Level) - buffer
		structural := (worst - stopPrice) / cfg.PipSize
		room := c.BuyRoomPips
		if direction == market.Sell {
			worst = zoneLow
			stopPrice = math.Max(last.High, best.Level) + buffer
			structural = (stopPrice - worst) / cfg.PipSize
			room = c.SellRoomPips
		}
		stop, ok := strategyutil.ScalpStopPips(structural, cfg.StopMinimumPips, cfg.StopMaximumPips)
		if !ok {
			continue
		}
		invalidation := worst - stop*cfg.PipSize
		if direction == market.Sell {
			invalidation = worst + stop*cfg.PipSize
		}
		if direction == market.Buy && !(invalidation < zoneLow && zoneLow < zoneHigh) || direction == market.Sell && !(invalidation > zoneHigh && zoneHigh > zoneLow) {
			continue
		}
		targetPrice, targetPips, ok := strategyutil.ScalpTarget(direction, worst, room, stop, cfg.MinimumNetTargetPips, cfg.PipSize, cfg.RewardRiskLadder)
		if !ok {
			continue
		}

		evidence := []string{"m5_breakout_level_" + best.Source, "m5_breakout_accepted", "m5_breakout_retest_confirmed", "m1_execution_confirmed"}
		if best.Box != nil {
			evidence = append([]string{"m5_prebreakout_box"}, evidence...)
		}
		quality := opportunity.StrategyQuality{
			Overall: strategyutil.Clamp01(best.Score / 100),
			Components: map[string]float64{
				"displacement": best.Components[0], "acceptance": best.Components[1], "retest_quality": best.Components[2],
				"retest_speed": best.Components[3], "confirmation": best.Components[4],
			},
		}
		setupKey := fmt.Sprintf("scalpbr:%s:%s:%s", best.Source, direction, priceToken(best.Level, cfg.PriceDigits))
		candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
			ID: string(ID), Version: Version, SetupKey: setupKey, Symbol: ctx.Symbol, Direction: direction,
			EntryLow: zoneLow, EntryHigh: zoneHigh, Invalidation: invalidation, InvalidationLabel: "m5_breakout_retest_failed",
			Target: targetPrice, TargetLabel: "scalp_" + targetLabel(targetPips, stop), Evidence: evidence, Quality: quality,
			FormedAt: minInt64(best.BreakTime, confirm.Time), ConfirmedAt: confirm.Time, ExpiryHours: cfg.ExpiryMinutes / 60, Fingerprint: s.fingerprint,
		})
		if err != nil {
			continue
		}
		out = append(out, candidate)
	}
	return out
}

func targetLabel(targetPips, stopPips float64) string {
	if stopPips > 0 && math.Abs(targetPips-2*stopPips) < 1e-9 {
		return "1to2"
	}
	return "1to1"
}

func minInt64(a, b int64) int64 {
	if a < b {
		return a
	}
	return b
}

func tail(bars []market.Candle, n int) []market.Candle {
	if n > 0 && len(bars) > n {
		return bars[len(bars)-n:]
	}
	return bars
}

// priceToken is the identity text of a level price: rounded to the
// instrument's digits so the same level keeps one identity bar after bar.
func priceToken(price float64, digits int) string {
	return fmt.Sprintf("%.*f", digits, price)
}

type m1Confirmation = strategyutil.M1Confirmation

// confirmM1Execution is microstructure.confirm_m1_execution (see strategyutil).
func confirmM1Execution(m1 []market.Candle, direction market.Direction, level, tolerance float64, lookback int) (m1Confirmation, bool) {
	return strategyutil.ConfirmM1Execution(m1, direction, level, tolerance, lookback)
}

type qualityInput struct {
	BreakDistanceATR     *float64
	AcceptanceBars       int
	RetestPenetrationATR *float64
	BarsUntilRetest      *int
	ConfirmationType     string
}

func ptrIf(ok bool, v float64) *float64 {
	if !ok {
		return nil
	}
	return &v
}

// qualityScore mirrors strategies._breakout_retest_quality_score: a 0–100
// rank of an already armed episode. A component with no evidence scores 0.5.
func qualityScore(in *qualityInput) (float64, [5]float64) {
	var c [5]float64
	c[0] = 0.5
	if in.BreakDistanceATR != nil {
		c[0] = math.Min(1, math.Max(0, *in.BreakDistanceATR))
	}
	c[1] = 0.5
	if in.AcceptanceBars != 0 {
		c[1] = math.Min(1, 1/float64(maxInt(1, in.AcceptanceBars)))
	}
	c[2] = 0.5
	if in.RetestPenetrationATR != nil {
		c[2] = math.Max(0, 1-math.Min(1, *in.RetestPenetrationATR))
	}
	c[3] = 0.5
	if in.BarsUntilRetest != nil {
		c[3] = math.Max(0, 1-math.Min(1, float64(*in.BarsUntilRetest)/10))
	}
	switch in.ConfirmationType {
	case confirmationRetestHighBrk:
		c[4] = 1.0
	case confirmationRetestReclaim:
		c[4] = 0.7
	default:
		c[4] = 0.5
	}
	weights := [5]float64{0.30, 0.15, 0.25, 0.15, 0.15}
	sum := 0.0
	for i := range c {
		sum += weights[i] * c[i]
	}
	return 100 * sum, c
}
