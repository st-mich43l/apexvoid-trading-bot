# S14E: presentation, numeric precision, one reviewed ladder spec, and the risk-leg gate

Nothing here changes what the live Python-owned route places. The Go-origin canary stays on the
existing proven execution route; the executor-injected risk leg is a separate, default-off gate that
does not block the Kafka authority cutover.

## 1. Cards: the canonical helper, one thread

A Go-origin plan's root card is `format_plan_published_root_card`, the same helper Manual/Auto Algo use
(no second presentation implementation): instrument and timeframe, direction icon, setup label and stars,
entry zone, SL with its risk in pips, one TP line per target, a status slot the lifecycle edits in place.
`ensure_plan_published_root_card` reuses an existing card (edit, never re-send or delete/re-post);
`test_s14e_go_precision_and_card.py` proves exactly one Telegram root per setup. Leg-level updates are
threaded replies (S14D replays the executor's real events through the real handler). Confirmation is not a
card section by the existing design ("do not add scanner-style sections to the entry card"): a card exists
only after a confirmed, published plan.

## 2. Precision: the contract keeps it, the card shows the approved precision

On a real replayed Go opportunity with unrounded prices: the plan's entry zone is the exact decimal text
of Go's floats (`4291.21`, `4296.87`), `source_structure.invalidation_price` keeps
`4298.970714285714`, the match keeps Go's exact target (`4284.159928571428`), and the stop the executor
places is the planner's tick-aligned `4299.00`. The card renders `4,291 - 4,297`, `SL 4,299`: the existing
Manual Algo whole-point XAU display, with none of the raw digits. Findings:

* **Go's own target is not what is executed.** The plan's TP (`4285.9` here) comes from the builder's
  target rule; Go's TP only feeds the match's pip fields.
* The adapter's pip fields are whole-pip roundings (S14C measured up to 0.0499 in price): informational,
  never used to place an order.

## 3. One reviewed ladder specification

`contracts/autotrade/xau-ladder-spec.json` holds the reviewed Manual Algo ladder (80/20; Shallow at the
near edge, Deep at the zone midpoint or half-way to the stop for a degenerate zone; risk leg 15 pips inside
the stop, 0.02 lots below $1000 equity else 0.05; prices rounded to the instrument's digits, midpoints away
from zero) with hand-computed cases. **Manual Algo (`AutoTradeEngine`), the Auto Algo executor's risk leg
(`TradePlanRuntime`) and the Python calculator (`xau_ladder.py`) all pass the same cases**
(`LadderSpecParityTests.cs`, `test_s14e_ladder_spec.py`). Aligning the Python calculator required decimal
arithmetic and rounding: binary floats round `4101.005` down where C# rounds it up.

**The Auto Algo *entry* ladder is a different rule** (`execution_route._scale_ladder_legs`: leg 2 one ATR
step deeper than the proximal edge, capped at the far edge; ratios from the equity table). It is
deliberately not unified; a test pins where it differs from Manual Algo's Deep leg. Unifying them would be
the optional, separately gated change this phase declines to make.

## 4. Worst-case group risk (measured from the orders the executor submits)

XAU SELL, entry filled at 4354.10, L2 resting at 4355.50, stop 4359.83 (57.3 pips, the S14D plan), the
owner's equity-table lots, $10 per pip per lot. Base = declared ladder only; the risk leg adds a fixed
worst case (15 pips × its lots).

| equity | base loss | base % | risk leg adds | with leg % |
| ---: | ---: | ---: | ---: | ---: |
| 300 | 15.79 | 5.26 | 3.00 | 6.26 |
| 500 | 21.52 | 4.30 | 3.00 | 4.90 |
| 600 | 54.50 | 9.08 | 3.00 | 9.58 |
| 900 | 54.50 | 6.06 | 3.00 | 6.39 |
| 1,000 | 54.50 | 5.45 | 7.50 | 6.20 |
| 1,001 | 65.96 | 6.59 | 7.50 | 7.34 |
| 1,300 | 65.96 | 5.07 | 7.50 | 5.65 |
| 2,000 | 81.75 | 4.09 | 7.50 | 4.46 |
| 2,999 | 81.75 | 2.73 | 7.50 | 2.98 |
| 3,000 | 136.25 | 4.54 | 7.50 | 4.79 |
| 5,000 | 163.50 | 3.27 | 7.50 | 3.42 |
| 10,000 | 163.50 | 1.64 | 7.50 | 1.71 |

Findings, all pinned by tests: every submitted volume is within the broker minimum/maximum and step at
every equity; the risk leg adds exactly its own worst case; **`risk.max_group_risk_percent` (2.0) is
declarative: the executor never reads it** (an absurdly tight cap does not stop the order), and the
owner's equity-table sizing gives a base worst case above 2 % of equity at every tested equity below
$10,000. That is the existing route's behaviour, unchanged here; it is why enabling the extra leg must be
an explicit, reviewed decision rather than a side effect.

Partial fills and restart: with the leg present, a mid-ladder redelivery to a fresh executor resubmits and
cancels nothing, and the RISK leg later fills as one more leg of the same group on the shared stop.

## 5. The gate

`analysis.technical_authority.go_origin_risk_leg_enabled` (default **false**, independent of
`execution.reaction_risk_leg.enabled`, which keeps governing Python-owned plans exactly as before). While
false, every Go-origin match is tagged `risk_leg:disabled`; the executor (`PlanDisablesReactionRiskLeg`)
then places only the declared ladder. Turning it on removes the tag and the executor injects the leg for
Go-origin plans as it does today for Python-owned ones. **Acceptance to turn it on** (not met, not claimed):
an accepted ceiling for group worst case that the table above is judged against, and enforcement of it
(today nothing does); a review of fill ordering on real fills; the S14F evidence.
