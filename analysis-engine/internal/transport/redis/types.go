package redis

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
)

// Series is one independently cursor-tracked Redis bar stream.
type Series struct {
	Symbol    market.Symbol
	Timeframe market.Timeframe
	Depth     int
}

func (s Series) Key() string {
	return "bars:" + strings.ToUpper(string(s.Symbol)) + ":" + strings.ToUpper(string(s.Timeframe))
}

func (s Series) String() string { return string(s.Symbol) + ":" + string(s.Timeframe) }

func (s Series) Validate() error {
	if strings.TrimSpace(string(s.Symbol)) == "" {
		return fmt.Errorf("redis: series symbol is required")
	}
	if err := marketdata.ValidateTimeframe(s.Timeframe); err != nil {
		return fmt.Errorf("redis: series %s: %w", s.Symbol, err)
	}
	if s.Depth <= 0 {
		return fmt.Errorf("redis: series %s depth must be positive", s)
	}
	return nil
}

// BarDTO matches the real .NET RedisBar serialization in RedisBarSink.cs.
// It is intentionally transport-only; Dispatch receives marketdata.BarEvent.
type BarDTO struct {
	Time   int64   `json:"t"`
	Open   float64 `json:"o"`
	High   float64 `json:"h"`
	Low    float64 `json:"l"`
	Close  float64 `json:"c"`
	Volume float64 `json:"v"`
}

// DecodeBar decodes the exact .NET RedisBar JSON shape into the domain event
// used by Engine.Dispatch. It is exported for cross-language contract tests.
func DecodeBar(raw string, series Series) (marketdata.BarEvent, error) {
	decoder := json.NewDecoder(bytes.NewBufferString(raw))
	decoder.DisallowUnknownFields()
	var dto BarDTO
	if err := decoder.Decode(&dto); err != nil {
		return marketdata.BarEvent{}, fmt.Errorf("redis: decode %s: %w", series, err)
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		return marketdata.BarEvent{}, fmt.Errorf("redis: decode %s: trailing JSON data", series)
	}
	event := marketdata.BarEvent{
		Symbol: series.Symbol, Timeframe: series.Timeframe,
		Candle: market.Candle{Time: dto.Time, Open: dto.Open, High: dto.High, Low: dto.Low, Close: dto.Close, Volume: dto.Volume},
	}
	if err := marketdata.ValidateCandle(event.Candle); err != nil {
		return marketdata.BarEvent{}, fmt.Errorf("redis: bar %s: %w", series, err)
	}
	return event, nil
}

// SpotDTO matches RedisSpot from RedisBarSink.cs.
type SpotDTO struct {
	Bid float64 `json:"bid"`
	Ask float64 `json:"ask"`
	TS  int64   `json:"ts"`
}

func (s SpotDTO) Validate() error {
	if s.TS <= 0 || !market.Price(s.Bid).IsFinite() || !market.Price(s.Ask).IsFinite() || s.Bid <= 0 || s.Ask <= 0 || s.Ask < s.Bid {
		return fmt.Errorf("redis: invalid spot")
	}
	return nil
}

type Notification struct {
	Symbol    market.Symbol
	Timeframe market.Timeframe
	Timestamp int64
}

// parseNotification decodes existing `SYMBOL:TIMEFRAME:UNIX_SECONDS`
// bars:new messages. The message is only a wake-up, never candle content.
// ParseNotification validates the existing bars:new wake-up payload.
func ParseNotification(payload string) (Notification, error) {
	parts := strings.Split(payload, ":")
	if len(parts) != 3 || parts[0] == "" || parts[1] == "" || parts[2] == "" {
		return Notification{}, fmt.Errorf("redis: invalid bars notification %q", payload)
	}
	tf, err := market.ParseTimeframe(strings.ToUpper(parts[1]))
	if err != nil {
		return Notification{}, fmt.Errorf("redis: notification %q: %w", payload, err)
	}
	var timestamp int64
	if _, err := fmt.Sscan(parts[2], &timestamp); err != nil || timestamp <= 0 {
		return Notification{}, fmt.Errorf("redis: invalid notification timestamp in %q", payload)
	}
	return Notification{Symbol: market.Symbol(strings.ToUpper(parts[0])), Timeframe: tf, Timestamp: timestamp}, nil
}
