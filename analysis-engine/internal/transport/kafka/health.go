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

	configured    bool
	connected     bool
	producerReady bool
	lastProduceAt time.Time
	lastError     error
	lastErrorAt   time.Time
}

// NewHealth returns a Health tracker. configured should be
// Config.Enabled — when false, Snapshot.Ready() always reports ready
// regardless of every other field (Kafka being off is a legitimate mode,
// not a degraded one; see Config.Validate's own doc comment).
func NewHealth(configured bool) *Health {
	return &Health{configured: configured}
}

func (h *Health) SetConnected(v bool)       { h.mu.Lock(); h.connected = v; h.mu.Unlock() }
func (h *Health) SetProducerReady(v bool)   { h.mu.Lock(); h.producerReady = v; h.mu.Unlock() }
func (h *Health) MarkProduced(at time.Time) { h.mu.Lock(); h.lastProduceAt = at; h.mu.Unlock() }

func (h *Health) MarkError(err error, at time.Time) {
	h.mu.Lock()
	h.lastError = err
	h.lastErrorAt = at
	h.mu.Unlock()
}

// Snapshot is a point-in-time, read-only copy — source task §36's
// minimum field set: configured, connected/reachable, producer readiness,
// last successful produce, and last error.
type Snapshot struct {
	Configured    bool
	Connected     bool
	ProducerReady bool
	LastProduceAt time.Time
	LastError     error
	LastErrorAt   time.Time
}

func (h *Health) Snapshot() Snapshot {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return Snapshot{
		Configured: h.configured, Connected: h.connected,
		ProducerReady: h.producerReady,
		LastProduceAt: h.lastProduceAt,
		LastError:     h.lastError, LastErrorAt: h.lastErrorAt,
	}
}

// Ready implements the readiness-vs-liveness distinction (source task
// §68): when Kafka is not configured, readiness never depends on it.
// When it is, the engine is only ready once transport is actually
// connected and its producer is ready. The composition root deliberately
// has no Kafka consumer in this architecture.
func (s Snapshot) Ready() bool {
	if !s.Configured {
		return true
	}
	return s.Connected && s.ProducerReady
}
