package redis

import (
	"fmt"
	"strings"
	"time"
)

// Config is resolved only from Configuration V3. It deliberately contains
// no environment-derived topology or feed fallback.
type Config struct {
	URL                    string
	BarsChannel            string
	ReconciliationInterval time.Duration
}

func (c Config) Validate() error {
	if strings.TrimSpace(c.URL) == "" {
		return fmt.Errorf("redis: runtime.redis.url is required")
	}
	if strings.TrimSpace(c.BarsChannel) == "" {
		return fmt.Errorf("redis: runtime.redis.bars_channel is required")
	}
	if c.ReconciliationInterval <= 0 {
		return fmt.Errorf("redis: runtime.redis.reconciliation_interval_seconds must be positive")
	}
	return nil
}
