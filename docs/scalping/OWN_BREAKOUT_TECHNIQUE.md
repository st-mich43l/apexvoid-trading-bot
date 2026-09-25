# ApexVoid Breakout Technique

Own M5 setup and M1 confirmation rules for `breakout_retest` (**Breakout Retest Scalp**).
Not ZoneWatch technique; not a break-without-retest chase.

## Why the old path was silent

Live discovery used `context.active_range_low/high` — the **min/max of the
last 24 M5 bars** (`context.py`). That is a rolling envelope, not a
consolidation box. Price almost never does clean *break → retest → hold*
against an expanding envelope, so idle stayed `breakout_retest:not_matched`
and funnel `hfs:breakout_retest` stayed empty.

Secondary gaps: evidence flags hardcoded `True`, retest was touch-only (no
rejection), no compression quality, no math model, no per-reason reject
telemetry.

## State machine

```text
no_box → wait_break → wait_retest → armed
              ↓              ↓
         failed_break   failed_break
```

| State | Meaning |
| --- | --- |
| `no_box` | No M5 compression window (tight range + multi-touch) |
| `wait_break` | Box ready; no accepted displacement close beyond it |
| `wait_retest` | Break accepted; no rejection retest yet |
| `failed_break` | Close back into / through the opposite side of the box |
| `armed` | Break + acceptance + rejection retest + hold → discoverable |

## Formulas (M5 setup, M1 confirmation)

### Compression box

Scan recent closed M5 for a window of `min_box_bars`…`max_box_bars` where:

\[
\text{width} = \text{box\_high} - \text{box\_low} \le \text{box\_max\_atr} \cdot \text{ATR}
\]

and each extreme has at least `min_touches_per_side` touches within
`touch_tol_atr · ATR`. Emit `box_low`, `box_high`, `box_bars`,
`compression_atr`, `touch_count`.

**Range Sweep keeps** `active_range_*` unchanged — only breakout uses this box.

### Break

Decisive **close** beyond the broken boundary by
\(\ge \text{min\_break\_atr} \cdot \text{ATR}\) with a directional body
(no wick-only pierce).

### Acceptance

At least one further bar after the break that does **not** fully reclaim
into the box (BUY: close stays \(\ge\) `box_high`; SELL mirrored).

### Retest + rejection

Return to the broken boundary within `retest_lookback_bars` with
**rejection** when `require_retest_rejection` (default true):

- BUY: low \(\le\) level and close \(>\) level (wick/touch then reclaim)
- SELL: high \(\ge\) level and close \(<\) level

### Hold

Newest bar closes beyond the broken level (body color not required).

### Invalidation

Close back through the opposite side of the box → `failed_break`.

### Book

Same scalp RR as Impulse: prefer 1:2 (50/50 + BE after TP1); 1:1 full
volume when room forces it.

## Reject codes (telemetry)

Redis: `scalp:metric:{SYMBOL}:breakout:{reason}`

| reason | When |
| --- | --- |
| `no_box` | Compression missing |
| `wait_break` | Box only |
| `wait_retest` | Break accepted, no rejection retest / hold |
| `failed_break` | Reclaim / opposite-side close |
| `armed` | Discoverable this cycle (optional counter) |
| `disabled` / `not_permitted` | Config gate |

Idle cycle reasons use `breakout_retest:{reason}` for `scalp:last_cycle`.

## Live vs math

| Layer | Role |
| --- | --- |
| `find_compression_box` + `detect_breakout_retest` | Live discovery authority |
| `discover_breakout_retest` | Builds `ScalpOpportunity` when `armed` |
| `evaluate_breakout_retest_continuation` | Observe-only counterfactual stamp |
| `research_stamp` | `math_counterfactual` — **does not** flip allow/block |

## Config (`strategies.scalping.breakout`)

| Knob | Default (XAU M1) | Role |
| --- | --- | --- |
| `box_max_atr` | 1.5 | Max compression width |
| `min_break_atr` | 0.25 | Displacement floor beyond box |
| `min_box_bars` / `max_box_bars` | 8 / 20 | Compression window |
| `retest_lookback_bars` | 5 | Touch/rejection scan |
| `require_retest_rejection` | true | Wick reclaim required |
| `min_touches_per_side` | 2 | Level quality |
| `touch_tol_atr` | 0.20 | Touch band |

## Gate roadmap (later)

Do **not** enable MAD hard gate or ControlledLive from this model until
stamped outcomes justify it. Sequence: research → replay → shadow gates →
live gates.

## Related

- [OWN_SCALP_MECHANISM.md](OWN_SCALP_MECHANISM.md) — archetype ↔ math map
- [README.md](README.md) — scalp engine overview
- Code: `app/scalping/microstructure.py`, `strategies.py`, `math_strategies.py`
