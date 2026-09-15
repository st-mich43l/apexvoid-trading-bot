# Controlled live rollout (PR I)

Default policy is **disabled**. Do not enable until shadow + paper holdout are green.

## Policy defaults (`app.scalping.rollout.ControlledLivePolicy`)

| Field | Default | Meaning |
|-------|---------|---------|
| `strategy` | `liquidity_sweep_reversal` | Single strategy only |
| `risk_fraction` | `0.05` | Half of typical scalping 0.10 |
| `maximum_session_trades` | 6 | Caps vs live 12 |
| `maximum_daily_trades` | 12 | Caps vs live 30 |
| `enabled` | `false` | Must flip explicitly |
| `kill_switch` | `false` | Instant halt when true |
| `session_allowlist` | asia, london, overlap, ny_open | Asia included (2026-08-26) |

## Promotion checklist

1. Phase 1 audit reviewed (`PHASE1_AUDIT.md`)
2. PR A features unit-tested
3. Replay lab ([REPLAY_LAB.md](REPLAY_LAB.md)): development + validation positive expectancy; **holdout untouched during tuning**
4. Shadow density + block reasons reviewed (`math_shadow` Redis last key; per-opp `measured.math_liquidity_sweep` on range_sweep)
5. Paper MAE/MFE acceptable
6. Enable one strategy only at reduced risk; keep Impulse Pullback off until validated

## Modes

| Mode | Behaviour |
|------|-----------|
| `shadow` | Existing scalping discover + math sidecar; no broker |
| `paper` | Paper outcomes via `replay.evaluate_paper_outcome` |
| `live` | Current scalping live path; math sidecar records observe-only (`scalp:last_math_shadow:*`); ControlledLivePolicy stays off until explicit enable |
