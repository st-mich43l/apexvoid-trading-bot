package migrationaudit_test

import (
	"encoding/json"
	"math"
	"os"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
)

type confluenceCase struct {
	HTF, Wick, Disp, Session, Structural, Fib, CHoCH bool
	Touches                                          int
	ZoneScore                                        float64 `json:"zone_score"`
	ZoneTouches                                      int     `json:"zone_touches"`
	MAD                                              float64
	V1                                               int     `json:"v1"`
	V2Raw                                            float64 `json:"v2_raw"`
	V2                                               int     `json:"v2"`
}

// TestConfluenceMatchesTheHistoricalPythonOnEveryFactorCombination replays the exhaustive factor grid produced by
// oracle_confluence.py from the frozen Python and demands identical v1 stars, v2 raw score and v2 stars.
func TestConfluenceMatchesTheHistoricalPythonOnEveryFactorCombination(t *testing.T) {
	path := os.Getenv("AUDIT_CONFLUENCE_ORACLE")
	if path == "" {
		t.Skip("AUDIT_CONFLUENCE_ORACLE not set")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Constants map[string]float64 `json:"constants"`
		Cases     []confluenceCase   `json:"cases"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	cfg := confluence.Config{ScoringVersion: "v1", StarThreeRatio: 0.585, StarTwoRatio: 0.390, ZoneQualityWeight: 4.0, MADScoreWeight: 2.0, FibonacciWeight: 2.5}
	mismatches, maxErr := 0, 0.0
	for i, c := range doc.Cases {
		f := confluence.Factors{HTFAligned: c.HTF, Touches: c.Touches, WickRejection: c.Wick, DisplacementGrade: c.Disp, SessionContext: c.Session,
			StructuralAgreement: c.Structural, FibTouch: c.Fib, CHoCH: c.CHoCH}
		got := confluence.Evaluate(confluence.ZoneQuality{Score: c.ZoneScore, Touches: c.ZoneTouches}, f, cfg, c.MAD)
		maxErr = math.Max(maxErr, math.Abs(got.V2Raw-c.V2Raw))
		if got.V1Stars != c.V1 || got.V2Stars != c.V2 || math.Abs(got.V2Raw-c.V2Raw) > 1e-9 {
			mismatches++
			if mismatches <= 5 {
				t.Errorf("case %d %+v: go v1=%d v2=%d raw=%v, python v1=%d v2=%d raw=%v", i, c, got.V1Stars, got.V2Stars, got.V2Raw, c.V1, c.V2, c.V2Raw)
			}
		}
	}
	t.Logf("%d factor combinations; %d mismatches; max |v2_raw error| = %g; python constants %v", len(doc.Cases), mismatches, maxErr, doc.Constants)
	if mismatches > 0 {
		t.Fatalf("%d of %d combinations differ", mismatches, len(doc.Cases))
	}
}
