package architecture_test

import (
	"go/ast"
	"go/parser"
	"go/token"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"testing"
)

// Every strategy owns its mechanism (docs/strategies/README.md): structure definition, qualification, confirmation, entry, stop, target and room live in
// the strategy's own package. A migrated package may read facts (bars, ATR, pools, levels, the higher timeframe bias) and call the shared contracts
// (confluence rubric, stop budget, candidate builder, config parsing), but it must not call the shared confirmation spine below. A strategy that needs
// one of those behaviours copies the code into its own package.

// migrated are the strategy packages that own their mechanism. Each ownership PR adds its package.
var migrated = []string{"fvg", "orderblock", "supply", "demand"}

// forbiddenPackageSymbols are exported symbols of shared packages a migrated strategy must not use.
var forbiddenPackageSymbols = map[string][]string{
	"strategyutil": {
		"ConfirmedTechnique", "ConfirmedFrozenTechnique", "TechniqueSource", "NewTechniqueSource", "TechniqueDecision", "TechniqueSpec", "TechniqueCandidate",
		"ZoneFamilyQuality", "SharedReactionDimensions", "QualityGeometry", "ReactionFactors", "FactorsForConfirmation", "ReactionAnchoredInvalidation",
		"ParseReactionConfig", "ParseTechniqueGeometry", "OptimizeImbalanceEntryZone", "LegacyConfirmation", "LegacyResult",
	},
	"reaction":      {"Evaluate"},
	"techniquezone": {"SupplyDemand", "Displacement", "OrderBlocks", "BreakerBlocks", "FlipZones", "FVG", "MarkMitigation", "MergeZones", "InstancesFromScoredZones", "CollectInstances"},
}

// forbiddenMethods are the shared LegacyDetector decision methods (and the engine-built technique instances) a migrated strategy must not call.
var forbiddenMethods = []string{
	"EntryValid", "LevelValid", "Reaction", "ReactionFrom", "ReactionWithLookbacks", "ZoneReaction", "Finish", "BestValidZone", "CandidateZones", "ZoneGrab",
	"LevelGrab", "ZoneKey", "ZoneID", "Techniques",
}

// forbiddenImports are packages a migrated strategy must not import at all.
var forbiddenImports = []string{"/internal/zone", "/internal/reaction"}

func TestMigratedStrategiesOwnTheirMechanism(t *testing.T) {
	forbiddenMethod := map[string]bool{}
	for _, name := range forbiddenMethods {
		forbiddenMethod[name] = true
	}
	for _, pkg := range migrated {
		files, err := filepath.Glob(filepath.Join("..", "..", "internal", "strategy", pkg, "*.go"))
		if err != nil || len(files) == 0 {
			t.Fatalf("%s: no files (%v)", pkg, err)
		}
		fset := token.NewFileSet()
		for _, file := range files {
			if strings.HasSuffix(file, "_test.go") {
				continue
			}
			parsed, err := parser.ParseFile(fset, file, nil, 0)
			if err != nil {
				t.Fatal(err)
			}
			// local name of each imported package, by its import path's last element
			local := map[string]string{}
			for _, imp := range parsed.Imports {
				path, _ := strconv.Unquote(imp.Path.Value)
				for _, forbidden := range forbiddenImports {
					if strings.HasSuffix(path, forbidden) {
						t.Errorf("%s imports %s: a migrated strategy owns this mechanism", file, path)
					}
				}
				name := filepath.Base(path)
				if imp.Name != nil {
					name = imp.Name.Name
				}
				local[name] = filepath.Base(path)
			}
			var found []string
			ast.Inspect(parsed, func(n ast.Node) bool {
				// a forbidden method is a CALL (candidate.Reaction is a field, not the detector's Reaction method)
				if call, ok := n.(*ast.CallExpr); ok {
					if sel, ok := call.Fun.(*ast.SelectorExpr); ok && forbiddenMethod[sel.Sel.Name] {
						found = append(found, fset.Position(sel.Pos()).String()+" ."+sel.Sel.Name+"()")
					}
				}
				sel, ok := n.(*ast.SelectorExpr)
				if !ok {
					return true
				}
				if ident, ok := sel.X.(*ast.Ident); ok {
					for _, symbol := range forbiddenPackageSymbols[local[ident.Name]] {
						if sel.Sel.Name == symbol {
							found = append(found, fset.Position(sel.Pos()).String()+" "+ident.Name+"."+symbol)
						}
					}
				}
				return true
			})
			sort.Strings(found)
			for _, f := range found {
				t.Errorf("%s uses the shared spine: %s", pkg, f)
			}
		}
	}
}
