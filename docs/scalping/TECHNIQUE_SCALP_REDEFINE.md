# Technique + M1 Scalping redefine (single delivery)

> Naming: live display names are ``* Scalp`` (see
> [`SCALP_UNIFY_M1_M5.md`](SCALP_UNIFY_M1_M5.md)). The former **HFS** product
> tag is erased — config lives under ``strategies.scalping`` (YAML alias
> ``high_frequency_scalp`` still loads). Legacy ``HFS *`` labels remain
> accepted only for open plans / historical fills.

One product contract for both lanes. London chart evidence informed this; the
scope is **all sessions**.

## Binding rules

1. **MAD ≠ scalp control plane.** Only `accum` soft-favors **Range Edge Scalp**.
2. **Scalping uses M5 structure + M1 confirmation** — Impulse continuation is
   L1; Range Sweep is reclaim-only; Breakout Retest stays.
3. **Few hard gates:** cost, chase, one invalidation, min RR
   after cost, **filled-only** concurrency. Armed contexts expire and never
   count as open positions.
4. **Continuation must not require sweep-reclaim** (`require_sweep_body` is for
   range/reclaim families, not Impulse).

## Family map

| Family | Lane | Job |
|--------|------|-----|
| L1 Continuation | Impulse Pullback Scalp | Pullback / thrust with trend |
| L2 Sweep-reclaim | Range Sweep Scalp | Fade raid after reclaim |
| L3 Reaction | ZoneWatch | Key Level / S/D / Trendline / Session — FX MAD hard gate |
| L3r Range Edge | ZoneWatch | Mean-revert at edge; MAD accum soft; FX gate blocks `expand` |
| L4 Breakout retest | Breakout Retest Scalp | Accepted break + retest |

Do not add new hard session/timezone or MAD gates to scalping without a
separate owner decision. Session preferences may contribute to quality score,
but cannot silence an otherwise valid setup.

## Config map (HFS → scalping)

| Old | New |
|-----|-----|
| `strategies.high_frequency_scalp` | `strategies.scalping` |
| `HFS_*` env | `SCALPING_*` (`HFS_*` deprecated aliases) |
| `execution.technique.hfs_require_killzone` | `scalp_require_killzone` |
| Funnel `…:hfs:{archetype}` | `…:scalp:{archetype}` |
| `structural_source=hfs` | `structural_source=scalp` |
