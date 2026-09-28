# S14H: rollback and Python cleanup

## Emergency rollback

```bash
python -m app.scripts.analysis_authority status --symbol XAU                                 # read the epoch
python -m app.scripts.analysis_authority rollback --symbol XAU --scope supply --expected-epoch <n> --actor <you> --reason <why>
python -m app.scripts.analysis_authority rollback-all --actor <you> --reason <why>            # every Go-bound scope
python -m app.scripts.analysis_authority withdraw --symbol XAU --scope supply --actor <you> --reason <why>   # re-runnable; Redis only
```

Order (fixed): **1** the fence flips: Go can create and publish nothing new, Python waits out the drain; **2**
withdrawal: matches, unpublished setups and queued/unfilled plans (cancel intent the executor honours, first reason
wins, 7-day tombstone); open positions are never closed. If step 2 fails after step 1 the command exits 3 and prints the
error; `withdraw` repeats it.

### Proved (real PostgreSQL + Redis; executor drills on the same fixture bytes)

| stage of Go-origin work | Python | executor (`GoRollbackDrill`) |
| --- | --- | --- |
| match waiting / pending plan never submitted | match removed, setup invalidated, intent written; worker refuses to publish it | `cancelled_unsubmitted`, no order ever placed |
| submitted, unfilled ladder | intent | every resting order cancelled, plan cancelled |
| partially filled ladder | intent | resting leg withdrawn, filled leg kept and still managed to its TP (closes normally) |
| open position | intent | `positions_kept`: nothing closed, stop and target still apply |

`test_there_is_never_a_second_publisher_at_any_instant…` samples every second of grant → drain → Go → rollback →
drain → Python on the real Postgres fence: never two publishers, nobody during either drain, Python restored after.
Turning the consumer off mid-ownership does not reopen the scope to legacy Python (S14B snapshot).

### Dependency trouble: where a hard stop replaces a fallback

* **Kafka down/lagging**: no new Go events; the freshness gate refuses any backlog on recovery. Rollback needs no Kafka.
* **Kafka data lost** (before the log dir was moved onto the volume every recreate did this): topics and committed offsets vanish
  together, so nothing is replayed or skipped inconsistently, but a terminal event for a creation published earlier never
  arrives. The consumer-side match keeps a TTL of `expires_at`, so an orphaned match ends by expiry, and rollback/`withdraw` does not
  need Kafka. This is why the persistent log dir and the engine's ledger volume are a precondition of the shadow window.
* **PostgreSQL down**: the fence cannot be read, so **both** publishers are denied (fail closed): plans are skipped, never
  doubled. `status`/`rollback`/`grant` refuse loudly. `withdraw` needs only Redis and works (tested with Postgres
  unreachable). To bring Python back you need Postgres: there is deliberately no bypass flag.
* **Redis down**: the executor and plan stream are down; no fallback exists or is added.
* Consumer flag off is **not** a rollback (a Go-owned scope stays closed to legacy plans).

## Python cleanup: blocked

Only after stable, accepted production operation may the proven-unused Python detector entrypoints for the transferred
scope be removed, and only after re-running `python s13_legacy_classification.py`. Re-run for this change: 62 legacy modules /
32,873 lines, 0 unclassified, **19 production importers still block deletion** (worker, delivery, strategy match, structural
barriers/target room, scale context, zone execution, main, ...), 4 unreachable scalping research modules. The worker's Python
opposing-barrier and target-room reads are **retained** (S14C: Go supplies no equivalent). No detector was removed by this change.
Keep the Python Analysis Client, policy and risk checks, TradePlan builder, Telegram/manual trading, cTrader reconciliation,
offline research and every detector Python-owned scopes still use.
