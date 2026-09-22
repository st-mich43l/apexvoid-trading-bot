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
//	  -> structure / liquidity / zone
//	  -> context
//	  -> opportunity
//	  -> strategy / confluence / state
//	  -> engine
//	  -> transport (incl. transport/kafka, transport/redis)
//
// visualization and telemetry are leaf consumers: absent from this map,
// meaning (a) their own imports are never checked (they may depend on
// anything), and (b) nothing else may import them — enforced separately
// below, not via rank.
var rank = map[string]int{
	"market": 0,

	"indicator":  1,
	"marketdata": 1,
	"config":     1,

	"structure": 2,
	"liquidity": 2,
	"zone":      2,

	"context": 3,

	"opportunity": 4,

	"strategy":   5,
	"confluence": 5,
	"state":      5,

	"engine": 6,

	"transport":       7,
	"transport/kafka": 7,
	"transport/redis": 7,
}

// leaf packages: exempt from having their own imports checked; nothing
// else may import them.
var leaf = map[string]bool{
	"visualization": true,
	"telemetry":     true,
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
