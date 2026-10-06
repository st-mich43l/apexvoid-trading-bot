package strategyutil

import (
	"fmt"
	"math"
	"sort"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// The technique publishers' shared constants, from the frozen detector
// contract (zones.merge_overlap, measurements.max_merged_zone_atr,
// confluence_technique_bonus_score, STAR_TWO_SCORE). They are schema defaults
// there, never settable configuration, so they are not settable here either.
const (
	techniqueMergeOverlap   = 0.5
	techniqueMaxMergedATR   = 3.0
	techniqueConfluenceBump = 2.5
	techniqueStarTwoScore   = 8.0
)

// Technique names as the zone-chain instances carry them.
const (
	TechniqueSupplyDemand = "supply_demand"
	TechniqueOrderBlock   = "order_block"
	TechniqueFVG          = "fvg"
	TechniqueIFVG         = "ifvg"
	TechniqueCRT          = "crt"
)

// TechniqueDecision is one frozen technique publisher's decision for the bar
// (_technique_reaction / confluence_zone_reaction): the single best confirmed
// reaction, with the evidence the shared qualification produced.
type TechniqueDecision struct {
	Technique    string
	Direction    market.Direction
	Instance     *techniquezone.Instance // nil for a confluence band
	Band         *ConfluenceBand         // nil for a single technique
	Detector     *LegacyDetector
	Confirmation *LegacyConfirmation
	Result       *LegacyResult
	// ID is the stable structural identity (instance or band).
	ID string
}

// EntryLow/EntryHigh are the published entry band: the proximal clip.
func (d *TechniqueDecision) EntryLow() float64  { return d.Result.Zone.Low() }
func (d *TechniqueDecision) EntryHigh() float64 { return d.Result.Zone.High() }

// Matches reports whether this decision is for the given instance.
func (d *TechniqueDecision) Matches(technique, side string, originIndex int, low, high float64) bool {
	if d == nil || d.Instance == nil || d.Instance.Technique != technique || d.Instance.Side != side || d.Instance.OriginIndex != originIndex {
		return false
	}
	return math.Abs(d.Instance.Low-low) < 1e-9 && math.Abs(d.Instance.High-high) < 1e-9
}

// ConfluenceBand mirrors confluence_zone.ConfluenceBand: a price band where two
// or more distinct techniques overlap on one side.
type ConfluenceBand struct {
	Low, High float64
	Side      string
	Tags      []string
	Score     float64
	Touches   int
	Members   []int
}

// ID is the band's identity: side, sorted tags and the bucketed midpoint.
func (b ConfluenceBand) ID(pipSize, atr float64) string {
	step := pipSize * 40
	stable := 0.0
	if step > 0 {
		stable = math.RoundToEven(math.Max(0, atr)/step) * step
	}
	bucket := math.Max(pipSize*10, stable*0.25)
	mid := (b.Low + b.High) / 2
	if bucket > 0 {
		mid = math.RoundToEven(mid/bucket) * bucket
	}
	return fmt.Sprintf("confluence:%s:%s:%.6f", b.Side, strings.Join(b.Tags, "+"), mid)
}

func overlapRatio(aLow, aHigh, bLow, bHigh float64) float64 {
	overlap := math.Min(aHigh, bHigh) - math.Max(aLow, bLow)
	if overlap <= 0 {
		return 0
	}
	smaller := math.Min(aHigh-aLow, bHigh-bLow)
	if smaller <= 0 {
		if aLow <= bHigh && bLow <= aHigh {
			return 1
		}
		return 0
	}
	return overlap / smaller
}

// BuildConfluenceBands mirrors build_confluence_bands: per side, instances
// ordered by (low, high) join the first cluster they overlap by at least the
// merge ratio without the union growing past maxWidth; a cluster of two or more
// distinct techniques is a band.
func BuildConfluenceBands(instances []techniquezone.Instance, maxWidth float64) []ConfluenceBand {
	type cluster struct {
		side    string
		members []int
	}
	var clusters []*cluster
	for _, side := range []string{"buy", "sell"} {
		var order []int
		for i, item := range instances {
			if item.Side == side {
				order = append(order, i)
			}
		}
		sort.SliceStable(order, func(a, b int) bool {
			ia, ib := instances[order[a]], instances[order[b]]
			if ia.Low != ib.Low {
				return ia.Low < ib.Low
			}
			return ia.High < ib.High
		})
		for _, idx := range order {
			item := instances[idx]
			placed := false
			for _, c := range clusters {
				if c.side != side {
					continue
				}
				cLow, cHigh := math.Inf(1), math.Inf(-1)
				for _, m := range c.members {
					cLow, cHigh = math.Min(cLow, instances[m].Low), math.Max(cHigh, instances[m].High)
				}
				if overlapRatio(cLow, cHigh, item.Low, item.High) < techniqueMergeOverlap {
					continue
				}
				if math.Max(cHigh, item.High)-math.Min(cLow, item.Low) > maxWidth {
					continue
				}
				c.members = append(c.members, idx)
				placed = true
				break
			}
			if !placed {
				clusters = append(clusters, &cluster{side: side, members: []int{idx}})
			}
		}
	}
	var bands []ConfluenceBand
	for _, c := range clusters {
		techniques := map[string]bool{}
		low, high := math.Inf(1), math.Inf(-1)
		score, touches := math.Inf(-1), 0
		for _, m := range c.members {
			in := instances[m]
			techniques[in.Technique] = true
			low, high = math.Min(low, in.Low), math.Max(high, in.High)
			score = math.Max(score, in.ZoneScore)
			if in.Touches > touches {
				touches = in.Touches
			}
		}
		if len(techniques) < 2 {
			continue
		}
		tags := make([]string, 0, len(techniques))
		for tag := range techniques {
			tags = append(tags, tag)
		}
		sort.Strings(tags)
		bands = append(bands, ConfluenceBand{
			Low: low, High: high, Side: c.side, Tags: tags, Touches: touches, Members: c.members,
			Score: score + techniqueConfluenceBump*float64(len(tags)-1),
		})
	}
	return bands
}

func bandCovers(band ConfluenceBand, in techniquezone.Instance) bool {
	return in.Side == band.Side && overlapRatio(band.Low, band.High, in.Low, in.High) >= techniqueMergeOverlap
}

// TechniqueSource is what the technique publishers need from a frame.
type TechniqueSource struct {
	Detector  *LegacyDetector
	Instances []techniquezone.Instance
	// RetestMaxTouches is technique_retest_max_touches.
	RetestMaxTouches int
}

// NewTechniqueSource binds the execution frame's detector and instances. It
// returns false when the frame carries neither.
func NewTechniqueSource(ctx *analysiscontext.MarketContext, tf market.Timeframe, settings LegacyDetectorSettings) (*TechniqueSource, bool) {
	base, ok := NewLegacyDetectorForFrame(ctx, tf, settings)
	if !ok || base.Frame.Techniques == nil {
		return nil, false
	}
	instances := base.Frame.Techniques()
	if len(instances) == 0 {
		return nil, false
	}
	return &TechniqueSource{Detector: base, Instances: instances, RetestMaxTouches: settings.RetestMaxTouches}, true
}

func instanceZone(in techniquezone.Instance) techniquezone.Zone {
	side := "supply"
	if in.Side == "buy" {
		side = "demand"
	}
	zone := techniquezone.Zone{
		Bottom: in.Low, Top: in.High, Side: side, OriginIndex: in.OriginIndex, Touches: in.Touches, Mitigated: in.Mitigated,
		Source: in.Technique, Sources: append([]string(nil), in.Sources...), BreakIndex: -1, Score: in.ZoneScore,
	}
	return zone
}

func instanceDirection(in techniquezone.Instance) market.Direction {
	if in.Side == "buy" {
		return market.Buy
	}
	return market.Sell
}

// publish mirrors _publish_technique.
func (s *TechniqueSource) publish(in techniquezone.Instance) *TechniqueDecision {
	if in.Mitigated && (s.RetestMaxTouches <= 0 || in.Touches > s.RetestMaxTouches) {
		return nil
	}
	direction := instanceDirection(in)
	d := s.Detector.WithDirection(direction)
	zone := instanceZone(in)
	structLow, structHigh := zone.Low(), zone.High()
	if in.EntryClipped {
		structLow, structHigh = in.StructuralLow, in.StructuralHigh
	}
	if !d.EntryValid(zone) {
		return nil
	}
	conf := d.Reaction(structLow, structHigh, d.ZoneGrab(zone))
	if conf == nil {
		return nil
	}
	factors := FactorsForConfirmation(confluence.Factors{
		HTFAligned: d.HTFAligned(), Touches: zone.Touches, WickRejection: true, StructuralAgreement: true,
		DisplacementGrade: zone.Score >= techniqueStarTwoScore,
	}, conf.Type)
	result := d.Finish(d.ZoneKey(zone), zone, factors, in.Technique, &structLow, &structHigh)
	if result == nil {
		return nil
	}
	instance := in
	return &TechniqueDecision{Technique: in.Technique, Direction: direction, Instance: &instance, Detector: d, Confirmation: conf, Result: result, ID: instanceID(in, d)}
}

func instanceID(in techniquezone.Instance, d *LegacyDetector) string {
	origin := int64(0)
	if in.Technique == TechniqueCRT {
		origin = in.H1Time
	} else if in.OriginIndex >= 0 && in.OriginIndex < len(d.Frame.Bars) {
		origin = d.Frame.Bars[in.OriginIndex].Time
	}
	return fmt.Sprintf("technique:%s:%s:%d", in.Technique, in.Side, origin)
}

// Bands are the confluence bands of the bar's instances (the frozen contract's
// merge width is a multiple of the execution ATR).
func (s *TechniqueSource) Bands() []ConfluenceBand {
	atr := s.Detector.ATR
	pip := math.Max(s.Detector.Settings.PipSize, 1e-12)
	return BuildConfluenceBands(s.Instances, techniqueMaxMergedATR*math.Max(atr, pip))
}

// Technique mirrors _technique_reaction: the best confirmed reaction among the
// technique's instances not already covered by a confluence band — highest
// stars, nearest entry on a tie.
func (s *TechniqueSource) Technique(technique string) *TechniqueDecision {
	bands := s.Bands()
	var best *TechniqueDecision
	bestDistance := math.Inf(1)
	for _, in := range s.Instances {
		if in.Technique != technique {
			continue
		}
		covered := false
		for _, band := range bands {
			if bandCovers(band, in) {
				covered = true
				break
			}
		}
		if covered {
			continue
		}
		decision := s.publish(in)
		if decision == nil {
			continue
		}
		distance := decision.Detector.ZoneDistance(decision.Result.Zone)
		if best == nil || decision.Result.Stars > best.Result.Stars || decision.Result.Stars == best.Result.Stars && distance < bestDistance {
			best, bestDistance = decision, distance
		}
	}
	return best
}

// ConfluenceZone mirrors confluence_zone_reaction: the best (most stars; the
// first on a tie) confirmed band.
func (s *TechniqueSource) ConfluenceZone() *TechniqueDecision {
	var best *TechniqueDecision
	for _, band := range s.Bands() {
		direction := market.Buy
		side := "demand"
		if band.Side == "sell" {
			direction, side = market.Sell, "supply"
		}
		d := s.Detector.WithDirection(direction)
		zone := techniquezone.Zone{Bottom: band.Low, Top: band.High, Side: side, Source: "confluence", Sources: append([]string(nil), band.Tags...), BreakIndex: -1, Score: band.Score}
		if !d.EntryValid(zone) {
			continue
		}
		conf := d.Reaction(band.Low, band.High, d.ZoneGrab(zone))
		if conf == nil {
			continue
		}
		factors := FactorsForConfirmation(confluence.Factors{
			HTFAligned: d.HTFAligned(), Touches: band.Touches, WickRejection: true, StructuralAgreement: true,
			DisplacementGrade: band.Score >= techniqueStarTwoScore,
		}, conf.Type)
		low, high := zone.Low(), zone.High()
		result := d.Finish(d.ZoneKey(zone), zone, factors, strings.Join(band.Tags, "+"), &low, &high)
		if result == nil {
			continue
		}
		copied := band
		decision := &TechniqueDecision{Technique: "confluence_zone", Direction: direction, Band: &copied, Detector: d, Confirmation: conf, Result: result, ID: band.ID(s.Detector.Settings.PipSize, s.Detector.ATR)}
		if best == nil || decision.Result.Stars > best.Result.Stars {
			best = decision
		}
	}
	return best
}

// ConfirmedTechnique evaluates one frozen technique publisher on the frame and
// returns its confirmed opportunity, if any. direction, when set, restricts the
// result to one side (supply and demand split one publisher between them).
func ConfirmedTechnique(ctx *analysiscontext.MarketContext, legacy LegacyDetectorSettings, technique string, direction market.Direction, spec TechniqueSpec) []opportunity.Candidate {
	source, ok := NewTechniqueSource(ctx, market.M5, legacy)
	if !ok {
		return nil
	}
	var dec *TechniqueDecision
	if technique == "confluence_zone" {
		dec = source.ConfluenceZone()
	} else {
		dec = source.Technique(technique)
	}
	if dec == nil || direction != "" && dec.Direction != direction {
		return nil
	}
	candidate, ok := TechniqueCandidate(ctx, dec, spec)
	if !ok {
		return nil
	}
	return []opportunity.Candidate{candidate}
}
