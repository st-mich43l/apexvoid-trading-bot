// Package architecture_test statically enforces the dependency direction
// frozen in docs/architecture/dependency-rules.md — a lower layer
// importing a higher one is a build-time-discoverable violation, not
// something a doc can only describe. See docs/adr/006 for why this test
// lives under the centralized test/ tree rather than beside any one
// package it inspects (it inspects all of them).
package architecture_test

import (
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"testing"
)

const modulePrefix = "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/"

// rank is this repo's frozen dependency order (lower imports only from a
// strictly lower rank), per docs/architecture/dependency-rules.md:
//
//	market / telemetry
//	  -> indicator / marketdata / config
//	  -> structure
//	  -> zone           (depends on structure: Swing/StructureBreak/DetectDisplacement)
//	  -> liquidity      (depends on structure: Pool.Layer, Update(swings))
//	  -> context        (depends on structure, zone, AND liquidity)
//	  -> opportunity
//	  -> strategy / confluence / state
//	  -> transport (incl. transport/kafka, transport/redis)
//	  -> engine
//
// liquidity/context's ranks and telemetry's leaf->rank-0 reclassification
// are amendments made when Analysis Engine V2 was implemented — see
// docs/architecture/dependency-rules.md's own notes on those changes.
//
// transport sitting BELOW engine (not above it, as the original
// architecture freeze had it) is a third amendment, made implementing the
// Kafka transport task. That task's own §1 states the allowed direction
// as "engine -> transport/kafka" (engine IMPORTS transport to publish/
// consume) and explicitly forbids "structure/liquidity/zone/strategy/
// indicator -> kafka". Under this test's own rule ("a package may only
// import a strictly lower rank"), engine importing transport requires
// transport's rank to be LOWER than engine's — the original freeze had
// transport as the highest rank (8, above engine at 7), which would make
// that required edge illegal. Placing transport at rank 7 (strictly
// above strategy/confluence/state at 6, strictly below engine at 8)
// satisfies both requirements at once with the same plain monotonic rule,
// no special-cased exception: engine (8) can import transport (7); every
// package at or below strategy's own rank (6) — including strategy
// itself — cannot import transport (7), which is exactly source task
// §1's "strategy -> kafka" forbidden edge, and matches this repo's
// pre-existing "Strategies must NOT depend on: Kafka · Redis..." rule
// (docs/architecture/dependency-rules.md's Strategy dependency rule,
// §52) enforced structurally instead of by a separate carve-out.
//
// zone promoted from rank 2 (same-rank sibling of structure) to rank 3
// (same-rank sibling of liquidity) is a fourth amendment, made
// implementing Phase S3 (the canonical Zone domain). Real zone kinds
// (Order Block, Breaker, Flip Zone, Supply/Demand) need structure.Swing/
// structure.StructureBreak/structure.DetectDisplacement directly — the
// identical situation liquidity already hit needing structure.Swing, and
// the identical fix: promote above structure. zone and liquidity are
// mutually independent (neither imports the other), so they share a
// rank rather than needing a fifth distinct one.
//
// fib joins zone/liquidity at rank 3 for the identical reason, made
// implementing Phase S4's second domain: Resolve/Update's bracketing
// swing-pair search needs structure.Swing.Kind/Price directly
// (dealing_range.py::_bracketing_pair/_last_opposing_pair's own Python
// shape). fib does not import zone or liquidity, nor do they import it.
var rank = map[string]int{
	"market":    0,
	"telemetry": 0,

	"indicator":  1,
	"marketdata": 1,
	"config":     1,

	"structure": 2,

	"zone":      3,
	"liquidity": 3,
	"fib":       3,

	"context": 4,

	"opportunity": 5,

	"strategy":   6,
	"confluence": 6,
	"state":      6,

	"transport":       7,
	"transport/kafka": 7,
	"transport/redis": 7,

	"engine": 8,
}

// leaf packages: exempt from having their own imports checked; nothing
// else may import them.
var leaf = map[string]bool{
	"visualization": true,
}

func TestInternalDependencyDirection(t *testing.T) {
	internalDir := internalPackagesDir(t)

	byDomain := map[string][]string{} // domain -> list of "domain: bad import" failures collected per file, for readable output
	fset := token.NewFileSet()

	err := filepath.WalkDir(internalDir, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(path, ".go") {
			return nil
		}
		domain := domainOf(internalDir, path)

		file, perr := parser.ParseFile(fset, path, nil, parser.ImportsOnly)
		if perr != nil {
			t.Fatalf("architecture: parsing %s: %v", path, perr)
		}

		for _, imp := range file.Imports {
			raw := strings.Trim(imp.Path.Value, `"`)
			if !strings.HasPrefix(raw, modulePrefix) {
				continue // stdlib / third-party — not this test's concern
			}
			importedDomain := strings.TrimPrefix(raw, modulePrefix)

			if leaf[importedDomain] {
				byDomain[domain] = append(byDomain[domain],
					domain+" imports leaf package "+importedDomain+" — nothing may depend on a leaf/observability package")
				continue
			}
			if leaf[domain] {
				continue // leaf packages (visualization, telemetry) may import anything
			}

			fromRank, fromOK := lookupRank(domain)
			toRank, toOK := lookupRank(importedDomain)
			if !fromOK || !toOK {
				// New package not yet added to the rank table above — fail
				// closed rather than silently pass an unranked edge.
				byDomain[domain] = append(byDomain[domain],
					domain+" imports "+importedDomain+" — one of these packages is missing from the rank table in this test; add it before merging")
				continue
			}
			if toRank >= fromRank {
				byDomain[domain] = append(byDomain[domain],
					domain+" (rank "+itoa(fromRank)+") imports "+importedDomain+" (rank "+itoa(toRank)+") — a package may only import a strictly lower rank")
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("architecture: walking %s: %v", internalDir, err)
	}

	var domains []string
	for d := range byDomain {
		domains = append(domains, d)
	}
	sort.Strings(domains)
	for _, d := range domains {
		for _, msg := range byDomain[d] {
			t.Error(msg)
		}
	}
}

func lookupRank(domain string) (int, bool) {
	if r, ok := rank[domain]; ok {
		return r, true
	}
	// transport/kafka, transport/redis etc. — fall back to the top-level
	// segment's rank if the exact subpackage isn't listed.
	if i := strings.Index(domain, "/"); i > 0 {
		return lookupRank(domain[:i])
	}
	return 0, false
}

func domainOf(internalDir, filePath string) string {
	rel, _ := filepath.Rel(internalDir, filepath.Dir(filePath))
	return filepath.ToSlash(rel)
}

func internalPackagesDir(t *testing.T) string {
	t.Helper()
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("architecture: could not resolve test file's own path")
	}
	// this file: analysis-engine/test/architecture/dependency_test.go
	root := filepath.Dir(filepath.Dir(filepath.Dir(thisFile))) // -> analysis-engine/
	dir := filepath.Join(root, "internal")
	if info, err := os.Stat(dir); err != nil || !info.IsDir() {
		t.Fatalf("architecture: expected %s to be the internal/ directory", dir)
	}
	return dir
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}
