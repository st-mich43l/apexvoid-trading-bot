package zone

// RelevanceConfig mirrors config/analysis.yml's
// analysis.zone_relevance.{immediate_atr,nearby_atr,remote_atr} leaves —
// ported from algo-bot/app/autotrade/zone_relevance.py's
// AnalysisZoneRelevanceConfig defaults (0.25/1.25/3.0), which exist today
// only as a Python schema default, not yet a YAML leaf; this is the
// first place these values become config-driven. Callers must enforce
// ImmediateATR < NearbyATR < RemoteATR (Python's own validated
// invariant) — this package does not re-validate it, matching every
// other *Config type in this domain (config/scripts/config_check.py is
// the enforcement point, not runtime code).
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

// IsDead ports zone_relevance.py::is_dead_zone — true once a zone is
// more than 2x RemoteATR away (a hysteresis-doubled band so oscillation
// near the Remote boundary doesn't churn a zone in and out of
// existence). A zero/negative atr means "never dead" — no ATR unit, no
// meaningful distance, matching ClassifyRelevance's own fallback
// reasoning.
func IsDead(z Zone, mid, atr float64, cfg RelevanceConfig) bool {
	if atr <= 0 {
		return false
	}
	return distanceToZone(z, mid)/atr > 2*cfg.RemoteATR
}
