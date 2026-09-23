package session

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// Session window names — the "which session is active" classification,
// matching the prefix Level.Name values below (ASIA_H strips to ASIA,
// etc.).
const (
	Asia   = "ASIA"
	London = "LONDON"
	NY     = "NY"
)

// Config aggregates every session-domain tunable — the same "one
// coherent struct, not scattered parameters" convention
// structure.Settings/zone.Config already establish. AsiaStartHour/
// LondonStartHour/NYStartHour are UTC hours (0-23) reused as-is from
// analysis.sessions.{asia,london,ny}_start; DailyRolloverUTCHour is the
// one new leaf this phase adds (analysis.sessions.daily_rollover_utc_hour,
// Python's real default of 21 — session_liquidity.py::_daily_rollover).
type Config struct {
	AsiaStartHour        int
	LondonStartHour      int
	NYStartHour          int
	DailyRolloverUTCHour int
}

// window is one session's UTC hour range — session_liquidity.py's
// SessionWindow, unexported since only Update needs it (windows are
// derived from Config, never constructed by a caller).
type window struct {
	name      string
	startHour int
	endHour   int
}

// windows returns the three session windows in the same order Python's
// own _windows(cfg) builds them: Asia -> London -> NY, each window's end
// being the next session's start (a full 24h partition, ny.end wraps
// back to asia.start).
func (c Config) windows() []window {
	return []window{
		{name: Asia, startHour: c.AsiaStartHour, endHour: c.LondonStartHour},
		{name: London, startHour: c.LondonStartHour, endHour: c.NYStartHour},
		{name: NY, startHour: c.NYStartHour, endHour: c.AsiaStartHour},
	}
}

// Level is one named price level — session_liquidity.py's SessionLevel
// dataclass. Name is one of ASIA_H/ASIA_L/LONDON_H/LONDON_L/NY_H/NY_L/
// PDH/PDL/PWH/PWL, the exact literal tag strings Python uses (nothing
// consuming this data should have to guess the naming convention — see
// doc.go).
type Level struct {
	Name    string
	Price   market.Price
	Time    int64 // the extreme candle's own Time
	Swept   bool
	SweptAt *int64 // nil unless Swept
}

// State is the complete session read for one symbol+timeframe as of the
// latest closed bar — the session analogue of structure.StructureState/
// zone.ZoneState. Levels is a flat slice (mirrors zone.ZoneState.Zones);
// Active names which of Asia/London/NY the latest candle falls in.
type State struct {
	Levels []Level
	Active string
}

// Book is the session-domain slice of a symbol's canonical state — same
// shape as structure.Book/zone.Book.
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
