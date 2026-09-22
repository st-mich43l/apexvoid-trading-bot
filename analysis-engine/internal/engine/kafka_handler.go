package engine

import (
	"context"
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

// KafkaHandler implements kafka.Handler by routing a decoded, validated
// bar-closed event through the exact same Engine.Dispatch path any other
// caller (cmd/replay, a future live feed) uses — source task §42: "If
// the real analysis pipeline is not ready yet, provide a safe explicit
// transport handler/stub that validates and acknowledges accepted
// events without pretending to generate technical opportunities." This
// is deliberately NOT a stub: Engine.Dispatch already runs the real,
// tested Analysis Engine V2 pipeline (structure/liquidity/context) —
// what it does not do is fabricate an opportunity.Candidate, because no
// strategy exists yet to produce one for real (that is the next task).
//
// legal per docs/architecture/dependency-rules.md's third amendment:
// engine (rank 8) is the only package permitted to import
// internal/transport/kafka (rank 7).
type KafkaHandler struct {
	engine *Engine
}

// NewKafkaHandler wraps e for use as a kafka.Handler.
func NewKafkaHandler(e *Engine) *KafkaHandler {
	return &KafkaHandler{engine: e}
}

// HandleBarClosed dispatches event through Engine.Dispatch. An
// unregistered symbol is classified Permanent — Register happens once,
// at startup, from the configured live-instrument list
// (cmd/analysis-engine/main.go); a symbol absent from that list will
// never become registered later in this process's lifetime, so
// retrying is never going to help (source task §19's poison-message
// distinction applies to this condition just as much as a decode
// failure).
func (h *KafkaHandler) HandleBarClosed(ctx context.Context, event marketdata.BarEvent) error {
	if _, err := h.engine.Dispatch(event); err != nil {
		return kafka.Permanent(fmt.Errorf("engine: kafka handler: %w", err))
	}
	return nil
}

// HandleTick accepts and acknowledges a validated tick without
// pretending to process it — no tick pipeline exists anywhere in this
// codebase yet (source task §42's own "do not fabricate fake ... output
// just to demonstrate publishing" applies here too: a fabricated
// tick-driven recalculation would be worse than an honest no-op).
// Disabled by default (kafka.Config.TickConsumptionEnabled=false).
func (h *KafkaHandler) HandleTick(ctx context.Context, tick kafka.TickPayload) error {
	return nil
}
