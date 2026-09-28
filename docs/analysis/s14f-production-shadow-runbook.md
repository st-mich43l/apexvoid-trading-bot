# S14F: production shadow and acceptance (operator runbook)

Engineering is complete for this phase; **the production evidence is not**. Nothing below was run
against production by the engineering change, and no acceptance measurement is claimed. This runbook
is how the operator gets the evidence, and `python -m app.scripts.shadow_acceptance` turns it into a
report in which any gate without evidence is `not_measured` (a blocker), never a pass.

## 1. Preconditions (verify, do not assume)

1. Merge order: #641 (S14B) → #642 (S14A) → #643 (S14C, independent) → #644 (S14D) → #645 (S14E) → the S14F–H tooling PR.
2. Deploy the Python decoder/policy **before** relying on extended Go events (they already publish `technical_context`, #640).
3. Producer and consumer images match the reviewed commits: record `analysis-engine` and `algo-bot` image
   SHAs from the running containers (`docker inspect` on the VPS; see the deploy repo for the exact label)
   in `images.json` and the reviewed commits in `reviewed.json`; the report compares them.
4. `python -m app.scripts.analysis_authority status` shows **no** scope owned by Go (shadow is Python-owned).
5. Kafka state survives a container recreate: `docker exec apexvoid-kafka ls /var/lib/kafka/data` lists topic directories and
   `docker inspect apexvoid-kafka` shows `KAFKA_LOG_DIRS=/var/lib/kafka/data`. (Until the S14 production-review PR the broker logged to
   `/tmp/kafka-logs` in the container layer: every recreate wiped topics **and** consumer offsets while the `kafkadata` volume stayed
   empty. The analysis-engine publication ledger likewise had no volume.) Do not start the window before this holds.

## 2. Enable `go_shadow` (its own small change, **in the ansible vars, not `config/analysis.yml`**)

The Python bot reads `/config/trading-bot.yml`, rendered by ansible from `apexvoid_trading_bot_config` (ansible-library
`inventory/group_vars/all/vars.yml`). `config/analysis.yml` is read only by the Go engine and the C# executor and does **not** switch
the bot. On 2026-09-26 `mode: go` was merged into `config/analysis.yml` and production stayed `python`/consumer off: the boot audit
row said so, no consumer health key existed and no Kafka group was ever created. Set, in the ansible vars:

```yaml
apexvoid_trading_bot_config:
  analysis:
    technical_authority:
      mode: go_shadow
      consumer_enabled: true
```

then redeploy and **verify the bot really runs it** (all three must hold, otherwise the window is not evidence):

```bash
psql "$DATABASE_URL" -c "SELECT at, mode, consumer_enabled FROM analysis_authority_runtime_audit ORDER BY audit_id DESC LIMIT 1"   # go_shadow | t
docker exec apexvoid-trading-redis redis-cli GET auto_trade:component_health:analysis_opportunity_consumer                         # {"state":"ready",...}
docker exec apexvoid-kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group apexvoid-algo-bot-analysis-opportunity-v1
```

The consumer group is `apexvoid-algo-bot-analysis-opportunity-v1`. In `go_shadow` each creation is applied to the ledger and then run
through the real policy dry run (S14A): no plan, reservation, card or order is possible. Roll back by reverting the ansible var.
The acceptance report's first gate (`go_consumer_is_running_in_the_process_under_test`) fails when the process under test is
`python`/consumer-off or the consumer never reported ready.

**Weekends and bootstrap.** The Go engine publishes only for live closed bars; bars replayed while it rebuilds state at startup are
recorded as `suppressed` and never published. Kafka is therefore legitimately empty after a deploy and on a closed market
(checked 2026-09-26: both opportunity topics at offset 0 on every partition, 1198 suppressed ledger records). Start the counting
window on a live session.

## 3. Verify connectivity (record the outputs)

```bash
kafka-consumer-groups.sh --bootstrap-server <broker> --describe --group apexvoid-algo-bot-analysis-opportunity-v1   # assignment, committed offsets, lag
python -m app.scripts.analysis_authority status                                                                     # every scope Python-owned
docker compose logs bot | grep -E "technical-authority|Go analysis shadow"                                          # consumer up, decisions
psql "$DATABASE_URL" -c "SELECT disposition, count(*) FROM analysis_opportunity_events GROUP BY 1"
psql "$DATABASE_URL" -c "SELECT mode, outcome, reason, count(*) FROM analysis_shadow_decisions GROUP BY 1,2,3 ORDER BY 4 DESC"
psql "$DATABASE_URL" -c "SELECT count(*) FROM analysis_opportunity_rejections"
```

## 4. Collect across representative XAU activity

Both directions where available, live quote freshness, HTF updates, terminal events, at least one consumer
restart and one broker/DB outage (drills below). Supplement scarce live samples with the S14C replay and
**label** them: `docs/analysis/reports/s14c-policy-replay-xau-20260921.md` is historical replay on a real
capture, not observed production behaviour.

## 5. Operator-supplied measurements (only the operator can produce these)

`kafka.json`: `{"consumer_group":..., "committed_offsets":{...}, "lag_messages":N, "restart_recovery":{"ok":true,...}, "outage_recovery":{"ok":true,...}}`
after actually restarting the consumer and interrupting Kafka. `risk.json` only after a live phase. `approvals.json`
(`{"approved": {"<open gate>": {"approved_by": "<name>", "evidence": "<ref>"}}}`) only when the owner has
reviewed a difference; an entry without approver and evidence does not count.

## 6. Build the report

```bash
python -m app.scripts.shadow_acceptance --symbol XAU --since <ISO> --until <ISO> \
  --kafka-json kafka.json --replay-report docs/analysis/reports/s14c-policy-replay-xau-20260921.json \
  --dispositions approvals.json --images-json images.json --expected-shas-json reviewed.json \
  --json /reports/acceptance.json --md /reports/acceptance.md
```

| gate | evidence | what makes it pass |
| --- | --- | --- |
| no orphan / resurrected lifecycle | ledger | terminals follow creations; no active row carries a terminal |
| no duplicate plans or orders | Redis stream + journal | no thesis with two Go plans (and events flowed) |
| no stale / bootstrap / replay / pre-activation live plan | `mode=go` decisions | age, lag and boundary inside limits; **not_measured until a Go-owned window exists** |
| no missing-confirmation / HTF bypass | ledger envelopes + decisions | every evaluated event carried both |
| no shadow plan / reservation / card / broker effect | Redis + journal | none exist during a pure `go_shadow` window (and shadow ran) |
| no risk-limit / group-exposure violation | operator risk review | **not_measured in shadow** |
| Kafka lag / restart / outage | ledger timing + operator drills | timing within limit and both drills ok |
| Go/Python differences reconciled or approved | S14C report + approvals | replay verdict pass, or every open gate approved by name with evidence |
| deployed SHAs match reviewed commits | operator files | equal |

## 7. Outstanding today (exact)

Every gate above except the ones the shadow window itself can populate. Concretely open: production
`go_shadow` window (not started), Kafka drills, deployed-SHA record, risk review (live phase), and owner
disposition of the S14C differences: 22 strict Go/Python matches of 185 Go cases (155 Python-only, 163 Go-only; many-to-many
coverage 39/185 Go and 53/177 Python), Go confluence a constant 4 (every case Tier A), 9 matched setups with an H1 conflict, 12 cases
without H1 bias, Go prices not on the tick (0/185), simple-vs-Wilder ATR spread of about -24 % to +31 % (median +1 %).
