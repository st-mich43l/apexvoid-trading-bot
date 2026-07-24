# Demo-eval worker veto regression

This note records the investigation and deterministic replay for the
post-23-Jul-2026 candidate-frequency collapse. It covers the worker policy
boundary only. It does not claim a historical broker fill count where no
production OHLC/event archive was retained.

## Reviewed revisions

- Known active comparison: `4be59b123604af38df08b56bc37dea02d1d1d59c`
  (PR #87, 23 Jul 2026).
- Reviewed master before the fix:
  `7ba44e581e17d14566730b7ee9ddd6897e1d39ca`.
- Master was fetched before editing and had not moved beyond the reviewed SHA.
- Fix branch: `fix/demo-eval-worker-veto-regression`.

The comparison was a targeted diff of the Python worker, execution policy,
map strategy, zone analysis and config plus the C# engine, options, Redis
sink, Compose and deployment template. The baseline was not blindly restored.

## Guard diff before implementation

| Guard | Existed on baseline | Current-master behaviour | Paths affected | Runtime counter | Classification |
|---|---|---|---|---|---|
| Opposing barrier ahead | Yes | Added unconditional containment and put every key level in a directionless bounds list | private, StrategyMatch, trend | `opposing_barrier`, `entry_inside_opposing_zone` | structural quality |
| HTF veto | Yes | Hard-returned before publish | private, trend | `htf_veto` | structural quality |
| Range regime | Yes | A non-chop range match was consumed | StrategyMatch | `range_edge_not_chop` | structural quality |
| Counter-bias target barrier | No | Any barrier before EQ/target consumed the match | StrategyMatch | `counter_bias_target_barrier` | target planning |
| Zone cooldown | No | Every broker disappearance was treated like a stop loss | all autonomous paths | `zone_cooldown` | ambiguous lifecycle |
| BUY/SELL map overlap | No | Direction-agnostic hard veto for both theses | all autonomous paths | `overlapping_zone_conflict` | structural ambiguity |
| Entry drift | Fixed configured pip distance | `min(configured, ATR factor, 45% room)` could collapse to 3–5 pips and consumed the match | StrategyMatch | `strategy_entry_moved` | execution timing |
| Zone reconciliation | No | Reconciled zones could replace detector input before strategy evaluation | scanner/detectors | reconciliation counters | pre-execution structural mutation |
| Quote/news/idempotency/geometry | Yes or executor-owned | Technical protections remained terminal | publisher/executor | lifecycle and executor metrics | technical correctness |

The material regression is cumulative: containment, cooldown, overlap,
counter-bias and tight drift were added after the active comparison. They
were sequential hard returns after detection, so structure cards and
StrategyMatches could exist while the candidate stream stayed quiet.

## Guard matrix after implementation

| Condition | `demo_eval` / `observe` | `balanced` | `strict` |
|---|---|---|---|
| Candidate's primary source | allow and record source exclusion | allow | allow |
| Supportive structure | allow with telemetry | allow with warning | configurable strict evaluation |
| Unrelated opposing structure containing entry | wait; preserve match | block zero-room containment | block |
| Opposing structure ahead with usable room | warn or adapt target | allow with warning | block |
| Ambiguous demand/supply overlap | wait for M1 reaction; preserve both theses | wait/block according to geometry | block |
| Bullish/bearish M1 reaction resolves overlap | allow matching thesis | allow matching thesis | allow matching thesis |
| Counter-bias barrier before target | select the largest fitting target | adapt target | adapt or block if no valid room |
| Confirmed stop loss near the same zone | disabled by demo default | configurable | configurable cooldown |
| Manual/external/unknown close | no blocking cooldown | no blocking cooldown | no blocking cooldown |
| Temporary preferred-drift miss | wait; preserve match | wait; preserve match | wait until hard cap |
| Crossed invalidation / consumed target room | terminal block | terminal block | terminal block |
| Duplicate, malformed geometry, non-demo account | terminal/fail closed | terminal/fail closed | terminal/fail closed |

Legacy `auto_trade:gate_reject:*` counters now increment only for terminal
blocks. Every structural evaluation also increments:

```text
auto_trade:guard_evaluation:{symbol}:{guard}:{outcome}
```

The latest typed decision is stored at `auto_trade:last_guard:{symbol}` and
is shown by `/auto_status`.

## Deterministic replay comparison

The worker replay uses the same four structural snapshots (two BUY, two SELL)
and the same M1 reclaim/rejection OHLC frames for each revision. Detector and
StrategyMatch inputs are held constant so the table isolates the worker veto
regression. The original eight pre-fix assertions passed only 1/8 on reviewed
master and pass 8/8 after the fix; the expanded suite has 34 cases.

| Metric | Active baseline | Reviewed master before fix | Fixed branch |
|---|---:|---:|---:|
| Structural detections supplied | 4 | 4 | 4 |
| StrategyMatches supplied | 4 | 4 | 4 |
| Candidates published in own-source cohort | 4 | 0 | 4 |
| Blocked by own-source opposing guard | 0 | 4 | 0 |
| Executor accepted | n/a (worker replay boundary) | 0 | n/a (covered by C# suite) |
| Duplicate suppressed | 0 | 0 | 0 |
| Invalid geometry | 0 | 0 | 0 |
| Input BUY / SELL | 2 / 2 | 2 / 2 | 2 / 2 |
| Published BUY / SELL | 2 / 2 | 0 / 0 | 2 / 2 |
| Strategy-family split | mapped-zone 4 | mapped-zone 4 | mapped-zone 4 |

This is a controlled regression replay, not a fabricated historical trade
count. The repository does not contain the exact 23-Jul production OHLC,
Redis event stream and broker acknowledgement archive needed for a truthful
full-session baseline/current/fixed fill comparison. Post-deploy deltas below
are therefore required before judging live frequency.

## Replay and non-regression coverage

`telegram-bot/tests/test_worker_veto_regression_replay.py` covers:

- primary key-level, supply and demand sources;
- unrelated opposing structures in observe and strict modes;
- bullish and bearish M1 overlap resolution plus ambiguous preservation;
- confirmed-SL-only cooldown semantics;
- manual, external, take-profit and unknown close non-blocking semantics;
- demo cooldown bypass;
- BUY/SELL counter-bias target adaptation and explicit insufficient room;
- range/map/trend drift floors, hard cap and consumed-room behaviour;
- invalidation, news wait, exact-match removal and sibling isolation.

The wider Python and C# suites retain duplicate suppression, malformed
geometry rejection, manual `/algo` isolation, group ownership, concurrent
hedged strategies, executor readiness and live-account fail-closed behaviour.

## Deployment verification

Fetch and deploy the merged commit:

```bash
cd /root/Projects/apexvoid-trading-bot
git fetch origin
git checkout master
git pull --ff-only origin master
git rev-parse HEAD
docker compose config
docker compose build bot ctrader-engine
docker compose up -d --force-recreate --no-deps bot ctrader-engine
docker compose ps bot ctrader-engine
docker compose logs --since=10m bot ctrader-engine
```

Verify the resolved process environments:

```bash
docker compose exec bot env | sort | grep '^AUTO_TRADE_'
docker compose exec ctrader-engine env | sort | grep '^AUTO_TRADE_'
```

Verify cross-service config and readiness:

```bash
docker compose exec redis redis-cli GET auto_trade:config_manifest:python
docker compose exec redis redis-cli GET auto_trade:config_manifest:ctrader
docker compose exec redis redis-cli GET auto_trade:config_health
docker compose exec redis redis-cli GET auto_trade:executor_readiness
```

Snapshot counters immediately after deployment. Keep this file so subsequent
checks are deltas rather than misleading lifetime totals:

```bash
docker compose exec redis redis-cli --scan \
  --pattern 'auto_trade:guard_evaluation:XAU:*' |
  sort |
  while read key; do
    printf '%s ' "$key"
    docker compose exec -T redis redis-cli HGETALL "$key" | tr '\n' ' '
    printf '\n'
  done | tee /tmp/xau-guard-before.txt
docker compose exec redis redis-cli HGETALL auto_trade:metrics:XAU |
  tee /tmp/xau-metrics-before.txt
date -u +%FT%TZ | tee /tmp/xau-guard-deployed-at.txt
```

Inspect only post-deploy evidence:

```bash
docker compose exec redis redis-cli GET auto_trade:last_guard:XAU
docker compose exec redis redis-cli HGETALL auto_trade:zone_reconcile:XAU
docker compose exec redis redis-cli XREVRANGE auto_trade:candidates + - COUNT 30
docker compose exec redis redis-cli XREVRANGE auto_trade:lifecycle_events + - COUNT 50
docker compose exec redis redis-cli XREVRANGE auto_trade:events + - COUNT 50
docker compose exec redis redis-cli GET auto_trade:strategy_matches:XAU
docker compose exec redis redis-cli GET auto_trade:strategy_match:XAU
docker compose exec redis redis-cli --scan --pattern 'auto_trade:executor:candidate:*'
docker compose exec redis redis-cli SMEMBERS auto_trade:positions
docker compose exec redis redis-cli --scan --pattern 'auto_trade:position:*'
docker compose exec redis redis-cli --scan --pattern 'auto_trade:lifecycle_state:*'
docker compose logs --since="$(cat /tmp/xau-guard-deployed-at.txt)" \
  bot ctrader-engine |
  grep -E 'candidate|accepted|filled|duplicate|invalid|config_fatal'
```

Compare counter snapshots without deleting historical data:

```bash
docker compose exec redis redis-cli --scan \
  --pattern 'auto_trade:guard_evaluation:XAU:*' |
  sort |
  while read key; do
    printf '%s ' "$key"
    docker compose exec -T redis redis-cli HGETALL "$key" | tr '\n' ' '
    printf '\n'
  done | tee /tmp/xau-guard-after.txt
diff -u /tmp/xau-guard-before.txt /tmp/xau-guard-after.txt || true
```

## Safe rollback

Disable new autonomous intake first. Do not delete Redis volumes or any open
group, position reconciliation, ownership, pending-order or broker state.

```bash
cd /root/Projects/apexvoid-trading-bot
cp .env /tmp/apexvoid-trading-bot.env.rollback
sed -i 's/^AUTO_TRADE_ENABLED=.*/AUTO_TRADE_ENABLED=false/' .env
docker compose up -d --force-recreate --no-deps bot ctrader-engine
docker compose exec redis redis-cli GET auto_trade:executor_readiness
docker compose logs --since=5m bot ctrader-engine
git fetch origin
git checkout -b revert/demo-worker-veto origin/master
git revert --no-edit <merged_commit_sha>
git push -u origin revert/demo-worker-veto
# Open and merge a rollback PR; never push a revert directly to master.
# After that PR merges:
git checkout master
git pull --ff-only origin master
docker compose build bot ctrader-engine
docker compose up -d --force-recreate --no-deps bot ctrader-engine
```

Re-enable only after the reverted Python/C# manifests agree and the executor
reports ready on a broker-confirmed demo account. Configuration/status cache
keys may be cleared only if stale; operational state keys must remain intact:

```bash
docker compose exec redis redis-cli DEL \
  auto_trade:config_manifest:python \
  auto_trade:config_manifest:ctrader \
  auto_trade:config_health \
  auto_trade:executor_readiness
docker compose restart bot ctrader-engine
```
