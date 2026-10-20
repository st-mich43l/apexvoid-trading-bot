package strategyutil

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// formationSource is a BUY technique source whose last bar (index 9) is a strong
// reclaim of the band [99, 100]. The instance is anchored at origin and may be
// touched from touchableFrom.
func formationSource(t *testing.T, origin, touchableFrom int) *TechniqueSource {
	t.Helper()
	d := detector(t, market.Buy, nil)
	in := techniquezone.Instance{
		Technique: TechniqueFVG, Side: "buy", Low: 99, High: 100, OriginIndex: origin, TouchableFrom: touchableFrom, ZoneScore: 12,
	}
	return &TechniqueSource{Detector: d, Instances: []techniquezone.Instance{in}, RetestMaxTouches: 30}
}

func TestAStructureIsNotConfirmedByTheBarsItWasBuiltOn(t *testing.T) {
	// The reclaim bar is bar 9. A structure completed by bar 9 (a fair value gap whose
	// third candle is bar 9, or whose impulse is bar 9) did not exist while that bar
	// was being rejected from it: it is the formation, not a reaction.
	if got := formationSource(t, 9, 10).Technique(TechniqueFVG); got != nil {
		t.Fatalf("the bar that completed the structure confirmed it: %+v", got.Confirmation)
	}
	// A structure completed earlier is reacted to by bar 9.
	got := formationSource(t, 5, 6).Technique(TechniqueFVG)
	if got == nil || got.Confirmation.ConfirmationTime != 3000 {
		t.Fatalf("a reaction after the formation must confirm it, got %+v", got)
	}
	// The frozen publishers (kept for the oracle golden) accepted the formation bar.
	frozen := formationSource(t, 9, 10)
	frozen.FrozenFormationReaction = true
	if frozen.Technique(TechniqueFVG) == nil {
		t.Fatal("the frozen oracle behaviour must remain reproducible")
	}
}

func TestAnUnrestrictedInstanceKeepsEveryBarEligible(t *testing.T) {
	// TouchableFrom 0 is "unknown": hand-built and legacy instances are not narrowed.
	if formationSource(t, 5, 0).Technique(TechniqueFVG) == nil {
		t.Fatal("an instance with no recorded formation boundary must still confirm")
	}
}

func TestHigherTimeframeFormationIsBoundAtTheCloseOfItsLastBar(t *testing.T) {
	// M5 bar i opens at (i+1)*300. The M15 formation bar opens at 3000 and closes
	// at 3900, so the first M5 bar that can touch the structure is the one opening
	// at 3900 (index 12), not the one opening at 3000 (index 9).
	m15Bars := []market.Candle{{Time: 60}, {Time: 3000}}
	ctx := m15Context(m15Bars, nil)
	execBars := ctx.Timeframes[market.M5].Legacy.Bars
	in := techniquezone.Instance{Technique: "supply_demand", Side: "buy", OriginIndex: 1, TouchableFrom: 2}
	if got := boundTouchableFrom(in, m15Bars, execBars, market.M15); got != 12 || execBars[got].Time != 3900 {
		t.Fatalf("touchable from %d", got)
	}
	in.TouchableFrom = 0
	if got := boundTouchableFrom(in, m15Bars, execBars, market.M15); got != 0 {
		t.Fatalf("an unrestricted instance stays unrestricted, got %d", got)
	}
}

func TestAZoneIsNotConfirmedByTheBreakThatMadeIt(t *testing.T) {
	// Bar 9 is a strong reclaim of [99, 100]. A flip zone whose structure break is
	// bar 9 was made by that bar; one broken at bar 6 is retested by it.
	d := detector(t, market.Buy, nil)
	zone := techniquezone.Zone{Bottom: 99, Top: 100, Side: "demand", OriginIndex: 9, BreakIndex: 9, Source: "flip_zone"}
	if conf := d.ZoneReaction(zone); conf != nil {
		t.Fatalf("the break bar confirmed its own flip zone: %+v", conf)
	}
	zone.OriginIndex, zone.BreakIndex = 6, 6
	if conf := d.ZoneReaction(zone); conf == nil || conf.ConfirmationIndex != 9 {
		t.Fatalf("a retest after the break must confirm, got %+v", conf)
	}
	// An order block whose break is -1 (none) is judged from its origin.
	zone.OriginIndex, zone.BreakIndex = 9, -1
	if conf := d.ZoneReaction(zone); conf != nil {
		t.Fatalf("the origin bar confirmed its own zone: %+v", conf)
	}
}

func TestPublishedFactorsNameTheEvidenceTheConfirmationShowed(t *testing.T) {
	source := formationSource(t, 5, 6)
	decision := source.Technique(TechniqueFVG)
	if decision == nil || decision.Confirmation.Type != "strong_reclaim" {
		t.Fatalf("fixture must confirm by a strong reclaim, got %+v", decision)
	}
	f := decision.Result.Confluence.Factors
	if f.WickRejection || f.DisplacementGrade || !f.StructuralAgreement {
		t.Fatalf("a strong reclaim is structural evidence only, got %+v", f)
	}
	frozen := formationSource(t, 5, 6)
	frozen.FrozenFormationReaction = true
	f = frozen.Technique(TechniqueFVG).Result.Confluence.Factors
	if !f.WickRejection || !f.StructuralAgreement || !f.DisplacementGrade {
		t.Fatalf("the oracle asserts all three, got %+v", f)
	}
}
