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

## Status (Stage 1 + partial Stage 2)

Ported, with golden-master parity tests against real Python output:

- `internal/market` — `Candle`, `CandleWindow` (bounded ring buffer),
  `Timeframe`, `Geometry` (per-instrument pip/digit geometry, fails closed
  on an unknown symbol per the source prompt's §12).
- `internal/config` — `ResolvedRuntimeManifest` loader (`APEXVOID_RUNTIME_
  MANIFEST_FILE`, same env var Python's `runtime_manifest_boot.py` reads)
  and the one place manifest → `market.Geometry` translation happens.
- `internal/indicator` — `TrueRange`, `SimpleATR` (`math_utils.atr_series`
  — simple rolling mean), `WilderATR` (`indicators.atr` — pandas_ta RMA),
  `AtrAt`, `AtrScalar`. **Both ATR formulas are ported side by side,
  deliberately** — see the audit §2.1: they diverge ~6.7% on real data and
  which one is canonical is an open question for the owner, not resolved
  here.

Not yet started: swings, market structure/breaks, zones/techniques, MAD,
regime, trendlines, scalp structure, the `SymbolState`/`TimeframeState`
engine, detectors, Redis wiring. See the audit's §6 for the next slice.

## Working on this module

Go is not installed on the host in this environment; everything runs
through the same `golang:1.23-alpine` Docker image already cached
locally, mirroring how `ctrader-engine/` is built/tested via the
`mcr.microsoft.com/dotnet/sdk:8.0` image in this repo:

```bash
cd analysis-engine
docker run --rm -v "$(pwd)":/src -w /src golang:1.23-alpine \
  sh -c "gofmt -l . && go build ./... && go vet ./... && go test ./..."

# race detector needs cgo:
docker run --rm -v "$(pwd)":/src -w /src golang:1.23-alpine \
  sh -c "apk add --no-cache gcc musl-dev && go test -race ./..."
```

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
- `runtime-manifest-example.json` — copy of
  `contracts/configuration/runtime-manifest-example.generated.json`, used
  to test the manifest loader against the real, full-size shape instead of
  a hand-trimmed one.
