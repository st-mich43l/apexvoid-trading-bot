package zone

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// Kind names which technique produced a Zone — algo-bot's zones.py
// "source" tag (supply_demand/order_block/bullish_fvg/bearish_fvg/
// breaker/flip_zone), split into the seven independent kinds this
// package's Update dispatches to. Kind is orthogonal to Side: an
// OrderBlock/FVG/iFVG/Breaker/Flip zone can be either Side, but a
// Supply/Demand zone's Kind and Side are always paired (KindSupply <->
// Supply, KindDemand <-> Demand).
type Kind uint8

const (
	KindSupply Kind = iota
	KindDemand
	KindOrderBlock
	KindFVG
	KindIFVG
	KindBreaker
	KindFlip
)

func (k Kind) String() string {
	switch k {
	case KindSupply:
		return "supply"
	case KindDemand:
		return "demand"
	case KindOrderBlock:
		return "order_block"
	case KindFVG:
		return "fvg"
	case KindIFVG:
		return "ifvg"
	case KindBreaker:
		return "breaker"
	case KindFlip:
		return "flip"
	default:
		return "unknown"
	}
}

// Side is which side of price a zone represents — Demand (buyers expected
// to step in; a bullish reaction zone) or Supply (sellers expected to
// step in; a bearish reaction zone). A dedicated type rather than reusing
// market.Direction, the same choice liquidity.LiquiditySide already made
// for the same reason (see liquidity/pool.go's own doc comment): "demand
// zone" describes the zone's own identity, not a trade action, even
// though the two align.
type Side uint8

const (
	Demand Side = iota
	Supply
)

func (s Side) String() string {
	if s == Demand {
		return "demand"
	}
	return "supply"
}

// Direction is the trade direction a reaction from this zone implies —
// Demand -> Buy, Supply -> Sell.
func (s Side) Direction() market.Direction {
	if s == Demand {
		return market.Buy
	}
	return market.Sell
}

// Zone is one canonical technical zone — always a single, per-origin
// instance (PR #578: never a merged/composite of multiple overlapping
// zones; see doc.go). StructureRef/DisplacementRef are opaque
// cross-reference IDs into structure.Swing/structure.StructureBreak/the
// structure.Displacement run that produced this zone — not a direct Go
// type embedding, keeping Zone a flat value type independent of how
// richly those structural facts are represented (mirrors
// apexvoid-bot-prompts/rebuild-strategies.md §15's own Zone snippet,
// which specifies these as plain strings). Layer is cheap enough to hold
// directly, the same choice liquidity.Pool already makes for the same
// field.
type Zone struct {
	ID string

	Kind Kind
	Side Side

	Low  market.Price
	High market.Price

	Layer     structure.StructureLayer
	Timeframe market.Timeframe

	OriginTime int64

	StructureRef    string
	DisplacementRef string

	CreatedAt     int64
	LastTouchedAt *int64

	TouchCount int

	// BreakIndex is the candle index lifecycle scanning (mitigation,
	// touches, invalidation episodes) starts from — algo-bot's zones.py
	// documented fix: scanning must start at the confirming
	// displacement/break's OWN end, never the zone's origin+1, or the
	// impulse leg's first bar (which wicks back into its own base by
	// construction) falsely self-mitigates the zone on its formation bar.
	BreakIndex int

	Strength float64

	// State is the structural lifecycle at the latest candle supplied to
	// the zone update. Relevance is deliberately separate: a valid zone
	// can be remote without being invalidated.
	State     State
	Relevance Relevance
}
