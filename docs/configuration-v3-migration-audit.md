# Configuration V3 Migration — Audit (Stage C0)

Source prompt: `apexvoid-bot-prompts/rebuild-configuration-architecture.md`
(pasted directly into chat, not a checked-in file at time of writing).
Scope: full configuration-source inventory across Python, Go, .NET, and
deployment, per the prompt's §25/§44, before any category YAML is written
or any runtime code changes.

**Read this alongside the existing docs it does not replace**:
[`docs/configuration/configuration-architecture.md`](configuration/configuration-architecture.md)
already documents the current (soon-to-be-superseded) architecture
accurately; this audit's job is to map *that* system onto the new one, not
to re-derive it from scratch. The current system is not naive — it already
has a single Python authority, a catalog-driven ENV contract, two
fingerprint concepts, and a generated `.env.example` — the new
architecture is a real simplification, not a first attempt at discipline.

## 1. Current configuration sources (all three languages + deployment)

### 1.1 Python (`algo-bot/app/configuration/`, ~9.7k lines across `*.py` + `models/`)

Confirmed precedence chain (`resolver.py::_LAYERS`, and stated directly in
`configuration-architecture.md`):

```
schema defaults → profile → file secrets → CONFIG_FILE (YAML) → dotenv
  → process ENV → init values
```

i.e. **a live process environment variable already outranks
`config/trading-bot.yml` today** for any field with a `canonical_env`
binding — this is exactly the "alternate configuration authority" the new
architecture's §1 forbids, and it is the current, intentional, documented
behavior, not a bug. 677 catalog entries total
(`app.configuration.catalog.iter_catalog_entries()`), of which:

- **9 are secrets** (`ctrader.*` credentials, `postgres.password`/`url`,
  `telegram.bot_token`).
- **561 are non-secret fields with a `canonical_env` binding** — i.e. 561
  distinct trading/operational settings that can *today* be overridden by
  a process environment variable, each with its own Python-side default
  baked into a `config_field(default, ...)` call in `app/configuration/
  models/*.py`. Full list already generated at
  [`docs/configuration/environment-reference.generated.md`](configuration/environment-reference.generated.md)
  (582 lines) — not re-transcribed here; that file remains the queryable
  inventory this audit points at rather than duplicates.
- **1 deprecated** entry, plus a small number of `deprecated_env_aliases`
  scattered through `models/*.py` (e.g. `BootstrapLoggingConfig.directory`
  accepts both `LOG_DIR` and the deprecated `APEXVOID_LOG_DIR`).

In practice, only a handful of these 561 are actually wired up anywhere
(`.env.example`, `docker-compose.yml`, ansible) — see §1.4. The other
~550+ are theoretically ENV-overridable by the framework but not exercised
in any deployment today; still a real risk surface (§1 of the new
architecture bars the *capability*, not just observed use), and 100% of
them have a **code-level default** regardless of whether ENV is ever set
(see §1.3).

`BootstrapConfig` (`models/bootstrap.py`) is the one part of this system
that already matches the new architecture's "secrets/bootstrap only via
ENV" model reasonably well: cTrader credentials, Postgres/Redis/Telegram
connection info, logging. It is a reasonable starting shape for what stays
ENV-driven post-cutover, **except** that several of its fields are not
secrets and have real non-secret defaults today (logging directory/
retention/file-enabled/level/filename; Postgres db/user) — these are
candidates to move into YAML's `runtime.yml`/`database.yml` per §3 of the
new architecture, flagged individually in §6 below.

### 1.2 Go (`analysis-engine/internal/config/`)

Built in the prior migration slice (this repo's own
`docs/go-analysis-migration-audit.md`), **not yet wired to anything live**.
Reads `APEXVOID_RUNTIME_MANIFEST_FILE` and parses the compiled
`ResolvedRuntimeManifest` JSON — i.e. it currently depends on exactly the
artifact the new architecture's §21 wants removed. This is expected and
correct for where that migration stood: Stage C4 of *this* prompt
("Replace `ResolvedRuntimeManifest` dependency in analysis-engine with
YAML V3") is the point at which `analysis-engine/internal/config` gets a
categorized-YAML loader instead. No Go ENV-var tuning exists beyond that
one manifest-path variable (confirmed: only `ManifestFileEnv` is read
anywhere in `analysis-engine/`).

### 1.3 .NET (`ctrader-engine/`)

Two live things worth flagging, not one:

1. **A genuine second, independent configuration system.**
   `ResolvedRuntimeManifest.cs` reads `CTRADER_CONFIGURATION_SOURCE`
   (`environment` | `manifest`) to choose between the manifest-based
   config (current production: `manifest`, per `docker-compose.yml`) and
   a **legacy, fully separate ENV-based system**:
   `AutoTradeOptions.cs`'s own `EnvironmentResolver` class, with its own
   canonical-env + `deprecated_env_aliases` resolution and its own
   hardcoded C# fallback defaults (`String(canonical, fallback,
   fallbackSource: "application_default", aliases...)`) — a full parallel
   implementation of what Python's catalog does, independently
   maintained. `CTRADER_MANIFEST_PARITY_MODE` (`off` in compose today)
   exists specifically to cross-check the two against each other. This is
   exactly the kind of alternate authority §1 forbids, currently kept
   alive for parity-checking rather than as the live path.
2. **15 direct `Environment.GetEnvironmentVariable` call sites** across
   `AutoTradeConfigHealth.cs`, `AutoTradeOptions.cs`, `CTraderAccountOptions.cs`,
   `DailyFileLog.cs`, `FeedOptions.cs`, `Program.cs`, `ResolvedRuntimeManifest.cs`.
   `CTraderAccountOptions.cs`/`FeedOptions.cs` read genuine
   bootstrap/secret keys (cTrader credentials/connection) — legitimate
   under the new architecture's §13 allowlist. `DailyFileLog.cs` reads
   `LOG_FILE_ENABLED`/`LOG_DIR`/`LOG_FILE_NAME`/`LOG_RETENTION_DAYS`
   directly, matching Python's bootstrap logging fields exactly (same
   ENV names, same defaults) — the .NET and Python logging config is
   *already* effectively shared via these ENV vars, which is actually a
   point in favor of moving it to YAML's `runtime.yml` rather than
   leaving it ENV-duplicated in two languages.

### 1.4 Deployment (`docker-compose.yml`, `.env.example`, `.env`)

`docker-compose.yml` (136 lines, 4 services: `postgres`, `redis`,
`config-compiler`, `ctrader-engine`, `bot`) is the ground truth for what's
*actually* ENV-tuned today, as opposed to merely ENV-tunable:

```
APEXVOID_CONFIG_FILE=/config/trading-bot.yml            (bot only)
APEXVOID_RUNTIME_MANIFEST_FILE=/runtime/resolved-runtime.json  (compiler, ctrader-engine, bot)
CTRADER_CONFIGURATION_SOURCE=manifest                     (ctrader-engine)
CTRADER_MANIFEST_PARITY_MODE=off                          (ctrader-engine)
AUTO_TRADE_PROFILE=${AUTO_TRADE_PROFILE:-demo_eval}        (bot)
AUTO_TRADE_MAPPED_ZONE_ENABLED=${...:-false}               (bot)
AUTO_TRADE_MARKET_MAP_GUARD_ENABLED=${...:-${AUTO_TRADE_MAPPED_ZONE_ENABLED:-false}}  (bot — a default that
                                                             itself falls back to ANOTHER env var's default;
                                                             two layers of hidden precedence in one line)
LOG_DIR / LOG_RETENTION_DAYS / LOG_FILE_ENABLED / LOG_FILE_NAME (ctrader-engine + bot, same values both places)
```

`.env.example` (42 lines, generated by `python -m app.configuration.generate
--write` per its own header) lists the real secrets (`POSTGRES_PASSWORD`,
`TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, `CTRADER_CLIENT_ID`/`CLIENT_SECRET`/
`ACCESS_TOKEN`/`REFRESH_TOKEN`/`ACCOUNT_ID`) plus exactly the same
non-secret "optional knobs" compose already sets: `SIGNAL_VIP_CHANNEL_ID`,
`REDIS_URL`, `AUTO_TRADE_PROFILE`, `AUTO_TRADE_MAPPED_ZONE_ENABLED`,
`AUTO_TRADE_MARKET_MAP_GUARD_ENABLED`, `LOG_DIR`, `LOG_RETENTION_DAYS`,
`LOG_FILE_ENABLED`. `AUTO_TRADE_PROFILE`, `AUTO_TRADE_MAPPED_ZONE_ENABLED`,
and `AUTO_TRADE_MARKET_MAP_GUARD_ENABLED` are **verbatim the three examples
the new architecture's §13 names as forbidden** — confirmed real, not
hypothetical. `.env` itself (the actual local secrets file) is empty in
this checkout — nothing to audit for leakage there.

No ansible templates exist in this repository (`config/trading-bot.yml`'s
own header comment says "Production is rendered by ansible from cleartext
vars onto the host" — that rendering pipeline is outside this repo's
tracked files and out of this audit's reach; flagged for the owner, not
something I can inventory from here).

### 1.5 Generated manifest / projection dependencies

```
config/trading-bot.yml
        ↓ (config-compiler service: `python -m app.configuration.runtime_manifest_cli`)
runtime-config volume: resolved-runtime.json
        ↓
ctrader-engine (CTRADER_CONFIGURATION_SOURCE=manifest)
bot            (reads both APEXVOID_CONFIG_FILE AND APEXVOID_RUNTIME_MANIFEST_FILE)
analysis-engine (reads only the manifest — see §1.2; no direct YAML reader exists yet)
```

Both `contracts/configuration/runtime-manifest-*.generated.json` files are
themselves generated artifacts (schema + example + ENV-migration report),
already used as parity/test fixtures by `analysis-engine`'s own Stage-1
tests (`internal/config/manifest_test.go`) and by
`algo-bot/tests/test_config_runtime_manifest.py` /
`ctrader-engine/tests/ResolvedRuntimeManifestTests.cs`. Per this prompt's
§21, the generated JSON "may remain temporarily for migration tests only"
— none of these test fixtures need deleting yet, only stop being a runtime
dependency.

## 2. Code-level defaults that affect trading behavior

Every one of the 677 Python catalog entries has a hardcoded default via
`config_field(default, ...)` in `app/configuration/models/*.py` — this is
the systemic instance of §14's concern, not a handful of stray constants.
The catalog's own `default_contexts` field goes further: several entries
carry **two different defaults for two different runtime contexts**
(`DefaultContext.PYTHON_SCHEMA` vs `DefaultContext.CTRADER_FROM_ENVIRONMENT`)
— e.g. `BootstrapLoggingConfig.retention_days` defaults to `14` under both
contexts today (they happen to agree), but the *mechanism* for them to
silently diverge exists and is exercised by the codebase's own
parity-checking infrastructure, which only makes sense as a concept
because two independent default sources exist. §14's "missing setting:
startup failure, do not silently fall back" is a direct, correct fix for
this class of problem — not a hypothetical one.

`.NET`'s `AutoTradeOptions.EnvironmentResolver.String/…(canonical,
fallback, fallbackSource: "application_default", aliases...)` pattern
(§1.3) is the same problem a second time, independently implemented, with
its own hardcoded fallback values baked into C# call sites rather than a
catalog.

No comparable default-injection pattern was found in `analysis-engine`
(Go) — it has no trading-behavior config yet (only the manifest geometry
loader from the prior migration slice), so there is nothing there to
audit for hidden defaults in *this* pass; flagged as something to hold
the line on once Stage C4 gives it real config surface.

## 3. Docker Compose defaults that change runtime behavior

Enumerated in full at §1.4. The three `AUTO_TRADE_*` variables and the
four `LOG_*` variables are the ones that change trading/operational
behavior (as opposed to `APEXVOID_CONFIG_FILE`/`APEXVOID_RUNTIME_MANIFEST_FILE`/
`CTRADER_CONFIGURATION_SOURCE`/`CTRADER_MANIFEST_PARITY_MODE`, which are
pure plumbing — which config system to use, not a trading parameter
themselves, and which disappear entirely once the new architecture lands,
per §21).

## 4. Existing generated manifest/projection dependencies

Covered in §1.5. Restated here per the prompt's own numbered list: the
generated `resolved-runtime.json` (via `config-compiler`) is the one
artifact every runtime currently depends on that the new architecture
explicitly wants removed as a *runtime* dependency (§21) while allowed to
persist as a migration-test fixture.

## 5. Category destination for every existing configuration leaf

`config/trading-bot.yml` (898 lines, `version: 1`) is already organized
into 14 top-level keys that map onto the new category files with
reasonable fidelity — this migration is substantially a **file split with
a few deliberate recategorizations**, not a redesign of what the settings
mean. Mapping, with every non-trivial judgment call called out explicitly
rather than silently decided:

| Current top-level key | New category file | Notes |
|---|---|---|
| `actionability.*` | `auto-algo.yml` | Matches the new architecture's own §5 example (`auto_algo.actionability.minimum_confluence`) directly. |
| `analysis.*` (trendlines, triggers, techniques, zones) | `analysis.yml` | Direct match. |
| `contract.account.*`, `contract.mode` | `runtime.yml` | Service/broker identity (`expected_broker`, `require_demo`, `v8_only` contract mode) — this is "what kind of runtime am I," matching §3's "service identity." |
| `contract.instrument.*` | **superseded by `instruments.yml`** | `contract.instrument.canonical_symbol`/`symbols` is a flat duplicate of the per-symbol list `instruments.yml` already carries structurally; drop rather than carry two representations of the same list forward. Flagged for confirmation, not dropped silently — see §9. |
| `contract.streams.*`, `contract.versions.*` | `transport.yml` | These are the Redis stream names/candidate versioning (`auto_trade:candidates`, `auto_trade:events`, `execution:trade_plans`) — the direct analogue of the new architecture's Kafka `topics:`/versions block, just for the transport actually in use today. **No Kafka topics exist anywhere in this codebase** (confirmed by repo-wide grep) — the new architecture's `transport.kafka.topics` example is aspirational per its own §33 ("may move... to Kafka"); Stage C1 populates `transport.yml.redis` with real current values and does **not** fabricate a `kafka:` block with invented topic names. |
| `delivery.*` | `telegram.yml` | Direct match to the new architecture's §8 example (`lifecycle`, `reports.weekly`, `presentation`, `telegram.*`). One nested field, `delivery.presentation.seq_reset_tz`, is a timezone but is specifically Telegram sequence-reset scoped, not the service's general runtime timezone (`runtime.timezone` in the new scheme, which doesn't exist as a distinct setting today) — kept under `telegram.yml` rather than promoted, flagged in §9. |
| `execution.*` | `execution.yml` | Direct match. |
| `instrument_packs.*` + `instruments.*` | `instruments.yml` | The existing pack-inheritance mechanism (`instruments.<SYM>.pack: <name>`, pack merges under the instrument, instrument always wins on a shared key) is **preserved as-is** in Stage C1, not flattened — see §9; flattening every symbol to its fully-resolved geometry would itself be a real, silent design change (losing pack reuse) that Stage C1's "do not change trading behavior" scope should not make unilaterally. |
| `lifecycle.*` | `auto-algo.yml` | Setup/candidate/zone lifecycle — matches §5's "lifecycle: setup_expiry_bars" example. |
| `manual_algo.*` | `manual-algo.yml` | Direct match. |
| `market_data.*` | `analysis.yml` (proposed; **not confidently categorized — see §9**) | Lookbacks/sessions/calendar read as analysis inputs; `market_data.ctrader_feed.*` (the feed's own symbol/timeframe list) arguably belongs in `transport.yml` or `runtime.yml` instead. The new architecture's own category list in §3 never names a home for `market_data` at all — genuine gap, flagged for the owner rather than guessed past. |
| `risk.*` | `auto-algo.yml` | Exposure/position-limits/sizing/tiers are "should ApexVoid act on it" policy — matches the architectural boundary §5 draws explicitly. |
| `runtime.auto_trade.*`, `runtime.scanner.enabled` | `auto-algo.yml` | **Important naming collision, not a translation**: the *current* top-level `runtime:` key (auto-trade/scanner enable toggles) means something entirely different from the *new* `runtime.yml` file (timezone/logging/service identity per §3). Renaming the destination file without renaming what it means would silently conflate two unrelated things under the same word — called out here explicitly so it isn't missed. |
| `strategies.*` | `auto-algo.yml` | Matches §5's own example (`auto_algo.strategies.breakout_retest.enabled`) directly. |
| `version: 1` | dropped; replaced by root `apexvoid.yml`'s `version: 3` | Per §18. |
| *(not present in trading-bot.yml today)* `runtime.timezone`, `runtime.logging.*`, `runtime.environment` | `runtime.yml` | Currently ENV/code-default only (`bootstrap.py`'s `BootstrapLoggingConfig`, `AUTO_TRADE_PROFILE`) — populated in Stage C1 with today's real effective defaults (`/var/log/apexvoid`, retention 14, file_enabled true, level INFO), not invented values. `runtime.environment` is new — see §9 on the profile→environment rename. |
| *(not present today)* `database.yml`'s `host`/`port`/`database`/`username` | `database.yml` | Today the whole Postgres connection is one opaque `DATABASE_URL` secret (no separate non-secret host/port fields in the Python model) — populated in Stage C1 from the *known* non-secret topology (compose's own `postgres` service name, default port 5432, `POSTGRES_DB`/`POSTGRES_USER` defaults `signals`/`apexvoid`), which is documentation of the current real topology, not a behavior change. Actually switching Python's runtime connection to build a DSN from these fields plus a bare password secret is a Stage C3 decision, not done here. |

## 6. Proposed YAML V3 structure

Implemented in Stage C1 (this same change) exactly as laid out in the new
architecture's §2: `config/apexvoid.yml` root with `version: 3` and
`includes:`, plus `runtime.yml`, `transport.yml`, `database.yml`,
`instruments.yml`, `analysis.yml`, `auto-algo.yml`, `manual-algo.yml`,
`execution.yml`, `telegram.yml`, `journal.yml`, `environments/{development,
paper,production}.yml`. `journal.yml` is new — no existing YAML section
covers trade-journaling behavior today (it's implicit in `contract.streams`/
Postgres writes); populated with the same shape as the new architecture's
own §9 example, values chosen to match current effective behavior (the
system already persists trade plans, analysis snapshots via
`auto_trade:events`, and execution events — nothing here is currently
configurable as an on/off switch, so Stage C1 records the *observed*
always-on state rather than inventing toggles that don't exist yet).

## 7. Proposed merge/overlay semantics

Not implemented in this pass (Stage C2). Recorded here for continuity:
deep merge for mappings, full replacement for scalars and lists (never
concatenate), environment overlay as the only override layer, one base
owner per dotted path enforced by a duplicate-path check across the base
category files (excluding the overlay). The existing two-profile system
(`conservative`, `demo_eval`, selected via `AUTO_TRADE_PROFILE`) is the
direct predecessor of `environments/*.yml` — see §9 for the naming
question this raises.

## 8. Proposed cross-language validation strategy

Not implemented in this pass (Stage C2+). Recorded for continuity: a
JSON Schema under `contracts/configuration/apexvoid-config-v3.schema.json`
(§17), a canonical resolved fixture
(`contracts/configuration/examples/resolved-production-v3.json`, §29) that
Python/Go/.NET loaders must each reproduce byte-for-byte after their own
normalization, and a fingerprint (§19) computed the same way in all three.
The existing `configuration_contract_fingerprint`/
`configuration_document_fingerprint` machinery in `fingerprints.py` is a
reasonable starting point to adapt for the V3 scheme rather than a design
to discard — it already solves "fingerprint the behaviorally-relevant
subset, not comments/formatting."

## 9. Open questions for the owner (flagged, not decided unilaterally)

1. **`runtime:` naming collision** (§5 table) — the current
   `trading-bot.yml`'s `runtime.auto_trade.*` and the new `runtime.yml`
   file mean different things. Stage C1 puts the auto-trade toggles in
   `auto-algo.yml` and reserves `runtime.yml` for the new
   timezone/logging/environment meaning, per the new architecture's own
   §3 definition — flagging in case that reading is wrong.
2. **`market_data.*` has no named destination** in the new architecture's
   own category list. Stage C1 places it in `analysis.yml`; confirm or
   redirect (candidates: split `ctrader_feed`/`scanner` into
   `transport.yml`, keep `calendar`/`sessions`/`spot`/`watcher` in
   `analysis.yml`).
3. **Profile → environment rename**: today's two profiles are
   `conservative` and `demo_eval`; the new architecture's example overlay
   names are `development`/`paper`/`production`. Stage C1 creates
   `environments/production.yml` (≈ today's `conservative`, the live
   profile) and `environments/demo_eval.yml` (≈ today's `demo_eval`,
   kept under its current name since it's a real, load-bearing name
   referenced by `AUTO_TRADE_PROFILE=demo_eval` in compose/ansible today)
   rather than inventing a `development.yml`/`paper.yml` split that has
   no current behavioral referent. Renaming/splitting further is a
   product decision, not mine to make silently.
4. **`instrument_packs` flattening** — kept as-is in `instruments.yml`
   for Stage C1 (§5 table); flattening to fully-resolved per-symbol
   geometry is a legitimate simplification the new architecture's own
   `instruments.yml` example implies (no `pack:` key shown there) but is
   a real design change, deferred to C2/C3.
5. **`database.yml` host/port/username split vs. the current single
   `DATABASE_URL` secret** — Stage C1 documents the known-real non-secret
   topology; actually changing how services connect is out of scope here.
6. **Kafka** — no topics exist yet anywhere in the codebase; `transport.yml`
   ships with only `redis:` populated in Stage C1. Add `kafka:` once a
   real topic/consumer-group naming decision is made, not before.

## 10. Files/modules proposed for deletion after cutover (not touched in this pass)

Per the new architecture's §21/§26 (Stage C7/C8) — listed for the record,
none of these are removed as part of Stage C1:

- `config-compiler` Docker Compose service, `runtime-config` volume,
  `APEXVOID_RUNTIME_MANIFEST_FILE`, `CTRADER_CONFIGURATION_SOURCE`,
  `CTRADER_MANIFEST_PARITY_MODE`.
- `algo-bot/app/configuration/runtime_manifest.py`,
  `runtime_manifest_boot.py`, `runtime_manifest_cli.py` (manifest
  generation), once Python reads categorized YAML directly (Stage C3).
- `ctrader-engine/src/ResolvedRuntimeManifest.cs`,
  `ManifestRuntimeFactory.cs`, `ResolvedRuntimeManifestLoader.cs`,
  `RuntimeManifestParity.cs`, and `AutoTradeOptions.cs`'s
  `EnvironmentResolver`/legacy ENV path, once .NET reads categorized YAML
  directly (Stage C5).
- `analysis-engine/internal/config/manifest.go`/`geometry.go` (the
  manifest-JSON reader built in the prior migration slice), replaced by a
  categorized-YAML loader (Stage C4) — note this is code I wrote two
  migration slices ago in *this same session*; it was correct for where
  that migration stood, and is now itself one of the things this
  migration supersedes.
- Large parts of `algo-bot/app/configuration/` per the new architecture's
  own §22 target (`catalog.py`, `catalog_validation.py`,
  `compatibility_rules.py`, `environment_aliases.py`,
  `environment_option_resolution.py`, `environment_usage_audit.py`,
  `instrument_packs.py`, `instrument_runtime_scope.py`,
  `migrate_env_to_config.py`, `profiles.py`, `profile_validation.py`,
  `python_sources.py`, `source_policy.py`, `sources.py`,
  `source_types.py`, `traversal.py`) — only after Stage C3 parity is
  proven, exactly as §22 says ("Delete obsolete modules only after parity
  is proven").
- `.env.example`'s non-secret entries (`AUTO_TRADE_PROFILE`,
  `AUTO_TRADE_MAPPED_ZONE_ENABLED`, `AUTO_TRADE_MARKET_MAP_GUARD_ENABLED`,
  `LOG_DIR`, `LOG_RETENTION_DAYS`, `LOG_FILE_ENABLED`,
  `SIGNAL_VIP_CHANNEL_ID`, `REDIS_URL`) once those values live in YAML —
  keep only the real secrets plus `APEXVOID_CONFIG_FILE`.
