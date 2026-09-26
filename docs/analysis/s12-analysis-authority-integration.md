# S12 — Go Analysis Authority Integration

## Scope delivered: S12A and S12B

The Algo Bot now has a strict, durable receiving boundary for the Go Analysis
Engine's V1 Kafka events. Deployment defaults retain Python authority:

```yaml
analysis:
  technical_authority:
    mode: python
    consumer_enabled: false
```

No deployment of this change starts a Kafka consumer or changes a live trading
decision. `mode: go` is rejected at configuration load time; it requires the
separately approved S12D implementation.

## S12A: lifecycle foundation

- `analysis_client.models` validates topic/envelope agreement, expected
  producer, V1 version, time order, price geometry, directional stop/targets,
  quality and required evidence before policy sees an event.
- `analysis_client.repository` writes every accepted delivery to PostgreSQL
  before a Kafka offset may be committed. `event_id` is the delivery fence;
  `opportunity_id` is the lifecycle identity.
- Terminal events received before a creation create a terminal tombstone. A
  later creation cannot reactivate it. Replays are idempotent.
- Contract-invalid records are durably stored in
  `analysis_opportunity_rejections` before their offset is committed, so one
  poison record cannot stall the partition indefinitely.
- Consumer offsets are manually committed one record at a time after durable
  processing. Database failures leave the offset uncommitted for redelivery.

## S12B: policy shadow

In `go_shadow`, each accepted creation produces a durable `contract_gap`
decision. It does not call a TradePlan builder, Redis trade-plan publisher,
risk reservation, Telegram delivery, or broker path.

The current `analysis.opportunity.v1` contract does not contain the factual
inputs needed for safe parity with the legacy Python execution path:

1. current executable price and spread context;
2. ATR used for policy/geometry;
3. confluence components and score provenance;
4. source-structure geometry and structural context;
5. execution confirmation state; and
6. strategy routing/mode facts.

Those belong in a reviewed V2 contract extension. Python must not infer them by
running a second detector.

## S12C entry criteria

Enable `go_shadow` only in a dedicated configuration/deployment change after:

- broker/topic/group reachability is proven;
- consumer lag, rejected-record rate, lifecycle dispositions and shadow
  outcomes have dashboards/alerts;
- same-input Python/Go comparison is reconciled across XAU and FX; and
- the known producer publication latency is accounted for independently from
  technical `occurred_at` age.

Rollback is configuration-only: set `consumer_enabled: false` (or retain
`mode: python`) and redeploy. Existing ledger rows remain audit evidence;
there is no execution state to unwind.

## S13B: fenced technical-authority switch (implemented)

Kafka publication is not a cutover. Who may create an executable TradePlan for a
`(symbol, catalog strategy)` scope is a separate, durable, fenced decision:
`algo-bot/app/analysis_client/authority.py`.

| Property | Behaviour |
|---|---|
| Default | Every scope is Python-owned (no row = epoch 0). Deploying this changes nothing. |
| Consulted when | Only if `consumer_enabled` is true (or a match is Go-tagged). With the consumer off, Python owns everything and no DB read is made. |
| Handover | `python → draining → go` and back. While draining **neither** publisher may create a plan; the target takes effect after `drain_until`. Minimum drain is 3× the read-cache TTL, so a process holding a stale read is provably quiet first. |
| Fencing token | Every handover advances a monotonic `epoch`, compare-and-set on the caller's `expected_epoch` (real Postgres test: 8 concurrent handovers → exactly 1 wins). A Go match carries its accepted epoch and is refused after any later handover, rollback or re-grant. |
| Go needs acceptance | A grant requires an operator-recorded, unexpired acceptance for the exact symbol/scope/evidence. **Nothing in the code base writes one.** |
| Rollback | Always allowed, needs no acceptance, works mid-drain; Go stops immediately, Python resumes after the drain. `rollback-all` is the emergency form. |
| Failure mode | Any fence read failure **denies** publication (two publishers is worse than a skipped plan). |
| Where enforced | The single executable-plan path: `worker._publish_trade_plan_v8` (the only caller of `publish_trade_plan`). Reconciling an already-published plan is deliberately not fenced; ownership governs creation, never management of existing positions. |
| Not fenced | Analysis-only Telegram observations, manual trading, position management, retired legacy strategy names (no Go equivalent). |

Operator interface (audited, nothing automatic):

```bash
python -m app.scripts.analysis_authority status [--symbol XAU]
python -m app.scripts.analysis_authority accept   --symbol XAU --scope supply --evidence <ref> --approved-by <name>
python -m app.scripts.analysis_authority grant    --symbol XAU --scope supply --expected-epoch 0 --evidence <ref> --actor <name> --reason <text>
python -m app.scripts.analysis_authority rollback --symbol XAU --scope supply --expected-epoch <n> --actor <name> --reason <text>
python -m app.scripts.analysis_authority rollback-all --actor <name> --reason <text>
```

Every transition is appended to `analysis_authority_transitions`. Rollback is
therefore both configuration-free (a DB row) and configuration-level
(`consumer_enabled: false` returns every scope to Python with no DB read).

What this does **not** do: it does not itself produce Go-owned plans — see the
S13C adapter (`autotrade/go_opportunity_policy.py`) and the two policy blockers
it exposed in `s13-python-retirement.md`. Until those are resolved by an owner
decision, no acceptance should be recorded.

## S13B: additive `technical_context` (policy inputs from Go)

The S12B shadow recorded six missing inputs. `analysis.opportunity.v1` now
carries an **additive, optional** `technical_context` block, assigned by
`SymbolWorker` at the same observation boundary as `timeframe` (never by a
strategy) from the exact closed bar that first made the setup actionable:

| Field | Meaning | Proof |
|---|---|---|
| `atr` | canonical ATR of `timeframe` as of the observed bar | engine test: equals `CanonicalATR(candles[:i+1])` for every live opportunity on real XAU data (no look-ahead, no recompute drift) |
| `reference_price`, `reference_time` | close / open time of the observed bar | equals the real bar's close; `reference_time == created_at` |
| `bias` (optional) | confirmed structural bias; **omitted**, never a guessed neutral | schema rejects `NEUTRAL` |

Resolution of the S12B gap list:

1. *current executable price / spread* — **deliberately not carried.** A live
   quote is the policy layer's own concern; embedding one in a technical event
   would be stale on arrival. The schema forbids spread/account fields.
2. *ATR* — provided (above).
3. *confluence components* — the strategy's own `evidence` and `quality`
   components are the facts; there is no universal score. The Python mapping
   from these to the legacy integer `confluence` is an adapter concern and is
   reviewed there (S13B-4), not smuggled into the contract.
4. *source-structure geometry* — the entry band **is** the zone/level band for
   zone-anchored strategies; `strategy` + `direction` identify the kind.
5. *execution confirmation* — a Go opportunity is a resting technical thesis,
   not a confirmed reaction; the existing V8 execution-confirmation machinery
   (quote-in-zone, WAITING_RETEST) stays Python policy.
6. *strategy routing* — a reviewed per-scope profile in the adapter, not data.

When the block is absent (e.g. ATR not yet available, or a retained pre-S13B
event) consumers must fail closed. **Deployment order matters**: `algo-bot`'s
decoder forbids unknown fields, so a Python build without this change would
reject every new opportunity; the Python consumer is off by default, but roll
`algo-bot` out first.

The bytes are pinned in `contracts/analysis/examples/opportunity-v1-technical-context.json`.
Go's contract test regenerates and compares them (`UPDATE_GOLDEN=1` to refresh);
algo-bot's decoder test parses the same file, so neither side can drift alone.
