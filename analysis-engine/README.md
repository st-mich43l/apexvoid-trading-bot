# analysis-engine

Go port of ApexVoid's deterministic market-analysis domain, per
`apexvoid-bot-prompts/rebuild-analysis-engine.md`. Extracts the CPU-heavy,
deterministic analysis math out of `algo-bot/` into Go while
`ctrader-engine/` (execution) and `algo-bot/` (control plane, Telegram,
research) stay as they are. **Not a rewrite of the trading strategy** —
Python remains the reference implementation and stays authoritative until
a shadow-mode parity run earns cutover (see the source prompt's §14/§26).

Read **`docs/go-analysis-migration-audit.md`** first — the computation
graph, every duplicate/divergent calculation found in Python so far, and
why each package boundary here is drawn where it is.

## Status (Stage 1 + partial Stage 2; Configuration V3 Stage C4)

Ported, with golden-master parity tests against real Python output:

- `internal/market` — `Candle`, `CandleWindow` (bounded ring buffer),
  `Timeframe`, `Geometry` (per-instrument pip/digit geometry, fails closed
  on an unknown symbol per the source prompt's §12).
- `internal/config` — Configuration V3 direct YAML reader (see
  `docs/configuration-v3-migration-audit.md`'s Stage C4 section):
  `ResolveDocument` implements the same include/merge/overlay spec (§14)
  as `algo-bot/app/configuration/v3_root.py` and `config/scripts/
  resolve_reference.py` — three independent implementations of one spec,
  proven against the real `config/apexvoid.yml`. `GeometryFor`/
  `LiveInstruments` replace the old `ResolvedRuntimeManifest`-JSON reader
  entirely (deleted, not kept as a fallback — the source prompt's own
  §34: "Do not leave manifest OR yaml mode selection. YAML V3 only.").
- `internal/indicator` — `TrueRange`, `SimpleATR` (`math_utils.atr_series`
  — simple rolling mean), `WilderATR` (`indicators.atr` — pandas_ta RMA),
  `AtrAt`, `AtrScalar`. **Both ATR formulas are ported side by side,
  deliberately** — see the audit §2.1: they diverge ~6.7% on real data and
  which one is canonical is an open question for the owner, not resolved
  here.

Not yet started: swings, market structure/breaks, zones/techniques, MAD,
regime, trendlines, scalp structure, the `SymbolState`/`TimeframeState`
engine, detectors, Redis wiring. See `docs/go-analysis-migration-audit.md`
§6 for the next analysis slice.

## Working on this module

Go is not installed on the host in this environment; everything runs
through the same `golang:1.23-alpine` Docker image already cached
locally, mirroring how `ctrader-engine/` is built/tested via the
`mcr.microsoft.com/dotnet/sdk:8.0` image in this repo. `internal/config`'s
tests read the real `config/apexvoid.yml` two directories up, so mount
the **whole repository**, not just `analysis-engine/`:

```bash
docker run --rm -v "$(pwd)":/src -w /src/analysis-engine golang:1.23-alpine \
  sh -c "gofmt -l . && go build ./... && go vet ./... && go test ./..."

# race detector needs cgo:
docker run --rm -v "$(pwd)":/src -w /src/analysis-engine golang:1.23-alpine \
  sh -c "apk add --no-cache gcc musl-dev && go test -race ./..."
```

(Run from the repository root, not from inside `analysis-engine/`.)

## Fixtures (`testdata/`)

- `atr_fixtures.json` — golden-master cases (real XAU M5 bars plus
  warmup/flat/gap edge cases) comparing Go's `TrueRange`/`SimpleATR`/
  `WilderATR` against the same Python functions' real output.
  Regenerate with `scripts/export_atr_fixtures.py` (run from `algo-bot/`,
  needs its venv) — **only when the Python formula itself changes**, never
  to make a failing Go test pass (source prompt §31: "do NOT simply adjust
  the fixture").
- `raw_xau_m5_snapshot.jsonl` — the real bar data the ATR fixtures above
  are built from (a `bars:XAU:M5` Redis snapshot, 2026-09-22).

`internal/config`'s tests have no `testdata/` fixture of their own — they
read `../../../config/apexvoid.yml` and `.../apexvoid.demo-eval.yml`
directly (the real files, not a copy), the same choice
`algo-bot/tests/test_config_v3_parity.py` makes and for the same reason:
the point is proving parity against what every other language actually
reads, not a fixture that could quietly drift from it.
