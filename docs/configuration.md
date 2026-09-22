# Configuration (V3)

Status: **Stage C1 only** (categorized YAML files exist and are parity-
verified against today's live config; no runtime reads them yet). See
[`docs/configuration-v3-migration-audit.md`](configuration-v3-migration-audit.md)
for the full audit and the staged plan (C0–C8). Until Stage C3/C4/C5 land,
**`config/trading-bot.yml` plus the generated `ResolvedRuntimeManifest`
remain the live authority** — everything below describes the target
architecture and what's built so far toward it, not what's running in
production today. That older, still-live system is documented separately
at
[`docs/configuration/configuration-architecture.md`](configuration/configuration-architecture.md).

## Root file and category files

`config/apexvoid.yml` is the entrypoint. It declares `version: 3` and an
`includes:` list of category files, each owning one non-overlapping
domain:

| File | Owns |
|---|---|
| `runtime.yml` | Timezone, environment, logging, service/broker identity, execution contract mode. |
| `transport.yml` | Redis (and, once real topics exist, Kafka) connection + stream/topic names. |
| `database.yml` | Non-secret Postgres topology. |
| `instruments.yml` | Per-instrument geometry, pack inheritance, rollout state. |
| `analysis.yml` | Market-analysis behavior (ATR algorithm, trendlines, zones, techniques) plus market-data inputs (calendar, sessions, feed, scanner). |
| `auto-algo.yml` | Auto-trading orchestration/policy: actionability, lifecycle, risk, strategy enable/threshold, scalping. |
| `manual-algo.yml` | Manual/operator workflow behavior not already instrument-scoped in `instruments.yml`. |
| `execution.yml` | Execution/broker mechanics — the executor stays mechanical; no market interpretation here. |
| `telegram.yml` | Telegram presentation/lifecycle/reporting. |
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

## Include semantics

`apexvoid.yml`'s `includes:` list is read in order; each entry is a path
relative to `config/`. Not yet implemented: an actual loader that reads
this list and merges the files (Stage C2). Today the list is documentation
of intended load order, verified by
[`config/scripts/verify_stage_c1_parity.py`](../config/scripts/verify_stage_c1_parity.py)
only insofar as that script checks every leaf value, not the include
mechanism itself.

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

Merge semantics (deep merge for maps, full replacement for scalars and
lists, environment overlay as the only override layer, one base owner per
dotted path) are specified in the audit's §7 but **not implemented** —
Stage C2.

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

Not implemented (Stage C2+). Placeholders only:

- JSON Schema: `contracts/configuration/apexvoid-config-v3.schema.json`
  (not created yet).
- Canonical resolved fixture:
  `contracts/configuration/examples/resolved-production-v3.json` (not
  created yet).
- Fingerprint: the existing `configuration_contract_fingerprint`/
  `configuration_document_fingerprint` machinery in
  `algo-bot/app/configuration/fingerprints.py` is the intended starting
  point to adapt, per the audit's §8 — not yet adapted.

## Adding a new configuration value (target workflow, once C2+ lands)

1. Add the leaf to the owning category file (use the ownership boundary
   above to pick one — it must have exactly one base owner).
2. Add it to the JSON Schema.
3. Add it to each service's typed DTO (Go struct / Pydantic model / C#
   record) that reads that category.
4. Add a parity/validation test.
5. Run the config-check command (not yet built).

**Today**, while Stage C1 is the only thing built: add the value to
`config/trading-bot.yml` as usual (the live system), and separately keep
the corresponding category file's leaf in sync by hand until a real
loader exists — `config/scripts/verify_stage_c1_parity.py` will catch a
drift between the two.

## Adding a new instrument

Add an entry under `instruments.yml`'s `instruments:` map, following the
existing pack-inheritance pattern (`pack: <name>` plus only the leaves
that genuinely differ for that instrument) — see the extensive comments
already in that file. This part of the workflow doesn't change once a
loader exists; it's how instruments are added today too.

## Adding a Kafka topic

Not applicable yet — no Kafka transport exists in this codebase (see the
audit's §5/§9). When one is actually introduced, its topics/consumer
groups belong under `transport.yml`'s `transport.kafka` block, matching
the shape `transport.redis`/`transport.redis_streams` already use.
