package zone

// RelevanceConfig mirrors the zone-relevance leaves in config/analysis.yml.
// Callers must enforce ImmediateATR < NearbyATR < RemoteATR; this package
// keeps the value object deliberately free of policy side effects.
type RelevanceConfig struct {
	ImmediateATR float64
	NearbyATR    float64
	RemoteATR    float64
}

// Relevance is structural validity's orthogonal axis (spec §17): a zone
// may be a fully valid, un-invalidated Zone (State) yet too remote from
// current price to matter right now — Relevance answers that second
// question. Never persisted; recomputed fresh from the current price
// every call, so a Dormant zone can freely become Immediate again the
// instant price returns (algo-bot/app/autotrade/zone_relevance.py's own
// documented design: this is pure/stateless by construction).
type Relevance uint8

const (
	Immediate Relevance = iota
	Nearby
	Remote
	Dormant
)

func (r Relevance) String() string {
	switch r {
	case Immediate:
		return "immediate"
	case Nearby:
		return "nearby"
	case Remote:
		return "remote"
	case Dormant:
		return "dormant"
	default:
		return "unknown"
	}
}

// distanceToZone is 0 when mid is inside [low,high], otherwise the gap
// to the nearer edge.
func distanceToZone(z Zone, mid float64) float64 {
	low, high := float64(z.Low), float64(z.High)
	switch {
	case mid < low:
		return low - mid
	case mid > high:
		return mid - high
	default:
		return 0
	}
}

// ClassifyRelevance ports zone_relevance.py::classify_zone_relevance. atr
// <= 0 falls back to the coarse Python behavior: Immediate if price is
// inside the zone, Dormant otherwise (no ATR unit means no meaningful
// banding).
func ClassifyRelevance(z Zone, mid, atr float64, cfg RelevanceConfig) Relevance {
	distance := distanceToZone(z, mid)
	if atr <= 0 {
		if distance <= 0 {
			return Immediate
		}
		return Dormant
	}
	distanceATR := distance / atr
	switch {
	case distanceATR <= cfg.ImmediateATR:
		return Immediate
	case distanceATR <= cfg.NearbyATR:
		return Nearby
	case distanceATR <= cfg.RemoteATR:
		return Remote
	default:
		return Dormant
	}
}
