package keylevel

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// Kind distinguishes a price-cluster reaction level from a round-number
// level — levels.py's Level.kind literal strings ("reaction"/"round").
type Kind uint8

const (
	KindReaction Kind = iota
	KindRound
)

func (k Kind) String() string {
	if k == KindRound {
		return "round"
	}
	return "reaction"
}

// Level mirrors levels.py's Level dataclass exactly — no support/
// resistance field here; that is Role's job (role.go), kept deliberately
// separate per key_level_role.py's own module boundary.
type Level struct {
	Price    market.Price
	Kind     Kind
	Touches  int
	Band     float64
	Strength float64
}

// Config aggregates every key-level tunable — same "one coherent
// struct" convention every other S4 domain's Config already establishes.
// No config/analysis.yml precedent existed before this phase — all four
// leaves are key_levels()'s own real Python function-default values.
type Config struct {
	// ClusterATR is level_cluster_atr (default 0.5): the ATR multiple
	// defining how close two swings must be to join the same cluster.
	ClusterATR float64

	// RoundStep is round_step (default 5.0): the round-number price
	// increment scanned for round-level touches.
	RoundStep float64

	// MinimumTouches is min_touches (default 2): the minimum swing (or,
	// after wick-touch re-enrichment, episode) count for a level to be
	// reported at all.
	MinimumTouches int

	// MaximumClusterSpanMultiple is max_cluster_span_multiple (default
	// 2.0): a cluster's total price span is capped at
	// tolerance*this, even if every pairwise gap individually clears
	// ClusterATR's own tolerance — prevents a long chain of
	// just-barely-adjacent swings from silently growing one cluster
	// into an unbounded price range.
	MaximumClusterSpanMultiple float64
}

// State is one timeframe's complete key-level read.
type State struct {
	Levels []Level
}

// Book is the key-level-domain slice of a symbol's canonical state —
// same shape as zone.Book/fib.Book.
type Book struct {
	ByTimeframe map[market.Timeframe]State
}

// NewBook returns an empty Book ready for Set/Get.
func NewBook() *Book {
	return &Book{ByTimeframe: make(map[market.Timeframe]State)}
}

// Set stores tf's latest State, replacing whatever was there.
func (b *Book) Set(tf market.Timeframe, state State) {
	b.ByTimeframe[tf] = state
}

// Get returns tf's latest State, or the zero value and false if none has
// been computed yet.
func (b *Book) Get(tf market.Timeframe) (State, bool) {
	state, ok := b.ByTimeframe[tf]
	return state, ok
}
