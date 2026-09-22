# XAU technique structure fixed_rr

XAU **technique / reaction** auto now follows the FX structure book:

| Layer | Behavior |
|-------|----------|
| Stop | Structure swing → fit pack envelope **50–60** pips (entry-targeted, `execution_route.risk_targeted_entry_price`, XAU only) |
| Targets | **1R / 2R / 3R / 4R** of final stop; closes **40 / 20 / 20 / 20** |
| Runner | After TP1 (1R), stop moves to **breakeven** (group weighted fill) |
| Fallback | Single **1R** full close when opposing room cannot hold the full ladder |
| Pack | `instrument_packs.xau_fixed_4r_v1` / policy `xau_fixed_4r_v1` |

2026-09-15 (owner-reported): switched from the earlier 0.5R/1R/2R/3R shape
to manual `/algo`'s own default 1R/2R/3R/4R ladder. 2026-09-22
(owner-reported): the pack/policy name itself was still `xau_fixed_2r_v1`
from before that change — renamed to `xau_fixed_4r_v1` to match what it
has actually enforced since 09-15. No targeting-contract change from
either rename.

**M1 scalping is unchanged.** Discovery still picks 1:2 or 1:1 room; publish
builds `(1R, 2R)` ladders. `technique_fixed_rr_targeting(symbol, strategy)`
returns fixed_rr targeting only for non-scalp strategies so execution policy
does not expand scalp matches into the technique R ladder.

## Code anchors

- [`app/core/instrument_geometry.py`](../../algo-bot/app/core/instrument_geometry.py) — `technique_fixed_rr_targeting`
- [`app/autotrade/execution_policy.py`](../../algo-bot/app/autotrade/execution_policy.py) — technique-only fixed_rr expansion
- [`app/scalping/context.py`](../../algo-bot/app/scalping/context.py) — XAU remains scalp-eligible with fixed_rr
- Config: `config/trading-bot.yml` → `instrument_packs.xau_fixed_4r_v1`, `instruments.XAU.pack`

## Calibration

Envelope max (60) is a pack leaf. Raise/lower from live Key Level
`stop_exceeds_envelope_*` rates without a code fork.
