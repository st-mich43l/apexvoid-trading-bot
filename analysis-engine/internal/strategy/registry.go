package strategy

import (
	"fmt"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// Config is the common, configuration-owned identity of one approved V2
// strategy. Parameters deliberately remains an untyped copy of the rest of
// that strategy's configuration: each concrete strategy owns parsing and
// validation of its own technical thresholds, rather than a generic registry
// inventing shared strategy behavior or defaults.
type Config struct {
	ID         StrategyID
	Version    string
	Enabled    bool
	Parameters map[string]any
}

// Factory constructs one concrete, enabled strategy from its own V3 config.
// Implementations are registered by the composition root once they exist in
// Phase S7; the registry itself never imports a strategy subpackage.
type Factory func(Config) (Strategy, error)

// Evaluation is the deterministic result of handling one closed bar. A
// strategy is deferred until every timeframe it declared is present in the
// canonical MarketContext. It is not an error for a fresh symbol to be
// missing H1 history while M1 bars are already arriving.
type Evaluation struct {
	Candidates []opportunity.Candidate
	Evaluated  []StrategyID
	Deferred   []StrategyID
}

type registeredStrategy struct {
	strategy Strategy
	config   Config
	required map[market.Timeframe]struct{}
}

// Registry contains only enabled concrete strategies. Its order is the
// canonical catalog order, never map iteration order, so replay evaluation is
// reproducible.
type Registry struct {
	strategies []registeredStrategy
}

// KnownIDs is the semantic V2 catalog approved in Phase S2. It is a config
// contract, not an implementation list: Phase S6 intentionally keeps every
// entry disabled until Phase S7 supplies its independent technical thesis.
func KnownIDs() []StrategyID {
	return append([]StrategyID(nil), knownIDs...)
}

var knownIDs = []StrategyID{
	"key_level",
	"confluence_zone",
	"supply",
	"demand",
	"order_block",
	"fvg",
	"ifvg",
	"crt",
	"flip_zone",
	"session_level",
	"trendline",
	"range_edge",
	"box_breakout",
	"momentum_ride",
	"snap_back",
	"liquidity_sweep",
	"range_sweep",
	"impulse_pullback",
	"scalp_breakout_retest",
}

// ValidateConfigs fails closed for unknown, duplicate, malformed, or missing
// catalog entries. Requiring every approved ID makes a future strategy
// rollout an explicit configuration decision instead of a silent default.
func ValidateConfigs(configs []Config) error {
	known := make(map[StrategyID]struct{}, len(knownIDs))
	for _, id := range knownIDs {
		known[id] = struct{}{}
	}
	seen := make(map[StrategyID]struct{}, len(configs))
	for _, cfg := range configs {
		if _, ok := known[cfg.ID]; !ok {
			return fmt.Errorf("strategy: unknown configured strategy %q", cfg.ID)
		}
		if _, duplicate := seen[cfg.ID]; duplicate {
			return fmt.Errorf("strategy: duplicate configuration for %q", cfg.ID)
		}
		seen[cfg.ID] = struct{}{}
		if cfg.Version == "" {
			return fmt.Errorf("strategy: %q requires a strategy version", cfg.ID)
		}
	}
	for _, id := range knownIDs {
		if _, ok := seen[id]; !ok {
			return fmt.Errorf("strategy: missing required configuration for %q", id)
		}
	}
	return nil
}

// NewRegistry validates the complete configured catalog, then constructs only
// enabled strategies. An enabled catalog entry without a Phase S7 factory is
// a startup error, never a silently inactive strategy.
func NewRegistry(configs []Config, factories map[StrategyID]Factory) (*Registry, error) {
	if err := ValidateConfigs(configs); err != nil {
		return nil, err
	}
	byID := make(map[StrategyID]Config, len(configs))
	for _, cfg := range configs {
		byID[cfg.ID] = cloneConfig(cfg)
	}

	registry := &Registry{}
	for _, id := range knownIDs {
		cfg := byID[id]
		if !cfg.Enabled {
			continue
		}
		factory, ok := factories[id]
		if !ok || factory == nil {
			return nil, fmt.Errorf("strategy: %q is enabled but has no registered implementation", id)
		}
		instance, err := factory(cloneConfig(cfg))
		if err != nil {
			return nil, fmt.Errorf("strategy: constructing %q: %w", id, err)
		}
		if instance == nil {
			return nil, fmt.Errorf("strategy: factory for %q returned nil", id)
		}
		if instance.ID() != id {
			return nil, fmt.Errorf("strategy: factory for %q returned strategy ID %q", id, instance.ID())
		}
		required, err := requiredTimeframes(instance)
		if err != nil {
			return nil, fmt.Errorf("strategy: %q: %w", id, err)
		}
		registry.strategies = append(registry.strategies, registeredStrategy{
			strategy: instance,
			config:   cfg,
			required: required,
		})
	}
	return registry, nil
}

// EnabledIDs returns the concrete strategies in deterministic catalog order.
func (r *Registry) EnabledIDs() []StrategyID {
	if r == nil {
		return nil
	}
	ids := make([]StrategyID, len(r.strategies))
	for i, registered := range r.strategies {
		ids[i] = registered.config.ID
	}
	return ids
}

// Evaluate runs only strategies that declared the timeframe which just
// closed. It does not write the OpportunityBook: lifecycle observation and
// publication are deliberately separate Phase S8/S9 responsibilities.
func (r *Registry) Evaluate(ctx *analysiscontext.MarketContext, closed market.Timeframe) (Evaluation, error) {
	if r == nil {
		return Evaluation{}, fmt.Errorf("strategy: nil registry")
	}
	if ctx == nil {
		return Evaluation{}, fmt.Errorf("strategy: nil market context")
	}
	if _, ok := closed.Minutes(); !ok {
		return Evaluation{}, fmt.Errorf("strategy: unrecognized closed timeframe %q", closed)
	}

	result := Evaluation{}
	seenCandidates := make(map[string]StrategyID)
	for _, registered := range r.strategies {
		if _, affected := registered.required[closed]; !affected {
			continue
		}
		if !hasRequiredTimeframes(ctx, registered.required) {
			result.Deferred = append(result.Deferred, registered.config.ID)
			continue
		}
		candidates := registered.strategy.Evaluate(ctx)
		result.Evaluated = append(result.Evaluated, registered.config.ID)
		for _, candidate := range candidates {
			if err := validateCandidate(candidate, registered, ctx.Symbol); err != nil {
				return Evaluation{}, err
			}
			if previous, duplicate := seenCandidates[candidate.ID]; duplicate {
				return Evaluation{}, fmt.Errorf("strategy: duplicate candidate ID %q from %q and %q in one evaluation", candidate.ID, previous, registered.config.ID)
			}
			seenCandidates[candidate.ID] = registered.config.ID
			result.Candidates = append(result.Candidates, candidate)
		}
	}
	return result, nil
}

func requiredTimeframes(instance Strategy) (map[market.Timeframe]struct{}, error) {
	timeframes := instance.RequiredTimeframes()
	if len(timeframes) == 0 {
		return nil, fmt.Errorf("requires at least one timeframe")
	}
	required := make(map[market.Timeframe]struct{}, len(timeframes))
	for _, tf := range timeframes {
		if _, ok := tf.Minutes(); !ok {
			return nil, fmt.Errorf("declares unrecognized timeframe %q", tf)
		}
		if _, duplicate := required[tf]; duplicate {
			return nil, fmt.Errorf("declares timeframe %q more than once", tf)
		}
		required[tf] = struct{}{}
	}
	return required, nil
}

func hasRequiredTimeframes(ctx *analysiscontext.MarketContext, required map[market.Timeframe]struct{}) bool {
	for tf := range required {
		if ctx.Timeframes[tf] == nil {
			return false
		}
	}
	return true
}

func validateCandidate(candidate opportunity.Candidate, registered registeredStrategy, symbol market.Symbol) error {
	if candidate.Strategy != registered.config.ID {
		return fmt.Errorf("strategy: %q returned candidate for %q", registered.config.ID, candidate.Strategy)
	}
	if candidate.StrategyVersion != registered.config.Version {
		return fmt.Errorf("strategy: %q returned candidate version %q, configured version is %q", registered.config.ID, candidate.StrategyVersion, registered.config.Version)
	}
	if candidate.Symbol != symbol {
		return fmt.Errorf("strategy: %q returned candidate for symbol %q while evaluating %q", registered.config.ID, candidate.Symbol, symbol)
	}
	if err := candidate.Validate(); err != nil {
		return fmt.Errorf("strategy: %q returned invalid candidate %q: %w", registered.config.ID, candidate.ID, err)
	}
	return nil
}

func cloneConfig(cfg Config) Config {
	clone := cfg
	if cfg.Parameters != nil {
		clone.Parameters = make(map[string]any, len(cfg.Parameters))
		for key, value := range cfg.Parameters {
			clone.Parameters[key] = cloneParameter(value)
		}
	}
	return clone
}

func cloneParameter(value any) any {
	switch typed := value.(type) {
	case map[string]any:
		clone := make(map[string]any, len(typed))
		for key, child := range typed {
			clone[key] = cloneParameter(child)
		}
		return clone
	case []any:
		clone := make([]any, len(typed))
		for index, child := range typed {
			clone[index] = cloneParameter(child)
		}
		return clone
	default:
		return value
	}
}
