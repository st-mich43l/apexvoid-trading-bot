# Auto-trade execution integrity

TradePlan V8 is the only execution contract. Algo Bot declares one complete,
versioned plan - exact entry instruction, absolute stop, absolute targets -
and cTrader Engine executes it without recomputing any of it. Autonomous
setups and owner-armed manual `/algo` signals travel the same path. When any
ownership or exposure evidence is uncertain, execution fails closed.

## One plan, one order path

- Algo Bot builds a plan (`app/autotrade/trade_plan_builder.py` for analysis
  setups, `app/signals/manual_plan.py` for manual intents) and appends it to
  `execution:trade_plans`. See `docs/redis-contract.md` for the key layout.
- cTrader Engine claims the plan, sizes it against live equity, and submits
  the declared legs. Python never talks to the broker.
- Manual plans carry `strategy_family=manual` and `plan_id=manual:{signal}:{rev}`.
  Their events are labelled `algo_manual` (kept out of autonomous accounting)
  and they are exempt from the autonomous exposure rules below: an owner
  instruction is a direct decision, not analysis output.
- Owner controls (`/trade_close`, `/trade_close_auto`, `/trade_sl`,
  `/auto_close_all`, `/trade_cancel`) act on the plan runtime: closes and
  stop moves go through the executor, cancels through the plan cancel intent.
  The owner pause (`auto_trade:paused`) holds new submissions.

## Autonomous exposure

Opposite-direction exposure on a symbol is decided per instrument from
`instruments.yml` (`exposure.opposite_position`): FX never allows it; XAU only
at >= 150 pips from every opposite group. Algo Bot checks it at admission
(`evaluate_opposite_exposure`) and cTrader Engine enforces it again before the
first broker order of a plan (`OppositeExposureFence`), against tracked plan
state plus real broker positions and pending orders. A missing policy fails
closed. Same-direction stacking is a separate rule: a non-scalp plan waits
until every live same-direction plan has booked TP2.

## Ranked cycle ownership

One closed M1 event is one arbitration cycle. The worker ranks all executable
intents first, then acquires:

```text
auto_trade:cycle_route_lock:{SYMBOL}:{cycle_id}
```

The cycle lock serializes evaluation of the complete ranked list. A separate
per-route lock:

```text
auto_trade:route_lock:{SYMBOL}:{match_id}
```

prevents two workers from evaluating the same StrategyMatch concurrently. The
route lock does not choose the cycle winner. Both lock types use random owner
tokens, a finite TTL, and compare-and-delete Lua release so an expired owner
cannot delete a successor's lock.

Publication returns a typed result. `route_in_progress`, `cycle_conflict`,
`duplicate_reaction`, `duplicate_thesis`, and `publication_unavailable` all
preserve the preferred route and block lower-ranked intents. Only an intent-specific
`terminal_reject` permits fallback. The authoritative cycle winner is stored
at:

```text
auto_trade:cycle_owner:{SYMBOL}:{cycle_id}
```

Duplicate event delivery therefore cannot replace the first valid winner.

## XAU risk-leg execution policy

`execution.reaction_risk_leg.enabled` is the single switch for the executor-
injected XAU RISK leg (fixed 0.02/0.05 lots depending on equity, 15 pips
inside the stop), matching the Manual Algo mechanism. It applies to every
XAU multi-leg TradePlan regardless of whether its technical provenance is Go
or Python. Technical source ownership must not alter execution leg geometry.
An explicit `risk_leg:disabled` tag remains available as a plan-level opt-out.

The gate exists because worst-case group risk is a function of the full
group — declared ladder plus any injected risk leg — not of either leg in
isolation. `xau_ladder.worst_case_group_loss` computes that combined
worst case (ladder loss at every leg's widest permitted stop, plus the risk
leg's own fixed worst case) so it can be reviewed against account equity
before the leg is used in production; `risk.max_group_risk_percent`
is declarative only — the executor never reads it, so an absurdly tight cap
does not by itself stop an order. Turning the gate on requires that review
to have actually happened: an accepted ceiling for group worst case, and
something enforcing it.
