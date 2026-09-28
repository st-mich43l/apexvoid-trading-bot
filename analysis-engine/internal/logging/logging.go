// Package logging provides the analysis engine's structured runtime logger.
// Logs are JSON on stderr so container log collectors can index fields
// without scraping human-formatted strings.
package logging

import (
	"log/slog"
	"os"
	"strings"
)

// New creates a service logger. APEXVOID_LOG_LEVEL accepts debug, info, warn,
// or error and defaults to info.
func New(service string) *slog.Logger {
	level := slog.LevelInfo
	switch strings.ToLower(strings.TrimSpace(os.Getenv("APEXVOID_LOG_LEVEL"))) {
	case "debug":
		level = slog.LevelDebug
	case "warn", "warning":
		level = slog.LevelWarn
	case "error":
		level = slog.LevelError
	}
	return slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: level})).With(
		"service", service,
	)
}
