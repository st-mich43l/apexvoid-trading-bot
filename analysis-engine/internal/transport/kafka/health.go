package kafka

import (
	"sync"
	"time"
)

// Health is the Kafka transport's own liveness/readiness state (source
// task §36/§68) — distinguishes "the Go process is alive" from "Kafka
// is actually usable," which a bare process-alive check can never
// answer. One Health instance is shared between a Producer and Consumer
// wired to the same broker by the composition root (cmd/analysis-engine).
type Health struct {
	mu sync.RWMutex

	configured      bool
	connected       bool
	consumerRunning bool
	producerReady   bool
	lastConsumeAt   time.Time
	lastProduceAt   time.Time
	lastError       error
	lastErrorAt     time.Time
}

// NewHealth returns a Health tracker. configured should be
// Config.Enabled — when false, Snapshot.Ready() always reports ready
// regardless of every other field (Kafka being off is a legitimate mode,
// not a degraded one; see Config.Validate's own doc comment).
func NewHealth(configured bool) *Health {
	return &Health{configured: configured}
}

func (h *Health) SetConnected(v bool)       { h.mu.Lock(); h.connected = v; h.mu.Unlock() }
func (h *Health) SetConsumerRunning(v bool) { h.mu.Lock(); h.consumerRunning = v; h.mu.Unlock() }
func (h *Health) SetProducerReady(v bool)   { h.mu.Lock(); h.producerReady = v; h.mu.Unlock() }
func (h *Health) MarkConsumed(at time.Time) { h.mu.Lock(); h.lastConsumeAt = at; h.mu.Unlock() }
func (h *Health) MarkProduced(at time.Time) { h.mu.Lock(); h.lastProduceAt = at; h.mu.Unlock() }

func (h *Health) MarkError(err error, at time.Time) {
	h.mu.Lock()
	h.lastError = err
	h.lastErrorAt = at
	h.mu.Unlock()
}

// Snapshot is a point-in-time, read-only copy — source task §36's
// minimum field set: configured, connected/reachable, consumer running,
// producer ready, last successful consume, last successful produce,
// last error.
type Snapshot struct {
	Configured      bool
	Connected       bool
	ConsumerRunning bool
	ProducerReady   bool
	LastConsumeAt   time.Time
	LastProduceAt   time.Time
	LastError       error
	LastErrorAt     time.Time
}

func (h *Health) Snapshot() Snapshot {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return Snapshot{
		Configured: h.configured, Connected: h.connected,
		ConsumerRunning: h.consumerRunning, ProducerReady: h.producerReady,
		LastConsumeAt: h.lastConsumeAt, LastProduceAt: h.lastProduceAt,
		LastError: h.lastError, LastErrorAt: h.lastErrorAt,
	}
}

// Ready implements the readiness-vs-liveness distinction (source task
// §68): when Kafka is not configured, readiness never depends on it.
// When it is, the engine is only ready once transport is actually
// connected AND at least one side (consumer or producer) the
// composition root asked for is up — never "ready" merely because the
// Go process is alive.
func (s Snapshot) Ready() bool {
	if !s.Configured {
		return true
	}
	return s.Connected && (s.ConsumerRunning || s.ProducerReady)
}
