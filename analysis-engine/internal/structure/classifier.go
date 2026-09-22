package structure

// SwingRelation is the explicit classification of one swing against the
// preceding same-kind swing at the same layer — source task §18: "do not
// use vague bias inference... must state HH/HL/LH/LL/EqualHigh/EqualLow."
type SwingRelation uint8

const (
	RelationUnknown SwingRelation = iota
	HigherHigh
	HigherLow
	LowerHigh
	LowerLow
	EqualHigh
	EqualLow
)

func (r SwingRelation) String() string {
	switch r {
	case HigherHigh:
		return "HH"
	case HigherLow:
		return "HL"
	case LowerHigh:
		return "LH"
	case LowerLow:
		return "LL"
	case EqualHigh:
		return "EqH"
	case EqualLow:
		return "EqL"
	default:
		return "unknown"
	}
}

// ClassifySwingRelation compares current against the preceding same-kind
// swing (previous) at the same layer, using an ATR-normalized equality
// band rather than direct floating-point equality (§18: "do not rely on
// direct floating-point equality... tolerance based on instrument
// geometry/volatility"). RelationUnknown if the two swings are not the
// same Kind — comparing a high against a low is a caller error, not a
// meaningful relation.
func ClassifySwingRelation(current, previous Swing, atr, toleranceATR float64) SwingRelation {
	if current.Kind != previous.Kind {
		return RelationUnknown
	}
	tolerance := toleranceATR * atr
	diff := current.Price.Sub(previous.Price) // current - previous, signed
	if absf(diff) <= tolerance {
		if current.Kind == SwingHigh {
			return EqualHigh
		}
		return EqualLow
	}
	if current.Kind == SwingHigh {
		if diff > 0 {
			return HigherHigh
		}
		return LowerHigh
	}
	// SwingLow
	if diff > 0 {
		return HigherLow
	}
	return LowerLow
}

func absf(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}
