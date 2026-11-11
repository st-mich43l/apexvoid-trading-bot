package opportunity

import (
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// ZoneLiveness is one strategy's verdict, after one evaluation, on the zones of the frames it read. It is how a zone's own lifecycle governs the lifecycle of
// the opportunities that rest on it: a published opportunity must never outlive the zone that produced it.
//
//	Frames    the structure frames the strategy actually read (a frame with no data is absent, so a missing frame never retires anything).
//	Live      the zones that are still zones on those frames: not spent, not invalidated. A zone that is merely ineligible right now (too wide for the entry
//	          rule, price far from it, a weak body) is still live: remoteness and ineligibility are not structural invalidation.
//	Terminal  the zones the strategy explicitly judged finished, with the reason (ReasonZoneMitigated, ReasonZoneInvalidated, ...).
//
// A zone on a read frame that is neither Live nor Terminal is one the strategy no longer holds at all (it left the frame's structure): expired.
type ZoneLiveness struct {
	Frames   map[market.Timeframe]struct{}
	Live     map[string]struct{}
	Terminal map[string]ReasonCode
}

// Verdict says whether a zone on timeframe is finished, and why. ok is false when the zone stands or the strategy did not read its frame this evaluation.
func (z ZoneLiveness) Verdict(zoneID string, timeframe market.Timeframe) (reason ReasonCode, ok bool) {
	if zoneID == "" {
		return "", false
	}
	if _, read := z.Frames[timeframe]; !read {
		return "", false
	}
	if reason, terminal := z.Terminal[zoneID]; terminal {
		return reason, true
	}
	if _, live := z.Live[zoneID]; live {
		return "", false
	}
	return ReasonZoneExpired, true
}

// RetireByZones retires (invalidates, or for a zone that has run out expires) every live opportunity of strategy whose zone the strategy has just judged terminal (or no longer holds). It is idempotent: a
// retired opportunity is a Noop on every later call, so a duplicate verdict (or a replayed bar) publishes nothing twice. A zone that is live, an opportunity
// that rests on no zone, an opportunity of another strategy and an opportunity on a frame the strategy did not read are never touched. Results are in
// deterministic ID order. Fills are not this method's business: it only ends the opportunity, and positions the plan already opened are managed on.
func (b *Book) RetireByZones(strategy StrategyID, liveness ZoneLiveness, at int64) []Transition {
	if b == nil || at < 0 || len(liveness.Frames) == 0 {
		return nil
	}
	ids := make([]string, 0, len(b.records))
	for id := range b.records {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	var transitions []Transition
	for _, id := range ids {
		record := b.records[id]
		if record.State != StateCreated && record.State != StateActive {
			continue
		}
		candidate := record.Candidate
		if candidate.Strategy != strategy || candidate.ZoneID == "" {
			continue
		}
		frame := candidate.StructureTimeframe
		if frame == "" {
			frame = candidate.ObservedTimeframe
		}
		reason, finished := liveness.Verdict(candidate.ZoneID, frame)
		if !finished {
			continue
		}
		if reason == ReasonZoneExpired {
			// A zone the strategy no longer holds has run out: the opportunity EXPIRES (the same published terminal event, ending in the Expired state), it was not
			// broken by price.
			if at < record.LastObservedAt {
				continue
			}
			record.State = StateExpired
			record.Terminal = &Terminal{Reason: reason, At: at}
			b.records[id] = record
			transitions = append(transitions, transition(TransitionExpired, record))
			continue
		}
		invalidated, err := b.Invalidate(id, reason, at)
		if err != nil || invalidated.Kind != TransitionInvalidated {
			continue
		}
		transitions = append(transitions, invalidated)
	}
	return transitions
}
