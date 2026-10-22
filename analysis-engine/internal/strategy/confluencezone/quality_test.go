package confluencezone

import (
	"strings"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structuralconfluence"
)

func buySetup(groups int) Setup {
	return Setup{
		Direction: market.Buy, Region: structuralconfluence.Region{Groups: make([]structuralconfluence.Group, groups)},
		EntryLow: 100, EntryHigh: 100.5, Stop: 99, Target: 103.8, ATR: 1,
		OverlapWidthATR: 0.5, OverlapIntegrity: 1, SourceIntegrity: 1,
		Reaction: Reaction{CloseStrength: 0.9, WickRatio: 0.55},
	}
}

func scoreCZ(t *testing.T, s Setup, price float64, trend string, choch, htf bool) (float64, map[string]float64) {
	t.Helper()
	d := &strategyutil.LegacyDetector{Frame: &analysiscontext.LegacyFrame{Structure: trend}, Direction: s.Direction, Price: price, ATR: s.ATR}
	m := quality.New(QualityModel)
	scoreQuality(m, s, d, choch, htf, 3)
	if err := quality.Verify(m.Overall(), m.Components()); err != nil {
		t.Fatal(err)
	}
	return m.Overall(), m.Components()
}

func TestStrongMediumAndWeakConfluenceZonesScoreDifferently(t *testing.T) {
	strong := buySetup(4)

	medium := buySetup(3)
	medium.OverlapWidthATR, medium.OverlapIntegrity, medium.SourceIntegrity = 1.6, 0.8, 0.7
	medium.Reaction = Reaction{CloseStrength: 0.7, WickRatio: 0.3}
	medium.Target = 102.6

	weak := buySetup(2)
	weak.OverlapWidthATR, weak.OverlapIntegrity, weak.SourceIntegrity = 3.4, 0.3, 0.2
	weak.Reaction = Reaction{CloseStrength: 0.5, WickRatio: 0.1}
	weak.Target = 100.6

	s, _ := scoreCZ(t, strong, 100.6, "up", false, true)
	m, _ := scoreCZ(t, medium, 101.3, "range", false, false)
	w, wc := scoreCZ(t, weak, 102, "down", false, false)
	if !(s > m && m > w) {
		t.Fatalf("strong %.3f, medium %.3f, weak %.3f must be ordered", s, m, w)
	}
	if s < 0.8 || m < 0.25 || m > 0.75 || w > 0.3 {
		t.Fatalf("scores strong=%.3f medium=%.3f weak=%.3f outside the documented bands (weak: %v)", s, m, w, wc)
	}
}

func TestStructuresSharingAnOriginAreOneSourceAndAHairlineOverlapIsNotPrecise(t *testing.T) {
	// The setup already groups dependent structures; three facts in one group are one source.
	one := buySetup(1)
	one.Region.Groups = []structuralconfluence.Group{{Representative: "a", Members: []string{"a", "b", "c"}}}
	_, c := scoreCZ(t, one, 100.6, "up", false, true)
	if c["score_source_breadth"] != 0 {
		t.Fatalf("three facts in one group are one source, not breadth: %v", c["score_source_breadth"])
	}
	hair := buySetup(3)
	hair.OverlapWidthATR = 0.02
	if _, c := scoreCZ(t, hair, 100.6, "up", false, true); c["score_overlap_precision"] != 0 {
		t.Fatalf("a hairline overlap: %v", c["score_overlap_precision"])
	}
}

func TestConfluenceZoneHasNoConstantDimensionAndMissingEvidenceEarnsNothing(t *testing.T) {
	s := buySetup(3)
	s.ATR = 0
	s.Target = s.EntryHigh
	s.OverlapIntegrity, s.SourceIntegrity = -1, -1
	_, c := scoreCZ(t, s, 100.6, "up", false, true)
	for _, name := range []string{"score_entry_location", "score_room", "score_structure_integrity"} {
		if c[name] != 0 {
			t.Errorf("%s = %v without evidence", name, c[name])
		}
	}
	n := 0
	for name := range c {
		if strings.HasPrefix(name, "score_") {
			n++
		}
	}
	if n != 7 {
		t.Fatalf("seven dimensions, got %d", n)
	}
}
