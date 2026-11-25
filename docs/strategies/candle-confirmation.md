# Candle confirmation is an execution condition (AE-CF1)

`analysis.require_candle_confirmation` in `config/analysis.yml` decides, for every strategy at once, whether a
structurally valid setup also needs a strategy's own entry-confirmation candle before it is executable. The Go
engine is the only place that decides; the Algo Bot adds no second authority and only stops refusing what Go
published.

| Value | Meaning |
|---|---|
| `true` | The existing behaviour, exactly. Every confirmation rule is unchanged (rejection, engulfing, wick, strong reclaim, rejection CHoCH, retest rejection, M1 continuation). Replays on five symbols are byte-identical to the previous release. |
| `false` (committed) | A valid structural setup is executable when price reaches its entry location, without an additional confirmation candle. The confirmation evidence is still computed and used for scoring whenever a candle shows it. |

A missing leaf makes the engine fail to start (like every other analysis leaf). Set it back to `true` to restore the
previous behaviour; no code change is needed.

## What stays the same in both modes

Structural formation (a sweep, a reclaim, a structure shift, an inversion, an accepted break), market structure,
invalidation, the stop and its budget, the target and its room, the leg ladders, the confluence floor, the
opportunity-quality model (`quality_ranking` stays `false`), the execution protections and every Algo Bot policy.
No confirmation function was removed or rewritten.

## What `false` changes

Each strategy takes the touch of its entry location as the trigger when no confirming candle exists:

* **A pattern is always preferred and reported when one exists**, so with `false` the set of setups is a superset of
  `true` by thesis (the same pattern-confirmed setups, plus the touch-only ones).
* **Nothing is made up.** A touch-only setup carries no `technical_context.confirmation`, no `*_rejection_confirmed`
  or `*_retest_rejection` evidence code, no wick or engulfing credit in the confluence factors, and the evidence code
  `candle_confirmation_optional`. The measured candle numbers in the quality model are the touch candle's own.
* **Go says it is executable.** The Algo Bot accepts a reaction-less match of a zone, level or line strategy only
  when the option is off **and** Go marked it `candle_confirmation_optional`. A resting observation (confluence zone,
  no reaction, no marker) stays non-executable.

## Per strategy

| Strategy | Entry confirmation that becomes optional | Trigger when it is absent | Unchanged formation |
|---|---|---|---|
| `supply`, `demand`, `order_block`, `fvg` | rejecting candle after the touch (`evaluateConfirmation`, FVG `view.confirm`) | the latest touch of the zone inside the confirmation window | zone, structure break, sweep gate, room |
| `key_level` | reaction candle at the level (`confirm.go`) | the latest touch of the level inside the window | level, role, sweep rule, target basis |
| `flip_zone`, `trendline` | `reaction.Evaluate` through `LegacyDetector` | the latest touch of the zone or line | role flip, two anchors, valid-side touch |
| `confluence_zone` | measured rejection (`measure`) | the touch candle itself (`ci == ti`), with its measured numbers | independent-structure overlap |
| `ifvg` | rejecting retest candle | the first retest touch | inversion |
| `break_retest` | retest rejection candle (M5; required since v4, `require_confirmation: true`) | the first retest touch | break, acceptance, protected structure |
| `round_number`, `asia_range` (accepted-break kind) | wick or engulfing retest candle (`FindLevelRetest`) | the first candle entering the band | the sweep, reclaim and structure-shift kind |
| `shock_retest` | wick or engulfing retest candle | the first candle in the retracement band | shock leg, band, dead zone |
| `impulse_pullback` | directional M1 candle that interacted with the reference (`ConfirmM1Execution`) | an M1 candle that interacted with it (`TouchM1Execution`) | M5 impulse, corrective pullback, reference |
| `crt` | none to relax: the sweep, reclaim and M5 structure shift (or the reclaim alone in `sweep_reclaim` mode) *are* the setup | unchanged | all of it |
| `range_scalp`, `impulse_retrace` | none: the probe close and the displacement bar are the setup | unchanged | all of it |

## Known limits of `false`

* The confluence star floor is a quality gate and is not touched, but for `supply`, `demand`, `order_block` and `fvg` it
  does **not read the reaction**. `confluence.Evaluate` scores a setup from its **zone score whenever that is above
  zero** (the v1 stars come from the zone score alone, normalised by 24.5 and cut at 8/20.5 and 12/20.5, plus 2.5 when
  the zone touches a fibonacci level; a zone with touches is capped at 2 stars). Supply and demand always have one:
  `scoreZone` adds a base of 1.5 to every zone. Order block and FVG take the frame's own score for the same zone
  (`attachScores`), and in the 21 days to 23 Nov every one of their 431 and 1,028 published opportunities carried one.
  The reaction factors (wick, displacement, change of character, higher-timeframe alignment, structural agreement,
  session) are computed and published in `technical_context.confluence`, but **they do not decide stars for these
  four**: a touch-only setup publishes whenever its zone scores at least 9.56 of 24.5 (7.06 with a fibonacci touch),
  exactly as a pattern-confirmed one does. A raw `m5_*_rejection_confirmed` is evidence, not a gate.
* The strategies whose zone carries **no** score (the factors then decide the stars, so a touch-only setup with no
  rejection wick to score may stay under the floor and not publish; every other factor still counts) are, as read at
  each call site of `confluence.Evaluate` and `LegacyDetector.Finish`: `key_level` and `trendline` (they build their
  zone without a score), `ifvg`, `crt`, `break_retest`, `round_number`, `asia_range` and `shock_retest`
  (`ZoneQuality{}`). `flip_zone` and `confluence_zone` pass their zone's score (the frame's score, and the region score
  of the overlap); in the same 21 days all 104 flip-zone opportunities and 676 of 1,115 confluence-zone ones had one,
  so for confluence zone the factors decide the stars on the rest. Measured: `analysis_opportunities`,
  `technical_context.confluence.zone_quality_score` above zero.
* A touch-only setup and a pattern-confirmed one of the same zone are separate opportunities (the setup key carries
  the confirmation bar). Go arbitrates them within one evaluation; across time the Algo Bot's thesis claim and
  exposure rules decide whether the second trades.
* `true` and `false` change the strategy fingerprint (the parameter is part of it), so opportunity ids differ
  between modes by design.

## Verification

`go test ./...` covers both modes (`internal/reaction`, `internal/strategyutil`, `internal/engine`,
`internal/replaycapture/candle_confirmation_test.go` on the real Trendline fixture with the confirming candles
removed) and `python -m unittest tests.test_candle_confirmation_optional` covers the adapter. Replay of five symbols
(XAU, EURUSD, GBPUSD, USDJPY, GBPJPY): `true` is identical to the previous release on every envelope; `false`
publishes more setups (supply 291 to 891, demand 316 to 847, fvg 136 to 793, order_block 117 to 308, key_level 1241
to 1447, confluence_zone 469 to 756, flip_zone 13 to 70, trendline 39 to 59, break_retest 25 to 36, round_number 3 to
13, ifvg 49 to 50) and every reaction-less one carries the marker. The Algo Bot's exposure, overlap and thesis rules
still apply to the larger flow.

## Measured (PR-X0, 23 Nov)

`algo-bot/scripts/xau_review_replay.py` (read-only; PR #901) applied the owner's pre-registered rule on XAU supply,
demand, order block and FVG trades closed 10-23 Nov:

* touch-only: n = 10, expectancy +0.27 R; confirmed: n = 3, expectancy +0.84 R;
* neither of the rules was met (A needs the touch-only cohort at least 0.20 R below the confirmed one with n >= 20 in
  each cohort in each half; B needs expectancy rising across the raw-factor-score terciles in both halves), so the
  branch is **C**: nothing changes, and this page is corrected to say what the floor really reads;
* at the window's trade rate the touch-only cohort reaches the 20-per-half sample around 2 Jan 2027 and the confirmed
  cohort around 6 May 2027;
* the decision is re-taken then by re-running `algo-bot/scripts/xau_review_replay.py` with the same rules.
