package structuralconfluence

import (
	"crypto/sha256"
	"encoding/hex"
	"math"
	"sort"
	"strings"
)

// NoCutoff is the asOf value that admits every fact whatever its availability.
const NoCutoff = int64(math.MaxInt64)

// Rejection reasons for an overlap that is not a confluence region.
const (
	RejectSingleIndependentOrigin = "single_independent_origin"
	RejectOverlapTooNarrow        = "overlap_too_narrow"
	RejectProvenanceUnverified    = "provenance_unverified"
	RejectNotYetAvailable         = "structure_not_yet_available"
)

// Params are the region contract: how many independent source groups must
// support a price, and how wide a supported region must be to be tradable.
type Params struct {
	// MinimumFacts is the number of independent source groups that must support
	// every price of a region (the strategy's minimum_facts).
	MinimumFacts int
	// MinimumWidth is the narrowest tradable region, in price units. A region
	// must be strictly wider; a shared boundary has zero width and never counts.
	MinimumWidth float64
}

// Segment is one elementary price interval between two consecutive fact bounds
// and the facts covering all of it.
type Segment struct {
	Low, High float64
	Active    []string
	Groups    int
}

// Region is a maximal contiguous price interval every point of which is
// supported by at least MinimumFacts independent groups. It is not the union of
// its facts and not necessarily the intersection of all of them: each segment
// may be supported by a different subset, which is exactly what makes extended
// chains of overlapping structures work without an exponential subset search.
type Region struct {
	Side      string
	Low, High float64
	// Support are the facts overlapping the region (sorted); Groups their
	// independent source groups.
	Support []string
	Groups  []Group
	// MinGroups is the smallest independent support anywhere in the region.
	MinGroups int
	Segments  []Segment
	// EpisodeID names this confluence episode independently of input order.
	EpisodeID string
}

// Width is High - Low.
func (r Region) Width() float64 { return r.High - r.Low }

// Contains reports whether the price lies within the region (inclusive).
func (r Region) Contains(price float64) bool { return price >= r.Low && price <= r.High }

// Reject records why an overlap, or a fact, did not become part of a region.
type Reject struct {
	Reason    string
	Low, High float64
	FactIDs   []string
}

// EpisodeID is the stable identity of a confluence episode: the side and the
// sorted IDs of the facts supporting it. It is a function of the set, so the
// order in which facts arrive can never change it.
func EpisodeID(side string, support []string) string {
	ids := append([]string(nil), support...)
	sort.Strings(ids)
	sum := sha256.Sum256([]byte(side + "\x00" + strings.Join(ids, "\x00")))
	return "cz3:" + side + ":" + hex.EncodeToString(sum[:8])
}

// Find returns the confluence regions of one side as of a cutoff, and the
// overlaps and facts that were rejected, each with its reason.
//
// Only verified facts whose AvailableAt is at or before asOf take part, so a
// structure that became confirmable later can never support an earlier reaction.
// The output is identical for every permutation (and every duplication) of facts.
func Find(facts []Fact, side string, asOf int64, p Params) ([]Region, []Reject) {
	minimum := p.MinimumFacts
	if minimum < 1 {
		minimum = 1
	}
	all, _ := Dedupe(facts)
	var rejects []Reject
	var usable []Fact
	var unverified, late []string
	for _, f := range all {
		if f.Side != side {
			continue
		}
		switch {
		case !f.Verified:
			unverified = append(unverified, f.ID)
		case f.AvailableAt > asOf:
			late = append(late, f.ID)
		case f.High > f.Low:
			usable = append(usable, f)
		}
	}
	if len(unverified) > 0 {
		rejects = append(rejects, Reject{Reason: RejectProvenanceUnverified, FactIDs: unverified})
	}
	if len(late) > 0 {
		rejects = append(rejects, Reject{Reason: RejectNotYetAvailable, FactIDs: late})
	}

	var bounds []float64
	for _, f := range usable {
		bounds = append(bounds, f.Low, f.High)
	}
	sort.Float64s(bounds)
	uniq := bounds[:0]
	for i, b := range bounds {
		if i == 0 || b != bounds[i-1] {
			uniq = append(uniq, b)
		}
	}
	bounds = uniq

	type elementary struct {
		seg      Segment
		qualify  bool
		covering []Fact
	}
	var elems []elementary
	for i := 0; i+1 < len(bounds); i++ {
		lo, hi := bounds[i], bounds[i+1]
		var cover []Fact
		for _, f := range usable {
			if f.Low <= lo && f.High >= hi {
				cover = append(cover, f)
			}
		}
		if len(cover) == 0 {
			continue
		}
		groups := Groups(cover)
		ids := make([]string, len(cover))
		for k, f := range cover {
			ids[k] = f.ID
		}
		sort.Strings(ids)
		elems = append(elems, elementary{
			seg:     Segment{Low: lo, High: hi, Active: ids, Groups: len(groups)},
			qualify: len(groups) >= minimum, covering: cover,
		})
	}

	// Contiguous runs: qualifying segments become regions, overlapping but
	// insufficiently independent ones are rejected as a single origin.
	var regions []Region
	for i := 0; i < len(elems); {
		j := i + 1
		for j < len(elems) && elems[j].qualify == elems[i].qualify && elems[j].seg.Low == elems[j-1].seg.High {
			j++
		}
		run := elems[i:j]
		i = j
		lo, hi := run[0].seg.Low, run[len(run)-1].seg.High
		if !run[0].qualify {
			var ids []string
			multi := false
			for _, e := range run {
				if len(e.seg.Active) >= 2 {
					multi = true
				}
				ids = append(ids, e.seg.Active...)
			}
			if multi {
				rejects = append(rejects, Reject{Reason: RejectSingleIndependentOrigin, Low: lo, High: hi, FactIDs: uniqueSorted(ids)})
			}
			continue
		}
		var support []string
		var covering []Fact
		minGroups := math.MaxInt32
		segments := make([]Segment, len(run))
		for k, e := range run {
			segments[k] = e.seg
			support = append(support, e.seg.Active...)
			covering = append(covering, e.covering...)
			if e.seg.Groups < minGroups {
				minGroups = e.seg.Groups
			}
		}
		support = uniqueSorted(support)
		if !(hi-lo > p.MinimumWidth) {
			rejects = append(rejects, Reject{Reason: RejectOverlapTooNarrow, Low: lo, High: hi, FactIDs: support})
			continue
		}
		regions = append(regions, Region{
			Side: side, Low: lo, High: hi, Support: support, Groups: Groups(covering), MinGroups: minGroups,
			Segments: segments, EpisodeID: EpisodeID(side, support),
		})
	}
	sort.SliceStable(regions, func(a, b int) bool {
		if regions[a].Low != regions[b].Low {
			return regions[a].Low < regions[b].Low
		}
		return regions[a].High < regions[b].High
	})
	return regions, rejects
}

func uniqueSorted(in []string) []string {
	sort.Strings(in)
	out := in[:0]
	for i, s := range in {
		if i == 0 || s != in[i-1] {
			out = append(out, s)
		}
	}
	return append([]string(nil), out...)
}
