package mad

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Relation statuses: how the AMD evidence stands to one particular setup.
const (
	RelationAligned     = "aligned"     // evidence that belongs to this setup supports its direction
	RelationConflicting = "conflicting" // evidence that belongs to this setup opposes its direction, or has failed
	RelationPartial     = "partial"     // some relevant evidence, not enough to support or oppose the setup
	RelationIrrelevant  = "irrelevant"  // AMD is not part of this strategy's technical case
	RelationUnavailable = "unavailable" // no current evidence could be read
)

// Setup is the part of a candidate the relation reads. ObservedAt is the open time
// of the bar that made the setup actionable and ObservedTimeframe its timeframe;
// FormedAt is when the setup's own primitive formed (0 when unknown).
type Setup struct {
	Strategy          string
	Direction         string
	FormedAt          int64
	ObservedAt        int64
	ObservedTimeframe market.Timeframe
}

// Relation is descriptive: it never says a setup may or may not trade. Reasons are
// stable machine-readable codes; EvidenceIDs name the range, the sequence and the
// stages of it that were read.
type Relation struct {
	Status      string
	Reasons     []string
	EvidenceIDs []string
}

type family int

const (
	familyNone family = iota
	familyManipulation
	familyContinuation
)

// relevance is explicit, never a substring guess. CRT, iFVG, Key Level, Range Scalp
// (the fade) and Trendline have a technical case that a sweep/reclaim or an
// expansion of the Asia range bears on. The impulse strategies are continuation
// scalps with no such case, asia_range is built on the same range (counting it
// again would double count), and the zone strategies are left for a later change.
func relevance(strategy string) (family, string) {
	switch normalize(strategy) {
	case "crt", "ifvg", "key_level", "range_scalp":
		return familyManipulation, ""
	case "trendline":
		return familyContinuation, ""
	case "asia_range":
		return familyNone, "strategy_owns_asia_range"
	case "impulse_pullback", "impulse_retrace":
		return familyNone, "continuation_only_strategy"
	default:
		return familyNone, "strategy_not_amd_relevant"
	}
}

// Relate classifies the AMD evidence of snapshot against one setup. It reads only
// facts the snapshot already holds and never calls back into price: absent or
// stale evidence is "unavailable", an irrelevant strategy is "irrelevant", and
// neither carries a score.
func Relate(s Snapshot, setup Setup, cfg Config) Relation {
	fam, why := relevance(setup.Strategy)
	if fam == familyNone {
		return Relation{Status: RelationIrrelevant, Reasons: []string{why}}
	}
	want := normalize(setup.Direction)
	if want != "buy" && want != "sell" {
		return Relation{Status: RelationUnavailable, Reasons: []string{"setup_direction_invalid"}}
	}
	if !s.Active && s.Sequence == nil {
		reason := s.ReasonCode
		if reason == "" {
			reason = "asia_range_missing"
		}
		return Relation{Status: RelationUnavailable, Reasons: []string{reason}}
	}
	out := Relation{}
	if s.Asia != nil {
		out.EvidenceIDs = append(out.EvidenceIDs, s.Asia.ID)
	}
	seq := s.Sequence
	if seq == nil {
		out.Status = RelationPartial
		if s.Phase == PhaseAccum {
			out.Reasons = []string{"accumulation_only"}
		} else {
			out.Reasons = []string{"no_sweep_or_expansion_event"}
		}
		return out
	}
	out.EvidenceIDs = append(out.EvidenceIDs, seq.ID)
	if seq.Status == SequenceStale {
		return Relation{Status: RelationUnavailable, Reasons: []string{"mad_evidence_stale"}, EvidenceIDs: out.EvidenceIDs}
	}
	wantDir := "BUY"
	if want == "sell" {
		wantDir = "SELL"
	}
	if seq.Status == SequenceInvalidated {
		switch {
		case seq.InvalidationReason == InvalidTwoSidedSweep || seq.InvalidationReason == InvalidConflictingSweep:
			// The evidence contradicts itself: it supports neither direction.
			out.Status, out.Reasons = RelationConflicting, []string{seq.InvalidationReason}
		case seq.Direction == wantDir:
			// The thesis this setup would lean on has failed.
			out.Status, out.Reasons = RelationConflicting, []string{seq.InvalidationReason}
		default:
			// A failed thesis the other way is not a contradiction of this one, and not support either.
			out.Status, out.Reasons = RelationPartial, []string{"opposing_sequence_invalidated", seq.InvalidationReason}
		}
		return out
	}
	if err := seq.OrderingError(rangeSealedAt(s)); err != nil {
		// Never present evidence whose own dates contradict each other.
		return Relation{Status: RelationUnavailable, Reasons: []string{"sequence_event_order_invalid"}, EvidenceIDs: out.EvidenceIDs}
	}
	if minutes, ok := s.Timeframe.Minutes(); ok && setup.ObservedAt > 0 && seq.LastEventAt+int64(minutes*60) > setup.ObservedAt+observedBarSeconds(setup) {
		// The evidence closes after the bar that made the setup actionable.
		return Relation{Status: RelationUnavailable, Reasons: []string{"evidence_after_observation"}, EvidenceIDs: out.EvidenceIDs}
	}
	sw, ex := seq.Sweep, seq.Expansion
	if sw != nil {
		out.EvidenceIDs = append(out.EvidenceIDs, seq.ID+"/"+StageSweep)
		if sw.Reclaimed() {
			out.EvidenceIDs = append(out.EvidenceIDs, seq.ID+"/"+StageReclaim)
		}
	}
	if ex != nil {
		out.EvidenceIDs = append(out.EvidenceIDs, seq.ID+"/"+StageExpansion)
	}
	reasons := func(status string, codes ...string) Relation {
		out.Status, out.Reasons = status, codes
		return out
	}

	switch fam {
	case familyContinuation:
		switch {
		case ex != nil && ex.Direction == wantDir:
			return reasons(RelationAligned, "expansion_continuation")
		case ex != nil:
			return reasons(RelationConflicting, "expansion_opposes_setup")
		case sw != nil && sw.Reclaimed() && sw.Direction == wantDir:
			return reasons(RelationAligned, "failed_break_reclaim_in_setup_direction")
		case sw != nil && sw.Reclaimed():
			return reasons(RelationConflicting, "sweep_direction_opposes_setup")
		default:
			return reasons(RelationPartial, "sweep_without_reclaim")
		}
	default: // familyManipulation
		if sw != nil && sw.Reclaimed() {
			if sw.Direction != wantDir {
				return reasons(RelationConflicting, "sweep_direction_opposes_setup")
			}
			if normalize(setup.Strategy) == "range_scalp" && ex != nil && ex.Direction != wantDir {
				return reasons(RelationConflicting, "fading_confirmed_expansion")
			}
			if sw.PenetrationATR < cfg.ManipMinimumPenetrationATR || sw.ReclaimDepthATR < cfg.ManipMinimumReclaimATR {
				return reasons(RelationPartial, "manipulation_below_minimum_measure")
			}
			if normalize(setup.Strategy) == "ifvg" {
				// The inversion follows the sweep that preceded the displacement: a sweep
				// that came after the setup formed is not the one it was built on.
				if setup.FormedAt <= 0 {
					return reasons(RelationPartial, "setup_formation_time_unavailable")
				}
				if sw.ReclaimAt > setup.FormedAt {
					return reasons(RelationPartial, "sweep_after_setup_formation")
				}
			}
			if ex != nil && ex.Direction == wantDir {
				return reasons(RelationAligned, "sweep_reclaim_in_setup_direction", "expansion_confirmed_in_setup_direction")
			}
			return reasons(RelationAligned, "sweep_reclaim_in_setup_direction")
		}
		switch {
		case sw != nil:
			return reasons(RelationPartial, "sweep_without_reclaim")
		case ex != nil && ex.Direction != wantDir:
			return reasons(RelationConflicting, "expansion_opposes_setup")
		default:
			return reasons(RelationPartial, "expansion_without_manipulation")
		}
	}
}

func rangeSealedAt(s Snapshot) int64 {
	if s.Asia == nil {
		return 0
	}
	return s.Asia.End
}

func observedBarSeconds(setup Setup) int64 {
	if minutes, ok := setup.ObservedTimeframe.Minutes(); ok {
		return int64(minutes * 60)
	}
	return 0
}

// String renders a relation for logs and test failures.
func (r Relation) String() string { return fmt.Sprintf("%s %v", r.Status, r.Reasons) }
