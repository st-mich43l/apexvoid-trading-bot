// Package fvg is the fair value gap strategy. It owns its whole mechanism: what a gap is and when it is dead (structure.go), how a reaction off it is
// confirmed (confirm.go), whether the entry is valid and the qualification (decision.go), and the candidate it publishes (candidate.go). It reads the
// market only through the closed bars and facts of the frames in the context.
package fvg

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"sync"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "fvg"
const Version = "v8"

const (
	structureVersion = "v2"
	liquidityVersion = "v1"
	zoneVersionUsed  = "v1"
	configVersion    = 3
)

// Strategy is the fair value gap strategy.
type Strategy struct {
	strategyutil.RefusalCounter
	cfg         Config
	fingerprint string

	mu       sync.Mutex
	refusals map[strategy.Refusal]int64
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("fvg: New called with strategy ID %q, want %q", cfg.ID, ID)
	}
	parsed, err := parseConfig(cfg.Parameters)
	if err != nil {
		return nil, fmt.Errorf("fvg: %w", err)
	}
	s := &Strategy{cfg: parsed, fingerprint: configFingerprint(cfg)}
	s.cfg.StopBudget.Refusals = &s.RefusalCounter
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

// RequiredTimeframes is M5 alone: the registry evaluates a strategy when one of its required frames closes, and a decision is made on a closed M5 bar. The
// structure frames are read from the context when they exist.
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

// Evaluate returns the confirmed reaction off the best gap of the structure frames (M15 and H1 by default). M5 confirms and executes; it never defines a
// gap.
func (s *Strategy) Evaluate(ctx *context.MarketContext) []opportunity.Candidate {
	base, ok := newView(ctx, &s.cfg)
	if !ok {
		return nil
	}
	refusals := map[strategy.Refusal]int64{}
	defer s.addRefusals(refusals)
	var instances []instance
	for _, tf := range s.cfg.StructureTimeframes {
		frameContext := ctx.Timeframes[tf]
		if frameContext == nil || frameContext.Legacy == nil {
			continue
		}
		found := s.structureInstances(frameContext.Legacy, tf, func(reason string) { refusals[strategy.Refusal{Reason: reason, Timeframe: tf}]++ })
		instances = append(instances, bind(tf, frameContext.Legacy.Bars, base.frame.Bars, found, s.cfg.BindWithinExecutionWindow)...)
	}
	d := s.best(base, instances, func(in instance, reason string) {
		refusals[strategy.Refusal{Reason: reason, Timeframe: in.Timeframe}]++
	})
	if d == nil {
		return nil
	}
	candidate, ok := s.candidate(candidateContext{symbol: ctx.Symbol}, d)
	if !ok {
		return nil
	}
	return []opportunity.Candidate{candidate}
}

func (s *Strategy) addRefusals(found map[strategy.Refusal]int64) {
	if len(found) == 0 {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.refusals == nil {
		s.refusals = map[strategy.Refusal]int64{}
	}
	for k, n := range found {
		s.refusals[k] += n
	}
}

// TakeRefusals returns the refusals counted since the last call and resets them (strategy.RefusalReporter).
func (s *Strategy) TakeRefusals() map[strategy.Refusal]int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := s.refusals
	s.refusals = nil
	return out
}

// configFingerprint is the strategy's version-pinned parameter digest.
func configFingerprint(cfg strategy.Config) string {
	keys := make([]string, 0, len(cfg.Parameters))
	for k := range cfg.Parameters {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	canonical := string(cfg.ID) + "\x00" + cfg.Version
	for _, k := range keys {
		canonical += "\x00" + k + "=" + fmt.Sprint(cfg.Parameters[k])
	}
	sum := sha256.Sum256([]byte(canonical))
	return hex.EncodeToString(sum[:16])
}
