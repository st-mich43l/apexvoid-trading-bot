package structure

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Swing-anchored supply and demand zones (SD-3).
//
// A zone is where a major swing was made and left: the SWING gives the structure (a confirmed, significant high for supply, low for demand), the DEPARTURE gives
// the proof (the displacement leg that moved price away from it), and the BASE is the actual consolidation the swing was made in, found from the candles
// themselves and not assumed to be "the previous three":
//
//	base start   walk back from the swing candle while the candle is not itself an impulse (its range is at most BaseMaxCandleATR x ATR), overlaps the base built so
//	             far, and the base has fewer than BaseMaxCandles candles; the swing candle always belongs to the base.
//	base end     the last candle before the departure leg (the swing candle itself when the leg starts on it).
//
// The structural range is the whole base, wicks included (never narrowed by a later entry decision); Proximal is the edge price meets first, Distal the far edge
// the structure is invalidated beyond. The zone is only a candidate when the departure leg begins within DepartureGapBars of the swing, and it is Strong,
// Moderate or Weak from the evidence alone:
//
//	Strong    a MAJOR swing that still stands, with a departure leg of at least StrongDepartureATR;
//	Moderate  a swing of at least INTERMEDIATE class that still stands;
//	Weak      anything else (a minor swing, a swing that was closed through, a base with no swing under it).
//
// Only the strategy decides what to publish (minimum_zone_strength); a Weak zone is kept as context. Everything here reads closed bars only: the swing is
// known when its right window has closed (DetectPivots), the departure when its last bar has, and the zone is touchable from the later of the two.

// Strength is how strong the zone's structure is.
type Strength uint8

const (
	StrengthWeak Strength = iota
	StrengthModerate
	StrengthStrong
)

func (s Strength) String() string {
	switch s {
	case StrengthStrong:
		return "strong"
	case StrengthModerate:
		return "moderate"
	default:
		return "weak"
	}
}

// Leg is a displacement leg of a frame (a run of strong same-direction candles) given by the strategy that defines it. Start and End are bar indices.
type Leg struct{ Start, End int }

// ZoneConfig is the zone formation's own configuration (analysis.yml strategies.supply|demand): all thresholds are in ATR of the frame or in bars.
type ZoneConfig struct {
	Major              MajorConfig
	DepartureGapBars   int
	BaseMaxCandles     int
	BaseMaxCandleATR   float64
	MinimumZoneATR     float64
	MaximumZoneATR     float64
	StrongDepartureATR float64
}

// Valid reports a configuration that can form zones.
func (c ZoneConfig) Valid() error {
	if err := c.Major.Valid(); err != nil {
		return err
	}
	if c.DepartureGapBars < 0 || c.BaseMaxCandles < 1 || c.BaseMaxCandleATR <= 0 || c.MinimumZoneATR < 0 || c.MaximumZoneATR <= c.MinimumZoneATR || c.StrongDepartureATR <= 0 {
		return errInvalidZoneConfig
	}
	return nil
}

type zoneConfigError string

func (e zoneConfigError) Error() string { return string(e) }

const errInvalidZoneConfig = zoneConfigError("structure: invalid swing zone configuration (base bounds, zone width bounds and strong departure must be positive and ordered)")

// SwingZone is one zone with its whole derivation.
type SwingZone struct {
	Supply bool
	// Low and High are the structural range of the complete base. Proximal is the edge price meets first (Low for supply, High for demand); Distal is the far edge the
	// structure is invalidated beyond.
	Low, High        float64
	Proximal, Distal float64

	Swing       MajorSwing
	AnchorPrice float64 // the swing's extreme price: the highest high of a supply base, the lowest low of a demand base

	BaseStart, BaseEnd int
	BaseFormedAt       int64 // open time of the last base candle
	LegStart, LegEnd   int
	// DepartureConfirmedAt is the open time of the leg's last bar; ConfirmedIndex the first bar index by which the swing AND the departure are both known.
	DepartureConfirmedAt int64
	ConfirmedIndex       int
	// ConfirmedAt is the open time of the bar at ConfirmedIndex: the zone is formed, and touchable, only from the CLOSE of that bar.
	ConfirmedAt int64

	// BodyLow and BodyHigh are the extremes of the base candles' BODIES (open and close), wicks excluded: the proximal body edge is an internal level of the base.
	BodyLow, BodyHigh float64
	DepartureATR      float64 // the leg's net move in ATR of its last bar
	Efficiency        float64 // net move over the sum of the leg's bar ranges (1 = every bar travelled the same way)
	WidthATR          float64
	Strength          Strength
	TooWide           bool // wider than a zone: a region
	TooNarrow         bool // an incidental candle region
	// Aged is the zone's CURRENT validity, apart from its formation: the swing is older than RelevanceBars as of the last bar given. It ends tradeability and never changes
	// Strength, which is fixed at formation.
	Aged bool
}

// Why a candidate departure made no zone. Every one is a fact about the bars, deterministic and causal.
const (
	RejectNoBase           = "no_base_before_departure"              // the candle before the departure is not a quiet candle of a base
	RejectNoSwing          = "no_confirmed_swing_in_base"            // the base holds no confirmed swing of the kind
	RejectExtremeNotSwing  = "base_extreme_is_not_a_confirmed_swing" // the base's extreme candle is not a confirmed swing: the association is ambiguous, so it is not guessed
	RejectAwaitingSwing    = "awaiting_swing_confirmation"           // the extreme candle's right window has not closed yet (formed later, not rejected for good)
	RejectSwingTooFar      = "swing_too_far_from_departure"          // the anchoring swing is more than base_departure_gap_bars before the departure
	RejectSwingAlreadyUsed = "swing_already_anchors_a_zone"          // one structural event is one zone
	RejectRegionTooWide    = "region_wider_than_a_zone"              // the structure around the swing is wider than maximum_zone_atr: a region, not a zone
)

// Rejection records a displacement leg that made no zone and why.
type Rejection struct {
	LegStart  int
	Departure int
	Reason    string
}

// FormSwingZones returns the zones anchored on the swings of bars. legs are the departure legs of the direction the zone needs (down for supply, up for demand).
// The result is in departure order. bars/atr are the closed bars of the frame and its ATR series.
func FormSwingZones(supply bool, bars []market.Candle, atr []float64, legs []Leg, cfg ZoneConfig) []SwingZone {
	zones, _ := FormSwingZonesDetailed(supply, bars, atr, legs, cfg)
	return zones
}

// FormSwingZonesDetailed is FormSwingZones with the reason every leg that made no zone made none.
//
// The base is built FROM THE DEPARTURE BACKWARDS, so it is by construction the region the departure left: from the last candle before the departure's first bar, a candle joins
// while it is a quiet candle (its range is at most base_max_candle_atr x ATR), overlaps the base so far, and the base is under base_max_candles candles (counted over the whole
// base, the candles between the swing and the departure included, and the departure's first candle) and the base stays within maximum_zone_atr (a slow trend is not absorbed one candle at a time). A candle that is a confirmed swing may join even when impulsive, by its rejected part only (a supply swing
// candle by its range from its body top to its high); the candles before it must then overlap that part, which a move into the structure does not. If the candle before the departure is none of these, the
// departure did not leave a base and there is no zone. The departure's first candle always contributes only what it traded before leaving (from its open to its extreme): its own
// travel is the departure.
//
// The zone is anchored on the swing that IS the base's extreme: the highest confirmed swing high of a supply base (the lowest swing low of a demand base). A base whose extreme
// candle is not a confirmed swing is ambiguous and is rejected, never guessed; a swing that already anchors a zone does not anchor a second one (one structural event, one zone);
// and AnchorPrice is that swing's own extreme, equal to the base's.
func FormSwingZonesDetailed(supply bool, bars []market.Candle, atr []float64, legs []Leg, cfg ZoneConfig) ([]SwingZone, []Rejection) {
	if cfg.Valid() != nil || len(bars) == 0 {
		return nil, nil
	}
	kind := PivotHigh
	if !supply {
		kind = PivotLow
	}
	swings := map[int]MajorSwing{}
	for _, sw := range ClassifyMajor(bars, atr, cfg.Major) {
		if sw.Kind == kind {
			swings[sw.BarIndex] = sw
		}
	}
	var out []SwingZone
	var rejected []Rejection
	anchored := map[int]bool{}
	atFormation := map[int][]MajorSwing{} // classification of the bars up to a formation index, shared by the zones formed on that bar
	last := len(bars) - 1
	for _, leg := range legs {
		var zone *SwingZone
		reason, departure := RejectNoBase, leg.Start
		for _, d := range departureStarts(bars, atr, leg, swings, cfg) {
			departure = d
			b, why := baseBefore(bars, atr, d, swings, supply, last, cfg)
			if why != "" {
				reason = why
				continue
			}
			if b.anchor.BarIndex < d-cfg.DepartureGapBars {
				reason = RejectSwingTooFar
				continue
			}
			if anchored[b.anchor.BarIndex] {
				reason = RejectSwingAlreadyUsed
				continue
			}
			z := assemble(supply, bars, atr, leg, d, b, cfg)
			zone = &z
			break
		}
		if zone == nil {
			rejected = append(rejected, Rejection{LegStart: leg.Start, Departure: departure, Reason: reason})
			continue
		}
		anchored[zone.Swing.BarIndex] = true
		// The strength is the evidence available when the zone was FORMED: the swing is known (its right window closed) and the departure has closed. It is read from the bars
		// up to that bar only, so a later shift, a later extension of the departure or a later close is never an upgrade of a zone that was formed without it, and the strength is
		// the same at every later bar. Whether the zone is still tradeable now (too old, closed through, spent) is the lifecycle's: Aged below and the strategy's verdicts.
		if _, cached := atFormation[zone.ConfirmedIndex]; !cached {
			atFormation[zone.ConfirmedIndex] = ClassifyMajor(bars[:zone.ConfirmedIndex+1], atr[:minInt(len(atr), zone.ConfirmedIndex+1)], cfg.Major)
		}
		formed := zone.Swing
		for _, f := range atFormation[zone.ConfirmedIndex] {
			if f.Kind == formed.Kind && f.BarIndex == formed.BarIndex {
				formed = f
			}
		}
		zone.Swing = formed
		zone.Strength = strengthOf(formed, standing(bars, formed, zone.ConfirmedIndex, supply), zone.DepartureATR, cfg)
		zone.Aged = last-formed.BarIndex > cfg.Major.RelevanceBars
		out = append(out, *zone)
	}
	return out, rejected
}

// departureStarts are the bars a leg's departure may begin at, in the order they are tried: the leg's first bar, and, when the leg is a run that began inside the
// consolidation (the base candles happen to be the leg's colour), its first impulse candle that is not itself a swing candle.
func departureStarts(bars []market.Candle, atr []float64, leg Leg, swings map[int]MajorSwing, cfg ZoneConfig) []int {
	starts := []int{leg.Start}
	if quiet(bars[leg.Start], atrAt(atr, leg.Start), cfg) {
		for j := leg.Start + 1; j <= leg.End; j++ {
			// A swing candle that happens to be tall is the structure's extreme, not the first bar of the move away from it.
			if _, isSwing := swings[j]; !quiet(bars[j], atrAt(atr, j), cfg) && !isSwing {
				starts = append(starts, j)
				break
			}
		}
	}
	return starts
}

// quiet reports a candle small enough to be part of a base.
func quiet(c market.Candle, unit float64, cfg ZoneConfig) bool {
	return unit > 0 && c.High-c.Low <= cfg.BaseMaxCandleATR*unit
}

// span is a price interval.
type span struct{ lo, hi float64 }

func (s span) overlaps(o span) bool { return s.lo <= o.hi && s.hi >= o.lo }

// rejected is the part of a candle a base takes when the candle is impulsive: what it traded before moving away, from the body edge to the extreme the structure is about.
func rejectedPart(c market.Candle, supply bool) span {
	if supply {
		return span{lo: math.Max(c.Open, c.Close), hi: c.High}
	}
	return span{lo: c.Low, hi: math.Min(c.Open, c.Close)}
}

// base is the region a departure left.
type base struct {
	start, end     int
	lo, hi         float64
	bodyLo, bodyHi float64
	anchor         MajorSwing
}

// baseBefore builds the base of a departure that begins at d and finds its anchor; a non-empty reason means there is none.
func baseBefore(bars []market.Candle, atr []float64, d int, swings map[int]MajorSwing, supply bool, last int, cfg ZoneConfig) (base, string) {
	var b base
	have := false
	include := func(j int, s span, bodyLo, bodyHi float64) {
		if !have {
			b = base{start: j, end: j, lo: s.lo, hi: s.hi, bodyLo: bodyLo, bodyHi: bodyHi}
			have = true
			return
		}
		b.start = j
		b.lo, b.hi = math.Min(b.lo, s.lo), math.Max(b.hi, s.hi)
		b.bodyLo, b.bodyHi = math.Min(b.bodyLo, bodyLo), math.Max(b.bodyHi, bodyHi)
	}
	// The departure's first candle contributes what it traded before leaving (a supply candle: from its body top to its high). Whether or not it is a swing, so the zone does not
	// change when that candle's right window closes later: if its extreme is the base's extreme it must be a confirmed swing, and until it is the zone is "awaiting", not different.
	p := rejectedPart(bars[d], supply)
	include(d, p, p.lo, p.hi)
	b.end = d
	capped := false
	count := 0 // base_max_candles counts the consolidation candles before the departure, not the departure's own first candle
	for j := d - 1; j >= 0 && count < cfg.BaseMaxCandles; j-- {
		c := bars[j]
		_, isSwing := swings[j]
		full := span{c.Low, c.High}
		bodyLo, bodyHi := math.Min(c.Open, c.Close), math.Max(c.Open, c.Close)
		s := full
		if !quiet(c, atrAt(atr, j), cfg) {
			if !isSwing {
				break
			}
			s = rejectedPart(c, supply)
			bodyLo, bodyHi = s.lo, s.hi
		}
		if have && !s.overlaps(span{b.lo, b.hi}) {
			break
		}
		if have && math.Max(b.hi, s.hi)-math.Min(b.lo, s.lo) > cfg.MaximumZoneATR*atrAt(atr, d) {
			capped = true
			break // a base is a LOCAL consolidation: a candle that would stretch it past a zone's width is the trend into it, not part of it
		}
		if !have {
			b.end = j
		}
		include(j, s, bodyLo, bodyHi)
		count++
	}
	if !have {
		return base{}, RejectNoBase
	}
	extreme := b.hi
	if !supply {
		extreme = b.lo
	}
	var candidates []MajorSwing
	for j := b.start; j <= b.end; j++ {
		if sw, ok := swings[j]; ok && float64(sw.Price) == extreme {
			candidates = append(candidates, sw)
		}
	}
	if len(candidates) == 0 && capped {
		return base{}, RejectRegionTooWide
	}
	if len(candidates) == 0 {
		// Is the extreme candle simply not confirmed yet? Then the zone forms later; otherwise the base's extreme is not a swing and the association is ambiguous.
		for j := b.start; j <= b.end; j++ {
			e := bars[j].High
			if !supply {
				e = bars[j].Low
			}
			if e == extreme && j+cfg.Major.PivotRightBars > last {
				return base{}, RejectAwaitingSwing
			}
		}
		for j := b.start; j <= b.end; j++ {
			if _, ok := swings[j]; ok {
				return base{}, RejectExtremeNotSwing
			}
		}
		return base{}, RejectNoSwing
	}
	// Equal extremes: the most significant swing, then the earliest.
	best := candidates[0]
	for _, c := range candidates[1:] {
		if c.Class > best.Class {
			best = c
		}
	}
	b.anchor = best
	return b, ""
}

// assemble builds the zone of a departure from its base.
func assemble(supply bool, bars []market.Candle, atr []float64, leg Leg, d int, b base, cfg ZoneConfig) SwingZone {
	i := b.anchor.BarIndex
	unit := atrAt(atr, d) // widths are judged against the volatility the zone formed in (the departure's bar), the same ATR the base was bounded by
	z := SwingZone{
		Supply: supply, Low: b.lo, High: b.hi, BodyLow: b.bodyLo, BodyHigh: b.bodyHi, Swing: b.anchor, BaseStart: b.start, BaseEnd: b.end, BaseFormedAt: bars[b.end].Time,
		LegStart: d, LegEnd: leg.End, DepartureConfirmedAt: bars[leg.End].Time, ConfirmedIndex: maxInt(leg.End, i+cfg.Major.PivotRightBars),
	}
	z.ConfirmedAt = bars[z.ConfirmedIndex].Time
	if supply {
		z.Proximal, z.Distal, z.AnchorPrice = b.lo, b.hi, float64(b.anchor.Price)
	} else {
		z.Proximal, z.Distal, z.AnchorPrice = b.hi, b.lo, float64(b.anchor.Price)
	}
	z.WidthATR = (b.hi - b.lo) / unit
	z.TooWide = z.WidthATR > cfg.MaximumZoneATR
	z.TooNarrow = z.WidthATR < cfg.MinimumZoneATR
	z.DepartureATR, z.Efficiency = legMeasure(bars, atr, Leg{Start: d, End: leg.End}, supply)
	return z
}

// legMeasure is the leg's net move in ATR of its last bar and its efficiency (net move over the sum of the bars' ranges).
func legMeasure(bars []market.Candle, atr []float64, leg Leg, supply bool) (departureATR, efficiency float64) {
	net := bars[leg.Start].Open - bars[leg.End].Close
	if !supply {
		net = bars[leg.End].Close - bars[leg.Start].Open
	}
	if unit := atrAt(atr, leg.End); unit > 0 {
		departureATR = math.Max(0, net) / unit
	}
	path := 0.0
	for j := leg.Start; j <= leg.End; j++ {
		path += bars[j].High - bars[j].Low
	}
	if path > 0 {
		efficiency = math.Max(0, net) / path
	}
	return departureATR, efficiency
}

// standing reports whether the swing was still a structure when the zone was formed: no close beyond it between the swing and the bar by which the zone was known. Age is NOT a
// part of the formation (adding bars must never downgrade a formed zone); it ends the zone's tradeability (SwingZone.Aged), which is the lifecycle's.
func standing(bars []market.Candle, s MajorSwing, confirmedIndex int, supply bool) bool {
	price := float64(s.Price)
	for j := s.BarIndex + 1; j <= confirmedIndex && j < len(bars); j++ {
		if (supply && bars[j].Close > price) || (!supply && bars[j].Close < price) {
			return false
		}
	}
	return true
}

func strengthOf(s MajorSwing, standing bool, departureATR float64, cfg ZoneConfig) Strength {
	switch {
	case s.Class == SwingMajor && standing && departureATR >= cfg.StrongDepartureATR:
		return StrengthStrong
	case s.Class >= SwingIntermediate && standing:
		return StrengthModerate
	}
	return StrengthWeak
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
