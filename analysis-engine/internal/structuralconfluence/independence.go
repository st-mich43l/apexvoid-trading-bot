package structuralconfluence

import "sort"

// Group is a set of facts that are one underlying event: connected through
// shared provenance atoms, directly or through intermediate facts. The
// representative is the smallest member ID, so it does not depend on input order.
type Group struct {
	Representative string
	Members        []string
}

// Dedupe returns the facts with duplicate IDs removed (the first of each ID is
// kept) and the removed count, sorted by ID. A structure delivered twice is
// still one structure.
func Dedupe(facts []Fact) ([]Fact, int) {
	seen := make(map[string]bool, len(facts))
	out := make([]Fact, 0, len(facts))
	dups := 0
	for _, f := range facts {
		if seen[f.ID] {
			dups++
			continue
		}
		seen[f.ID] = true
		out = append(out, f.normalised())
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out, dups
}

// Groups partitions the verified facts into independent source groups. A fact
// whose provenance is unverified is not in any group: it can neither count as
// independent nor be assumed dependent. The result is sorted by representative.
func Groups(facts []Fact) []Group {
	facts, _ = Dedupe(facts)
	var verified []Fact
	for _, f := range facts {
		if f.Verified {
			verified = append(verified, f)
		}
	}
	parent := make([]int, len(verified))
	for i := range parent {
		parent[i] = i
	}
	var find func(int) int
	find = func(i int) int {
		for parent[i] != i {
			parent[i] = parent[parent[i]]
			i = parent[i]
		}
		return i
	}
	union := func(a, b int) {
		ra, rb := find(a), find(b)
		if ra == rb {
			return
		}
		// Attach the larger index under the smaller so roots are stable.
		if ra < rb {
			parent[rb] = ra
		} else {
			parent[ra] = rb
		}
	}
	owner := map[string]int{}
	for i, f := range verified {
		atoms := f.Atoms
		if len(atoms) == 0 {
			// Defensive: a verified fact must carry atoms; one that does not is
			// treated as its own event rather than silently merged.
			atoms = []string{"self:" + f.ID}
		}
		for _, a := range atoms {
			if j, ok := owner[a]; ok {
				union(i, j)
			} else {
				owner[a] = i
			}
		}
	}
	members := map[int][]string{}
	for i, f := range verified {
		root := find(i)
		members[root] = append(members[root], f.ID)
	}
	out := make([]Group, 0, len(members))
	for _, ids := range members {
		sort.Strings(ids)
		out = append(out, Group{Representative: ids[0], Members: ids})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Representative < out[j].Representative })
	return out
}

// IndependentCount is the number of independent source groups among the facts.
func IndependentCount(facts []Fact) int { return len(Groups(facts)) }

// Dependent reports whether two facts share an underlying event directly.
func Dependent(a, b Fact) bool {
	a, b = a.normalised(), b.normalised()
	if !a.Verified || !b.Verified {
		return false
	}
	set := make(map[string]bool, len(a.Atoms))
	for _, x := range a.Atoms {
		set[x] = true
	}
	for _, y := range b.Atoms {
		if set[y] {
			return true
		}
	}
	return false
}
