package logging

import (
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestNewHonoursConfiguredLevel(t *testing.T) {
	t.Setenv("APEXVOID_LOG_LEVEL", "debug")
	t.Setenv("LOG_LEVEL", "")
	t.Setenv("LOG_FILE_ENABLED", "false")
	logger := New("test")
	if !logger.Enabled(nil, slog.LevelDebug) {
		t.Fatal("debug logging should be enabled")
	}
}

func TestNewHonoursCanonicalLogLevel(t *testing.T) {
	t.Setenv("APEXVOID_LOG_LEVEL", "")
	t.Setenv("LOG_LEVEL", "debug")
	t.Setenv("LOG_FILE_ENABLED", "false")
	logger := New("test")
	if !logger.Enabled(nil, slog.LevelDebug) {
		t.Fatal("debug logging should be enabled from LOG_LEVEL")
	}
}

func TestNewDefaultsToInfo(t *testing.T) {
	if err := os.Unsetenv("APEXVOID_LOG_LEVEL"); err != nil {
		t.Fatal(err)
	}
	if err := os.Unsetenv("LOG_LEVEL"); err != nil {
		t.Fatal(err)
	}
	t.Setenv("LOG_FILE_ENABLED", "false")
	logger := New("test")
	if logger.Enabled(nil, slog.LevelDebug) {
		t.Fatal("debug logging should be disabled by default")
	}
	if !logger.Enabled(nil, slog.LevelInfo) {
		t.Fatal("info logging should be enabled by default")
	}
}

func TestNewWritesToBothStderrAndTheDailyFileWhenEnabled(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("LOG_FILE_ENABLED", "true")
	t.Setenv("LOG_DIR", dir)
	t.Setenv("LOG_FILE_NAME", "svc.log")
	logger := New("svc")
	logger.Info("hello", "k", "v")

	today := time.Now().Format("2006-01-02")
	path := filepath.Join(dir, "svc.log."+today)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("expected a dated log file at %s: %v", path, err)
	}
	if !strings.Contains(string(data), "\"msg\":\"hello\"") || !strings.Contains(string(data), "\"k\":\"v\"") {
		t.Fatalf("log file missing expected JSON fields: %s", data)
	}
}

func TestNewNeverOpensAFileWhenDisabled(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("LOG_FILE_ENABLED", "false")
	t.Setenv("LOG_DIR", dir)
	New("svc").Info("hello")
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 0 {
		t.Fatalf("expected no files written when LOG_FILE_ENABLED=false, found %v", entries)
	}
}

func TestDailyFileWriterPrunesFilesOlderThanRetention(t *testing.T) {
	dir := t.TempDir()
	w := &dailyFileWriter{dir: dir, baseName: "svc.log", retention: 3}
	now := time.Date(2026, 9, 29, 12, 0, 0, 0, time.UTC)

	old := filepath.Join(dir, "svc.log.2026-09-20")
	recent := filepath.Join(dir, "svc.log.2026-09-28")
	if err := os.WriteFile(old, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(recent, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}

	w.prune(now)

	if _, err := os.Stat(old); !os.IsNotExist(err) {
		t.Fatalf("expected %s to be pruned, stat err=%v", old, err)
	}
	if _, err := os.Stat(recent); err != nil {
		t.Fatalf("expected %s to survive pruning: %v", recent, err)
	}
}
