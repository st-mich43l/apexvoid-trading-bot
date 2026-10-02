// Package opportunity holds the technical-opportunity domain: a Candidate
// is not a TradePlan (see docs/architecture/algo-bot.md's "TradePlan
// ownership" — analysis-engine never publishes broker-ready position
// sizing). It is the pure, behavior-free result of a strategy's
// evaluation: what the setup is, where it is, why (evidence), and how
// good it looks by that strategy's own quality model.
//
// This package deliberately has NO dependency on internal/strategy — see
// docs/architecture/dependency-rules.md's "the one correction" for why:
// strategy.Strategy.Evaluate returns []opportunity.Candidate, so
// opportunity must sit below strategy in the dependency graph, not above
// it as the source architecture task's own §51 literally (and, per that
// same task's §21, inconsistently) orders it.
package opportunity

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/candle"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// StrategyID identifies which strategy produced a Candidate. Defined here
// (not in internal/strategy) because both opportunity.Candidate and
// strategy.Strategy need it, and opportunity must not import strategy —
// internal/strategy aliases this as its own StrategyID for callers that
// only ever see the strategy package.
type StrategyID string

// EntryZone is a simple price range — deliberately not named "Zone" and
// deliberately not internal/zone's richer Zone type (supply/demand
// geometry with lifecycle/mitigation state, a higher layer opportunity
// must not depend on). See docs/architecture/analysis-engine.md's naming
// note on this exact point.
type EntryZone struct {
	Low  float64
	High float64
}

// Evidence is one machine-readable fact backing a Candidate — per the
// source task's §25, avoid opaque free-text-only explanations inside the
// core engine (e.g. "major_structure_bearish", "m5_bos_down",
// "liquidity_high_swept", "fvg_present", "retest_confirmed",
// "key_level_rejected"). Human-readable summaries are generated
// downstream, not stored here.
type Evidence struct {
	Code string
}

// Target is one take-profit thesis for a Candidate — a price plus why.
type Target struct {
	Price market.PriceLevel
}

// StrategyQuality is strategy-specific, not universal (§26): Overall is a
// single comparable score, Components is that strategy's own named
// dimensions (e.g. Breakout Retest: "breakout_quality", "retest_quality",
// "structure_quality", "location_quality" — a Liquidity Sweep strategy
// would use an entirely different set). No shared scoring formula is
// imposed here.
type StrategyQuality struct {
	Overall    float64
	Components map[string]float64
}

// AnalysisProvenance records the analytical versions used to establish a
// Candidate. Configuration provenance belongs in the Kafka envelope when S9
// publishes the lifecycle event; it remains here too so replay and a future
// journal can retain the complete technical fact without importing transport.
type AnalysisProvenance struct {
	StructureVersion  string
	LiquidityVersion  string
	ZoneVersion       string
	ConfigVersion     int
	ConfigFingerprint string
}

// Identity contains only setup-defining fields a strategy can use to create a
// deterministic opportunity ID. SetupKey is strategy-owned: it should be a
// stable canonical fact reference (for example an origin swing/zone pair),
// never an event ID or a candle's evaluation timestamp.
type Identity struct {
	Strategy        StrategyID
	StrategyVersion string
	Symbol          market.Symbol
	Direction       market.Direction
	SetupKey        string
}

// DeterministicID returns a stable, opaque opportunity ID for one semantic
// setup. Kafka event IDs are deliberately not included: the same opportunity
// can yield one created event and, later, one terminal event.
func DeterministicID(identity Identity) (string, error) {
	if identity.Strategy == "" || identity.StrategyVersion == "" || identity.Symbol == "" || !identity.Direction.IsValid() || identity.SetupKey == "" {
		return "", fmt.Errorf("opportunity: deterministic identity requires strategy, strategy version, symbol, direction, and setup key")
	}
	canonical := "opportunity/v1\x00" + string(identity.Strategy) + "\x00" + identity.StrategyVersion + "\x00" + string(identity.Symbol) + "\x00" + string(identity.Direction) + "\x00" + identity.SetupKey
	sum := sha256.Sum256([]byte(canonical))
	return "opp_" + hex.EncodeToString(sum[:]), nil
}

// ReactionConfirmation identifies the actual causal zone touch and later (or
// same-bar) closed-bar rejection. Resting-zone candidates leave it absent.
// A confirmed reaction receives a separate deterministic opportunity ID.
type ReactionConfirmation struct {
	ZoneID              string
	TouchBarTime        int64
	ConfirmationBarTime int64
	ReactionType        string
	// Pattern names which legacy confirmation pattern produced the
	// reaction (wick_rejection, rejection_choch, strong_reclaim,
	// engulfing, sweep_reclaim). Empty for the older strict
	// close-outside-the-zone confirmation, which has no named pattern.
	Pattern string
}

// ReactionPatterns are the confirmation patterns a reaction may carry.
var ReactionPatterns = map[string]bool{
	"wick_rejection": true, "rejection_choch": true, "strong_reclaim": true,
	"engulfing": true, "sweep_reclaim": true,
}

// TechnicalContext is the engine-owned technical facts an execution policy
// needs to evaluate a Candidate without re-running any detector or recomputing
// ATR from raw OHLC (S13B). Strategies never set it: SymbolWorker assigns it at
// the same observation boundary as ObservedTimeframe, from the exact closed bar
// that first made the setup actionable, so every value is causal at that bar.
//
// It deliberately carries no quote/spread (a live, executable-price concern the
// policy layer owns) or account fact. Confluence is included only as a named
// technical scoring result copied from the engine; it is not a universal
// execution decision and does not replace a strategy's own Quality.
// HigherTimeframeBias is a confirmed structural read from the named closed
// higher-timeframe candle; it is not the entry timeframe's bias or a guess.
type HigherTimeframeBias struct {
	Timeframe     market.Timeframe
	Direction     market.Direction
	Layer         string
	ReferenceTime int64
}

// MADContext is the engine-owned MAD/Asia phase snapshot used by the legacy
// detector's soft-confluence path. It carries technical facts only; it is
// never an execution gate and contains no quote, account, or sizing input.
type MADContext struct {
	Version             int
	Phase               string
	Confidence          float64
	Affinity            float64
	Direction           string
	SweepSide           string
	Reclaim             bool
	RangeQualityATR     *float64
	BreakDistanceATR    *float64
	DisplacementATR     *float64
	AcceptanceCloses    *int
	SweepPenetrationATR *float64
	ReclaimDepthATR     *float64
	ReasonCode          string
}

// ConfluenceFactors are the named technical inputs used by the versioned
// Python confluence scorer. They are facts, not execution gates. Touches is
// retained as a count because the scorer caps its contribution at three.
type ConfluenceFactors struct {
	HTFAligned          bool
	Touches             int
	WickRejection       bool
	DisplacementGrade   bool
	SessionContext      bool
	StructuralAgreement bool
	FibTouch            bool
	CHoCH               bool
}

// FibonacciLevelProvenance identifies the exact canonical ladder point that
// produced FibTouch. A boolean alone was insufficient for replay/parity work.
type FibonacciLevelProvenance struct {
	Ratio       float64
	Price       float64
	Kind        string
	DistanceATR float64
}

// LiquidityGrabProvenance identifies the canonical pool swept and reclaimed
// by the confirmation bar used for the Grade-A zone-quality bonus. Go's pool
// identity replaces the deleted Python Grab object's process-local index.
type LiquidityGrabProvenance struct {
	PoolID      string
	SweptAt     int64
	ReclaimedAt int64
}

// ConfluenceContext is the engine-owned result of the V1/V2 technical
// confluence calculation. The selected stars are the configured scorer's
// value; both versions and the raw components remain available for replay,
// cards, and parity diagnostics.
type ConfluenceContext struct {
	Version          string
	SelectedStars    int
	V1Stars          int
	V2Stars          int
	V2Raw            float64
	RawFactorScore   float64
	ZoneQualityScore float64
	MADBonus         float64
	Factors          ConfluenceFactors
	FibLevel         *FibonacciLevelProvenance
	GradeAGrab       *LiquidityGrabProvenance
}

type TechnicalContext struct {
	// ATR is the canonical ATR of ObservedTimeframe as of the observed bar.
	ATR float64
	// ReferencePrice is the close of that bar. It is a technical reference for
	// geometry, not an executable price.
	ReferencePrice float64
	// ReferenceTime is that bar's open time, Unix seconds.
	ReferenceTime int64
	// Bias is the engine's structural bias as of that bar (primary-timeframe
	// derived, see internal/context.DeriveBias). Absent (zero) when the
	// engine has no confirmed bias — never a guessed neutral.
	BiasDirection market.Direction
	BiasLayer     string
	// HigherTimeframes is ordered H1, H4, M15; each entry is read only after its
	// own bar has closed and is fresh at the opportunity's observation bar.
	HigherTimeframes []HigherTimeframeBias
	Confirmation     *ReactionConfirmation
	// CandleEvidence is additive descriptive evidence from the same closed
	// observation bar. It never gates a candidate or changes execution risk.
	CandleEvidence *candle.Evidence
	// MAD is additive soft-confluence telemetry from the engine's canonical
	// primary-timeframe phase snapshot. A nil value means no phase snapshot was
	// available; it never licenses a consumer to recompute Python detectors.
	MAD *MADContext
	// Confluence is the engine's named factor/V1/V2 technical score. It is
	// optional for compatibility with retained pre-S13C events.
	Confluence *ConfluenceContext
}

// Candidate is one strategy's technical opportunity, as of the source
// task's §24.
type Candidate struct {
	ID              string
	Strategy        StrategyID
	StrategyVersion string
	Symbol          market.Symbol
	// ObservedTimeframe is the closed-bar event that first made this setup
	// actionable. Strategies leave it empty; SymbolWorker assigns it at the
	// observation boundary so multi-timeframe evaluations remain auditable.
	ObservedTimeframe market.Timeframe

	Direction market.Direction

	// StructuralID is the strategy's own persistent identity for the
	// underlying technical object (a zone.Zone.ID, keylevel.Level.ID,
	// liquidity.Pool.ID, or an equivalent stable anchor) — the same real
	// identity DeterministicID's SetupKey hashes into ID, retained here in
	// recoverable form so a consumer (e.g. internal/arbitration's thesis
	// correlation) can tell whether two different Candidates are the same
	// real-world technical object without re-deriving an approximate
	// bucket from price geometry. Empty when a strategy has no such
	// persistent object to offer (never fabricated).
	StructuralID string

	Entry        EntryZone
	Invalidation market.PriceLevel
	Targets      []Target

	Evidence []Evidence
	Quality  StrategyQuality

	// FormedAt is when the underlying technical primitive formed. CreatedAt
	// is when this opportunity was first actionable and observed by the
	// strategy engine. They are intentionally distinct event semantics.
	FormedAt  int64
	CreatedAt int64
	// ExpiresAt is strategy-owned technical expiry, not Algo Bot's execution
	// maximum age. It is an absolute Unix-second deadline.
	ExpiresAt int64

	Provenance AnalysisProvenance

	// Technical is nil until SymbolWorker attaches it; a nil value means the
	// consumer must treat the policy inputs as unavailable (fail closed).
	Technical *TechnicalContext
	// Reaction is assigned by a strategy ONLY when its own confirmed thesis
	// is observed; worker copies it into technical context at publication.
	Reaction *ReactionConfirmation
	// StopEnvelope is nil until SymbolWorker attaches it (Phase 4) — the
	// technical stop-distance floor/cap this strategy family's own risk
	// policy allows, computed from this candidate's own target geometry.
	// A nil value means the consumer must fail closed the same way a nil
	// Technical does; it is never fabricated from a hardcoded default.
	StopEnvelope *StopEnvelope
}

// StopEnvelope is the technical stop-distance bounds for one candidate —
// engine-owned so algo-bot's execution policy never re-derives a
// per-strategy-family pip floor/cap from its own taxonomy lookup (source:
// the same execution.reaction/range/trend/scalping config algo-bot itself
// reads, ctrader-engine shares execution.trend.stop_max_pips already).
// Deliberately raw facts, not a final decision: DesiredMinimumPips is the
// single-leg "pin near this instance's own target" recommendation: a
// multi-leg group stop or a fixed-R:R instrument should use FloorPips as
// its minimum instead — that choice depends on execution-time context
// (how many legs, which instrument's targeting mode) StopEnvelope
// deliberately does not know, matching Candidate's own "no quote/
// spread/account" boundary.
type StopEnvelope struct {
	FloorPips          float64
	CapPips            float64
	DesiredMinimumPips float64
	Source             string
}

// Validate verifies the lifecycle-relevant, transport-neutral Candidate
// contract. Strategy-specific setup rules remain inside the strategy package.
func (c Candidate) Validate() error {
	if c.ID == "" {
		return fmt.Errorf("opportunity: candidate ID is required")
	}
	if c.Strategy == "" || c.StrategyVersion == "" {
		return fmt.Errorf("opportunity: candidate strategy and strategy version are required")
	}
	if c.Symbol == "" || !c.Direction.IsValid() {
		return fmt.Errorf("opportunity: candidate symbol and BUY/SELL direction are required")
	}
	if c.ObservedTimeframe != "" {
		if _, ok := c.ObservedTimeframe.Minutes(); !ok {
			return fmt.Errorf("opportunity: observed timeframe is invalid")
		}
	}
	if !finite(c.Entry.Low) || !finite(c.Entry.High) || c.Entry.Low > c.Entry.High {
		return fmt.Errorf("opportunity: entry must be a finite low-to-high range")
	}
	// Algo Bot rejects a zero-width band (degenerate_entry_band) and the
	// analysis.opportunity.v1 consumer contract requires the geometry below;
	// enforcing both here means a strategy defect drops the candidate at the
	// source instead of publishing an event every consumer must reject.
	if c.Entry.Low == c.Entry.High {
		return fmt.Errorf("opportunity: entry band must have positive width")
	}
	if !finite(float64(c.Invalidation.Price)) {
		return fmt.Errorf("opportunity: invalidation price must be finite")
	}
	if len(c.Targets) == 0 || len(c.Evidence) == 0 {
		return fmt.Errorf("opportunity: at least one technical target and evidence fact are required")
	}
	for _, target := range c.Targets {
		if !finite(float64(target.Price.Price)) {
			return fmt.Errorf("opportunity: target prices must be finite")
		}
	}
	invalidation := float64(c.Invalidation.Price)
	switch c.Direction {
	case market.Buy:
		if invalidation >= c.Entry.Low {
			return fmt.Errorf("opportunity: BUY invalidation must be below entry.low")
		}
		for _, target := range c.Targets {
			if float64(target.Price.Price) <= c.Entry.High {
				return fmt.Errorf("opportunity: BUY targets must be above entry.high")
			}
		}
	case market.Sell:
		if invalidation <= c.Entry.High {
			return fmt.Errorf("opportunity: SELL invalidation must be above entry.high")
		}
		for _, target := range c.Targets {
			if float64(target.Price.Price) >= c.Entry.Low {
				return fmt.Errorf("opportunity: SELL targets must be below entry.low")
			}
		}
	}
	for _, evidence := range c.Evidence {
		if evidence.Code == "" {
			return fmt.Errorf("opportunity: evidence codes must be non-empty")
		}
	}
	if c.FormedAt < 0 || c.CreatedAt < 0 {
		return fmt.Errorf("opportunity: formation and creation times must be non-negative")
	}
	if c.FormedAt > c.CreatedAt {
		return fmt.Errorf("opportunity: formation must not follow creation")
	}
	if c.ExpiresAt <= c.CreatedAt {
		return fmt.Errorf("opportunity: expiry must be after creation")
	}
	if !finite(c.Quality.Overall) {
		return fmt.Errorf("opportunity: quality must be finite")
	}
	for name, value := range c.Quality.Components {
		if name == "" || !finite(value) {
			return fmt.Errorf("opportunity: quality components need non-empty names and finite values")
		}
	}
	if c.Reaction != nil {
		r := c.Reaction
		if r.ZoneID == "" || r.ReactionType != "rejection" || (r.Pattern != "" && !ReactionPatterns[r.Pattern]) || r.TouchBarTime < 0 || r.ConfirmationBarTime < r.TouchBarTime || r.ConfirmationBarTime > c.CreatedAt {
			return fmt.Errorf("opportunity: confirmed reaction needs a causal touch and closed confirmation bar")
		}
	}
	if c.Technical != nil {
		t := c.Technical
		if !finite(t.ATR) || t.ATR <= 0 || !finite(t.ReferencePrice) || t.ReferencePrice <= 0 || t.ReferenceTime < 0 {
			return fmt.Errorf("opportunity: technical context needs finite positive ATR and reference price")
		}
		if t.BiasDirection != "" && !t.BiasDirection.IsValid() {
			return fmt.Errorf("opportunity: technical bias direction must be BUY or SELL when present")
		}
		if t.Confluence != nil {
			if t.Confluence.Version != "v1" && t.Confluence.Version != "v2" {
				return fmt.Errorf("opportunity: confluence scoring version must be v1 or v2")
			}
			if t.Confluence.SelectedStars < 1 || t.Confluence.SelectedStars > 3 || t.Confluence.V1Stars < 1 || t.Confluence.V1Stars > 3 || t.Confluence.V2Stars < 1 || t.Confluence.V2Stars > 3 || t.Confluence.Factors.Touches < 0 || !finite(t.Confluence.V2Raw) || !finite(t.Confluence.RawFactorScore) || !finite(t.Confluence.ZoneQualityScore) || !finite(t.Confluence.MADBonus) || t.Confluence.MADBonus < 0 {
				return fmt.Errorf("opportunity: confluence facts are invalid")
			}
		}
		seen := make(map[market.Timeframe]bool, len(t.HigherTimeframes))
		for _, higher := range t.HigherTimeframes {
			if higher.Timeframe != market.M15 && higher.Timeframe != market.H1 && higher.Timeframe != market.H4 {
				return fmt.Errorf("opportunity: higher timeframe must be M15, H1 or H4")
			}
			if seen[higher.Timeframe] || !higher.Direction.IsValid() || higher.Layer == "" || higher.ReferenceTime < 0 || higher.ReferenceTime > c.CreatedAt {
				return fmt.Errorf("opportunity: higher timeframe needs distinct causal confirmed structure")
			}
			seen[higher.Timeframe] = true
		}
	}
	if c.Provenance.StructureVersion == "" || c.Provenance.LiquidityVersion == "" || c.Provenance.ZoneVersion == "" || c.Provenance.ConfigVersion <= 0 || c.Provenance.ConfigFingerprint == "" {
		return fmt.Errorf("opportunity: complete analytical and configuration provenance is required")
	}
	if c.StopEnvelope != nil {
		e := c.StopEnvelope
		if !finite(e.FloorPips) || !finite(e.CapPips) || !finite(e.DesiredMinimumPips) || e.FloorPips <= 0 || e.CapPips < e.FloorPips || e.DesiredMinimumPips < e.FloorPips || e.Source == "" {
			return fmt.Errorf("opportunity: stop envelope needs a positive floor, a cap at or above the floor, and a desired minimum at or above the floor")
		}
	}
	return nil
}

// AtFirstObservation converts a strategy's formation-anchored candidate into
// an actionable opportunity observed on a closed bar. The strategy-owned TTL
// is preserved, while the earlier technical timestamp remains available as
// FormedAt for provenance and audit.
func AtFirstObservation(candidate Candidate, observedAt int64) (Candidate, error) {
	if observedAt < 0 {
		return Candidate{}, fmt.Errorf("opportunity: first observation time must be non-negative")
	}
	ttl := candidate.ExpiresAt - candidate.CreatedAt
	if ttl <= 0 {
		return Candidate{}, fmt.Errorf("opportunity: strategy expiry window must be positive")
	}
	if candidate.FormedAt == 0 {
		candidate.FormedAt = candidate.CreatedAt
	}
	if candidate.FormedAt > observedAt {
		candidate.FormedAt = observedAt
	}
	candidate.CreatedAt = observedAt
	candidate.ExpiresAt = observedAt + ttl
	return candidate, candidate.Validate()
}

func finite(v float64) bool { return !math.IsNaN(v) && !math.IsInf(v, 0) }

func cloneCandidate(c Candidate) Candidate {
	clone := c
	clone.Targets = append([]Target(nil), c.Targets...)
	clone.Evidence = append([]Evidence(nil), c.Evidence...)
	if c.Reaction != nil {
		reaction := *c.Reaction
		clone.Reaction = &reaction
	}
	if c.Technical != nil {
		technical := *c.Technical
		technical.HigherTimeframes = append([]HigherTimeframeBias(nil), c.Technical.HigherTimeframes...)
		if c.Technical.Confirmation != nil {
			confirmation := *c.Technical.Confirmation
			technical.Confirmation = &confirmation
		}
		if c.Technical.CandleEvidence != nil {
			evidence := *c.Technical.CandleEvidence
			evidence.AllPatterns = append([]string(nil), c.Technical.CandleEvidence.AllPatterns...)
			technical.CandleEvidence = &evidence
		}
		if c.Technical.MAD != nil {
			mad := *c.Technical.MAD
			mad.RangeQualityATR = cloneFloat(c.Technical.MAD.RangeQualityATR)
			mad.BreakDistanceATR = cloneFloat(c.Technical.MAD.BreakDistanceATR)
			mad.DisplacementATR = cloneFloat(c.Technical.MAD.DisplacementATR)
			mad.SweepPenetrationATR = cloneFloat(c.Technical.MAD.SweepPenetrationATR)
			mad.ReclaimDepthATR = cloneFloat(c.Technical.MAD.ReclaimDepthATR)
			mad.AcceptanceCloses = cloneInt(c.Technical.MAD.AcceptanceCloses)
			technical.MAD = &mad
		}
		if c.Technical.Confluence != nil {
			confluence := *c.Technical.Confluence
			technical.Confluence = &confluence
		}
		clone.Technical = &technical
	}
	if c.Quality.Components != nil {
		clone.Quality.Components = make(map[string]float64, len(c.Quality.Components))
		for name, value := range c.Quality.Components {
			clone.Quality.Components[name] = value
		}
	}
	if c.StopEnvelope != nil {
		envelope := *c.StopEnvelope
		clone.StopEnvelope = &envelope
	}
	return clone
}

func cloneFloat(value *float64) *float64 {
	if value == nil {
		return nil
	}
	copy := *value
	return &copy
}

func cloneInt(value *int) *int {
	if value == nil {
		return nil
	}
	copy := *value
	return &copy
}
