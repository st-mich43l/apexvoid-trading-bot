package logging

import (
	"log/slog"
	"os"
	"testing"
)

func TestNewHonoursConfiguredLevel(t *testing.T) {
	t.Setenv("APEXVOID_LOG_LEVEL", "debug")
	logger := New("test")
	if !logger.Enabled(nil, slog.LevelDebug) {
		t.Fatal("debug logging should be enabled")
	}
}

func TestNewDefaultsToInfo(t *testing.T) {
	if err := os.Unsetenv("APEXVOID_LOG_LEVEL"); err != nil {
		t.Fatal(err)
	}
	logger := New("test")
	if logger.Enabled(nil, slog.LevelDebug) {
		t.Fatal("debug logging should be disabled by default")
	}
	if !logger.Enabled(nil, slog.LevelInfo) {
		t.Fatal("info logging should be enabled by default")
	}
}
