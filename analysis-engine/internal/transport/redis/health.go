package redis

import (
	"sync"
	"time"
)

// Health tracks market-data usability independently from Kafka. Readiness
// needs Redis connectivity, an active subscription and at least one parsed
// authoritative bar; liveness is intentionally process-level elsewhere.
type Health struct {
	mu                 sync.RWMutex
	configured         bool
	connected          bool
	subscriptionActive bool
	lastBarRead        time.Time
	lastNotification   time.Time
	lastRecovery       time.Time
	lastError          error
	lastErrorAt        time.Time
}

func NewHealth(configured bool) *Health         { return &Health{configured: configured} }
func (h *Health) SetConnected(v bool)           { h.mu.Lock(); h.connected = v; h.mu.Unlock() }
func (h *Health) SetSubscriptionActive(v bool)  { h.mu.Lock(); h.subscriptionActive = v; h.mu.Unlock() }
func (h *Health) MarkBarRead(at time.Time)      { h.mu.Lock(); h.lastBarRead = at; h.mu.Unlock() }
func (h *Health) MarkNotification(at time.Time) { h.mu.Lock(); h.lastNotification = at; h.mu.Unlock() }
func (h *Health) MarkRecovery(at time.Time)     { h.mu.Lock(); h.lastRecovery = at; h.mu.Unlock() }
func (h *Health) MarkError(err error, at time.Time) {
	h.mu.Lock()
	h.lastError = err
	h.lastErrorAt = at
	h.mu.Unlock()
}

type HealthSnapshot struct {
	Configured, Connected, SubscriptionActive                bool
	LastBarRead, LastNotification, LastRecovery, LastErrorAt time.Time
	LastError                                                error
}

func (h *Health) Snapshot() HealthSnapshot {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return HealthSnapshot{h.configured, h.connected, h.subscriptionActive, h.lastBarRead, h.lastNotification, h.lastRecovery, h.lastErrorAt, h.lastError}
}

func (s HealthSnapshot) Ready() bool {
	return s.Configured && s.Connected && s.SubscriptionActive && !s.LastBarRead.IsZero()
}
