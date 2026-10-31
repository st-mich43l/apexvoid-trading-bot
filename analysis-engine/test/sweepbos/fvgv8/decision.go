package fvgv8

import (
	"fmt"
	"math"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// tolerance is the comparison slack of the entry and level tests.
const tolerance = 1e-9

// view is the execution frame as one evaluation reads it: the closed M5 bars and the frame's read-only facts (its ATR, grabs, swings, pools, the higher
// timeframe bias). It decides nothing.
type view struct {
	frame *analysiscontext.LegacyFrame
	read  analysiscontext.LegacyRead
	cfg   *Config
	dir   market.Direction
	price float64 // the latest closed bar's close
	atr   float64 // the execution frame's detector ATR
}

func newView(ctx *analysiscontext.MarketContext, cfg *Config) (*view, bool) {
	if ctx == nil || ctx.Legacy == nil {
		return nil, false
	}
	frame := ctx.Timeframes[market.M5]
	if frame == nil || frame.Legacy == nil || len(frame.Legacy.Bars) < 5 {
		return nil, false
	}
	bars := frame.Legacy.Bars
	atr := frame.Legacy.DetectorATR
	if !(atr > 0) {
		atr = 1
	}
	return &view{frame: frame.Legacy, read: *ctx.Legacy, cfg: cfg, price: bars[len(bars)-1].Close, atr: atr}, true
}

func (v *view) withDirection(direction market.Direction) *view {
	copy := *v
	copy.dir = direction
	return &copy
}

func (v *view) htfAligned() bool { return v.read.AlignedWithHTF(v.dir) }

// entryValid: price is at the band or within maximum_entry_atr of its proximal edge, and not through it.
func (v *view) entryValid(z zoneBand) bool {
	maxDistance := math.Max(0, v.atr) * math.Max(0, v.cfg.MaximumEntryATR)
	distance := 0.0
	if v.dir == market.Sell {
		if v.price > z.High()+tolerance {
			return false
		}
		if !(z.Low() <= v.price && v.price <= z.High()) {
			distance = z.Low() - v.price
		}
	} else {
		if v.price < z.Low()-tolerance {
			return false
		}
		if !(z.Low() <= v.price && v.price <= z.High()) {
			distance = v.price - z.High()
		}
	}
	return distance <= maxDistance+tolerance
}

func (v *view) levelValid(level float64) bool {
	if v.dir == market.Buy {
		return level <= v.price+tolerance
	}
	return level >= v.price-tolerance
}

func (v *view) zoneDistance(z zoneBand) float64 {
	if z.Low() <= v.price && v.price <= z.High() {
		return 0
	}
	if v.dir == market.Buy {
		return math.Abs(v.price - z.High())
	}
	return math.Abs(z.Low() - v.price)
}

// zoneKey is the band's reference level: the proximal edge once price is past it, the far one otherwise.
func (v *view) zoneKey(z zoneBand) float64 {
	if v.dir == market.Buy {
		if z.High() <= v.price+tolerance {
			return z.High()
		}
		return z.Low()
	}
	if z.Low() >= v.price-tolerance {
		return z.Low()
	}
	return z.High()
}

// recentCHoCH: a change of character in the trade direction within the last lookback bars.
func (v *view) recentCHoCH(lookback int) bool {
	earliest := maxInt(0, len(v.frame.Bars)-maxInt(1, lookback)-1)
	wanted := "down"
	if v.dir == market.Buy {
		wanted = "up"
	}
	for _, brk := range v.frame.Breaks {
		if brk.Kind == "CHoCH" && brk.Direction == wanted && brk.Index >= earliest {
			return true
		}
	}
	return false
}

func (v *view) fibTouch(level float64) bool {
	if !v.cfg.FibonacciEnabled || v.atr <= 0 {
		return false
	}
	ladder := v.frame.FibLadder
	if len(ladder) == 0 {
		swings := make([]structure.Swing, 0, len(v.frame.Swings))
		for _, swing := range v.frame.Swings {
			kind := structure.SwingHigh
			if swing.Kind == "low" {
				kind = structure.SwingLow
			}
			swings = append(swings, structure.Swing{Kind: kind, Price: market.Price(swing.Price)})
		}
		ladder = fib.LadderForPrice(swings, level)
	}
	_, ok := fib.NearestLevel(ladder, market.Price(level), market.Price(v.atr), v.cfg.FibonacciEpsilonATR)
	return ok
}

// result is a decision that passed the qualification.
type result struct {
	zone           zoneBand // the entry band
	structuralLow  float64
	structuralHigh float64
	score          confluence.Score
	stars          int
}

// finish is the qualification: the entry band (clipped to its proximal slice when wide), level and entry validity, the fibonacci touch, the confluence
// stars and the confluence floor.
func (v *view) finish(level float64, z zoneBand, factors confluence.Factors, structLow, structHigh float64) *result {
	side := "buy"
	if v.dir == market.Sell {
		side = "sell"
	}
	if clippedLow, clippedHigh, wasClipped := clipEntry(side, z.Low(), z.High(), v.cfg.EntryMaxWidthPrice); wasClipped {
		z.Bottom, z.Top = clippedLow, clippedHigh
	}
	outLow, outHigh := structLow, structHigh
	if !v.levelValid(level) || !v.entryValid(z) {
		return nil
	}
	if v.fibTouch(level) {
		factors.FibTouch = true
	}
	score := confluence.Evaluate(confluence.ZoneQuality{Score: z.Score, Touches: z.Touches}, factors, v.cfg.Confluence, 0)
	if score.SelectedStars < v.cfg.ConfluenceFloor {
		return nil
	}
	return &result{zone: z, structuralLow: outLow, structuralHigh: outHigh, score: score, stars: score.SelectedStars}
}

func (r *result) confluenceContext() *opportunity.ConfluenceContext {
	score := r.score
	return &opportunity.ConfluenceContext{
		Version: score.Version, SelectedStars: score.SelectedStars, V1Stars: score.V1Stars, V2Stars: score.V2Stars,
		V2Raw: score.V2Raw, RawFactorScore: score.RawFactorScore, ZoneQualityScore: score.ZoneQualityScore, MADBonus: score.MADBonus,
		Factors: opportunity.ConfluenceFactors{
			HTFAligned: score.Factors.HTFAligned, Touches: score.Factors.Touches, WickRejection: score.Factors.WickRejection,
			DisplacementGrade: score.Factors.DisplacementGrade, SessionContext: score.Factors.SessionContext,
			StructuralAgreement: score.Factors.StructuralAgreement, FibTouch: score.Factors.FibTouch, CHoCH: score.Factors.CHoCH,
		},
	}
}

// decision is one gap's confirmed reaction on the bar, with the geometry it was judged on.
type decision struct {
	inst         instance
	direction    market.Direction
	v            *view
	conf         *confirmation
	res          *result
	id           string
	reference    float64 // the proximal edge of the entry band
	invalidation float64
	anchored     bool   // the reaction's own extreme set the stop
	budgetCode   string // stop-budget evidence ("" without a budget)
	room         roomResult
}

func directionOf(in instance) market.Direction {
	if in.Side == "buy" {
		return market.Buy
	}
	return market.Sell
}

func (in instance) band() zoneBand {
	side := "supply"
	if in.Side == "buy" {
		side = "demand"
	}
	return zoneBand{Bottom: in.Low, Top: in.High, Side: side, Score: in.Score, Touches: in.Touches}
}

// factorsFor are the confluence factors the confirmation measurably showed: the wick of the rejecting bar, its engulfing body or the gap's own middle
// candle being a body of at least one structure ATR, the higher timeframe bias and the gap's touches; the rejecting bar closing through the latest
// execution-frame swing in the trade direction that was already confirmed before it is the structural agreement. (The fibonacci touch is added by finish.)
func (v *view) factorsFor(in instance, z zoneBand, conf *confirmation) confluence.Factors {
	return confluence.Factors{
		HTFAligned: v.htfAligned(), Touches: z.Touches,
		WickRejection:       conf.Type == patternWickRejection,
		DisplacementGrade:   conf.Type == patternEngulfing || v.middleBodyATR(in) >= 1.0,
		StructuralAgreement: v.confirmedThroughRecentSwing(conf.ConfirmationIndex),
	}
}

// publish decides one gap: entry validity, the measured confirmation after the gap formed, the qualification, the stop and the stop budget, then room and
// leg extension. refuse is told why a gap that was in play did not publish.
func (s *Strategy) publish(base *view, in instance, refuse func(reason string)) *decision {
	direction := directionOf(in)
	v := base.withDirection(direction)
	z := in.band()
	structLow, structHigh := in.structural()
	if !v.entryValid(z) {
		return nil
	}
	conf, reason := v.confirm(in, z.Low(), z.High(), structLow, structHigh)
	if conf == nil {
		if reason != "" {
			refuse(reason)
		}
		return nil
	}
	res := v.finish(v.zoneKey(z), z, v.factorsFor(in, z, conf), structLow, structHigh)
	if res == nil {
		return nil
	}
	reference := res.zone.High()
	if direction == market.Sell {
		reference = res.zone.Low()
	}
	buffer := s.cfg.InvalidationBufferATR * v.atr
	invalidation, anchored := reactionAnchoredInvalidation(v.frame.Bars, conf, direction, res.structuralLow, res.structuralHigh, buffer)
	budgetCode, ok := s.cfg.StopBudget.Apply(reference, invalidation)
	if !ok {
		return nil
	}
	room := v.room(in, reference, math.Abs(reference-invalidation))
	if len(room.Reasons) > 0 {
		for _, r := range room.Reasons {
			refuse(r)
		}
		return nil
	}
	return &decision{
		inst: in, direction: direction, v: v, conf: conf, res: res, id: v.instanceID(in),
		reference: reference, invalidation: invalidation, anchored: anchored, budgetCode: budgetCode, room: room,
	}
}

// instanceID is the structure's stable identity: technique, side, the open time of the bar that completed the gap on the execution frame, and the
// higher timeframe when the gap lives there.
func (v *view) instanceID(in instance) string {
	origin := in.OriginTime // the structure bar's own time, never an index: identity must not change when the origin leaves the M5 window
	if in.Timeframe != "" {
		return fmt.Sprintf("technique:fvg:%s:%d@%s", in.Side, origin, in.Timeframe)
	}
	return fmt.Sprintf("technique:fvg:%s:%d", in.Side, origin)
}

// best is the single best confirmed reaction among instances: highest stars, nearest entry on a tie.
func (s *Strategy) best(base *view, instances []instance, refuse func(in instance, reason string)) *decision {
	var best *decision
	bestDistance := math.Inf(1)
	for _, in := range instances {
		d := s.publish(base, in, func(reason string) { refuse(in, reason) })
		if d == nil {
			continue
		}
		distance := d.v.zoneDistance(d.res.zone)
		if best == nil || d.res.stars > best.res.stars || d.res.stars == best.res.stars && distance < bestDistance {
			best, bestDistance = d, distance
		}
	}
	return best
}

func lowerTimeframe(tf market.Timeframe) string { return strings.ToLower(string(tf)) }
