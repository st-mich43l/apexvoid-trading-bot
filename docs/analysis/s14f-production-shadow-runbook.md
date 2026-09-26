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

## 2. Enable `go_shadow` (its own small change)

`config/analysis.yml`: `mode: go_shadow`, `consumer_enabled: true` (PR "S14F: enable go_shadow"). The consumer group is
`apexvoid-algo-bot-analysis-opportunity-v1`. In `go_shadow` each creation is applied to the ledger and then run through the
real policy dry run (S14A): no plan, reservation, card or order is possible. Roll back by reverting the config change.

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
