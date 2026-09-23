package engine

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/demand"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/flipzone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/fvg"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/orderblock"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/sessionlevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/supply"
)

// strategyFactories is Phase S8's composition root — the one place in
// this module that imports a concrete strategy subpackage. internal/
// strategy's own Registry deliberately never does this (see registry.go's
// doc comment: "the registry itself never imports a strategy
// subpackage"), and no other package is permitted to either: engine is
// rank 8, the only rank strictly above every strategy/<name> subpackage's
// own rank 7 (test/architecture/dependency_test.go), so it is the only
// package that CAN import them.
//
// This is a fixed, compile-time-known set, not runtime configuration — a
// catalog entry with `enabled: true` here and no matching key below fails
// analysis-engine startup via strategy.NewRegistry's own fail-closed rule
// ("enabled but has no registered implementation"), never silently
// becomes a no-op. Adding a Phase S7-style strategy package later means
// adding one line here, nothing else in this file changes.
var strategyFactories = map[strategy.StrategyID]strategy.Factory{
	supply.ID:       supply.New,
	demand.ID:       demand.New,
	orderblock.ID:   orderblock.New,
	fvg.ID:          fvg.New,
	flipzone.ID:     flipzone.New,
	keylevel.ID:     keylevel.New,
	sessionlevel.ID: sessionlevel.New,
}
