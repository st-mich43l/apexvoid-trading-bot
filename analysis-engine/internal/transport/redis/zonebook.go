package redis

import (
	"context"
	"encoding/json"
	"sort"
	"strings"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/barrier"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// zoneBookTTL bounds how long a published zone book is trusted. Comfortably
// longer than one M5 bar so a single missed publish cycle is never visible,
// short enough that a stopped engine's zones eventually disappear rather
// than being read as live forever. Algo Bot's own reader treats a missing
// or expired key as "unavailable," never as "no opposing structure exists"
// (a positive claim this package must not make on the engine's behalf) —
// see algo-bot/app/autotrade/go_zone_book.py's own doc comment.
const zoneBookTTL = 20 * time.Minute

// ZoneBookEntry is one zone, from one tracked timeframe, in the published
// book — the minimal shape Algo Bot's worker needs for its execution-time
// opposing-barrier/target-room recheck (side, price band, and enough of the
// zone's own lifecycle/strength to apply the same "still a live barrier"
// filter analysis-engine's own strategies already use — see
// internal/strategy/keylevel/opposing.go's identical filter).
type ZoneBookEntry struct {
	Timeframe  string  `json:"timeframe"`
	Kind       string  `json:"kind"` // "supply" | "demand"
	Low        float64 `json:"low"`
	High       float64 `json:"high"`
	ATR        float64 `json:"atr"`
	Strength   float64 `json:"strength"`
	TouchCount int     `json:"touch_count"`
	State      string  `json:"state"` // zone.State.String()
}

// ZoneBookDTO is the full published document for one symbol.
//
// Barriers is the normalized opposing-structure book (execution-width gate,
// M5/M15/H1 scope, same-side merge, cross-side reconciliation) built by
// internal/barrier from Entries. It is additive: a JSON null (or an absent
// key from an older publisher) means "not computed", which a consumer must
// treat differently from an empty array ("computed: no opposing structure").
type ZoneBookDTO struct {
	Symbol      string            `json:"symbol"`
	GeneratedAt int64             `json:"generated_at"`
	Entries     []ZoneBookEntry   `json:"entries"`
	Barriers    []barrier.Barrier `json:"barriers"`
}

// ZoneBookKey is the Redis key one symbol's zone book is published under.
// Exported so the .NET/Python contract tests can assert against the exact
// same literal instead of duplicating the naming rule.
func ZoneBookKey(symbol market.Symbol) string {
	return "analysis:zone_book:" + strings.ToUpper(string(symbol))
}

// liveZoneStates are the lifecycle states that still count as a standing
// barrier - the same "not Invalidated/Mitigated" rule keylevel/opposing.go
// applies on the Go side and go_zone_book.py applied on the Python side.
func liveZoneState(s zone.State) bool {
	return s == zone.StateFresh || s == zone.StateTouched || s == zone.StatePartiallyMitigated
}

// BuildZoneBook flattens every tracked timeframe's zone.ZoneState into one
// published document. Pure and side-effect-free so it is independently
// testable from the Redis write itself. A nil barrierCfg leaves Barriers nil
// (not computed); otherwise Barriers is always a non-nil slice.
func BuildZoneBook(symbol market.Symbol, zonesByTimeframe map[market.Timeframe]zone.ZoneState, generatedAt int64, barrierCfg *barrier.Config) ZoneBookDTO {
	timeframes := make([]market.Timeframe, 0, len(zonesByTimeframe))
	for tf := range zonesByTimeframe {
		timeframes = append(timeframes, tf)
	}
	sort.Slice(timeframes, func(i, j int) bool { return timeframes[i] < timeframes[j] })
	var entries []ZoneBookEntry
	var barrierZones []barrier.Zone
	for _, tf := range timeframes {
		state := zonesByTimeframe[tf]
		for _, z := range state.Zones {
			var kind string
			switch z.Kind {
			case zone.KindSupply:
				kind = "supply"
			case zone.KindDemand:
				kind = "demand"
			default:
				continue
			}
			entries = append(entries, ZoneBookEntry{
				Timeframe: string(tf), Kind: kind, Low: float64(z.Low), High: float64(z.High),
				ATR: state.ATR, Strength: z.Strength, TouchCount: z.TouchCount, State: z.State.String(),
			})
			side := barrier.Buy
			if z.Kind == zone.KindSupply {
				side = barrier.Sell
			}
			barrierZones = append(barrierZones, barrier.Zone{
				Timeframe: string(tf), Side: side, Low: float64(z.Low), High: float64(z.High),
				ATR: state.ATR, Score: z.Strength, Touches: z.TouchCount, Live: liveZoneState(z.State),
			})
		}
	}
	doc := ZoneBookDTO{Symbol: string(symbol), GeneratedAt: generatedAt, Entries: entries}
	if barrierCfg != nil {
		doc.Barriers = barrier.Build(barrierZones, *barrierCfg)
		if doc.Barriers == nil {
			doc.Barriers = []barrier.Barrier{}
		}
	}
	return doc
}

// PublishZoneBook writes symbol's current zone book, reusing this Runtime's
// own Redis connection (no second connection pool). Errors are the
// caller's to handle — see the call site in cmd/analysis-engine/main.go for
// why a publish failure must never interrupt candle ingestion, the same
// "Kafka outage is never a candle-ingestion outage" principle this package
// already applies to the Kafka producer.
func (r *Runtime) PublishZoneBook(ctx context.Context, symbol market.Symbol, zonesByTimeframe map[market.Timeframe]zone.ZoneState, now time.Time) error {
	doc := BuildZoneBook(symbol, zonesByTimeframe, now.Unix(), r.barrierConfig(symbol))
	payload, err := json.Marshal(doc)
	if err != nil {
		return err
	}
	return r.client.Set(ctx, ZoneBookKey(symbol), payload, zoneBookTTL).Err()
}

// SetBarrierConfig sets the opposing-barrier normalization policy for symbol.
// Symbols without a config publish a zone book with Barriers == null, which
// consumers must read as "not computed" (see ZoneBookDTO).
func (r *Runtime) SetBarrierConfig(symbol market.Symbol, cfg barrier.Config) {
	r.barrierMu.Lock()
	defer r.barrierMu.Unlock()
	if r.barrierCfgs == nil {
		r.barrierCfgs = make(map[market.Symbol]barrier.Config)
	}
	r.barrierCfgs[market.Symbol(strings.ToUpper(string(symbol)))] = cfg
}

func (r *Runtime) barrierConfig(symbol market.Symbol) *barrier.Config {
	r.barrierMu.RLock()
	defer r.barrierMu.RUnlock()
	cfg, ok := r.barrierCfgs[market.Symbol(strings.ToUpper(string(symbol)))]
	if !ok {
		return nil
	}
	return &cfg
}
