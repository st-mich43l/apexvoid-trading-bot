# Configuration (V3)

Status: **Stage C3 done for Python, local/dev only.** `algo-bot`'s
`bot` service (docker-compose.yml) now reads `config/apexvoid.demo-eval.yml`
directly and is proven byte-for-byte behavior-equivalent to the old
`trading-bot.yml` path for production values (890/890 leaves — see the
audit's Stage C3 section). **Actual ansible-driven production is
untouched** — it's outside this repository and needs its own
`APEXVOID_CONFIG_FILE` update to complete that cutover; until then it
keeps reading `trading-bot.yml` exactly as before. `config-compiler` and
`ctrader-engine` (.NET) are also untouched — `ResolvedRuntimeManifest`
remains their live authority until Stage C5. See
[`docs/configuration-v3-migration-audit.md`](configuration-v3-migration-audit.md)
for the full audit, the staged plan (C0–C8), and exactly what Stage C3
does and does not claim. That older, still-live .NET/manifest system is
documented separately at
[`docs/configuration/configuration-architecture.md`](configuration/configuration-architecture.md).

## Root file and category files

`config/apexvoid.yml` is the entrypoint. It declares `version: 3` and an
`includes:` list of category files, each owning one non-overlapping
domain:

| File | Owns |
|---|---|
| `runtime.yml` | Timezone, environment, logging, service/broker identity, execution contract mode. |
| `transport.yml` | Redis connection + stream names (the stream *names* are a protocol constant, not a tuning knob — see the audit's Stage C2 §2 classification note — even though they live in this file); Kafka once real topics exist. |
| `database.yml` | Non-secret Postgres topology. |
| `instruments.yml` | Per-instrument geometry, pack inheritance, rollout state — the **only** owner of price-denominated geometry (zone widths, merge gaps, separation prices); no other file may declare a global default for these (Stage C2 §7). |
| `analysis.yml` | Market-analysis behavior (ATR algorithm, trendlines, zones, techniques) plus market-data inputs (calendar, sessions, execution/HTF timeframe semantics). Feed subscription symbols/timeframes are NOT here — they derive from `instruments.yml` (Stage C2 §3–§5). |
| `auto-algo.yml` | Auto-trading orchestration/policy: actionability, lifecycle, risk, strategy enable/threshold, scalping. |
| `manual-algo.yml` | Manual/operator workflow behavior not already instrument-scoped in `instruments.yml`. |
| `execution.yml` | Execution/broker mechanics — the executor stays mechanical; no market interpretation here. |
| `telegram.yml` | Telegram presentation/lifecycle/reporting. `presentation.seq_reset_tz` was removed (Stage C2 §6) — it's DERIVED from `runtime.timezone`, not a second value. |
| `journal.yml` | Trade journaling/history behavior. |
| `environments/*.yml` | One overlay per environment (see below). |

The root file never duplicates a setting an included file already owns.

## Ownership boundary: analysis vs. auto-algo vs. execution

Three files sit next to each other in the pipeline and are easy to
conflate — keep this boundary in mind before adding a setting:

- **`analysis.yml`** answers *"what opportunity exists?"* — pure market
  interpretation (ATR, structure, zones, techniques).
- **`auto-algo.yml`** answers *"should ApexVoid act on it?"* — policy on
  top of what analysis found (confluence gates, risk sizing, which
  strategies are enabled).
- **`execution.yml`** is mechanical — entry/stop/target mechanics once a
  decision has already been made. Never market interpretation.

A setting that changes which trades get *proposed* belongs in
`analysis.yml`; one that changes whether a proposed trade gets *acted on*
belongs in `auto-algo.yml`; one that changes *how* an accepted trade is
executed belongs in `execution.yml`.

## Include and merge semantics (specified and reference-tested, Stage C2)

`apexvoid.yml`'s `includes:` list is read in order; each entry is a path
relative to `config/`. Rules, all implemented and tested in
[`config/scripts/resolve_reference.py`](../config/scripts/resolve_reference.py):

- A missing include, a duplicate include, or an include that escapes
  `config/` (absolute path or `..`) is an error.
- Two base category files declaring the same top-level leaf is an error —
  each category owns its domain exclusively.
- Mappings deep-merge; the environment overlay is applied last.
- Scalars and lists are replaced wholesale by the overlay — never
  concatenated.

This is a **reference** implementation for proving the spec and seeding
the cross-language fixture — not the Stage C3/C4/C5 production loader in
any of the three languages; no runtime calls it.

## Environment overlays

`environments/production.yml` and `environments/demo_eval.yml` are direct
transcriptions of the two profiles the current system already has
(`app.configuration.profiles.CONSERVATIVE_PROFILE`/`DEMO_EVAL_PROFILE`),
converted into the new nested category layout. `production.yml` is
deliberately empty — "conservative"/production today means "run the base
config as declared, no overrides," and that stays true here. See the
audit's §9 for why these keep their current names instead of the new
architecture's own `development`/`paper` example names, which have no
real behavioral referent in this codebase yet.

## Secrets

Unchanged from today: `POSTGRES_PASSWORD`/`DATABASE_URL`,
`TELEGRAM_BOT_TOKEN`, and the `CTRADER_*` credential variables stay
environment-only, never in YAML. Every other non-secret ENV variable
currently wired into `docker-compose.yml`/`.env.example`
(`AUTO_TRADE_PROFILE`, `AUTO_TRADE_MAPPED_ZONE_ENABLED`,
`AUTO_TRADE_MARKET_MAP_GUARD_ENABLED`, `LOG_DIR`, `LOG_RETENTION_DAYS`,
`LOG_FILE_ENABLED`, `SIGNAL_VIP_CHANNEL_ID`) has a YAML home now in the
files above; removing them from ENV is Stage C7/C8, after a real loader
makes YAML authoritative.

## Validation, fingerprint, cross-language parity

- **JSON Schema**: [`contracts/configuration/apexvoid-config-v3.schema.json`](../contracts/configuration/apexvoid-config-v3.schema.json)
  — `additionalProperties: false` throughout; every leaf in the nine
  "singleton" category files is `required` (a missing `RUNTIME_CONFIG`
  value is meant to fail validation, not silently fall back);
  `instruments.yml`'s per-symbol/per-pack maps use a shared, mostly-
  optional sub-schema since packs/instruments legitimately declare
  different subsets by design.
- **Canonical resolved fixture**:
  [`contracts/configuration/examples/resolved-production-v3.json`](../contracts/configuration/examples/resolved-production-v3.json)
  — the production environment resolved and validated by
  `resolve_reference.py`. Go and .NET loaders reproducing this exact
  document byte-for-byte (after their own normalization) is the Stage C6
  parity bar; those loaders don't exist yet.
- **Fingerprint**: still not implemented. The existing
  `configuration_contract_fingerprint`/`configuration_document_fingerprint`
  machinery in `algo-bot/app/configuration/fingerprints.py` remains the
  intended starting point to adapt.
- **`config-check`**: [`config/scripts/config_check.py`](../config/scripts/config_check.py)
  runs YAML syntax + Stage C2 parity + schema validation for every
  environment as one command. Honest about its own scope in its
  docstring — no cross-language parity or ENV-usage source scanning yet,
  since those need Stage C3–C5 to exist first.

## Adding a new configuration value (target workflow, once C3+ lands)

1. Add the leaf to the owning category file (use the ownership boundary
   above to pick one — it must have exactly one base owner).
2. Add it to the JSON Schema.
3. Add it to each service's typed DTO (Go struct / Pydantic model / C#
   record) that reads that category.
4. Add a parity/validation test.
5. Run `config/scripts/config_check.py`.

**Today**, while no runtime reads these files yet: add the value to
`config/trading-bot.yml` as usual (the live system), and separately keep
the corresponding category file's leaf in sync by hand —
`config/scripts/verify_stage_c2_parity.py` (or, for a leaf Stage C2
didn't touch, `verify_stage_c1_parity.py`) will catch a drift between the
two, and `config_check.py` will refuse an unknown/missing leaf against
the schema.

## Adding a new instrument

Add an entry under `instruments.yml`'s `instruments:` map, following the
existing pack-inheritance pattern (`pack: <name>` plus only the leaves
that genuinely differ for that instrument) — see the extensive comments
already in that file. This part of the workflow doesn't change once a
loader exists; it's how instruments are added today too. Every
instrument-price-denominated leaf (`price_scale.*`, `stop_envelope.*`,
`analysis.zones.*`) must be declared somewhere in the pack/instrument
chain — there is no global fallback to inherit from since Stage C2 §7.

## Adding a Kafka topic

Not applicable yet — no Kafka transport exists in this codebase (see the
audit's §5/§9). When one is actually introduced, its topics/consumer
groups belong under `transport.yml`'s `transport.kafka` block, matching
the shape `transport.redis`/`transport.redis_streams` already use.
