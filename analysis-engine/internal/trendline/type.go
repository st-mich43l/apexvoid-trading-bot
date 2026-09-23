package trendline

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// Kind is which side of price a line acts on — trendline_v2.py's own
// "support"/"resistance" line-kind strings (not to be confused with a
// Swing's own "high"/"low" pivot kind that anchors it).
type Kind uint8

const (
	KindSupport Kind = iota
	KindResistance
)

func (k Kind) String() string {
	if k == KindResistance {
		return "resistance"
	}
	return "support"
}

// Config aggregates every trendline tunable — same "one coherent
// struct" convention every other S4 domain's Config establishes.
// Twelve leaves already exist in config/analysis.yml's
// analysis.trendlines.* (this phase adds no new meaning to them); six
// more (MinimumSlopeATR through DedupSlopePercent) existed only as
// Python getattr fallback-default names, never real YAML keys — this
// phase adds them as real leaves at those same real fallback values,
// closing the gap rather than inventing new numbers (see
// config/analysis.yml's own comments).
type Config struct {
	MinimumSlopeATR                        float64
	MaximumSlopeATR                        float64
	MinimumTouchSpacingBars                int
	MinimumSpanBars                        int
	MinimumValidationTouchSpacingBars      int
	ValidationTouchToleranceATR            float64
	InvalidationPenetrationATR             float64
	CloseViolationATR                      float64
	ApproachMinDistanceATR                 float64
	MinimumValidationFavorableExcursionATR float64
	ValidationReactionBars                 int
	MinimumValidationTouches               int
	ExhaustionValidationTouches            int
	MaximumWickViolations                  int
	DedupValueATR                          float64
	DedupSlopePercent                      float64

	// InteractionBandATR is EvaluateInteraction's own leaf
	// (interaction_band_atr) — not used by Build.
	InteractionBandATR float64
}

// ValidationTouch is one later pivot that validated an anchor pair's
// projection — trendline_v2.py's TrendlineValidationTouch, with Time (a
// candle's own Unix-second Time) replacing Python's string timestamp,
// matching every other domain package's convention in this codebase.
type ValidationTouch struct {
	BarIndex           int
	Time               int64
	ProjectedLinePrice market.Price
	ActualExtreme      market.Price

	TouchErrorPrice float64
	TouchErrorATR   float64

	PenetrationPrice float64
	PenetrationATR   float64

	CloseDistanceFromLine float64
	CloseDistanceATR      float64

	FavorableExcursionPrice float64
	FavorableExcursionATR   float64
	AdverseExcursionPrice   float64
	AdverseExcursionATR     float64

	ReactionBars int
	Reclaimed    bool

	RejectionStrength float64

	StructureConfirmed     bool
	ApproachDirectionValid bool
}

// Trendline is one causal V2 line — trendline_v2.py's own Trendline
// dataclass, narrowed to the fields this Go port actually needs (the
// Python dataclass carries several V1-legacy fields — point_idx,
// touches, fit_error_atr, violations, bars_since_last_touch — kept there
// only for V1-payload backward compatibility, which does not apply
// here). AnchorA/AnchorB are structure.Swing.ID references — the same
// opaque-string-reference pattern internal/zone already established for
// StructureRef/DisplacementRef, never a direct structure.Swing embed.
type Trendline struct {
	Kind Kind

	AnchorA      string
	AnchorB      string
	AnchorAIndex int // candle index (within the window Build was given) of AnchorA
	AnchorBIndex int

	Slope          float64
	Intercept      float64
	SlopeATRPerBar float64

	State State

	ValidationTouches []ValidationTouch

	WickViolations  int
	CloseViolations int

	// BrokenAt is the candle Time of the line's unresolved close
	// violation (structure.py's own "an unresolved close defines
	// broken" rule — see lifecycle.go), nil if the line has never
	// broken.
	BrokenAt           *int64
	ViolationReclaimed bool

	SpanBars  int
	Exhausted bool
}

// ValueAt returns the line's price at a given candle index — only
// meaningful against the SAME candles window (same index-0 origin) the
// line was built from (see doc.go).
func ValueAt(tl Trendline, index int) market.Price {
	return market.Price(tl.Slope*float64(index) + tl.Intercept)
}

// TrendlineState is one timeframe's complete trendline read — named
// (rather than plain "State", like zone.ZoneState/session.State) because
// this package's own State type already names the per-line lifecycle
// enum below.
type TrendlineState struct {
	Lines []Trendline
}

// Book is the trendline-domain slice of a symbol's canonical state —
// same shape as zone.Book/fib.Book/keylevel.Book.
type Book struct {
	ByTimeframe map[market.Timeframe]TrendlineState
}

// NewBook returns an empty Book ready for Set/Get.
func NewBook() *Book {
	return &Book{ByTimeframe: make(map[market.Timeframe]TrendlineState)}
}

// Set stores tf's latest TrendlineState, replacing whatever was there.
func (b *Book) Set(tf market.Timeframe, state TrendlineState) {
	b.ByTimeframe[tf] = state
}

// Get returns tf's latest TrendlineState, or the zero value and false if
// none has been computed yet.
func (b *Book) Get(tf market.Timeframe) (TrendlineState, bool) {
	state, ok := b.ByTimeframe[tf]
	return state, ok
}
