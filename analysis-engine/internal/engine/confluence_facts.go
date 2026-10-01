package engine

import (
	"math"
	"strings"

	confluencescore "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// confluenceContext translates the canonical Go state already attached to a
// candidate into the versioned technical score. It deliberately reads no raw
// OHLC outside the worker's closed-bar state and performs no eligibility or
// execution action.
func (w *SymbolWorker) confluenceContext(event marketdata.BarEvent, candidate opportunity.Candidate, technical *opportunity.TechnicalContext) *opportunity.ConfluenceContext {
	if technical == nil {
		return nil
	}
	tf, ok := w.state.Context.Timeframes[event.Timeframe]
	if !ok || tf == nil {
		return nil
	}

	quality, touches := w.canonicalZoneQuality(candidate, tf, event.Timeframe)
	factors := confluencescore.Factors{
		HTFAligned:     higherTimeframeAligned(technical.HigherTimeframes, candidate.Direction),
		Touches:        touches,
		SessionContext: tf.Session.Active != "",
		FibTouch:       fibTouch(tf.Fib, candidate, technical.ATR, w.settings.Fib.EpsilonATR),
	}
	if candidate.Reaction != nil {
		switch candidate.Reaction.Pattern {
		case "wick_rejection":
			factors.WickRejection = true
		case "engulfing":
			factors.DisplacementGrade = true
		case "strong_reclaim", "sweep_reclaim":
			factors.StructuralAgreement = true
		case "rejection_choch":
			factors.StructuralAgreement = true
			factors.CHoCH = true
		}
	}
	// Non-zone strategies carry their own machine-readable evidence. These
	// keyword mappings are only additive factor facts; strategy quality and
	// the selected Go strategy remain authoritative for the candidate itself.
	for _, evidence := range candidate.Evidence {
		code := strings.ToLower(evidence.Code)
		if strings.Contains(code, "wick") || strings.Contains(code, "rejection") {
			factors.WickRejection = true
		}
		if strings.Contains(code, "displacement") || strings.Contains(code, "engulf") {
			factors.DisplacementGrade = true
		}
		if strings.Contains(code, "reclaim") || strings.Contains(code, "structure") || strings.Contains(code, "break") {
			factors.StructuralAgreement = true
		}
	}
	if recentCHoCH(tf.Structure.Breaks, candidate.Direction, candidate.CreatedAt) {
		factors.CHoCH = true
	}

	score := confluencescore.Evaluate(quality, factors, w.settings.Confluence, madBonus(technical))
	return &opportunity.ConfluenceContext{
		Version: score.Version, SelectedStars: score.SelectedStars,
		V1Stars: score.V1Stars, V2Stars: score.V2Stars, V2Raw: score.V2Raw,
		RawFactorScore: score.RawFactorScore, ZoneQualityScore: score.ZoneQualityScore,
		MADBonus: score.MADBonus,
		Factors: opportunity.ConfluenceFactors{
			HTFAligned: score.Factors.HTFAligned, Touches: score.Factors.Touches,
			WickRejection: score.Factors.WickRejection, DisplacementGrade: score.Factors.DisplacementGrade,
			SessionContext: score.Factors.SessionContext, StructuralAgreement: score.Factors.StructuralAgreement,
			FibTouch: score.Factors.FibTouch, CHoCH: score.Factors.CHoCH,
		},
	}
}

// madBonus returns the already-computed engine affinity. The scorer remains
// unaware of the MAD package and therefore cannot accidentally reclassify it.
func madBonus(t *opportunity.TechnicalContext) float64 {
	if t == nil || t.MAD == nil {
		return 0
	}
	return t.MAD.Affinity
}

func higherTimeframeAligned(higher []opportunity.HigherTimeframeBias, direction market.Direction) bool {
	for _, item := range higher {
		if item.Direction == direction {
			return true
		}
	}
	return false
}

func fibTouch(state fib.State, candidate opportunity.Candidate, atr, epsilon float64) bool {
	if atr <= 0 || len(state.Ladder) == 0 {
		return false
	}
	level := (candidate.Entry.Low + candidate.Entry.High) / 2
	_, ok := fib.NearestLevel(state.Ladder, market.Price(level), market.Price(atr), epsilon)
	return ok
}

func recentCHoCH(breaks []structure.StructureBreak, direction market.Direction, createdAt int64) bool {
	// Reaction confirmation already carries the exact CHoCH fact when it is
	// causal. This fallback covers non-reaction strategies and is deliberately
	// bounded to the latest few closed breaks rather than treating old history
	// as current confluence.
	seen := 0
	for i := len(breaks) - 1; i >= 0 && seen < 3; i-- {
		brk := breaks[i]
		if brk.Time > createdAt {
			continue
		}
		seen++
		if brk.Event == structure.EventCHoCH && brk.Direction == direction {
			return true
		}
	}
	return false
}

func (w *SymbolWorker) canonicalZoneQuality(candidate opportunity.Candidate, tf *analysiscontext.TimeframeContext, eventTF market.Timeframe) (confluencescore.ZoneQuality, int) {
	for _, z := range tf.Zones.Zones {
		if z.ID != candidate.StructuralID {
			continue
		}
		score := 0.0
		if z.TouchCount == 0 {
			score += 3
		} else if z.TouchCount == 1 {
			score += 1
		}
		score += sourceScore(z)
		for _, level := range tf.KeyLevel.Levels {
			if overlapsKeyLevel(z, level) {
				score += 2
				break
			}
		}
		if roundInside(z, w.settings.KeyLevel.RoundStep) {
			score++
		}
		if liquidityConfluence(z, tf.Liquidity.Pools, w.settings.Geometry.PipSize) {
			score += 2
		}
		for _, level := range tf.Session.Levels {
			if sessionConfluence(z, level, w.settings.Geometry.PipSize) {
				score += 2
				break
			}
		}
		if tf.Fib.Range != nil {
			if z.Side == zone.Demand && float64(z.High) <= float64(tf.Fib.Range.Equilibrium) {
				score += 2
			}
			if z.Side == zone.Supply && float64(z.Low) >= float64(tf.Fib.Range.Equilibrium) {
				score += 2
			}
		}
		// The Go liquidity domain intentionally has no Python Grab grade yet;
		// do not invent a Grade-A bonus. HTF and trendline facts are canonical.
		eventMinutes, _ := eventTF.Minutes()
		for higherTF, higher := range w.state.Context.Timeframes {
			higherMinutes, _ := higherTF.Minutes()
			if higherTF == eventTF || higherMinutes <= eventMinutes {
				continue
			}
			for _, htfZone := range higher.Zones.Zones {
				if insideHigherZone(z, htfZone) {
					score += 3
					break
				}
			}
		}
		if len(tf.Candles) > 0 {
			last := len(tf.Candles) - 1
			for _, line := range tf.Trendline.Lines {
				if line.BrokenAt == nil {
					value := float64(trendline.ValueAt(line, last))
					if value >= float64(z.Low) && value <= float64(z.High) {
						score += 1.5
						break
					}
				}
			}
		}
		if score > 24.5 {
			score = 24.5
		}
		return confluencescore.ZoneQuality{Score: score, Touches: z.TouchCount}, z.TouchCount
	}
	return confluencescore.ZoneQuality{}, 0
}

func sourceScore(z zone.Zone) float64 {
	switch z.Kind {
	case zone.KindOrderBlock:
		return 3
	case zone.KindBreaker:
		return 2
	case zone.KindFlip:
		return 2
	case zone.KindSupply, zone.KindDemand:
		return 1.5
	case zone.KindFVG, zone.KindIFVG:
		return 1
	default:
		return 0
	}
}

func overlapsKeyLevel(z zone.Zone, level keylevel.Level) bool {
	band := math.Max(0, level.Band)
	return float64(z.Low) <= float64(level.Price)+band && float64(z.High) >= float64(level.Price)-band
}

func roundInside(z zone.Zone, step float64) bool {
	if step <= 0 {
		return false
	}
	first := math.Ceil(float64(z.Low)/step) * step
	return first <= float64(z.High)+1e-9
}

func liquidityConfluence(z zone.Zone, pools []liquidity.Pool, pipSize float64) bool {
	width := math.Max(float64(z.High-z.Low), 0)
	for _, pool := range pools {
		tolerance := math.Max(math.Max(float64(pool.High-pool.Low), width), pipSize)
		level := float64(pool.Low+pool.High) / 2
		if z.Side == zone.Demand && pool.Side == liquidity.LiquiditySellSide && float64(z.Low)-tolerance <= level && level <= float64(z.High) {
			return true
		}
		if z.Side == zone.Supply && pool.Side == liquidity.LiquidityBuySide && float64(z.Low) <= level && level <= float64(z.High)+tolerance {
			return true
		}
	}
	return false
}

func sessionConfluence(z zone.Zone, level session.Level, pipSize float64) bool {
	if level.Swept {
		return false
	}
	lowName := strings.HasSuffix(level.Name, "_L") || level.Name == "PDL" || level.Name == "PWL"
	highName := strings.HasSuffix(level.Name, "_H") || level.Name == "PDH" || level.Name == "PWH"
	width := math.Max(float64(z.High-z.Low), 0)
	tolerance := math.Max(width, pipSize)
	price := float64(level.Price)
	if z.Side == zone.Demand && lowName {
		return (float64(z.Low) <= price && price <= float64(z.High)) || (float64(z.Low)-tolerance <= price && price <= float64(z.Low))
	}
	if z.Side == zone.Supply && highName {
		return (float64(z.Low) <= price && price <= float64(z.High)) || (float64(z.High) <= price && price <= float64(z.High)+tolerance)
	}
	return false
}

func insideHigherZone(z, higher zone.Zone) bool {
	return z.Side == higher.Side && z.Low >= higher.Low && z.High <= higher.High
}
