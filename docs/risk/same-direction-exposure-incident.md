# Same-direction exposure: the 12-13 October USDJPY incident and the instrument-owned group policy

Status: **for owner review. No live limit is activated by this change.** The policy machinery is merged-ready and inert:
an instrument that does not declare `exposure.same_direction` behaves exactly as before. The proposed USDJPY setting is a
separate, unapplied patch ([same-direction-exposure-usdjpy.patch](same-direction-exposure-usdjpy.patch)).

Principle: **one failing structural thesis must not silently become several correlated account losses.**

## 1. What happened

On 12 October 2026 four Range Edge Scalp SELLs were admitted on USDJPY against what the engine kept calling one rising
resistance (06:05, 06:51, 08:16 and 09:01 UTC; a fifth SELL at 01:48 had stopped out earlier). They were open together,
at up to four concurrent groups, and stopped out between 09:39 and 10:46 as price continued up. On 13 October the same
shape repeated with a Key Level BUY and a Range Edge BUY (stopped together at 08:31) and a Key Level SELL and a Range Edge
SELL (stopped 13:02 and 13:13).

### Reconstruction

Source: production, read-only. `auto_trade_results` and `auto_trade_fills` (fills, volumes, closes), `analysis_opportunities`
(the Go opportunity as published: structure id, evidence, invalidation) and the algo-bot log
(`/var/log/apexvoid/algo-bot.log.*`, admission lines). Money is **derived** (lots x pips x the instrument pip value from
`config/instruments.yml`), never broker-confirmed; a plan's planned risk is its broker volume x planned stop distance.
Dataset: `algo-bot/tests/fixtures/exposure_groups_20261005_20261014.json` (332 autonomous + 7 manual groups); builder:
`algo-bot/tools/exposure_dataset.py`.

| Admitted (UTC) | Opportunity | Strategy | Dir | Structure (range id) | Fill | Closed | Lots | Planned stop (pips) | Planned risk | Same-direction groups already open | Admitted as stack | Result (derived) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 11 Oct 21:47:06 | `a4d69d19` | Confluence Zone | SELL | `confluence:sell:ifvg+supply_demand:158.300000` | 22:00:02 | 04:17:10 (+1d) | 0.24 | 17.1 | $25.73 | 0 | no | tp1_be $+28.59 |
| 12 Oct 01:48:03 | `1e84ba92` | Range Edge Scalp | SELL | `range:resistance:1791763500` | 01:48:05 | 04:53:18 | 0.15 | 18.0 | $16.93 | 1 | yes | full_stop $-16.93 |
| 12 Oct 06:05:03 | `4ac72614` | Range Edge Scalp | SELL | `range:resistance:1791782400` | 07:00:49 | 09:39:05 | 0.18 | 14.3 | $16.14 | 0 | no | full_stop $-15.80 |
| 12 Oct 06:51:01 | `ab2e1a3a` | Range Edge Scalp | SELL | `range:resistance:1791782100` | 06:51:05 | 09:39:07 | 0.11 | 17.9 | $12.35 | 1 | yes | full_stop $-12.41 |
| 12 Oct 08:16:03 | `17d2b4fc` | Range Edge Scalp | SELL | `range:resistance:1791788700` | 08:17:06 | 10:46:23 | 0.11 | 18.0 | $12.41 | 2 | yes | full_stop $-12.41 |
| 12 Oct 09:01:05 | `f5cd83b5` | Range Edge Scalp | SELL | `range:resistance:1791789000` | 09:01:11 | 10:27:58 | 0.11 | 17.7 | $12.21 | 3 | yes | full_stop $-12.41 |
| 12 Oct 11:12:02 | `b7d810e8` | Range Edge Scalp | SELL | `range:resistance:1791800700` | 11:12:07 | 02:01:06 (+1d) | 0.23 | 18.1 | $26.10 | 0 | no | tp2_full $+51.92 |
| 13 Oct 02:25:03 | `8639a301` | Key Level | BUY | `keylevel:reaction:158.226` | 02:25:59 | 08:31:14 | 0.26 | 17.7 | $28.85 | 0 | no | full_stop $-29.34 |
| 13 Oct 06:31:02 | `b6f125af` | Range Edge Scalp | BUY | `range:support:1791860700` | 06:31:13 | 08:31:02 | 0.14 | 18.0 | $15.80 | 1 | yes | full_stop $-15.80 |
| 13 Oct 08:41:02 | `2a73d004` | FVG | SELL | `technique:fvg:sell:1791880500` | 08:45:04 | 08:45:12 | 0.18 | 9.5 | $10.72 | 0 | no | manual_close $+13.54 |
| 13 Oct 10:01:04 | `90772433` | Key Level | SELL | `keylevel:round:158.105` | 10:01:17 | 13:02:47 | 0.23 | 17.8 | $25.67 | 0 | no | full_stop $-25.96 |
| 13 Oct 11:45:01 | `42d223ee` | Range Edge Scalp | SELL | `range:resistance:1791888300` | 11:46:48 | 13:13:43 | 0.14 | 17.9 | $15.71 | 1 | yes | full_stop $-15.80 |
| 13 Oct 13:21:02 | `04d6228d` | Supply Demand | SELL | `technique:supply_demand:sell:1791870600` | 13:21:05 | 15:01:30 | 0.24 | 18.1 | $27.24 | 0 | no | full_stop $-27.09 |
| 13 Oct 16:11:02 | `092a6149` | Range Edge Scalp | BUY | `range:support:1791899100` | 16:13:31 | 03:33:06 (+1d) | 0.24 | 17.0 | $25.58 | 0 | no | full_stop $-25.58 |

Four concurrent SELLs between 09:01 and 09:39 risked **$53 planned**, twice a normal USDJPY group (about $26); the four lost
**$53.03** between them (derived). The 13 Oct pairs lost $45.14 (stopped together at 08:31) and $41.76 (13:02 and 13:13).

For each Oct-12 trade (details also in the opportunity envelopes): the Go range state was `confirmed_range` or
`post_impulse_range`; the reaction was a wick rejection, engulfing, strong reclaim or `rejection_choch` at the barrier; the
technical invalidation (`range_edge_accepted_break`) sat 0.7-0.9 pips above the zone, but the published stop envelope
`scalp_room` (floor 18 pips, cap 28) put the broker stop **about 18 pips** away, six times the 3 pip M5 ATR; the intermediate
bias read BUY on the SELLs. The Go detector's own `break_distance_atr` was already 1.3 and 1.6 ATR on the 08:16 and 09:01 entries (absent on the first
two) and 10.2 ATR on the 11:12 one; that is the Go-side subject of Range Edge V3 (PR B).

### Why four distinct opportunities were admitted

Three independent guards exist and each had a hole the sequence went through:

| Guard | What it checks | Why it did not stop the sequence |
|---|---|---|
| Thesis claim (`claim_active_thesis`) | one active thesis owns at most one initial plan | each Range Edge opportunity carried a **different thesis** (its structure id is `range:resistance:<first-touch bar time>`, and the barrier was rebuilt higher each time: 1791782100, 1791782400, 1791788700, 1791789000, 1791800700) |
| Entry corridor (`reserve_entry_zone`) | same direction, zones within 1 ATR, for 45 minutes **from admission**, whether or not the position is open | the window is a clock, not exposure: 06:51:00 was **45 min 57 s** after the 06:05:03 admission (57 s past the window, with that order still pending until 07:00:49); 09:01:04 was 45 min 02 s after the 08:16:02 admission (2 s past it). The 08:30 candidate inside the window was refused (`entry_zone_overlap_same_direction`), proving the guard worked while it lasted |
| Same-direction exposure (`evaluate_entry_against_exposure`) | non-scalps must wait for TP2 and be Tier A | `allow_same_direction_stack = candidate_is_scalp`: **scalps stack freely**, at 60% size, with **no count and no risk budget** (log: `v8 same-direction stack ... size 60% on a single leg` at 06:51:00, 08:16:02, 09:01:04) |

So nothing bounded the number of same-direction groups once the 45 minute clock lapsed, and nothing measured what they
risked together. Pending exposure was visible (the 06:51 stack names the still-pending 06:05 plan, "active SELL @ 158.39"),
but visibility only chose the 60% size; it never refused.

Limits of this reconstruction: the production plan-runtime Redis state and the configuration fingerprint of 12 October are not
retained (the container was redeployed on 13 Oct 11:54). The 60% stack and the 45 minute window are confirmed from the log
text of the incident itself, not assumed from master. The executor's stop-out times come from `auto_trade_results`.

## 2. The policy

`config/instruments.yml`, per instrument, optional (absent = the behaviour above, unchanged):

```yaml
exposure:
  opposite_position: {allowed: false}     # existing, independent
  same_direction:
    max_groups: 1                  # concurrent groups per symbol and direction
    max_cumulative_risk: 30.0      # worst-case remaining loss, this direction, account currency
    max_symbol_risk: 60.0          # same, both directions
    risk_free_groups_count: true   # a group still counts toward max_groups after TP1/BE
    duplicate_thesis_block: true   # no second group on the same thesis or structure id
    strategy_overrides: {key_level: {max_groups: 2}}   # only where justified
    reservation_ttl_seconds: 600
    snapshot_max_age_seconds: 120
```

Decision (`app/autotrade/group_exposure.py`, called from `worker._publish_trade_plan_v8` after the plan is built and before it
is published): count every pending, submitted, partially filled and open group of the symbol; sum each group's **worst-case
remaining loss** (remaining lots x distance from fill to the current stop x pip value; unfilled legs at their intended price;
cancelled, closed and rejected legs contribute nothing; a stop at or beyond breakeven contributes zero but the group still
counts); add the incoming group's planned loss (the executor's own sizing rule, equity table x risk multiplier, mirrored from
`VolumePlanner.cs`); refuse on the first of: unknown exposure, duplicate thesis, group limit, direction budget, symbol budget.

* **Unknown is never zero.** A stale or missing executor snapshot, an unreadable runtime state, a broker group with no runtime
  state, and (under a money limit) any group or incoming plan whose risk cannot be computed all refuse admission with
  `exposure_unknown`. A count-only policy still counts a group of unknown risk.
* **No double counting.** Groups are keyed by plan/setup id: a plan listed twice, a reservation and its runtime state, and a
  Redis plan that is also in the broker snapshot are one group. Instrument aliases (`XAUUSD`/`XAU`) are normalised.
* **Atomic.** One Lua script per `symbol + direction` counts the in-flight reservations together with the runtime exposure the
  caller measured and writes the reservation: two workers cannot both take the last slot (60 concurrent admissions, 3 slots, 3
  winners, on a real Redis). A repeated admission of the same setup (duplicate Kafka delivery) is idempotent.
* **Lifecycle.** The reservation is released on every failure before the plan reaches the stream (lifecycle error, publish
  exception, terminal setup); it is superseded as soon as the plan is visible in the executor runtime; it is dropped when the
  executor acked it `rejected`/`cancelled`/`expired`, or when the plan never reached the plan store within 90 s; and it expires
  at `reservation_ttl_seconds`, so a crashed worker holds a slot for at most ten minutes. If Redis cannot run the script,
  admission is refused (`group_exposure_reservation_unavailable`, intent retained), never allowed.
* Manual Algo groups count as exposure but the autonomous policy never refuses a manual plan. Opposite-direction exposure
  keeps its own policy (`exposure.opposite_position`) untouched. Global arbitration and every strategy are untouched.

Out of scope, stated: the executor (`ctrader-engine`) is unchanged; a fence there would be a second, deeper line of defence but
is a separate approval (broker executor semantics).

## 3. Policy comparison on the production groups

Chronological replay of the groups production admitted, using the same decision function the worker uses, with every blocked
group **removed from the book** so later decisions see the exposure that policy would really have produced
(`algo-bot/tools/exposure_policy_replay.py`; results `same-direction-exposure-policy-results.json`). A: current unlimited
stacking. B: one group per symbol and direction. C: two groups and a $55 direction budget. D: one group per structure id and an
$80 symbol ceiling. Planned risk is held for the whole life of a group (no credit for TP1 or a moved stop): conservative.
"Correlated stop cluster" is two or more same-direction full stops within 20 minutes of each other.

**USDJPY, 12-14 Oct (the incident)**

| Policy | Allowed | Blocked | Peak groups | Peak planned risk | Correlated stop clusters | Cluster loss | Derived P&L, allowed | Derived P&L, blocked |
|---|---|---|---|---|---|---|---|---|
| A_unlimited | 14 | 0 (0W / 0L) | 4 | $53 | 4 | $-140 | $-115 | $+0 |
| B_one_group | 8 | 6 (0W / 6L) | 1 | $29 | 0 | $0 | $-30 | $-86 |
| C_two_groups_budget | 12 | 2 (0W / 2L) | 2 | $45 | 3 | $-115 | $-91 | $-25 |
| D_one_per_thesis_symbol_ceiling | 14 | 0 (0W / 0L) | 4 | $53 | 4 | $-140 | $-115 | $+0 |

**USDJPY, 5-14 Oct**

| Policy | Allowed | Blocked | Peak groups | Peak planned risk | Correlated stop clusters | Cluster loss | Derived P&L, allowed | Derived P&L, blocked |
|---|---|---|---|---|---|---|---|---|
| A_unlimited | 53 | 0 (0W / 0L) | 4 | $64 | 8 | $-289 | $+198 | $+0 |
| B_one_group | 34 | 19 (7W / 12L) | 1 | $31 | 0 | $0 | $+218 | $-20 |
| C_two_groups_budget | 48 | 5 (3W / 2L) | 2 | $47 | 7 | $-264 | $+168 | $+30 |
| D_one_per_thesis_symbol_ceiling | 53 | 0 (0W / 0L) | 4 | $64 | 8 | $-289 | $+198 | $+0 |

**USDJPY, 5-9 Oct (development half)**

| Policy | Allowed | Blocked | Peak groups | Peak planned risk | Correlated stop clusters | Cluster loss | Derived P&L, allowed | Derived P&L, blocked |
|---|---|---|---|---|---|---|---|---|
| A_unlimited | 38 | 0 (0W / 0L) | 3 | $64 | 3 | $-111 | $+326 | $+0 |
| B_one_group | 26 | 12 (7W / 5L) | 1 | $31 | 0 | $0 | $+247 | $+78 |
| C_two_groups_budget | 35 | 3 (3W / 0L) | 2 | $47 | 3 | $-111 | $+271 | $+55 |
| D_one_per_thesis_symbol_ceiling | 38 | 0 (0W / 0L) | 3 | $64 | 3 | $-111 | $+326 | $+0 |

**USDJPY, 10-14 Oct (held-out half)**

| Policy | Allowed | Blocked | Peak groups | Peak planned risk | Correlated stop clusters | Cluster loss | Derived P&L, allowed | Derived P&L, blocked |
|---|---|---|---|---|---|---|---|---|
| A_unlimited | 15 | 0 (0W / 0L) | 4 | $53 | 5 | $-178 | $-128 | $+0 |
| B_one_group | 8 | 7 (0W / 7L) | 1 | $29 | 0 | $0 | $-30 | $-98 |
| C_two_groups_budget | 13 | 2 (0W / 2L) | 2 | $45 | 4 | $-153 | $-103 | $-25 |
| D_one_per_thesis_symbol_ceiling | 15 | 0 (0W / 0L) | 4 | $53 | 5 | $-178 | $-128 | $+0 |

**XAU, 5-14 Oct**

| Policy | Allowed | Blocked | Peak groups | Peak planned risk | Correlated stop clusters | Cluster loss | Derived P&L, allowed | Derived P&L, blocked |
|---|---|---|---|---|---|---|---|---|
| A_unlimited | 127 | 0 (0W / 0L) | 2 | $121 | 10 | $-1156 | $+2589 | $+0 |
| B_one_group | 110 | 17 (5W / 12L) | 1 | $117 | 6 | $-750 | $+2682 | $-92 |
| C_two_groups_budget | 65 | 62 (24W / 38L) | 2 | $55 | 4 | $-262 | $+212 | $+2377 |
| D_one_per_thesis_symbol_ceiling | 97 | 30 (13W / 17L) | 2 | $79 | 5 | $-391 | $+922 | $+1667 |

**EURUSD, 5-14 Oct**

| Policy | Allowed | Blocked | Peak groups | Peak planned risk | Correlated stop clusters | Cluster loss | Derived P&L, allowed | Derived P&L, blocked |
|---|---|---|---|---|---|---|---|---|
| A_unlimited | 52 | 0 (0W / 0L) | 5 | $89 | 1 | $-49 | $+636 | $+0 |
| B_one_group | 35 | 17 (14W / 3L) | 1 | $32 | 0 | $0 | $+348 | $+288 |
| C_two_groups_budget | 48 | 4 (4W / 0L) | 2 | $51 | 1 | $-49 | $+516 | $+120 |
| D_one_per_thesis_symbol_ceiling | 51 | 1 (1W / 0L) | 4 | $73 | 1 | $-49 | $+601 | $+35 |

**GBPUSD, 5-14 Oct**

| Policy | Allowed | Blocked | Peak groups | Peak planned risk | Correlated stop clusters | Cluster loss | Derived P&L, allowed | Derived P&L, blocked |
|---|---|---|---|---|---|---|---|---|
| A_unlimited | 44 | 0 (0W / 0L) | 4 | $80 | 5 | $-214 | $+254 | $+0 |
| B_one_group | 27 | 17 (8W / 9L) | 1 | $39 | 0 | $0 | $+240 | $+14 |
| C_two_groups_budget | 36 | 8 (3W / 5L) | 2 | $48 | 3 | $-109 | $+278 | $-24 |
| D_one_per_thesis_symbol_ceiling | 44 | 0 (0W / 0L) | 4 | $80 | 5 | $-214 | $+254 | $+0 |

**GBPJPY, 5-14 Oct**

| Policy | Allowed | Blocked | Peak groups | Peak planned risk | Correlated stop clusters | Cluster loss | Derived P&L, allowed | Derived P&L, blocked |
|---|---|---|---|---|---|---|---|---|
| A_unlimited | 56 | 0 (0W / 0L) | 3 | $61 | 5 | $-183 | $+190 | $+0 |
| B_one_group | 41 | 15 (7W / 8L) | 1 | $41 | 1 | $-10 | $+154 | $+37 |
| C_two_groups_budget | 53 | 3 (1W / 2L) | 2 | $49 | 4 | $-111 | $+210 | $-20 |
| D_one_per_thesis_symbol_ceiling | 56 | 0 (0W / 0L) | 3 | $61 | 5 | $-183 | $+190 | $+0 |

**All symbols**

| Policy | Allowed | Blocked | Peak groups | Peak planned risk | Correlated stop clusters | Cluster loss | Derived P&L, allowed | Derived P&L, blocked |
|---|---|---|---|---|---|---|---|---|
| A_unlimited | 332 | 0 (0W / 0L) | 5 | $121 | 29 | $-1892 | $+3867 | $+0 |
| B_one_group | 247 | 85 (41W / 44L) | 1 | $117 | 7 | $-760 | $+3642 | $+226 |
| C_two_groups_budget | 250 | 82 (35W / 47L) | 2 | $55 | 19 | $-795 | $+1384 | $+2484 |
| D_one_per_thesis_symbol_ceiling | 301 | 31 (14W / 17L) | 4 | $80 | 24 | $-1127 | $+2165 | $+1702 |

Reading it:

* **The incident.** B would have admitted the first SELL and refused the next three on 12 Oct, and the second of each 13 Oct
  pair: six USDJPY groups, all losers (derived -$86 avoided of -$115), no correlated clusters, one group at risk at a time.
* **It is a concentration control, not an expectancy improver.** Over the whole fortnight B on USDJPY is roughly neutral
  (+$20) because it also removed seven winners: on the earlier half (5-9 Oct) B *cost* $78 (blocked 7W/5L); on the held-out
  half (10-14 Oct) it saved $98 (blocked 0W/7L). Do not read the incident as proof that stacking loses.
* **It must not be applied globally.** On EURUSD one group per direction would have blocked 17 groups (14 winners, 3 losers; +$288 derived); on XAU the
  $55 budget of C is absurd (XAU groups risk up to about $120 each) and removes groups worth a net +$2.4k. Budgets are instrument-sized
  amounts, never a shared number. D's symbol ceiling and thesis block are inert on USDJPY because the thesis ids differ,
  which is the Go-side defect that PR B addresses.
* USDJPY group-limit sweep (all fortnight): limit 1 blocks 19 (7W/12L), 0 clusters; limit 2 blocks 5, 7 clusters remain; limit 3
  blocks 1; limit 4 blocks none. Only a limit of 1 removes the clusters.

### Limitations

Simulated, not broker execution. The replay can remove trades but not add them: a candidate refused earlier by the corridor
guard (787 `entry_zone_overlap_same_direction` log lines in the period, most of them repeats of one setup) that a freed slot
would have let through is not in the data, so its effect is an unmeasured upside and downside. Money is derived from planned
volumes; the 7 autonomous groups without an admission log line enter at their first fill (their pending time is unmodelled); the
counted risk ignores TP1/BE credit. One fortnight, one regime, USDJPY n=53.

## 4. Proposed live setting (owner decision, not applied)

USDJPY only: `max_groups: 1` (one active same-direction group at a time, pending and submitted included). The patch is
[same-direction-exposure-usdjpy.patch](same-direction-exposure-usdjpy.patch); it touches only `config/instruments.yml`
and is **not applied** by this change. XAU, EURUSD, GBPUSD and GBPJPY keep the current behaviour; the replay does not support
one-group caps on them (EURUSD clearly not; XAU and GBPJPY are neutral to positive and need their own sizing).

Rollout: apply the patch, deploy algo-bot (config is read at start), watch `v8 group exposure refused` log lines and the
`same_direction_group_limit` rejection counts. Rollback: remove the `same_direction` block (restores the previous behaviour
exactly) and redeploy. No sizing, risk percent, ladder, TP, BE or executor setting is involved.
