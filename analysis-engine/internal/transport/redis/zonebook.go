package redis

import (
	"context"
	"encoding/json"
	"strings"
	"time"

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
	Strength   float64 `json:"strength"`
	TouchCount int     `json:"touch_count"`
	State      string  `json:"state"` // zone.State.String()
}

// ZoneBookDTO is the full published document for one symbol.
type ZoneBookDTO struct {
	Symbol      string          `json:"symbol"`
	GeneratedAt int64           `json:"generated_at"`
	Entries     []ZoneBookEntry `json:"entries"`
}

// ZoneBookKey is the Redis key one symbol's zone book is published under.
// Exported so the .NET/Python contract tests can assert against the exact
// same literal instead of duplicating the naming rule.
func ZoneBookKey(symbol market.Symbol) string {
	return "analysis:zone_book:" + strings.ToUpper(string(symbol))
}

// BuildZoneBook flattens every tracked timeframe's zone.ZoneState into one
// published document. Pure and side-effect-free so it is independently
// testable from the Redis write itself.
func BuildZoneBook(symbol market.Symbol, zonesByTimeframe map[market.Timeframe]zone.ZoneState, generatedAt int64) ZoneBookDTO {
	var entries []ZoneBookEntry
	for tf, state := range zonesByTimeframe {
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
				Strength: z.Strength, TouchCount: z.TouchCount, State: z.State.String(),
			})
		}
	}
	return ZoneBookDTO{Symbol: string(symbol), GeneratedAt: generatedAt, Entries: entries}
}

// PublishZoneBook writes symbol's current zone book, reusing this Runtime's
// own Redis connection (no second connection pool). Errors are the
// caller's to handle — see the call site in cmd/analysis-engine/main.go for
// why a publish failure must never interrupt candle ingestion, the same
// "Kafka outage is never a candle-ingestion outage" principle this package
// already applies to the Kafka producer.
func (r *Runtime) PublishZoneBook(ctx context.Context, symbol market.Symbol, zonesByTimeframe map[market.Timeframe]zone.ZoneState, now time.Time) error {
	doc := BuildZoneBook(symbol, zonesByTimeframe, now.Unix())
	payload, err := json.Marshal(doc)
	if err != nil {
		return err
	}
	return r.client.Set(ctx, ZoneBookKey(symbol), payload, zoneBookTTL).Err()
}
