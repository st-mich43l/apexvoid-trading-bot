package redis

import "sync"

const (
	MetricBarRead      = "redis_bar_read_total"
	MetricBarReadError = "redis_bar_read_error_total"
	MetricNotification = "redis_notification_total"
	MetricRecovery     = "redis_recovery_total"
	MetricRecoveredBar = "redis_recovered_bar_total"
	MetricDuplicateBar = "redis_duplicate_bar_total"
	MetricConflictBar  = "redis_conflict_bar_total"
	MetricBootstrapMS  = "redis_bootstrap_duration_ms"
	MetricRecoveryMS   = "redis_recovery_duration_ms"
)

// Metrics has bounded labels (symbol and timeframe originate only from
// configured Series) and is kept in-process until the service exporter lands.
type Metrics struct {
	mu        sync.Mutex
	counts    map[string]int64
	durations map[string]int64
}

func NewMetrics() *Metrics {
	return &Metrics{counts: map[string]int64{}, durations: map[string]int64{}}
}
func (m *Metrics) Count(name string, n int64) { m.mu.Lock(); m.counts[name] += n; m.mu.Unlock() }
func (m *Metrics) Duration(name string, ms int64) {
	m.mu.Lock()
	m.durations[name] += ms
	m.mu.Unlock()
}
func (m *Metrics) Snapshot() (map[string]int64, map[string]int64) {
	m.mu.Lock()
	defer m.mu.Unlock()
	counts, durations := make(map[string]int64, len(m.counts)), make(map[string]int64, len(m.durations))
	for k, v := range m.counts {
		counts[k] = v
	}
	for k, v := range m.durations {
		durations[k] = v
	}
	return counts, durations
}
