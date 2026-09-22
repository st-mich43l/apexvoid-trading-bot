package structure

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// EventKind is the structural-significance classification of a held break
// — source task §21/§22. Only ever set on a BreakClose or BreakDisplacement
// StructureBreak; BreakWick/BreakSweep/BreakFailed are never a structural
// event by definition (they did not hold).
type EventKind uint8

const (
	EventNone EventKind = iota
	EventBOS
	EventCHoCH
)

func (e EventKind) String() string {
	switch e {
	case EventBOS:
		return "BOS"
	case EventCHoCH:
		return "CHoCH"
	default:
		return "none"
	}
}

// ClassifyEvent answers "is this held break a BOS or a CHoCH" using the
// layer's trend/protected-level context AS OF JUST BEFORE the break —
// source task §21 ("BOS... continuation of the currently established
// structural direction") and §22/§23 ("CHoCH... depend on... what was
// protected", "protected low is the swing whose failure materially
// damages bullish structure").
//
//   - BOS: the break direction agrees with the already-established trend
//     (breaking up while Bullish, down while Bearish), OR there was no
//     established trend yet (Range/Unknown) — the first directional close
//     from an undirected state is the initiating BOS for that direction,
//     not a CHoCH (there is nothing established yet to "change from").
//   - CHoCH: the break direction OPPOSES the established trend AND the
//     broken swing is specifically the layer's current protected level
//     (ProtectedLow while Bullish, ProtectedHigh while Bearish) — a close
//     beyond some OTHER, non-protected swing in the counter-trend
//     direction (e.g. an internal pullback low, still well above the
//     protected low) is neither: it is EventNone, evidence worth keeping
//     on the break record but not yet a regime-change signal.
//   - Every BreakWick/BreakSweep/BreakFailed is EventNone unconditionally
//     — they did not hold, so they cannot continue or change structure.
//
// A CHoCH's Layer field (already on StructureBreak) is what distinguishes
// a micro CHoCH from a major one (§22's own explicit requirement) — there
// is deliberately no separate "major" event kind; a consumer filters by
// Layer.
func ClassifyEvent(brk StructureBreak, before LayerState) EventKind {
	if brk.Type != BreakClose && brk.Type != BreakDisplacement {
		return EventNone
	}
	switch before.Trend {
	case TrendBullish:
		if brk.Direction == market.Buy {
			return EventBOS
		}
		if before.ProtectedLow != nil && brk.BrokenSwingID == before.ProtectedLow.ID {
			return EventCHoCH
		}
		return EventNone
	case TrendBearish:
		if brk.Direction == market.Sell {
			return EventBOS
		}
		if before.ProtectedHigh != nil && brk.BrokenSwingID == before.ProtectedHigh.ID {
			return EventCHoCH
		}
		return EventNone
	default: // TrendRange, TrendUnknown
		if brk.Direction.IsValid() {
			return EventBOS
		}
		return EventNone
	}
}
