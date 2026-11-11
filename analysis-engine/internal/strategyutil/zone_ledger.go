package strategyutil

import (
	"sync"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// ZoneLedger records, during one Evaluate, which zones a zone-family strategy still holds and which it judged finished, and hands the verdict to the
// registry (strategy.ZoneReporter). A strategy marks every zone of every frame it reads: Live when the zone is still a zone (even if it is ineligible
// right now), Terminal(reason) when it is spent or invalidated. A zone it marks nowhere is a zone it no longer holds. The ledger is safe for the
// strategy's own concurrent use.
type ZoneLedger struct {
	mu       sync.Mutex
	verdict  opportunity.ZoneLiveness
	reported bool
}

// Begin starts a fresh verdict for the evaluation that is about to read the frames.
func (l *ZoneLedger) Begin() *ZoneVerdict {
	return &ZoneVerdict{ledger: l, v: opportunity.ZoneLiveness{
		Frames: map[market.Timeframe]struct{}{}, Live: map[string]struct{}{}, Terminal: map[string]opportunity.ReasonCode{},
	}}
}

// Take returns the last published verdict once and forgets it.
func (l *ZoneLedger) Take() (opportunity.ZoneLiveness, bool) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if !l.reported {
		return opportunity.ZoneLiveness{}, false
	}
	l.reported = false
	out := l.verdict
	l.verdict = opportunity.ZoneLiveness{}
	return out, true
}

// ZoneVerdict is one evaluation's verdict under construction (single goroutine).
type ZoneVerdict struct {
	ledger *ZoneLedger
	v      opportunity.ZoneLiveness
}

// Frame marks a structure frame as read: only a frame the strategy really evaluated can retire a zone.
func (z *ZoneVerdict) Frame(timeframe market.Timeframe) {
	if z != nil {
		z.v.Frames[timeframe] = struct{}{}
	}
}

// Live marks a zone that is still a zone.
func (z *ZoneVerdict) Live(id string) {
	if z != nil && id != "" {
		z.v.Live[id] = struct{}{}
	}
}

// Terminal marks a zone the strategy judged finished. A later Live for the same ID is ignored: terminal wins.
func (z *ZoneVerdict) Terminal(id string, reason opportunity.ReasonCode) {
	if z != nil && id != "" {
		z.v.Terminal[id] = reason
	}
}

// Publish makes the verdict available to the registry. A verdict with no frame is not published.
func (z *ZoneVerdict) Publish() {
	if z == nil || len(z.v.Frames) == 0 {
		return
	}
	z.ledger.mu.Lock()
	defer z.ledger.mu.Unlock()
	z.ledger.verdict, z.ledger.reported = z.v, true
}
