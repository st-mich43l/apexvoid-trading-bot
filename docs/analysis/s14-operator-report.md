# S14 operator report: Kafka-to-live algo cutover

**Status: engineering complete, production evidence and operator approval outstanding. Live Go-driven trading has NOT started.**
No `accept` or `grant` has been run, no approver or evidence reference was supplied, no production system was contacted, and no
shadow, acceptance or exposure figure in this repository is a production measurement. Everything below labelled "proved" was proved in
CI-equivalent runs (real Redis and PostgreSQL, the production Lua, the C# executor against a broker simulator, a real capture replay).

## 1. What changed, by PR (merge in this order)

| order | PR | phase | what it gives |
| --- | --- | --- | --- |
| 1 | #641 | S14B | **merged.** Durable activation boundary (`go_effective_at`), freshness gate (`pre_activation_event`, `opportunity_expired`, `event_too_old`, `delivery_lag_exceeded`), Kafka invalidation/expiry withdrawing queued work through a cancel intent the C# executor honours, fail-closed authority snapshot when `consumer_enabled` changes, boot/runtime audit, rollback = fence then withdraw |
| 2 | #642 | S14A | real policy dry-run shadow (overlay Redis, read-only DB, no fence bypass outside the overlay, no Telegram); replaces the static `contract_gap` |
| 3 | #644 | S14D | Go event to a real V8 plan through the unmodified chain, executor over a broker simulator, never `bypass_analysis_gates`; stable thesis identity (one plan per zone) |
| 4 | #645 | S14E | one reviewed XAU ladder spec with Python/C# parity, full-precision contract vs whole-point card, worst-case group-risk function, default-off `go_origin_risk_leg_enabled` |
| 5 | #646 | S14F-H | acceptance report, cutover packet, rollback drills, runbooks (this report) |
| 6 | #647 | S14F | config `go_shadow` in `config/analysis.yml` — **had no effect on the bot** (see section 8) |
| hold | #648 | S14G | config `mode: go` in `config/analysis.yml`, merged although drafted — **no effect on the bot**; returned to `python` in the review PR |
| any | #643 | S14C | deterministic Go-vs-Python replay + committed report on a real capture. Independent of the stack |

You merge; automation cannot. (#647 and #648 are stacked: retarget #647 to master after #646 merges.)

## 2. Proved

* **S14A.** Dry-run decisions come from the real policy on an overlay; a test enumerates every write path (Redis, PostgreSQL, streams, pub/sub, Telegram) and none escapes.
* **S14B.** Backlog, replay and pre-activation events cannot become live plans; an event is refused before the durable boundary even if delivered late; terminal/invalid/expired events withdraw unexecuted matches and queued plans; submitted-unfilled, partial and open positions have defined, tested outcomes; consumer-flag changes never reopen a Go-owned scope to legacy Python.
* **S14C.** A causal, deterministic replay. Findings (Go is **not** equivalent to the Python detector; see section 4).
* **S14D.** A Go-origin opportunity reaches the executor as one V8 plan, every Auto Algo control retained (risk/exposure/protection/stop/TP/trailing), same fixture bytes in Python and C#.
* **S14E.** One ladder spec; parity tests; worst-case group loss is computed, not assumed; risk leg off for Go-origin plans by default.
* **S14H.** Rollback at four lifecycle stages; never two publishers at any instant; `withdraw` works with PostgreSQL down; dependency outages fail closed.

Suites on the top of the stack: autotrade allowlist 1164 passed (the 1 failure and 23 errors are the identical pre-existing set on master, id-for-id), C# 858 passed against real Redis, Go build/vet/test green, `config-check` green.

## 3. Not done, and why (exact outstanding gates)

| gate | who | evidence needed | command |
| --- | --- | --- | --- |
| merge #642, #644, #645, #646, #647 | operator | green CI | (GitHub) |
| deploy the review PR (persistent Kafka log dir + engine ledger volume) and restart the executor | operator | Kafka data on the volume; executor healthy | section 7 |
| set `go_shadow` in the **ansible vars** (not `config/analysis.yml`) and verify the consumer runs | operator | boot audit `go_shadow / t`, consumer health `ready`, Kafka group exists | runbook `s14f-production-shadow-runbook.md` §2 |
| production `go_shadow` window on a live session | operator | consumer group assigned, lag, decision counts, side-effect counts | runbook §3 |
| Kafka restart and outage drills | operator | measured recovery | runbook §5 |
| deployed image SHAs equal reviewed commits | operator | `images.json`, `reviewed.json` | runbook §6 |
| acceptance report shows `ready_for_operator_review` | operator | all nine gates evidenced | `python -m app.scripts.shadow_acceptance ...` |
| disposition of the S14C differences | owner | named approval with evidence | runbook §5 `approvals.json` |
| live risk review | operator | after a live phase | runbook §5 |
| **explicit approval for the scoped cutover** | operator | approver name and evidence ref | `analysis_authority accept` / `grant` (see `s14g-scoped-cutover.md`) |

**STOP.** After the packet (`python -m app.scripts.cutover_packet ...`) the next step is a human decision. Do not run `accept` or `grant` on
anyone's behalf.

## 4. Findings the owner must decide on (from the S14C replay, `docs/analysis/reports/s14c-policy-replay-xau-20260921.md`)

Capture: real production XAU bars (H1 300, M15 600, M5 1500), Go 185 confirmed cases, Python 177 observations. Verdict `unresolved_differences`.

* Strict matches 22; Python-only 155; Go-only 163 (many-to-many coverage 39/185 Go, 53/177 Python).
* Go confluence is an evidence **count**, constant 4, so every Go case is Tier A (Python matched: 19 tier B, 3 tier A). The risk multiplier ignores the tier today, so this changes no size, but any tier-dependent policy would treat Go setups as strongest.
* Higher-timeframe: 9 matched setups conflict with H1 bias; 12 cases have no H1 bias and are rejected by the adapter (`higher_timeframe_bias_unavailable`). The production feed carries no H4; the replay did not synthesise one.
* ATR: Go reports the configured `simple` ATR (185/185 recompute equal); Python's detectors use Wilder; they differ by about -24 % to +31 % (median +1 %).
* Go prices are not on the instrument tick (0/185), stops are wide (median 113 pips), first-target R:R median 0.54. The builder's TP, not Go's target, is what the plan executes.
* Go carries no opposing-structure fact; Python's barrier and target-room reads are **retained** in the worker.

These are difference findings, not defects to auto-fix. Any change of Go behaviour belongs in its own reviewed change.

## 5. Risk-leg gate (S14E)

`go_origin_risk_leg_enabled` is `false`: Go-origin XAU plans carry the declared ladder only. Turning it on needs the worst-case group-risk
result for the account's equity to be reviewed first (`xau_ladder.worst_case_group_loss`; the packet prints the exposure with and without the leg).
It is a separate gate and does not block the authority cutover.

## 6. Python cleanup: blocked

Deletion only after stable accepted production operation and a fresh S13 classification. Today: 62 legacy modules, 0 unclassified,
19 production importers still block deletion. Nothing was removed. See `s14h-rollback-and-cleanup.md`.

## 7. Post-merge production check (read-only, 2026-09-26 ~15:00 UTC, master `a75b655` deployed)

Nothing was changed on the host. Facts, then what they mean:

| finding | evidence | consequence |
| --- | --- | --- |
| The bot runs `mode=python`, consumer **off** | `analysis_authority_runtime_audit` boot rows `python / f`; no `component_health:analysis_opportunity_consumer` key; Kafka group `apexvoid-algo-bot-analysis-opportunity-v1` does not exist; bot log has no consumer line | #647/#648 changed a file the bot does not read. Production behaviour unchanged (safe), but the shadow window has **not started**. The real switch is the ansible-rendered trading-bot.yml |
| No authority moved | `analysis_authority_scopes/transitions/acceptance` all 0 rows; log `non-Python scopes now none` | as designed; no `accept`/`grant` exists |
| Kafka is empty | both opportunity topics end offset 0 on all partitions | expected today (Saturday, closed market; ledger has 1198 bootstrap `suppressed` records, 0 published) — not evidence of a fault |
| **Kafka data is not persistent** | broker logs to `/tmp/kafka-logs` (container layer); the `kafkadata` volume at `/var/lib/kafka/data` is empty; the container was recreated 12:46:44 | every recreate wipes topics **and** consumer offsets; fixed in the review PR (`KAFKA_LOG_DIRS`), verified locally: topic data and a group offset survive a recreate |
| **Go publication ledger not persistent** | `/var/lib/apexvoid-analysis-engine` has no volume | ledger (ordering of creations/terminals) is lost on recreate; fixed in the review PR (`analysisdata` volume) |
| **Executor (ctrader-engine) frozen, unhealthy** | health file last touched 12:48:20; no event/log after 12:48:21; 455 consecutive health failures; process alive, TCP to the broker and Redis established; heartbeat is only touched when the broker sends a heartbeat | live plans are not being managed: EURUSD BUY 0.18 resting limit (stop 1.13749), GBPUSD SELL 0.18 (stop 1.32584), GBPJPY BUY 0.18 (stop 208.133), XAU SELL 0.08 at breakeven stop 4290.53. Broker-side stops exist; TP/BE/trailing management and new orders do not run. Cause **unproven** (Saturday 07:19 already logged broker `service_error`s: "No pooled connection", "Trading account is not authorized"; nothing in the cancel-intent path can block: no tombstone keys exist). Nothing restarts an unhealthy container (`restart: unless-stopped` ignores health) |
| Journal healthy | 1130 results, 0 duplicate `group_id`, 0 null pips / bad stops in 30 days | — |
| Infra healthy | Postgres/Redis on volumes, Redis AOF on, disk 29 %, no error lines in 24 h for the bot and the analysis engine | — |

Decision for the operator: restart `apexvoid-ctrader-engine` before Sunday's open (state is recovered from Redis on every start; the
frozen loop is the risk) and decide whether to add a watchdog that restarts it on a stale heartbeat. I did not do either: the first is a
live-trading action and the second needs the weekend heartbeat behaviour confirmed first, or it could restart-loop on weekends.

## 8. Rollback (one screen)

```bash
python -m app.scripts.analysis_authority status --symbol XAU
python -m app.scripts.analysis_authority rollback --symbol XAU --scope supply --expected-epoch <n> --actor <you> --reason <why>
python -m app.scripts.analysis_authority withdraw --symbol XAU --scope supply --actor <you> --reason <why>   # Redis only, re-runnable
```

Then revert the config pair to `go_shadow` / `true` (or `python` / `false`). Open positions are never closed by rollback.
