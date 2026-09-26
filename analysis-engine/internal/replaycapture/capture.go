// Package replaycapture turns one immutable, real multi-timeframe closed-bar
// capture into the chronological event stream a live feed would have
// delivered, and encodes discovered opportunities exactly as the Kafka
// producer would (S14C). It exists so the Go engine and the legacy Python
// detectors can be replayed from the *same* bytes; it never invents a bar.
//
// Rules that keep the replay honest:
//
//   - a bar is delivered only at its close (open + timeframe minutes), never
//     earlier, so no strategy can see a bar that had not closed;
//   - at the same close instant a higher timeframe is delivered before a lower
//     one, so an M5 bar closing on the hour sees the H1 bar that closed with it
//     (the live ordering guarantee the higher-timeframe freshness rule relies on);
//   - H4 is derived from H1 (UTC-aligned, complete buckets only) when the
//     capture has none, and the report labels it derived;
//   - malformed input (unordered, duplicate or non-OHLC-consistent bars) is an
//     error, never repaired.
package replaycapture

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
)

// Capture mirrors contracts/analysis/replay/*.json.
type Capture struct {
	Version     int                    `json:"version"`
	Description string                 `json:"description"`
	Symbol      market.Symbol          `json:"symbol"`
	Provenance  json.RawMessage        `json:"provenance"`
	Columns     []string               `json:"columns"`
	Timeframes  map[string][][]float64 `json:"timeframes"`
}

var wantColumns = []string{"t", "open", "high", "low", "close", "volume"}

// Load reads and validates a capture file.
func Load(path string) (*Capture, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var c Capture
	if err := json.Unmarshal(raw, &c); err != nil {
		return nil, fmt.Errorf("replaycapture: %s: %w", path, err)
	}
	if c.Version != 1 {
		return nil, fmt.Errorf("replaycapture: unsupported capture version %d", c.Version)
	}
	if c.Symbol == "" || len(c.Timeframes) == 0 {
		return nil, fmt.Errorf("replaycapture: capture needs a symbol and at least one timeframe")
	}
	if len(c.Columns) != len(wantColumns) {
		return nil, fmt.Errorf("replaycapture: columns must be %v", wantColumns)
	}
	for i, name := range wantColumns {
		if c.Columns[i] != name {
			return nil, fmt.Errorf("replaycapture: columns must be %v", wantColumns)
		}
	}
	for name := range c.Timeframes {
		if _, err := c.Candles(market.Timeframe(name)); err != nil {
			return nil, err
		}
	}
	return &c, nil
}

// Candles returns one timeframe's bars, oldest first, after validation.
func (c *Capture) Candles(tf market.Timeframe) ([]market.Candle, error) {
	rows, ok := c.Timeframes[string(tf)]
	if !ok {
		return nil, nil
	}
	minutes, valid := tf.Minutes()
	if !valid {
		return nil, fmt.Errorf("replaycapture: unknown timeframe %q", tf)
	}
	out := make([]market.Candle, 0, len(rows))
	for i, r := range rows {
		if len(r) != len(wantColumns) {
			return nil, fmt.Errorf("replaycapture: %s row %d has %d columns", tf, i, len(r))
		}
		candle := market.Candle{Time: int64(r[0]), Open: r[1], High: r[2], Low: r[3], Close: r[4], Volume: r[5]}
		if candle.High < candle.Low || candle.High < candle.Open || candle.High < candle.Close || candle.Low > candle.Open || candle.Low > candle.Close || candle.Low <= 0 {
			return nil, fmt.Errorf("replaycapture: %s bar %d at t=%d is not OHLC-consistent", tf, i, candle.Time)
		}
		if candle.Time%int64(minutes*60) != 0 {
			return nil, fmt.Errorf("replaycapture: %s bar %d at t=%d is not aligned to its timeframe", tf, i, candle.Time)
		}
		if i > 0 && candle.Time <= out[i-1].Time {
			return nil, fmt.Errorf("replaycapture: %s bars must be strictly increasing (t=%d after t=%d)", tf, candle.Time, out[i-1].Time)
		}
		out = append(out, candle)
	}
	return out, nil
}

// DeriveH4 builds UTC-aligned H4 bars (00,04,08,... UTC) from H1, keeping only
// buckets with all four H1 bars present. The alignment is an assumption about
// the broker's H4 boundaries and is reported as such by callers.
func DeriveH4(h1 []market.Candle) []market.Candle {
	const bucket = int64(4 * 3600)
	var out []market.Candle
	i := 0
	for i < len(h1) {
		start := h1[i].Time - h1[i].Time%bucket
		j := i
		for j < len(h1) && h1[j].Time-h1[j].Time%bucket == start {
			j++
		}
		group := h1[i:j]
		if len(group) == 4 && group[0].Time == start {
			candle := market.Candle{Time: start, Open: group[0].Open, High: group[0].High, Low: group[0].Low, Close: group[3].Close}
			for _, bar := range group {
				if bar.High > candle.High {
					candle.High = bar.High
				}
				if bar.Low < candle.Low {
					candle.Low = bar.Low
				}
				candle.Volume += bar.Volume
			}
			out = append(out, candle)
		}
		i = j
	}
	return out
}

// Events merges the given timeframes into one causal stream: ordered by close
// instant, higher timeframe first on ties, then by open time.
func Events(symbol market.Symbol, byTimeframe map[market.Timeframe][]market.Candle, origin marketdata.EventOrigin) ([]marketdata.BarEvent, error) {
	type item struct {
		closeAt int64
		minutes int
		event   marketdata.BarEvent
	}
	var items []item
	for tf, candles := range byTimeframe {
		minutes, ok := tf.Minutes()
		if !ok {
			return nil, fmt.Errorf("replaycapture: unknown timeframe %q", tf)
		}
		for _, candle := range candles {
			items = append(items, item{
				closeAt: candle.Time + int64(minutes)*60, minutes: minutes,
				event: marketdata.BarEvent{Symbol: symbol, Timeframe: tf, Candle: candle, Origin: origin},
			})
		}
	}
	sort.SliceStable(items, func(a, b int) bool {
		if items[a].closeAt != items[b].closeAt {
			return items[a].closeAt < items[b].closeAt
		}
		if items[a].minutes != items[b].minutes {
			return items[a].minutes > items[b].minutes
		}
		return items[a].event.Candle.Time < items[b].event.Candle.Time
	})
	events := make([]marketdata.BarEvent, len(items))
	for i, it := range items {
		events[i] = it.event
	}
	return events, nil
}
