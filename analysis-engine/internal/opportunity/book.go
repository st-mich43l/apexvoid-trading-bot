package opportunity

import (
	"fmt"
	"sort"
	"strings"
	"unicode"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// State is the analytical lifecycle of a technical opportunity. It has no
// execution/account states: an Algo Bot refusing an old candidate does not
// invalidate the market thesis.
type State string

const (
	StateCreated     State = "CREATED"
	StateActive      State = "ACTIVE"
	StateInvalidated State = "INVALIDATED"
	StateExpired     State = "EXPIRED"
)

// ReasonCode is a machine-readable, technical terminal reason. Strategies
// may add their own uppercase underscore-delimited codes when these common
// reasons are insufficient.
type ReasonCode string

const (
	ReasonStructureInvalidated       ReasonCode = "STRUCTURE_INVALIDATED"
	ReasonZoneInvalidated            ReasonCode = "ZONE_INVALIDATED"
	ReasonLiquidityObjectiveConsumed ReasonCode = "LIQUIDITY_OBJECTIVE_CONSUMED"
	ReasonRetestFailed               ReasonCode = "RETEST_FAILED"
	ReasonSetupExpired               ReasonCode = "SETUP_EXPIRED"
	ReasonOppositeDisplacement       ReasonCode = "OPPOSITE_DISPLACEMENT"
	ReasonSessionExpired             ReasonCode = "SESSION_EXPIRED"
	ReasonInvalidationPriceTraded    ReasonCode = "INVALIDATION_PRICE_TRADED_THROUGH"
)

// Terminal records the one technical terminal transition for a Record.
type Terminal struct {
	Reason ReasonCode
	At     int64
}

// Record is the lifecycle-owned representation of a Candidate. Candidate is
// retained as the original created technical fact so a later re-evaluation
// cannot silently alter the payload that S9 will publish.
type Record struct {
	Candidate      Candidate
	State          State
	LastObservedAt int64
	Terminal       *Terminal
}

// TransitionKind lets the engine/application layer decide whether a future
// Kafka publish is required without strategy packages importing transport.
type TransitionKind string

const (
	TransitionCreated     TransitionKind = "CREATED"
	TransitionActivated   TransitionKind = "ACTIVATED"
	TransitionDuplicate   TransitionKind = "DUPLICATE"
	TransitionInvalidated TransitionKind = "INVALIDATED"
	TransitionExpired     TransitionKind = "EXPIRED"
	TransitionNoop        TransitionKind = "NOOP"
)

// Transition is an immutable result of one Book operation. Created and the
// two terminal kinds are the only lifecycle events S9 will publish.
type Transition struct {
	Kind   TransitionKind
	Record Record
}

// ShouldPublish reports whether this transition becomes an opportunity
// lifecycle event when S9 wires the application layer to Kafka.
func (t Transition) ShouldPublish() bool {
	return t.Kind == TransitionCreated || t.Kind == TransitionInvalidated || t.Kind == TransitionExpired
}

// Book owns the current opportunity lifecycle for one symbol. SymbolWorker
// serializes all access today; Book deliberately adds no second mutex so its
// operations remain deterministic and easy to replay.
type Book struct {
	records map[string]Record
}

func NewBook() *Book { return &Book{records: make(map[string]Record)} }

// Observe records a strategy evaluation. The first valid semantic
// opportunity is Created, its next observation becomes Active, and every
// later observation is a Duplicate. Duplicates never produce new events.
// A strategy disappearing from one evaluation does not invalidate it: only a
// strategy-supplied technical reason or its own expiry may do that.
func (b *Book) Observe(candidate Candidate, observedAt int64) (Transition, error) {
	if err := candidate.Validate(); err != nil {
		return Transition{}, err
	}
	if observedAt < candidate.CreatedAt {
		return Transition{}, fmt.Errorf("opportunity: observation predates candidate creation")
	}
	b.ensureRecords()
	if existing, ok := b.records[candidate.ID]; ok {
		if !sameSemanticOpportunity(existing.Candidate, candidate) {
			return Transition{}, fmt.Errorf("opportunity: ID %q collides with a different semantic opportunity", candidate.ID)
		}
		if observedAt < existing.LastObservedAt {
			return Transition{}, fmt.Errorf("opportunity: observation is older than the current lifecycle record")
		}
		switch existing.State {
		case StateCreated:
			existing.State = StateActive
			existing.LastObservedAt = observedAt
			b.records[candidate.ID] = existing
			return transition(TransitionActivated, existing), nil
		case StateActive:
			existing.LastObservedAt = observedAt
			b.records[candidate.ID] = existing
			return transition(TransitionDuplicate, existing), nil
		default:
			return transition(TransitionNoop, existing), nil
		}
	}
	record := Record{Candidate: cloneCandidate(candidate), State: StateCreated, LastObservedAt: observedAt}
	if observedAt >= candidate.ExpiresAt {
		record.State = StateExpired
		record.Terminal = &Terminal{Reason: ReasonSetupExpired, At: observedAt}
		b.records[candidate.ID] = record
		return transition(TransitionExpired, record), nil
	}
	b.records[candidate.ID] = record
	return transition(TransitionCreated, record), nil
}

// Invalidate transitions a still-live opportunity once. Repeating the same
// invalidation is a Noop, ensuring an invalidation event is publishable once.
func (b *Book) Invalidate(id string, reason ReasonCode, at int64) (Transition, error) {
	if id == "" || !reason.valid() || at < 0 {
		return Transition{}, fmt.Errorf("opportunity: invalidation needs an ID, machine-readable reason, and non-negative time")
	}
	record, ok := b.records[id]
	if !ok {
		return Transition{}, fmt.Errorf("opportunity: cannot invalidate unknown ID %q", id)
	}
	if at < record.LastObservedAt {
		return Transition{}, fmt.Errorf("opportunity: invalidation predates the current lifecycle record")
	}
	if record.State == StateInvalidated || record.State == StateExpired {
		return transition(TransitionNoop, record), nil
	}
	record.State = StateInvalidated
	record.Terminal = &Terminal{Reason: reason, At: at}
	b.records[id] = record
	return transition(TransitionInvalidated, record), nil
}

// InvalidateByClose applies each live candidate's own technical invalidation
// threshold to the closed bar for the timeframe that observed it.  Candidate
// invalidation prices are strategy-owned facts; the universal lifecycle rule
// is only the directional close-through check.  A wick that does not close
// beyond the threshold is deliberately not enough, matching the strategy
// specifications' confirmed-close semantics.
func (b *Book) InvalidateByClose(timeframe market.Timeframe, close float64, at int64) []Transition {
	if b == nil || timeframe == "" || !finite(close) || at < 0 {
		return nil
	}
	ids := make([]string, 0, len(b.records))
	for id := range b.records {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	transitions := make([]Transition, 0)
	for _, id := range ids {
		record := b.records[id]
		if record.State != StateCreated && record.State != StateActive {
			continue
		}
		candidate := record.Candidate
		if candidate.ObservedTimeframe != timeframe {
			continue
		}
		invalidated := (candidate.Direction == market.Buy && close <= float64(candidate.Invalidation.Price)) ||
			(candidate.Direction == market.Sell && close >= float64(candidate.Invalidation.Price))
		if !invalidated {
			continue
		}
		transition, err := b.Invalidate(id, reasonCodeForInvalidationLabel(candidate.Invalidation.Label), at)
		if err != nil {
			// The checks above establish the lifecycle preconditions. Keep the
			// method defensive if a future caller changes Book mutation order.
			continue
		}
		if transition.Kind == TransitionInvalidated {
			transitions = append(transitions, transition)
		}
	}
	return transitions
}

// Expire applies each strategy-owned technical expiry deadline. It never
// consults account policy or a global execution age; those remain Algo Bot
// concerns. Results are sorted by ID for deterministic replay/publication.
func (b *Book) Expire(now int64) []Transition {
	if now < 0 {
		return nil
	}
	ids := make([]string, 0, len(b.records))
	for id := range b.records {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	transitions := make([]Transition, 0)
	for _, id := range ids {
		record := b.records[id]
		if (record.State == StateCreated || record.State == StateActive) && now >= record.Candidate.ExpiresAt {
			record.State = StateExpired
			record.Terminal = &Terminal{Reason: ReasonSetupExpired, At: now}
			b.records[id] = record
			transitions = append(transitions, transition(TransitionExpired, record))
		}
	}
	return transitions
}

// Live returns immutable candidates in deterministic ID order. Created and
// Active opportunities are technically live; terminal records stay available
// through Record for audit/replay but are never offered to strategies/bots.
func (b *Book) Live() []Candidate {
	ids := make([]string, 0, len(b.records))
	for id := range b.records {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	result := make([]Candidate, 0, len(ids))
	for _, id := range ids {
		record := b.records[id]
		if record.State == StateCreated || record.State == StateActive {
			result = append(result, cloneCandidate(record.Candidate))
		}
	}
	return result
}

// Record returns a defensive copy of one lifecycle record.
func (b *Book) Record(id string) (Record, bool) {
	record, ok := b.records[id]
	if !ok {
		return Record{}, false
	}
	return cloneRecord(record), true
}

func (b *Book) ensureRecords() {
	if b.records == nil {
		b.records = make(map[string]Record)
	}
}

func transition(kind TransitionKind, record Record) Transition {
	return Transition{Kind: kind, Record: cloneRecord(record)}
}

func cloneRecord(record Record) Record {
	clone := record
	clone.Candidate = cloneCandidate(record.Candidate)
	if record.Terminal != nil {
		terminal := *record.Terminal
		clone.Terminal = &terminal
	}
	return clone
}

// sameSemanticOpportunity is Observe's identity-collision sanity check —
// a genuine hash collision on Candidate.ID (same Strategy/Version/Symbol/
// SetupKey hash) is the one thing it must catch; drift in the technical
// content of a still-valid, repeatedly-evaluated setup is not a
// collision. It deliberately does NOT compare Entry/Invalidation/
// CreatedAt: those fields are
// legitimately ATR-relative or touch/swing-anchored in every strategy
// strategy, so they recompute to a slightly different value on almost
// every re-evaluation of the SAME real-world setup even though its
// DeterministicID (built only from Strategy/Version/Symbol/Direction/
// SetupKey) correctly stays identical. Observe already freezes the
// FIRST-seen Candidate's content into the stored Record and never
// overwrites it on a later Created/Active/Duplicate observation (see the
// switch below), so this check only ever needs to protect that identity,
// not demand byte-for-byte content stability that strategies
// were never designed to produce.
func sameSemanticOpportunity(a, b Candidate) bool {
	return a.Strategy == b.Strategy && a.StrategyVersion == b.StrategyVersion &&
		a.Symbol == b.Symbol && a.Direction == b.Direction
}

func (r ReasonCode) valid() bool {
	if r == "" {
		return false
	}
	for i, char := range string(r) {
		if i == 0 && !(char >= 'A' && char <= 'Z') {
			return false
		}
		if i > 0 && !(char >= 'A' && char <= 'Z') && !(char >= '0' && char <= '9') && char != '_' {
			return false
		}
	}
	return true
}

func reasonCodeForInvalidationLabel(label string) ReasonCode {
	if label == "" {
		return ReasonStructureInvalidated
	}
	var builder strings.Builder
	for _, r := range label {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			builder.WriteRune(unicode.ToUpper(r))
		} else {
			builder.WriteByte('_')
		}
	}
	code := strings.Trim(builder.String(), "_")
	if code == "" || code[0] < 'A' || code[0] > 'Z' {
		return ReasonStructureInvalidated
	}
	reason := ReasonCode(code)
	if !reason.valid() {
		return ReasonStructureInvalidated
	}
	return reason
}
