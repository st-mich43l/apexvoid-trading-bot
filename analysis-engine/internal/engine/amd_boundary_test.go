package engine

import (
	"go/parser"
	"go/token"
	"io/fs"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

const madImport = "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/mad"

// AMD is supporting evidence, never a strategy input: no strategy, quality or arbitration code may import the mad package.
// Only the engine (which attaches the context) and the context builder (which computes it) may.
func TestNoStrategyQualityOrArbitrationCodeReadsAMD(t *testing.T) {
	for _, dir := range []string{"../strategy", "../strategyutil", "../quality", "../arbitration", "../confluence", "../reaction", "../opportunity"} {
		err := filepath.WalkDir(dir, func(path string, d fs.DirEntry, err error) error {
			if err != nil || d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
				return err
			}
			file, perr := parser.ParseFile(token.NewFileSet(), path, nil, parser.ImportsOnly)
			if perr != nil {
				return perr
			}
			for _, imp := range file.Imports {
				if strings.Trim(imp.Path.Value, `"`) == madImport {
					t.Errorf("%s imports the AMD package: AMD must not feed strategy validity, quality, arbitration or the confluence scorer", path)
				}
			}
			return nil
		})
		if err != nil {
			t.Fatal(err)
		}
	}
}

func TestTheShippedConfigKeepsRankingOffAndConfirmationOff(t *testing.T) {
	doc, err := config.ResolveDocument(configDir + "/apexvoid.yml")
	if err != nil {
		t.Fatal(err)
	}
	settings, err := LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if settings.RequireCandleConfirmation {
		t.Fatal("analysis.require_candle_confirmation must stay false")
	}
	if settings.Confluence.ScoringVersion != "v1" {
		t.Fatalf("the selected confluence stays v1 (AMD feeds only the unselected V2 telemetry), got %s", settings.Confluence.ScoringVersion)
	}
	if !settings.Arbitration.IgnoreQuality {
		t.Fatal("arbitration.quality_ranking must stay false (arbitration ignores quality)")
	}
}

// The Asia range follows the session calendar: London opens at 07:00 UTC in BST and 08:00 UTC in GMT, Asia at 22:00 UTC all year.
func TestAsiaRangeWindowFollowsDaylightSaving(t *testing.T) {
	doc, err := config.ResolveDocument(configDir + "/apexvoid.yml")
	if err != nil {
		t.Fatal(err)
	}
	settings, err := LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if settings.MAD.AsiaWindow == nil || settings.MAD.SequenceValidityHours <= 0 {
		t.Fatalf("MAD must carry the session calendar and its validity: %+v", settings.MAD)
	}
	for _, c := range []struct {
		name      string
		at        time.Time
		endHourUC int
	}{
		{"summer", time.Date(2026, time.July, 14, 3, 0, 0, 0, time.UTC), 7},
		{"winter", time.Date(2026, time.November, 17, 3, 0, 0, 0, time.UTC), 8},
	} {
		start, end, ok := settings.MAD.AsiaWindow(c.at.Unix())
		if !ok {
			t.Fatalf("%s: no window", c.name)
		}
		if got := time.Unix(start, 0).UTC().Hour(); got != 22 {
			t.Errorf("%s: Asia opens at %02d:00 UTC, want 22:00", c.name, got)
		}
		if got := time.Unix(end, 0).UTC().Hour(); got != c.endHourUC {
			t.Errorf("%s: Asia ends at %02d:00 UTC, want %02d:00", c.name, got, c.endHourUC)
		}
		if !(start <= c.at.Unix() && c.at.Unix() < end) {
			t.Errorf("%s: the window must contain the instant it was asked for", c.name)
		}
	}
}
