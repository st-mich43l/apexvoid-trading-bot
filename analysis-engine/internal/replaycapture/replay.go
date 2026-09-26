package replaycapture

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

// Result is everything one replay observed, in dispatch order.
type Result struct {
	Symbol     market.Symbol
	Primary    market.Timeframe
	Events     int
	Timeframes int
	// DerivedH4 is true when H4 was built from H1 rather than captured.
	DerivedH4 bool
	Options   EnvelopeOptions
	// Discovered lists each opportunity once, at the closed bar that first
	// showed it, in dispatch order.
	Discovered []opportunity.Candidate
}

// Options tunes a replay without changing what the engine sees by default.
type Options struct {
	// DeriveH4 builds H4 from H1 (UTC-aligned) when the capture has none. Off by
	// default because the production feed delivers no H4 at all
	// (config/trading-bot.yml: the live trendbar subscription supports only
	// M1/M5/M15/M30/H1), so the faithful replay has H1 as its only higher
	// timeframe. Turn it on only to explore what H4 would add.
	DeriveH4 bool
}

// Replay dispatches a capture through the exact engine path a live feed uses
// and returns every opportunity it discovered. Two calls on the same inputs
// return identical results (no clock, no randomness, ordered iteration).
func Replay(doc *config.Document, capture *Capture, primary market.Timeframe, opts Options) (*Result, error) {
	settings, err := engine.LoadSettings(doc, primary, false)
	if err != nil {
		return nil, fmt.Errorf("loading Analysis Engine V2 settings: %w", err)
	}
	byTF := make(map[market.Timeframe][]market.Candle)
	for name := range capture.Timeframes {
		tf := market.Timeframe(name)
		candles, err := capture.Candles(tf)
		if err != nil {
			return nil, err
		}
		byTF[tf] = candles
	}
	derivedH4 := false
	if _, ok := byTF[market.H4]; opts.DeriveH4 && !ok && len(byTF[market.H1]) > 0 {
		byTF[market.H4] = DeriveH4(byTF[market.H1])
		derivedH4 = true
	}
	if len(byTF[primary]) == 0 {
		return nil, fmt.Errorf("capture has no %s bars", primary)
	}
	events, err := Events(capture.Symbol, byTF, marketdata.EventOriginReplay)
	if err != nil {
		return nil, err
	}
	raw, err := json.Marshal(doc.Raw())
	if err != nil {
		return nil, fmt.Errorf("fingerprinting config: %w", err)
	}
	sum := sha256.Sum256(raw)
	result := &Result{
		Symbol: capture.Symbol, Primary: primary, Events: len(events), Timeframes: len(byTF), DerivedH4: derivedH4,
		Options: EnvelopeOptions{
			Algorithm:         kafka.AlgorithmVersion{Structure: settings.Structure.Version, Liquidity: settings.Liquidity.Version},
			ConfigVersion:     3,
			ConfigFingerprint: hex.EncodeToString(sum[:]),
		},
	}
	e := engine.NewEngine(nil)
	if err := e.Register(capture.Symbol, settings); err != nil {
		return nil, fmt.Errorf("registering %s: %w", capture.Symbol, err)
	}
	seen := make(map[string]struct{})
	for _, event := range events {
		snap, err := e.Dispatch(event)
		if err != nil {
			return nil, fmt.Errorf("dispatching %s bar at t=%d: %w", event.Timeframe, event.Candle.Time, err)
		}
		for _, candidate := range snap.Opportunities {
			if _, exists := seen[candidate.ID]; exists {
				continue
			}
			seen[candidate.ID] = struct{}{}
			result.Discovered = append(result.Discovered, candidate)
		}
	}
	return result, nil
}
