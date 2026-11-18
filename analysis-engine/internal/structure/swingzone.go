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

	// BodyLow and BodyHigh are the extremes of the base candles' BODIES (open and close), wicks excluded: the proximal body edge is an internal level of the base.
	BodyLow, BodyHigh float64
	DepartureATR      float64 // the leg's net move in ATR of its last bar
	Efficiency        float64 // net move over the sum of the leg's bar ranges (1 = every bar travelled the same way)
	WidthATR          float64
	Strength          Strength
	TooWide           bool // wider than a zone: a region
	TooNarrow         bool // an incidental candle region
}

// FormSwingZones returns the zones anchored on the swings of bars. legs are the departure legs of the direction the zone needs (down for supply, up for demand).
// The result is in swing order. bars/atr are the closed bars of the frame and its ATR series.
func FormSwingZones(supply bool, bars []market.Candle, atr []float64, legs []Leg, cfg ZoneConfig) []SwingZone {
	if cfg.Valid() != nil || len(bars) == 0 {
		return nil
	}
	kind := PivotHigh
	if !supply {
		kind = PivotLow
	}
	var out []SwingZone
	used := map[int]bool{}
	for _, swing := range ClassifyMajor(bars, atr, cfg.Major) {
		if swing.Kind != kind {
			continue
		}
		i := swing.BarIndex
		leg, departure, ok := departureFrom(bars, atr, legs, i, cfg, used)
		if !ok {
			continue
		}
		used[leg.Start] = true
		unit := atrAt(atr, i)
		start, end := baseBounds(bars, atr, i, departure, cfg)
		low, high := bars[start].Low, bars[start].High
		bodyLow, bodyHigh := math.Min(bars[start].Open, bars[start].Close), math.Max(bars[start].Open, bars[start].Close)
		for j := start; j <= end; j++ {
			low, high = math.Min(low, bars[j].Low), math.Max(high, bars[j].High)
			bodyLow, bodyHigh = math.Min(bodyLow, math.Min(bars[j].Open, bars[j].Close)), math.Max(bodyHigh, math.Max(bars[j].Open, bars[j].Close))
		}
		z := SwingZone{
			Supply: supply, Low: low, High: high, BodyLow: bodyLow, BodyHigh: bodyHigh, Swing: swing, BaseStart: start, BaseEnd: end, BaseFormedAt: bars[end].Time,
			LegStart: departure, LegEnd: leg.End, DepartureConfirmedAt: bars[leg.End].Time,
			ConfirmedIndex: maxInt(leg.End, i+cfg.Major.PivotRightBars),
		}
		if supply {
			z.Proximal, z.Distal, z.AnchorPrice = low, high, high
		} else {
			z.Proximal, z.Distal, z.AnchorPrice = high, low, low
		}
		z.WidthATR = (high - low) / unit
		z.TooWide = z.WidthATR > cfg.MaximumZoneATR
		z.TooNarrow = z.WidthATR < cfg.MinimumZoneATR
		z.DepartureATR, z.Efficiency = legMeasure(bars, atr, Leg{Start: departure, End: leg.End}, supply)
		z.Strength = strengthOf(swing, standing(bars, swing, z.ConfirmedIndex, supply, cfg.Major), z.DepartureATR, cfg)
		out = append(out, z)
	}
	return out
}

// departureFrom finds the leg that left the swing and the bar where the departure began. A leg that begins on the swing candle or within DepartureGapBars after it
// is the departure from its first candle. A leg that began BEFORE the swing candle is a run of same-direction candles that contains the consolidation (the base
// candles that happen to be the leg's colour): the departure begins at the first impulse candle after the swing (its range above BaseMaxCandleATR x ATR), and a run
// that began more than a base ago, or never produces an impulse candle after the swing, did not leave it.
func departureFrom(bars []market.Candle, atr []float64, legs []Leg, swing int, cfg ZoneConfig, used map[int]bool) (Leg, int, bool) {
	for _, l := range legs {
		if used[l.Start] || l.End < swing || l.Start > swing+cfg.DepartureGapBars || l.Start < swing-cfg.BaseMaxCandles {
			continue
		}
		d := l.Start
		if l.Start < swing {
			d = -1
			for j := swing + 1; j <= l.End; j++ {
				if bars[j].High-bars[j].Low > cfg.BaseMaxCandleATR*atrAt(atr, j) {
					d = j
					break
				}
			}
			if d < 0 {
				continue
			}
		}
		if d-swing > cfg.DepartureGapBars {
			continue
		}
		return l, d, true
	}
	return Leg{}, 0, false
}

// baseBounds finds the base around the swing candle and before the leg. The swing candle is always in it; earlier candles join while they are not impulses, overlap the
// base so far and the base has room; the base ends at the candle before the leg (or at the swing candle when the leg starts on it).
func baseBounds(bars []market.Candle, atr []float64, swing, legStart int, cfg ZoneConfig) (start, end int) {
	end = maxInt(swing, legStart-1)
	start = swing
	low, high := bars[swing].Low, bars[swing].High
	for j := swing + 1; j <= end; j++ {
		low, high = math.Min(low, bars[j].Low), math.Max(high, bars[j].High)
	}
	for j := swing - 1; j >= 0 && end-j+1 <= cfg.BaseMaxCandles; j-- {
		c := bars[j]
		unit := atrAt(atr, j)
		if !(unit > 0) || c.High-c.Low > cfg.BaseMaxCandleATR*unit {
			break // an impulse candle is the move into the base, not part of it
		}
		if c.Low > high || c.High < low {
			break // no overlap: the candle belongs to a different price area
		}
		start = j
		low, high = math.Min(low, c.Low), math.Max(high, c.High)
	}
	return start, end
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

// standing reports whether the swing was still a structure when the zone was formed and is not too old: no close beyond it between the swing and the bar by which the
// zone was known, and not older than RelevanceBars. A later close beyond the far edge is the lifecycle's business (the strategy's breach verdict forgives a reclaimed
// sweep and retires an accepted break), never a downgrade of the structure that was formed.
func standing(bars []market.Candle, s MajorSwing, confirmedIndex int, supply bool, cfg MajorConfig) bool {
	if len(bars)-1-s.BarIndex > cfg.RelevanceBars {
		return false
	}
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
