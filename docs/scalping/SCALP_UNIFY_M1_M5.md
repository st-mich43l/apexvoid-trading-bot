# Scalp unify on M1 + M5 (HFS product tag erased)

## Intent

Centralize scalp strategies on **M5 setup structure + closed M1 confirmation**, use **Scalp**
display names only, and keep open-plan / historical compatibility for legacy
``HFS *`` labels.

## What shipped

| Layer | Change |
| --- | --- |
| Display | `Range Sweep Scalp`, `Impulse Pullback Scalp`, `Breakout Retest Scalp` |
| Config | `strategies.scalping` (loads alias `high_frequency_scalp`); env `SCALPING_*` |
| Legacy | `HFS *` names still accepted in taxonomy, C#, protective stop, confirmation |
| Publish | `family=scalp`, `strategy_mode=scalp_m1`, `structural_source=scalp` |
| Funnel | `auto_trade:funnel:{SYM}:scalp:{archetype}` |
| Context | `app.scalping.unified_context` loads shared M1/M5/M15/H1 windows; M5 owns setup and M1 confirms |
| MAD | Does not rank/gate scalping; only Range Edge may soft-favor `accum` |

## Kept (no hard delete)

- **Fade Scalp** / **Range Edge Scalp** — ZoneWatch technique path
- Legacy **`Momentum Chase Scalp`** display alias for historical fills only
- Deprecated env `HFS_*` / YAML `high_frequency_scalp` for one deploy cycle

## Related

- [TECHNIQUE_SCALP_REDEFINE.md](TECHNIQUE_SCALP_REDEFINE.md)
- [OWN_SCALP_MECHANISM.md](OWN_SCALP_MECHANISM.md)
