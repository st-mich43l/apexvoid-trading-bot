package structuralconfluence_test

import (
	"fmt"
	"reflect"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structuralconfluence"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// These are the structures the production engine actually held on real XAU M5
// bars (the committed production capture of 13 Oct 2026), derived exactly as the
// engine derives them. They are the facts behind the Confluence Zone v2 trades
// the forensic audit reconstructed (docs/strategies/confluence-zone-xau-forensic-audit.md).

const capture = "replay-xau-production-capture-20261013.json"

var minimumTwo = structuralconfluence.Params{MinimumFacts: 2, MinimumWidth: 0.1}

func factsAt(t *testing.T, bars []market.Candle, stamp string) []structuralconfluence.Fact {
	t.Helper()
	frame, instances := chain(windowAt(t, bars, stamp))
	return structuralconfluence.DeriveFacts(frame, instances)
}

func byPrefix(facts []structuralconfluence.Fact, prefix string) (structuralconfluence.Fact, bool) {
	for _, f := range facts {
		if len(f.ID) >= len(prefix) && f.ID[:len(prefix)] == prefix {
			return f, true
		}
	}
	return structuralconfluence.Fact{}, false
}

func TestRealOct12SupplyDemandAndOrderBlockAreOneDisplacement(t *testing.T) {
	// 12 Oct 17:40: the supply/demand zone and order block v2 counted as two
	// facts (and 7 retests twice) are cut from one displacement leg.
	facts := factsAt(t, loadM5(t, capture), "2026-10-12 17:40")
	sd, okSD := byPrefix(facts, "supply_demand:sell:")
	ob, okOB := byPrefix(facts, "order_block:sell:")
	if !okSD || !okOB || !sd.Verified || !ob.Verified {
		t.Fatalf("facts %+v", facts)
	}
	if !structuralconfluence.Dependent(sd, ob) || structuralconfluence.IndependentCount(facts) != 1 {
		t.Fatalf("one leg, two labels: %+v", facts)
	}
	regions, rejects := structuralconfluence.Find(facts, structuralconfluence.Sell, structuralconfluence.NoCutoff, minimumTwo)
	if len(regions) != 0 || len(rejects) != 1 || rejects[0].Reason != structuralconfluence.RejectSingleIndependentOrigin {
		t.Fatalf("regions %+v rejects %+v", regions, rejects)
	}
}

func TestRealOct13FVGAndItsInversionShareOneImpulse(t *testing.T) {
	// 13 Oct 01:15: the iFVG's inversion candle (00:00) is one of the candles of
	// the down leg that also made the FVG v2 paired it with.
	facts := factsAt(t, loadM5(t, capture), "2026-10-13 01:15")
	fvg, okF := byPrefix(facts, "fvg:sell:")
	ifvg, okI := byPrefix(facts, "ifvg:sell:")
	if !okF || !okI {
		t.Fatalf("facts %+v", facts)
	}
	if !structuralconfluence.Dependent(fvg, ifvg) {
		t.Fatalf("expected one displacement: %+v %+v", fvg.Atoms, ifvg.Atoms)
	}
	regions, _ := structuralconfluence.Find(facts, structuralconfluence.Sell, structuralconfluence.NoCutoff, minimumTwo)
	if len(regions) != 0 {
		t.Fatalf("the FVG and iFVG are one source, no confluence: %+v", regions)
	}
}

func TestRealOct9ZoneWasCreatedByTheReactionCandle(t *testing.T) {
	// 9 Oct 17:10 is the sweep_reclaim candle v2 confirmed on. The demand zone
	// it reacted to is cut from the up leg that STARTS on that very candle, so it
	// is not confirmable until the leg ends, after the touch.
	bars := loadM5(t, capture)
	facts := factsAt(t, bars, "2026-10-09 17:25")
	sd, ok := byPrefix(facts, "supply_demand:buy:1791565500") // origin 17:05
	if !ok {
		t.Fatalf("facts %+v", facts)
	}
	touch := at(t, "2026-10-09 17:10")
	if sd.AvailableAt <= touch {
		t.Fatalf("zone available %s, must be after the touch candle opens %s", timeStr(sd.AvailableAt), timeStr(touch))
	}
	other, ok := byPrefix(facts, "supply_demand:buy:1791563400") // an earlier, separate leg
	if !ok {
		t.Fatalf("facts %+v", facts)
	}
	regions, rejects := structuralconfluence.Find([]structuralconfluence.Fact{sd, other}, structuralconfluence.Buy, touch, minimumTwo)
	if len(regions) != 0 {
		t.Fatalf("a structure created by the reaction cannot support it: %+v", regions)
	}
	late := false
	for _, r := range rejects {
		if r.Reason == structuralconfluence.RejectNotYetAvailable && reflect.DeepEqual(r.FactIDs, []string{sd.ID}) {
			late = true
		}
	}
	if !late {
		t.Fatalf("want structure_not_yet_available for %s, got %+v", sd.ID, rejects)
	}
}

func TestRealOct5IFVGWasBornOnTheReactionCandle(t *testing.T) {
	// 5 Oct 14:05: the iFVG v2 treated as the second fact is inverted by the same
	// +66-pip candle that was the rejection. Seen from the candle that follows, it
	// is confirmable only once that candle has closed, which is after the touch.
	facts := factsAt(t, loadM5(t, capture), "2026-10-05 14:15")
	ifvg, ok := byPrefix(facts, "ifvg:buy:1791209100") // inverted at 14:05
	if !ok {
		t.Fatalf("facts %+v", facts)
	}
	touch := at(t, "2026-10-05 14:05")
	if ifvg.AvailableAt != touch+300 {
		t.Fatalf("available %s, want the close of the 14:05 candle", timeStr(ifvg.AvailableAt))
	}
	asOfTouch, rejects := structuralconfluence.Find(facts, structuralconfluence.Buy, touch, minimumTwo)
	for _, r := range asOfTouch {
		for _, id := range r.Support {
			if id == ifvg.ID {
				t.Fatalf("the iFVG supports a region it could not have been part of at the touch: %+v", r)
			}
		}
	}
	found := false
	for _, r := range rejects {
		if r.Reason == structuralconfluence.RejectNotYetAvailable {
			for _, id := range r.FactIDs {
				found = found || id == ifvg.ID
			}
		}
	}
	if !found {
		t.Fatalf("the iFVG must be reported as not yet available at the touch: %+v", rejects)
	}
}

func TestRealOct9ThreeSeparateOriginsAreThreeSources(t *testing.T) {
	// 9 Oct 13:10 is the one v2 trade whose three facts had three origins.
	facts := factsAt(t, loadM5(t, capture), "2026-10-09 13:10")
	if structuralconfluence.IndependentCount(facts) != 3 {
		t.Fatalf("want three independent sources, got %d: %+v", structuralconfluence.IndependentCount(facts), facts)
	}
	touch := at(t, "2026-10-09 13:05")
	regions, _ := structuralconfluence.Find(facts, structuralconfluence.Sell, touch, minimumTwo)
	if len(regions) != 1 {
		t.Fatalf("regions %+v", regions)
	}
	r := regions[0]
	// v2 published the 44.7-pip union [4178.77, 4183.24]; the supported region is
	// where at least two of the three structures overlap.
	if r.Low != 4179.75 || r.High != 4181.85 || r.MinGroups < 2 {
		t.Fatalf("region [%v,%v] min %d", r.Low, r.High, r.MinGroups)
	}
	if r.Width() >= 4183.24-4178.77 {
		t.Fatal("the region must be narrower than the union")
	}
}

func TestRealFactsDoNotDependOnWarmupHistory(t *testing.T) {
	// Loading more older history than the engine's window must not change a fact
	// the window already contains: same identity, provenance and availability.
	bars := loadM5(t, capture)
	for _, stamp := range []string{"2026-10-12 17:40", "2026-10-09 13:10", "2026-10-13 01:15"} {
		want := at(t, stamp)
		end := -1
		for i, b := range bars {
			if b.Time == want {
				end = i
			}
		}
		engineWindow := factsAt(t, bars, stamp)
		frame, instances := chain(bars[end+1-900 : end+1])
		longer := structuralconfluence.DeriveFacts(frame, instances)
		for _, f := range engineWindow {
			match, ok := byPrefix(longer, f.ID)
			if !ok {
				t.Fatalf("%s: %s vanished with more history", stamp, f.ID)
			}
			if match.AvailableAt != f.AvailableAt || !reflect.DeepEqual(match.Atoms, f.Atoms) || match.Verified != f.Verified {
				t.Fatalf("%s: %s changed with more history", stamp, f.ID)
			}
		}
	}
}

func TestRealFactsAreCausalAtEveryBar(t *testing.T) {
	// A fact's availability is never later than the last candle of the window it
	// was derived from, and a prefix replay reproduces it: appending future
	// candles does not change a fact already derived.
	bars := loadM5(t, capture)
	end := -1
	want := at(t, "2026-10-12 17:40")
	for i, b := range bars {
		if b.Time == want {
			end = i
		}
	}
	prefix := factsAt(t, bars, "2026-10-12 17:40")
	for _, f := range prefix {
		if f.AvailableAt > bars[end].Time+300 {
			t.Fatalf("%s available %s after the window ends", f.ID, timeStr(f.AvailableAt))
		}
	}
	// 40 further candles: the facts that existed must still have the same
	// identity, atoms and availability.
	frame, instances := chain(bars[end+1+40-400 : end+1+40])
	later := structuralconfluence.DeriveFacts(frame, instances)
	for _, f := range prefix {
		match, ok := byPrefix(later, f.ID)
		if !ok {
			continue // it may have been spent or mitigated since: that is not a change of the fact
		}
		if match.AvailableAt != f.AvailableAt || !reflect.DeepEqual(match.Atoms, f.Atoms) {
			t.Fatalf("%s changed when future candles were appended", f.ID)
		}
	}
}

func instanceKey(in techniquezone.Instance) string {
	return fmt.Sprintf("%s|%s|%d|%.6f|%.6f", in.Technique, in.Side, in.OriginIndex, in.Low, in.High)
}

func TestStructuresAreFactsIndependentOfWhereThePriceIsNow(t *testing.T) {
	// The frozen publishers' instances are only the structures price is at this
	// moment; the structural instances are every structure that is not spent or
	// invalidated. The first is always a subset of the second, and the second is
	// strictly larger once price has moved away from a zone.
	bars := loadM5(t, capture)
	chainSettings := techniquezone.ProductionChainSettings()
	proximal := techniquezone.ProductionTechniqueSettings()
	anywhere := proximal
	anywhere.IgnoreProximity = true
	larger, checked := 0, 0
	for _, stamp := range []string{"2026-10-05 14:15", "2026-10-06 03:20", "2026-10-09 13:10", "2026-10-09 17:25", "2026-10-12 17:40", "2026-10-13 01:15", "2026-10-13 02:00"} {
		w := windowAt(t, bars, stamp)
		near := techniquezone.TechniqueInstances(w, chainSettings, proximal)
		all := techniquezone.TechniqueInstances(w, chainSettings, anywhere)
		in := map[string]bool{}
		for _, a := range all {
			in[instanceKey(a)] = true
		}
		for _, n := range near {
			if !in[instanceKey(n)] {
				t.Fatalf("%s: %s is at the price now but missing from the structural instances", stamp, instanceKey(n))
			}
		}
		if len(all) > len(near) {
			larger++
		}
		checked++
	}
	if larger == 0 {
		t.Fatal("price proximity never excluded a structure on seven real windows: the test exercises nothing")
	}
	t.Logf("%d of %d windows hold structures price is not at", larger, checked)
}
