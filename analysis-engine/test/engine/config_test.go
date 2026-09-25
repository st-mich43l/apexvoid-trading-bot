package engine_test

import (
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
)

func TestZoneConfigFromConfigUsesCanonicalPhaseS3Leaves(t *testing.T) {
	repoRoot := filepath.Join("..", "..", "..")
	doc, err := config.ResolveDocument(filepath.Join(repoRoot, "config", "apexvoid.yml"))
	if err != nil {
		t.Fatalf("ResolveDocument: %v", err)
	}

	got, err := engine.ZoneConfigFromConfig(doc)
	if err != nil {
		t.Fatalf("ZoneConfigFromConfig: %v", err)
	}
	if got.Version != "v1" {
		t.Fatalf("Version = %q, want v1", got.Version)
	}
	if got.Displacement.RangeATR != 1.5 || got.Displacement.BodyDominance != 0.55 {
		t.Fatalf("Displacement = %+v, want range=1.5 body=0.55", got.Displacement)
	}
	if got.Lifecycle.InvalidationToleranceATR != 0.5 ||
		got.Lifecycle.SweepReclaimBars != 6 ||
		got.Lifecycle.MaxBreakEpisodes != 2 ||
		got.Lifecycle.RetestMaxTouches != 30 ||
		got.Lifecycle.EpsilonATR != 0.05 {
		t.Fatalf("Lifecycle = %+v, want canonical Phase S3 values", got.Lifecycle)
	}
	if got.Relevance.ImmediateATR != 0.25 ||
		got.Relevance.NearbyATR != 1.25 ||
		got.Relevance.RemoteATR != 3.0 {
		t.Fatalf("Relevance = %+v, want 0.25/1.25/3.0", got.Relevance)
	}
	if got.FlipAcceptBars != 2 || got.FlipBandBodyFraction != 0.5 ||
		got.FlipLevelBandATR != 0.05 {
		t.Fatalf("Flip config = %+v/%v/%v, want 2/0.5/0.05", got.FlipAcceptBars, got.FlipBandBodyFraction, got.FlipLevelBandATR)
	}
}
