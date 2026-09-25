# ADR-006: Go tests centralize under `analysis-engine/test/`

## Status
Accepted; already implemented for every existing package
(`test/config/`, `test/indicator/`, `test/market/`).

## Context
Go's idiomatic default is `foo_test.go` beside `foo.go` inside the same
package directory. ApexVoid deliberately does not do this.

## Decision
ApexVoid intentionally centralizes Go tests under
`analysis-engine/test/` rather than colocating `*_test.go` beside
production packages.

**Why**: the test tree mirrors the domain, not the package — `test/`'s own
top-level subdirectories are organized by *test intent*
(unit/domain, strategy, integration, parity, replay, architecture,
fixtures), which cuts across `internal/` package boundaries. A parity test
for ATR, a strategy test for breakout-retest, and an architecture test
checking import boundaries across five different `internal/` packages all
belong to different reviewers' mental model of "what kind of test is
this," not to any single package's directory. Keeping `*_test.go` beside
`atr.go` would force every one of those concerns into whichever single
package happens to own the code under test, obscuring the
unit/integration/parity/replay/architecture distinction the project
explicitly wants visible at the directory level (see `analysis-engine.md`'s
test category table).

**Structure**:

```text
test/
├── indicator/       test/structure/      test/liquidity/     test/zone/
├── strategy/         test/integration/     test/parity/         test/replay/
├── architecture/       test/fixtures/
```

organized by domain, mirroring `internal/`'s package names one level down
(e.g. `test/indicator/atr_test.go` tests `internal/indicator`).

**Internal-package import legality**: `test/<domain>/*_test.go` files are
declared as `package <domain>_test` (an external test package, e.g.
`package indicator_test` for `test/indicator/atr_test.go`) and import
`analysis-engine/internal/<domain>` like any other consumer — they exercise
only the package's exported API, never its unexported internals.
Confirmed against every existing test file
(`test/indicator/atr_test.go`: `package indicator_test`, imports
`.../internal/indicator`; `test/market/symbol_test.go`,
`test/market/window_test.go`: same pattern). The one place this needed
unexported access (`test/config`'s fixture-parity test) is documented in
`docs/configuration-v3-migration-audit.md`'s own "Go test layout" note —
resolved by exporting the minimum needed surface from `internal/config`
rather than reaching into it from outside the module.

**Distinction between test categories** (full detail in
`analysis-engine.md`): unit/domain tests validate deterministic behavior
in isolation; strategy tests validate one strategy's thesis independently
of others; integration tests validate multiple layers wired together
(bars → structure → context → strategy → opportunity); parity tests
compare against real legacy Python output byte-for-value (already the
pattern in `test/indicator/atr_fixture_test.go` and
`test/config/v3_fixture_parity_test.go`); replay tests compare old vs. new
strategy *outcomes* across historical data with no signal-parity
requirement.

**No second `go.mod` under `test/`.** `test/` is part of the single
`analysis-engine` module (`module
github.com/st-mich43l/apexvoid-trading-bot/analysis-engine`); every test
file imports `internal/*` by its module path like any other Go code in the
tree, and `go test ./...` from `analysis-engine/` runs everything —
confirmed by running it after this task's scaffolding (`go build ./... &&
go vet ./... && go test ./...`, all green).

## Consequences
- Adding a package with no corresponding `test/<domain>/` directory is
  incomplete by convention, not by tooling — reviewers should expect a new
  `internal/<pkg>/` to arrive with a `test/<pkg>/` sibling.
- `test/architecture/dependency_test.go` (added this task) lives in this
  same centralized tree, `package architecture_test`, and is itself the
  concrete example of why centralizing by domain matters: it tests
  *every* `internal/*` package's import list, which has no single natural
  package-colocated home under the idiomatic-Go convention this project
  rejected.
