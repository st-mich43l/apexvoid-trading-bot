# M1 + M5 Scalping Engine

Shadow/paper/live event-driven scalping on closed M1 bars with immutable M5
context. Separate lane from technique ZoneWatch publishers. Symbols are
gated by `is_scalping_symbol` / scalping config (production focus remains XAU).

**Own mechanism (research-first):** see
[OWN_SCALP_MECHANISM.md](OWN_SCALP_MECHANISM.md). Hard gates are deferred —
live still uses heuristic discovery; math stamps counterfactuals and
performance joins only. Breakout Retest rules:
[OWN_BREAKOUT_TECHNIQUE.md](OWN_BREAKOUT_TECHNIQUE.md).

## Mathematical program

Research-first roadmap (do not loosen thresholds first):

1. [PHASE1_AUDIT.md](PHASE1_AUDIT.md) — pipeline + threshold inventory
2. [OWN_SCALP_MECHANISM.md](OWN_SCALP_MECHANISM.md) — mechanism design + gate roadmap
2b. [OWN_BREAKOUT_TECHNIQUE.md](OWN_BREAKOUT_TECHNIQUE.md) — ApexVoid breakout state machine
3. `app/scalping/math_features.py` — ATR-normalized \(X_t\) features (PR A)
4. `app/scalping/math_strategies.py` — Liquidity Sweep / Impulse Pullback / Range Edge (research)
5. `app/scalping/research_stamp.py` — observe-only per-opp features + math counterfactual
6. `app/scalping/performance.py` — archetype × session × math_agree join
7. `app/scalping/ranking.py` — unified score when math_score_inputs present
8. `app/scalping/replay.py` / `replay_lab.py` — paper outcomes + calibration ([REPLAY_LAB.md](REPLAY_LAB.md))
9. `app/scalping/rollout.py` — shadow sidecar helpers
10. [CONTROLLED_LIVE.md](CONTROLLED_LIVE.md) — promotion checklist (gates later)
11. [MAD.md](MAD.md) — Asia phase clock + FX technique hard gates
12. [MAD_SOURCES.md](MAD_SOURCES.md) — ICT AMD lineage vs ApexVoid rules

Collect performance first; design hard gates later from data. Holdout is never
used for tuning. Demo host: ship live and trace from Redis/ledger.

## Architecture

```text
H1/M15 refresh on closed bars
        ↓
M5 ScalpContextSnapshot (immutable, Redis-pinned)
        ↓
M5 setup structure + closed M1 confirmation archetypes
        ↓
EntryLocation (enforce inside scalp zone) + activation + cost + risk
        ↓
shadow / paper / live ScalpSignal → TradePlan V8 (live mode)
        ↓
math_shadow sidecar + research.agree_rows (observe-only, no broker)
```

Owned by `bar_event_dispatcher_loop` (not a standalone M1 subscriber). Live
mode publishes via `worker.try_publish_executable_signal` when gates pass.
Requires `runtime.auto_trade.enabled=true` and a live cTrader consumer on the
trade-plan stream. Math shadow / research stamps record on live M1 cycles
(`scalp:last_math_shadow:{SYMBOL}`) without changing allow/block.

Structure/technique decide permits (not killzone clock). See
[OWN_SCALP_MECHANISM.md](OWN_SCALP_MECHANISM.md).

## Modes

| Mode | Behaviour |
|------|-----------|
| `off` | Loop exits immediately |
| `shadow` | Discover/evaluate/record only + math sidecar |
| `paper` | Paper TradePlan-like records, no broker |
| `live` | Publishes TradePlan V8 when gates pass + math sidecar observe-only |

## Archetypes

1. `range_sweep` — micro range edge false-break sweep/reclaim
2. `impulse_pullback` — join displacement after pullback
3. `breakout_retest` — M5 compression/structure → displacement break → M1 rejection retest → hold ([OWN_BREAKOUT_TECHNIQUE.md](OWN_BREAKOUT_TECHNIQUE.md))

## Promotion criteria (shadow → paper)

- Stable opportunity density (~15–30/day observation, not a quota)
- Buy-top / sell-bottom block rates reviewed
- Net expectancy and drawdown acceptable on holdout replay
- Spread/cost blocks behaving as expected
- No publication into live candidate streams while still shadow

## Replay

```bash
cd algo-bot
PYTHONPATH=. python -m app.scalping.replay --fixture path.jsonl --output artifacts/scalp-replay.json
```

Report includes `aggregate` plus `calibration` (development / validation / holdout).
