package structuralconfluence

import (
	"fmt"
	"math/rand"
	"reflect"
	"sort"
	"testing"
)

// fact builds a verified fact that became confirmable at the given time.
func fact(id, side string, low, high float64, avail int64, atoms ...string) Fact {
	return Fact{ID: id, Source: SourceSupplyDemand, Side: side, Low: low, High: high, StructuralLow: low, StructuralHigh: high, AvailableAt: avail, Atoms: atoms, Verified: true}
}

var two = Params{MinimumFacts: 2, MinimumWidth: 0}

func single(t *testing.T, regions []Region) Region {
	t.Helper()
	if len(regions) != 1 {
		t.Fatalf("want exactly one region, got %d: %+v", len(regions), regions)
	}
	return regions[0]
}

func TestUnionIsNotTheIntersection(t *testing.T) {
	// The required negative example: A 4100-4105 and B 4103-4108.
	facts := []Fact{fact("A", Buy, 4100, 4105, 10, "a"), fact("B", Buy, 4103, 4108, 10, "b")}
	regions, _ := Find(facts, Buy, NoCutoff, two)
	r := single(t, regions)
	if r.Low != 4103 || r.High != 4105 {
		t.Fatalf("region = [%v, %v], want the intersection [4103, 4105], not the union [4100, 4108]", r.Low, r.High)
	}
	if r.Contains(4101) {
		t.Fatal("a reaction at 4101 is outside the A+B overlap and must not be confirmation of it")
	}
	if !r.Contains(4104) {
		t.Fatal("a reaction at 4104 is inside the A+B overlap")
	}
	if r.MinGroups != 2 || len(r.Groups) != 2 {
		t.Fatalf("want two independent groups, got min %d groups %v", r.MinGroups, r.Groups)
	}
}

func TestSharedBoundaryHasNoWidth(t *testing.T) {
	facts := []Fact{fact("A", Sell, 4100, 4105, 10, "a"), fact("B", Sell, 4105, 4110, 10, "b")}
	regions, rejects := Find(facts, Sell, NoCutoff, two)
	if len(regions) != 0 {
		t.Fatalf("zones that merely touch intersect in a single price, not a tradable region: %+v", regions)
	}
	for _, r := range rejects {
		if r.Reason == RejectOverlapTooNarrow {
			t.Fatalf("a shared edge is not even an overlap: %+v", r)
		}
	}
}

func TestMinimumWidthIsStrict(t *testing.T) {
	facts := []Fact{fact("A", Buy, 100, 105, 10, "a"), fact("B", Buy, 104, 110, 10, "b")} // overlap width exactly 1
	regions, rejects := Find(facts, Buy, NoCutoff, Params{MinimumFacts: 2, MinimumWidth: 1})
	if len(regions) != 0 {
		t.Fatalf("width == minimum must be rejected: %+v", regions)
	}
	if len(rejects) != 1 || rejects[0].Reason != RejectOverlapTooNarrow || rejects[0].Low != 104 || rejects[0].High != 105 {
		t.Fatalf("want one overlap_too_narrow [104,105], got %+v", rejects)
	}
	regions, _ = Find(facts, Buy, NoCutoff, Params{MinimumFacts: 2, MinimumWidth: 0.999})
	single(t, regions)
}

func TestExtendedChainDoesNotNeedOneCommonIntersection(t *testing.T) {
	// A 4100-4105, B 4103-4108, C 4107-4112: A and C never meet. The chain has
	// two supported regions, [4103,4105] (A,B) and [4107,4108] (B,C), not zero.
	facts := []Fact{fact("A", Buy, 4100, 4105, 10, "a"), fact("B", Buy, 4103, 4108, 10, "b"), fact("C", Buy, 4107, 4112, 10, "c")}
	regions, _ := Find(facts, Buy, NoCutoff, two)
	if len(regions) != 2 {
		t.Fatalf("want two regions, got %+v", regions)
	}
	if regions[0].Low != 4103 || regions[0].High != 4105 || regions[1].Low != 4107 || regions[1].High != 4108 {
		t.Fatalf("regions = [%v,%v] [%v,%v]", regions[0].Low, regions[0].High, regions[1].Low, regions[1].High)
	}
	if !reflect.DeepEqual(regions[0].Support, []string{"A", "B"}) || !reflect.DeepEqual(regions[1].Support, []string{"B", "C"}) {
		t.Fatalf("support sets %v %v", regions[0].Support, regions[1].Support)
	}
}

func TestContiguousSupportIsOneRegionWithVaryingSubsets(t *testing.T) {
	// A 100-106, B 102-108, C 104-110: every price in [102,108] has two or three
	// independent supporters although A and C only meet in [104,106].
	facts := []Fact{fact("A", Buy, 100, 106, 10, "a"), fact("B", Buy, 102, 108, 10, "b"), fact("C", Buy, 104, 110, 10, "c")}
	regions, _ := Find(facts, Buy, NoCutoff, two)
	r := single(t, regions)
	if r.Low != 102 || r.High != 108 || r.MinGroups != 2 {
		t.Fatalf("region [%v,%v] min %d, want [102,108] with at least two everywhere", r.Low, r.High, r.MinGroups)
	}
	var three []Segment
	for _, s := range r.Segments {
		if s.Groups == 3 {
			three = append(three, s)
		}
	}
	if len(three) != 1 || three[0].Low != 104 || three[0].High != 106 {
		t.Fatalf("the triple support must be exactly [104,106], got %+v", three)
	}
}

func TestNestedTrueIntersections(t *testing.T) {
	facts := []Fact{fact("outer", Sell, 100, 110, 10, "o"), fact("mid", Sell, 102, 108, 10, "m"), fact("inner", Sell, 104, 106, 10, "i")}
	r := single(t, func() []Region { r, _ := Find(facts, Sell, NoCutoff, two); return r }())
	if r.Low != 102 || r.High != 108 {
		t.Fatalf("two independent supporters cover [102,108], got [%v,%v]", r.Low, r.High)
	}
	three := Params{MinimumFacts: 3}
	r3, _ := Find(facts, Sell, NoCutoff, three)
	got := single(t, r3)
	if got.Low != 104 || got.High != 106 {
		t.Fatalf("three independent supporters cover only the innermost [104,106], got [%v,%v]", got.Low, got.High)
	}
}

func TestSeveralDisjointRegionsAreAllReturned(t *testing.T) {
	facts := []Fact{
		fact("A", Buy, 100, 104, 10, "a"), fact("B", Buy, 102, 106, 10, "b"),
		fact("C", Buy, 200, 204, 10, "c"), fact("D", Buy, 203, 207, 10, "d"),
	}
	regions, _ := Find(facts, Buy, NoCutoff, two)
	if len(regions) != 2 || regions[0].Low != 102 || regions[1].Low != 203 {
		t.Fatalf("regions %+v", regions)
	}
}

func TestOneOriginUnderTwoLabelsIsNotConfluence(t *testing.T) {
	// Supply/Demand and Order Block cut from the same displacement leg.
	facts := []Fact{fact("sd", Sell, 100, 106, 10, "leg:1"), fact("ob", Sell, 102, 105, 10, "leg:1", "break:1")}
	regions, rejects := Find(facts, Sell, NoCutoff, two)
	if len(regions) != 0 {
		t.Fatalf("two labels of one origin are one fact: %+v", regions)
	}
	if len(rejects) != 1 || rejects[0].Reason != RejectSingleIndependentOrigin || !reflect.DeepEqual(rejects[0].FactIDs, []string{"ob", "sd"}) {
		t.Fatalf("want a single_independent_origin rejection naming both, got %+v", rejects)
	}
}

func TestThreeLabelsOverTwoIndependentOrigins(t *testing.T) {
	facts := []Fact{
		fact("sd", Buy, 100, 108, 10, "leg:1"), fact("ob", Buy, 101, 107, 10, "leg:1", "break:1"), // one origin
		fact("fvg", Buy, 103, 106, 10, "gap:9"), // a separate one
	}
	regions, _ := Find(facts, Buy, NoCutoff, two)
	r := single(t, regions)
	if r.Low != 103 || r.High != 106 || r.MinGroups != 2 || len(r.Groups) != 2 {
		t.Fatalf("region [%v,%v] min %d groups %v", r.Low, r.High, r.MinGroups, r.Groups)
	}
	if n := IndependentCount([]Fact{facts[0], facts[1], facts[2]}); n != 2 {
		t.Fatalf("independent count = %d, want 2", n)
	}
}

func TestDependenceIsTransitive(t *testing.T) {
	// A and B share nothing, but C ties them together: one event, not two.
	a := fact("A", Buy, 100, 110, 10, "x")
	b := fact("B", Buy, 100, 110, 10, "y")
	c := fact("C", Buy, 100, 110, 10, "x", "y")
	if n := IndependentCount([]Fact{a, b, c}); n != 1 {
		t.Fatalf("A-C-B is one connected event, got %d groups", n)
	}
	if n := IndependentCount([]Fact{a, b}); n != 2 {
		t.Fatalf("without the intermediate parent A and B are independent, got %d", n)
	}
	if Dependent(a, b) || !Dependent(a, c) {
		t.Fatal("Dependent reports only direct sharing")
	}
}

func TestDuplicateInputsCountOnce(t *testing.T) {
	a := fact("A", Buy, 100, 106, 10, "a")
	b := fact("B", Buy, 102, 108, 10, "b")
	regions, _ := Find([]Fact{a, a, b, b, a}, Buy, NoCutoff, two)
	single(t, regions)
	// A single structure delivered twice (same ID) is not two facts.
	if r, _ := Find([]Fact{a, a}, Buy, NoCutoff, two); len(r) != 0 {
		t.Fatalf("one structure delivered twice must not qualify: %+v", r)
	}
	// The same structure under two IDs (same provenance) is one group.
	a2 := fact("A-copy", Buy, 100, 106, 10, "a")
	if r, _ := Find([]Fact{a, a2}, Buy, NoCutoff, two); len(r) != 0 {
		t.Fatalf("one structure duplicated under separate IDs must not qualify: %+v", r)
	}
	if n := IndependentCount([]Fact{a, a2, b}); n != 2 {
		t.Fatalf("independent count %d, want 2", n)
	}
}

func TestUnverifiedFactNeverCounts(t *testing.T) {
	a := fact("A", Buy, 100, 106, 10, "a")
	b := fact("B", Buy, 102, 108, 10, "b")
	b.Verified, b.UnverifiedReason = false, UnverifiedParentGap
	regions, rejects := Find([]Fact{a, b}, Buy, NoCutoff, two)
	if len(regions) != 0 {
		t.Fatalf("an unverified fact must never award independence: %+v", regions)
	}
	found := false
	for _, r := range rejects {
		if r.Reason == RejectProvenanceUnverified && reflect.DeepEqual(r.FactIDs, []string{"B"}) {
			found = true
		}
	}
	if !found {
		t.Fatalf("the unverified fact must be reported, got %+v", rejects)
	}
	if n := IndependentCount([]Fact{a, b}); n != 1 {
		t.Fatalf("unverified facts are in no group, got %d", n)
	}
}

func TestAvailabilityCutoffExcludesLaterStructures(t *testing.T) {
	early := fact("early", Sell, 100, 106, 1000, "e")
	late := fact("late", Sell, 102, 108, 2000, "l")
	// A reaction that touched at 1500 cannot be supported by a structure that
	// only became confirmable at 2000.
	regions, rejects := Find([]Fact{early, late}, Sell, 1500, two)
	if len(regions) != 0 {
		t.Fatalf("a structure confirmable after the touch must not count: %+v", regions)
	}
	if len(rejects) != 1 || rejects[0].Reason != RejectNotYetAvailable || !reflect.DeepEqual(rejects[0].FactIDs, []string{"late"}) {
		t.Fatalf("want structure_not_yet_available for the late fact, got %+v", rejects)
	}
	// Confirmable exactly at the touch is admissible; one second later is not.
	if r, _ := Find([]Fact{early, late}, Sell, 2000, two); len(r) != 1 {
		t.Fatal("a structure confirmable at the instant of the cutoff is admissible")
	}
	if r, _ := Find([]Fact{early, late}, Sell, 1999, two); len(r) != 0 {
		t.Fatal("one second before availability it is not")
	}
}

func TestOtherSideIsIgnored(t *testing.T) {
	facts := []Fact{fact("A", Buy, 100, 106, 10, "a"), fact("B", Sell, 102, 108, 10, "b")}
	if r, _ := Find(facts, Buy, NoCutoff, two); len(r) != 0 {
		t.Fatalf("a demand and a supply zone are not confluence: %+v", r)
	}
}

func TestEpisodeIDIsAFunctionOfTheSupportSet(t *testing.T) {
	a, b, c := "A", "B", "C"
	if EpisodeID(Buy, []string{a, b}) != EpisodeID(Buy, []string{b, a}) {
		t.Fatal("episode identity must not depend on order")
	}
	if EpisodeID(Buy, []string{a, b}) == EpisodeID(Buy, []string{a, c}) || EpisodeID(Buy, []string{a, b}) == EpisodeID(Sell, []string{a, b}) {
		t.Fatal("different support or side must be a different episode")
	}
}

// randomFacts builds a reproducible mixed population of dependent and independent
// structures over a small price range.
func randomFacts(r *rand.Rand, n int) []Fact {
	facts := make([]Fact, 0, n)
	for i := 0; i < n; i++ {
		low := float64(r.Intn(30))
		width := float64(1 + r.Intn(10))
		atoms := []string{fmt.Sprintf("origin:%d", r.Intn(6))}
		if r.Intn(4) == 0 {
			atoms = append(atoms, fmt.Sprintf("origin:%d", r.Intn(6)))
		}
		f := fact(fmt.Sprintf("f%02d", i), Buy, low, low+width, int64(r.Intn(100)), atoms...)
		f.Verified = r.Intn(10) != 0
		facts = append(facts, f)
	}
	return facts
}

func TestFindIsIndependentOfInputOrder(t *testing.T) {
	r := rand.New(rand.NewSource(7))
	for trial := 0; trial < 300; trial++ {
		facts := randomFacts(r, 2+r.Intn(14))
		cutoff := int64(r.Intn(120))
		wantRegions, wantRejects := Find(facts, Buy, cutoff, Params{MinimumFacts: 2, MinimumWidth: 0.5})
		for k := 0; k < 5; k++ {
			shuffled := append([]Fact(nil), facts...)
			r.Shuffle(len(shuffled), func(i, j int) { shuffled[i], shuffled[j] = shuffled[j], shuffled[i] })
			if r.Intn(2) == 0 {
				shuffled = append(shuffled, shuffled[r.Intn(len(shuffled))]) // a duplicated delivery
			}
			gotRegions, gotRejects := Find(shuffled, Buy, cutoff, Params{MinimumFacts: 2, MinimumWidth: 0.5})
			if !reflect.DeepEqual(wantRegions, gotRegions) || !reflect.DeepEqual(wantRejects, gotRejects) {
				t.Fatalf("trial %d: order or duplication changed the result\nwant %+v %+v\ngot  %+v %+v", trial, wantRegions, wantRejects, gotRegions, gotRejects)
			}
		}
	}
}

// bruteSupport counts the independent groups of the facts covering a whole
// elementary interval, by brute force.
func bruteSupport(facts []Fact, side string, cutoff int64, lo, hi float64) int {
	var cover []Fact
	for _, f := range facts {
		if f.Side == side && f.Verified && f.AvailableAt <= cutoff && f.Low <= lo && f.High >= hi {
			cover = append(cover, f)
		}
	}
	return IndependentCount(cover)
}

func TestRegionsAreExactlyThePricesWithEnoughIndependentSupport(t *testing.T) {
	r := rand.New(rand.NewSource(11))
	for trial := 0; trial < 300; trial++ {
		facts := randomFacts(r, 2+r.Intn(12))
		cutoff := int64(30 + r.Intn(90))
		minimum := 2 + r.Intn(2)
		regions, _ := Find(facts, Buy, cutoff, Params{MinimumFacts: minimum, MinimumWidth: 0})
		for _, region := range regions {
			if !(region.Width() > 0) {
				t.Fatalf("zero-width region %+v", region)
			}
			if region.MinGroups < minimum {
				t.Fatalf("region %+v supported by %d < %d", region, region.MinGroups, minimum)
			}
		}
		// Sample every half-unit interval of the price range.
		for p := 0.0; p < 41; p += 0.5 {
			lo, hi := p, p+0.5
			want := bruteSupport(facts, Buy, cutoff, lo, hi) >= minimum
			got := false
			for _, region := range regions {
				if region.Low <= lo && region.High >= hi {
					got = true
				}
			}
			if want != got {
				t.Fatalf("trial %d: interval [%v,%v] qualifies=%v but region membership=%v\nfacts %+v\nregions %+v", trial, lo, hi, want, got, facts, regions)
			}
		}
	}
}

func TestRegionsAreMaximalAndDisjoint(t *testing.T) {
	r := rand.New(rand.NewSource(13))
	for trial := 0; trial < 200; trial++ {
		facts := randomFacts(r, 3+r.Intn(10))
		regions, _ := Find(facts, Buy, NoCutoff, Params{MinimumFacts: 2})
		sorted := sort.SliceIsSorted(regions, func(i, j int) bool { return regions[i].Low < regions[j].Low })
		if !sorted {
			t.Fatalf("regions not ordered: %+v", regions)
		}
		for i := 1; i < len(regions); i++ {
			if regions[i].Low <= regions[i-1].High {
				t.Fatalf("regions %+v and %+v touch or overlap, so the first is not maximal", regions[i-1], regions[i])
			}
		}
	}
}
