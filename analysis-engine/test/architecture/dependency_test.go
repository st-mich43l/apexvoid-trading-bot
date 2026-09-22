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
//	market
//	  -> indicator / marketdata / config
//	  -> structure
//	  -> zone           (independent of liquidity; not yet implemented)
//	  -> liquidity      (depends on structure: Pool.Layer, Update(swings))
//	  -> context        (depends on structure AND liquidity)
//	  -> opportunity
//	  -> strategy / confluence / state
//	  -> engine
//	  -> transport (incl. transport/kafka, transport/redis)
//
// liquidity/context's ranks are an amendment made when Analysis Engine V2
// was implemented (docs/adr's original freeze had structure/liquidity/zone
// as same-rank siblings) — see docs/architecture/dependency-rules.md's own
// note on this change, made the same documented, non-silent way as every
// other correction in this codebase's history.
//
// visualization is a leaf consumer: absent from this map, meaning (a) its
// own imports are never checked (it may depend on anything), and (b)
// nothing else may import it — enforced separately below, not via rank.
//
// telemetry is NOT a leaf, despite the original architecture freeze
// classifying it as one — that assumption (telemetry is only ever
// consumed externally, by an operator/dashboard) was wrong the moment
// engine needed to actually record its own timings (source task §57):
// the thing being measured must call INTO the recorder. telemetry has
// zero internal/* imports of its own (confirmed — see
// internal/telemetry/metrics.go), so it sits at rank 0 alongside market:
// a foundational, dependency-free utility anything at a higher rank may
// import, not a top-of-graph consumer. This correction is made the same
// documented, non-silent way as every other rank amendment in this file.
var rank = map[string]int{
	"market":    0,
	"telemetry": 0,

	"indicator":  1,
	"marketdata": 1,
	"config":     1,

	"structure": 2,
	"zone":      2,

	"liquidity": 3,

	"context": 4,

	"opportunity": 5,

	"strategy":   6,
	"confluence": 6,
	"state":      6,

	"engine": 7,

	"transport":       8,
	"transport/kafka": 8,
	"transport/redis": 8,
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
