package migrationaudit_test

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

// The tests below reproduce the migration audit's confirmed porting defects (`git show f02d62f:docs/architecture/python-go-migration-forensic-audit.md`).
// They are skipped unless requested. AUDIT_FINDINGS=1 MEASURES and logs (the suite stays green while the defects are open and nothing in
// production changes); AUDIT_STRICT=1 ASSERTS the Python behaviour and therefore fails until the defect is fixed.

func strict() bool { return os.Getenv("AUDIT_STRICT") == "1" }

// Each finding test replays a real capture (about 25 s), so they run only on request: AUDIT_FINDINGS=1 measures, AUDIT_STRICT=1 asserts.
func requested(t *testing.T) {
	t.Helper()
	if os.Getenv("AUDIT_FINDINGS") != "1" && !strict() {
		t.Skip("set AUDIT_FINDINGS=1 to measure or AUDIT_STRICT=1 to assert the Python behaviour")
	}
}

func replayFrames(t *testing.T, name string, visit func(e engine.Evaluation)) {
	t.Helper()
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe == market.M5 && e.BarTime%3600 == 0 && e.Context != nil {
			visit(e)
		}
	}}); err != nil {
		t.Fatal(err)
	}
}

// F-1: the profitable-week Key Level contract (LegacyFrame.SwingLevels) is the level book the frozen engine built with the
// INSTRUMENT's analysis.levels.minimum_key_touches (GBPJPY: 3). legacyread builds it with the base value (2).
func TestF1SwingLevelsHonourTheInstrumentMinimumKeyTouches(t *testing.T) {
	requested(t)
	const want = 3 // config/instruments.yml GBPJPY overrides.analysis.levels.minimum_key_touches
	below, total := 0, 0
	replayFrames(t, "replay-gbpjpy-production-capture-20261014.json", func(e engine.Evaluation) {
		frame := e.Context.Timeframes[market.M5].Legacy
		if frame == nil {
			return
		}
		for _, level := range frame.SwingLevels {
			total++
			if level.Touches < want {
				below++
			}
		}
	})
	t.Logf("GBPJPY SwingLevels: %d levels, %d below the instrument's %d-touch minimum", total, below, want)
	if strict() && below > 0 {
		t.Fatalf("%d of %d SwingLevels have fewer than %d touches; the frozen engine kept none", below, total, want)
	}
}

// F-2: the frozen engine scored the contract zones against the higher timeframes' zones ("HTF zone", +3). legacyread re-scores
// Zones and TechniqueZones with the higher-timeframe zones but never ContractZones.
func TestF2ContractZonesAreScoredAgainstHigherTimeframeZones(t *testing.T) {
	requested(t)
	var zonesWith, contractWith, contractTotal int
	replayFrames(t, "replay-eurusd-production-capture-20261014.json", func(e engine.Evaluation) {
		for _, tf := range []market.Timeframe{market.M5, market.M15} {
			frame := e.Context.Timeframes[tf].Legacy
			if frame == nil {
				continue
			}
			for _, z := range frame.Zones {
				for _, reason := range z.ScoreReasons {
					if reason == "HTF zone" {
						zonesWith++
					}
				}
			}
			for _, z := range frame.ContractZones {
				contractTotal++
				for _, reason := range z.ScoreReasons {
					if reason == "HTF zone" {
						contractWith++
					}
				}
			}
		}
	})
	t.Logf("\"HTF zone\" reasons: Zones %d, ContractZones %d (of %d contract zones)", zonesWith, contractWith, contractTotal)
	if strict() && zonesWith > 0 && contractWith == 0 {
		t.Fatalf("ContractZones never carry the HTF zone bonus although Zones do (%d)", zonesWith)
	}
}

// F-3 (cosmetic): score reasons embed a float printed without rounding ("round 1.1340000000000001"); the frozen engine printed "round 1.134".
func TestF3ScoreReasonsDoNotLeakFloatNoise(t *testing.T) {
	requested(t)
	noise := regexp.MustCompile(`\d\.\d{9,}`)
	var bad []string
	replayFrames(t, "replay-eurusd-production-capture-20261014.json", func(e engine.Evaluation) {
		frame := e.Context.Timeframes[market.M5].Legacy
		if frame == nil {
			return
		}
		for _, z := range frame.Zones {
			for _, reason := range z.ScoreReasons {
				if noise.MatchString(reason) && len(bad) < 5 {
					bad = append(bad, reason)
				}
			}
		}
	})
	t.Logf("float-noise reasons (first): %s", strings.Join(bad, "; "))
	if strict() && len(bad) > 0 {
		t.Fatal(fmt.Sprintf("score reasons leak float noise: %v", bad))
	}
}
