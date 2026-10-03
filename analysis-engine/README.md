# analysis-engine

Go owner of ApexVoid's deterministic automatic market-analysis domain, per
`apexvoid-bot-prompts/rebuild-analysis-engine.md`. The live Go service owns
technical facts, strategy detection, opportunity lifecycle, arbitration,
zones, levels, structure, regime, MAD, and candle evidence. `algo-bot/`
remains the execution-policy/control plane (freshness, quote, account,
exposure, TradePlan, Telegram), while `ctrader-engine/` remains the broker
execution service. The Python technical detector graph is no longer a live
automatic authority; its import reachability is enforced by
`algo-bot/s13_legacy_classification.py`.

Read **`docs/go-analysis-migration-audit.md`** first — the computation
graph, every duplicate/divergent calculation found in Python so far, and
why each package boundary here is drawn where it is.

## Status (Go automatic path live; S13 retirement audit active)

The live path includes:

- canonical V3 configuration, market history, ATR, structure and liquidity;
- zone construction/lifecycle and all 20 configured Go strategy factories;
- technical opportunity Kafka publication, lifecycle, arbitration and
  technical context (including candle-confirmation evidence);
- Redis market-data ingestion, production telemetry, replay and parity tests.

The remaining Python modules are either unreachable technical compatibility
code or intentionally retained execution/accounting/manual/presentation
code. The S13 classifier must remain green before any technical module is
deleted. Known calibration and long-window Python-vs-Go comparison gaps are
tracked in `docs/analysis-engine-v2-migration.md`.

## Working on this module

Tests are centralized under `test/`, one subdirectory per package domain
(`test/config/`, `test/indicator/`, `test/market/`) rather than colocated
as `internal/<pkg>/*_test.go` — each is a black-box `<pkg>_test` package
testing its package's exported API only (see
`docs/configuration-v3-migration-audit.md`'s "Go test layout" note for
why, and what that meant for the one test that needed unexported access).
Add new tests there, under the matching domain, not back inside
`internal/`.

When Go is unavailable on a host, use the same `golang:1.23-alpine` Docker
image used by CI. `test/config` reads the real `config/apexvoid.yml` two
directories up, so mount the **whole repository**, not just
`analysis-engine/`:

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

`test/config`'s tests have no `testdata/` fixture of their own — they
read `../../../config/apexvoid.yml` and `.../apexvoid.demo-eval.yml`
directly (the real files, not a copy), the same choice
`algo-bot/tests/test_config_v3_parity.py` makes and for the same reason:
the point is proving parity against what every other language actually
reads, not a fixture that could quietly drift from it.
