// Package logging provides the analysis engine's structured runtime logger.
// Logs are JSON so container log collectors can index fields without
// scraping human-formatted strings. By default output goes to stderr only
// (docker logs); setting LOG_FILE_ENABLED also tees it to a daily-rotating
// file on a host-mounted directory, the same LOG_DIR/LOG_FILE_NAME/
// LOG_RETENTION_DAYS contract ctrader-engine and algo-bot already use, so
// this service's history survives container recreation instead of being
// lost with docker logs' own container-lifetime window - the exact gap
// that made the 2026-09-29 opportunity-flood incident hard to diagnose
// from this service's own logs.
package logging

import (
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// New creates a service logger. APEXVOID_LOG_LEVEL accepts debug, info, warn,
// or error and defaults to info. LOG_FILE_ENABLED (default true) additionally
// tees output to LOG_DIR/LOG_FILE_NAME (defaults /var/log/apexvoid/<service>.log),
// rotating at local midnight and pruning files older than LOG_RETENTION_DAYS
// (default 14). A file-open failure never blocks logging: it falls back to
// stderr-only and reports the failure on stderr once.
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
	writer := io.Writer(os.Stderr)
	if fileEnabled() {
		if fw := newDailyFileWriter(service); fw != nil {
			writer = io.MultiWriter(os.Stderr, fw)
		}
	}
	return slog.New(slog.NewJSONHandler(writer, &slog.HandlerOptions{Level: level})).With(
		"service", service,
	)
}

func fileEnabled() bool {
	raw := strings.ToLower(strings.TrimSpace(os.Getenv("LOG_FILE_ENABLED")))
	return raw != "0" && raw != "false" && raw != "no"
}

func envOr(key, fallback string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return fallback
}

func retentionDays() int {
	raw := strings.TrimSpace(os.Getenv("LOG_RETENTION_DAYS"))
	if raw == "" {
		return 14
	}
	if parsed, err := strconv.Atoi(raw); err == nil && parsed > 0 {
		return parsed
	}
	return 14
}

// dailyFileWriter rotates to a new dated file at local midnight and prunes
// files older than the configured retention window. One instance per
// process; New calls this once, so no cross-process coordination is needed.
type dailyFileWriter struct {
	mu        sync.Mutex
	dir       string
	baseName  string
	retention int
	day       string
	file      *os.File
}

func newDailyFileWriter(service string) *dailyFileWriter {
	dir := envOr("LOG_DIR", "/var/log/apexvoid")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		os.Stderr.WriteString("logging: LOG_DIR unavailable, file logging disabled: " + err.Error() + "\n")
		return nil
	}
	base := envOr("LOG_FILE_NAME", service+".log")
	w := &dailyFileWriter{dir: dir, baseName: base, retention: retentionDays()}
	if err := w.rotateLocked(time.Now()); err != nil {
		os.Stderr.WriteString("logging: could not open log file, file logging disabled: " + err.Error() + "\n")
		return nil
	}
	return w
}

func (w *dailyFileWriter) Write(p []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	now := time.Now()
	if today := now.Format("2006-01-02"); today != w.day {
		if err := w.rotateLocked(now); err != nil {
			// The already-open file (if any) still works; keep writing to it
			// rather than dropping the log line over a rotation failure.
			if w.file == nil {
				return len(p), nil
			}
		}
	}
	if w.file == nil {
		return len(p), nil
	}
	return w.file.Write(p)
}

// rotateLocked must be called with mu held. It opens (or reopens, appending)
// today's file and prunes files older than the retention window.
func (w *dailyFileWriter) rotateLocked(now time.Time) error {
	if w.file != nil {
		_ = w.file.Close()
		w.file = nil
	}
	day := now.Format("2006-01-02")
	path := filepath.Join(w.dir, w.baseName+"."+day)
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	w.file = f
	w.day = day
	w.prune(now)
	return nil
}

func (w *dailyFileWriter) prune(now time.Time) {
	if w.retention <= 0 {
		return
	}
	entries, err := os.ReadDir(w.dir)
	if err != nil {
		return
	}
	cutoff := now.AddDate(0, 0, -w.retention)
	prefix := w.baseName + "."
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasPrefix(entry.Name(), prefix) {
			continue
		}
		day := strings.TrimPrefix(entry.Name(), prefix)
		parsed, err := time.ParseInLocation("2006-01-02", day, now.Location())
		if err != nil || !parsed.Before(cutoff) {
			continue
		}
		_ = os.Remove(filepath.Join(w.dir, entry.Name()))
	}
}
