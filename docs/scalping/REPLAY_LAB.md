# Scalp replay laboratory (PR B deepen)

Offline path: **OHLC dumps → LabEvents → math hard gates → paper fills → calibration**.

Live scalping discovery is unchanged. Use this lab to find wide positive expectancy
regions before touching production thresholds. **Never tune thresholds on holdout.**

## Historical builder (Liquidity Sweep)

Point-in-time M5 active range (24 bars ≤ \(t\)) + M1 pierce of range edge →
`liquidity_sweep_reversal` LabEvents. Math gates decide reclaim / location / room;
Scalping `discover_range_sweep` is **not** required.

### OHLC dump format (JSONL)

```json
{"t": 1720000000, "o": 4050.0, "h": 4051.0, "l": 4049.5, "c": 4050.5, "v": 0}
```

CSV with `time`/`t` plus `open,high,low,close` (or `o,h,l,c`) also works.

```bash
cd algo-bot

# Optional smoke dump from Redis (short lookback — not full calibration history)
PYTHONPATH=. python -m app.scalping.lab_event_builder \
  --dump-redis --symbol XAU \
  --out-m1 /tmp/xau_m1.jsonl --out-m5 /tmp/xau_m5.jsonl

# Build events + calibration report from offline dumps
PYTHONPATH=. python -m app.scalping.lab_event_builder \
  --m1 /path/to/xau_m1.jsonl \
  --m5 /path/to/xau_m5.jsonl \
  --out-events /tmp/lab_events.jsonl \
  --out-report /tmp/lab_calibration.json \
  --spread 0.2 --slippage 0.1 --bars-after 45
```

Declared spread/slippage are CLI flags for paper fills — do not fit them on holdout.

## Lab event fixture format (JSONL)

Each line is one research event:

```json
{
  "timestamp": 1720000000,
  "direction": "BUY",
  "price": 4050.5,
  "atr": 5.0,
  "range_low": 4048.0,
  "range_high": 4060.0,
  "strategy": "liquidity_sweep_reversal",
  "liquidity_level": 4050.0,
  "barrier": 4058.0,
  "bar": {"open": 4050.2, "high": 4051.0, "low": 4049.5, "close": 4050.6},
  "spread": 0.2,
  "slippage": 0.1,
  "target_min_price": 1.0,
  "stop_price": 4048.0,
  "target_price": 4054.0,
  "session": "london",
  "vr": "normal",
  "pip_size": 0.1,
  "bars_after": [{"open": 4050.6, "high": 4054.2, "low": 4050.4, "close": 4054.0}]
}
```

Strategies: `liquidity_sweep_reversal` (aliases `range_sweep`),
`impulse_pullback_continuation`, `range_edge_mean_reversion`.

## Lab CLI (replay existing events)

```bash
cd algo-bot
PYTHONPATH=. python -m app.scalping.replay_lab \
  --fixture tests/scalping/fixtures/lab_events.jsonl \
  --output /tmp/scalp_lab_report.json

# Parameter sweep on development slice only (never holdout)
PYTHONPATH=. python -m app.scalping.replay_lab \
  --fixture tests/scalping/fixtures/lab_events.jsonl \
  --output /tmp/scalp_lab_sweep.json \
  --sweep-param max_location_buy \
  --sweep-values 0.30,0.35,0.40,0.45
```

## Split discipline

| Slice | Fraction | Use |
|-------|----------|-----|
| development | 60% | Threshold exploration / sweeps |
| validation | 20% | Confirm wide positive regions |
| holdout | 20% | Final check only — **never tune on it** |

Reports include expectancy_r, profit_factor, MAE/MFE, and buckets
`by_session` / `by_archetype` / `by_vr`.

## Shadow wiring (PR C)

In scalping `shadow` / `paper` modes, each `range_sweep` opportunity is stamped with
`measured.math_liquidity_sweep` (and `math_score_inputs` when allowed). Cycle
Redis key `math_shadow` stores buy+sell edge evaluations plus
`range_sweep_annotated` count. **Live publish is not gated by these math
results** until Controlled Live promotion.
