package redis

import (
	"context"
	"encoding/json"
	"sort"
	"strings"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// liveOpportunitiesTTL bounds how long a published live set is trusted, for
// the same reason as zoneBookTTL: comfortably longer than one M5 bar, short
// enough that a stopped engine's set is read as unavailable rather than live.
const liveOpportunitiesTTL = 20 * time.Minute

// LiveOpportunitiesDTO is the set of opportunity IDs the engine currently
// holds as live (CREATED or ACTIVE) for one symbol.
//
// After a restart the engine rebuilds its book by replaying stored history
// under the current rules, so an opportunity that an earlier build created
// and the current rules would not is absent from this set. Algo Bot executes
// only opportunities present here: an old Kafka event that Go no longer
// vouches for cannot open a plan. A missing or expired key means
// "unavailable" and must never be read as "no live opportunities".
type LiveOpportunitiesDTO struct {
	Symbol      string   `json:"symbol"`
	GeneratedAt int64    `json:"generated_at"`
	IDs         []string `json:"ids"`
}

// LiveOpportunitiesKey is the Redis key one symbol's live set is published
// under. Exported so the Python contract test can assert the same literal.
func LiveOpportunitiesKey(symbol market.Symbol) string {
	return "analysis:live_opportunities:" + strings.ToUpper(string(symbol))
}

// BuildLiveOpportunities lists live candidate IDs in deterministic order.
// Pure so it is testable apart from the Redis write; IDs is never nil.
func BuildLiveOpportunities(symbol market.Symbol, live []opportunity.Candidate, generatedAt int64) LiveOpportunitiesDTO {
	ids := make([]string, 0, len(live))
	for _, candidate := range live {
		if candidate.ID != "" {
			ids = append(ids, candidate.ID)
		}
	}
	sort.Strings(ids)
	return LiveOpportunitiesDTO{Symbol: string(symbol), GeneratedAt: generatedAt, IDs: ids}
}

// PublishLiveOpportunities writes symbol's current live set on this Runtime's
// Redis connection. Like the zone book, a failure is the caller's to log and
// must never interrupt candle ingestion.
func (r *Runtime) PublishLiveOpportunities(ctx context.Context, symbol market.Symbol, live []opportunity.Candidate, now time.Time) error {
	doc := BuildLiveOpportunities(symbol, live, now.Unix())
	payload, err := json.Marshal(doc)
	if err != nil {
		return err
	}
	return r.client.Set(ctx, LiveOpportunitiesKey(symbol), payload, liveOpportunitiesTTL).Err()
}
