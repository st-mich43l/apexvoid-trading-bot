# Key Level

## Strategy ID / version

`key_level`, `v2`. Implemented in
`analysis-engine/internal/strategy/keylevel`. Algorithm versions:
`structure=v2`, `liquidity=v1`, `zone=v1` (now used directly — see
"Formation / trigger / confirmation"), `config=3`.

## Purpose / thesis

Ported from the legacy Python detector (`app/analysis/detectors.py::
key_level_reaction`), not the simplified proximity-only v1 this package
originally shipped with (see "2026-09 port" below): a sufficiently touched
level is a standing reaction zone only once its **role** is actually
classified — support/resistance from an explicit structural kind, or (the
level primitive only ever emits `"reaction"`/`"round"`, never an explicit
kind) a role inferred from price position, with a genuine opposing
supply/demand zone overlapping the level's own band allowed to contradict
that naive inference rather than being ignored. A level several
consecutive closes have already accepted through is reported **BROKEN**
and never re-traded here — Break & Retest/Trendline own that
reinterpretation. A bare touch is a technical observation, not a trade:
only a real, closed-bar rejection produces a Candidate, and a level where
both candidate directions independently confirm in the same evaluation is
a genuine contradiction, discarded entirely.

## Supported instruments / timeframes

Any symbol; single timeframe `M5`.

## Required regime / structure / liquidity / location

- `level.Touches >= minimum_touches` and `level.Strength >= minimum_strength`
  (both stricter-only floors this platform already had before the port;
  the legacy detector had no per-level strength gate of its own).
- `|currentPrice - level.Price| / ATR <= proximity_atr` (likewise a
  platform pre-filter, not present in the legacy detector, which relied on
  confirmation alone to bound relevance).
- `role` (`internal/keylevel.Role`, ported 1:1 from `key_level_role.py`)
  is not `BrokenSupport`/`BrokenResistance`.
- A real, closed-bar rejection (see below) confirms exactly one direction.
- An opposing-side liquidity pool exists at the required distance, for the
  confirmed direction's target.
- `ctx.Volatility.ATR > 0` and at least one closed M5 candle exists.

## Formation / trigger / confirmation

`internal/keylevel` owns level clustering (price-clustered swings +
round-number levels + wick-touch enrichment); `internal/keylevel.Role`
owns role classification from a closes series (ported 1:1 from
`key_level_role.py`) — both existed before this port but the strategy
itself never wired `Role` up, believing (per this doc's own prior text)
that `MarketContext` exposed no raw candle feed. That was stale: every
`TimeframeContext` has carried a `Candles []market.Candle` field
("Strategies may inspect price action") since Analysis Engine V2, and 11
other strategies (supply, demand, crt, trendline, ...) already read it.
This strategy now does too, for two things the legacy detector needed and
the v1 port skipped:

1. **Role.** `closes` from `Candles` classifies each level BROKEN /
   AMBIGUOUS / (explicit) SUPPORT / RESISTANCE — `breakout_accept_bars`
   consecutive closes beyond the band before a level is reported broken.
   `key_levels()`'s own levels are always `kind` `"reaction"`/`"round"`,
   never an explicit label, so role is in practice always BROKEN or
   AMBIGUOUS for this strategy's own levels.
2. **Opposing-zone contradiction** (`opposing.go`, ported from
   `detectors.py::_opposing_zone_contradicts`). AMBIGUOUS role falls back
   to a naive price-position guess (price above the level → support →
   BUY; below → resistance → SELL). A real, unmitigated (not
   `Invalidated`/`Mitigated`) opposing-side zone (`ctx.Timeframes[M5].
   Zones.Zones`) overlapping the level's own band means that guess might
   be wrong: rather than flip it outright, both directions are tried, the
   reaction window widens to the opposing zone's own bounds, and real
   confirmation decides.
3. **Confirmation** (`confirmation.go`). Generalizes supply/demand's own
   `confirmedRejection` (zone-specific) to an arbitrary `[low, high]` band
   + direction, since a `Level` carries none of `zone.Zone`'s lifecycle
   fields. A touch (the band and the bar's range overlap) followed by a
   directional close outside the band — same bar or the immediately next
   one — confirms; a stale historical touch never creates a delayed
   confirmation. This is Key Level's own rejection-only shape, the same
   confirmation sophistication supply/demand already have in this engine
   — **not** a port of the legacy Python candle-pattern library
   (engulfing/sweep-reclaim/CHoCH-aware grabs), which this port does not
   attempt to replicate. A multi-bar reaction lookback (rather than
   current-or-previous-bar only) is the main capability gap left against
   the legacy detector's `evaluate_structural_reaction`.

If both directions confirm for the same level in the same evaluation,
neither survives (see Purpose).

## Entry / invalidation / target

- **Entry**: the (possibly opposing-zone-widened) reaction band —
  `levelPrice ± level.Band`, or wider when an opposing zone contradicted
  the naive guess.
- **Invalidation**: anchored to the level's own price for the level's own
  direction, or to the opposing zone's own contradicting edge for the
  direction that only exists because of it — support (Buy) →
  `anchor - level.Band - invalidation_buffer_atr*ATR`; resistance (Sell) →
  `anchor + level.Band + invalidation_buffer_atr*ATR`.
- **Target**: nearest opposing-side liquidity pool at least
  `minimum_target_distance_atr*ATR` from the (possibly widened) entry
  band's far edge — unchanged by this port.

## Expiry

`expiry_hours` after the confirming candle's own closed-bar time.

## Failure cases / anti-patterns

Insufficient touches, below-threshold strength, too far from current
price, BROKEN role, no closed candle yet (a genuinely fresh symbol —
"insufficient history"), no confirmed rejection yet (a resting
observation), both directions independently confirming (genuine
contradiction), no opposing liquidity for the confirmed direction, zero
ATR.

## Quality components / evidence codes

`proximity_quality` (`1 - distanceATR/proximityLimitATR`), `touch_quality`
(`Touches/5`, capped at 1), `strength_quality` (`level.Strength`) —
unchanged by this port.

Evidence codes: `m5_key_level_<kind>` (`reaction`/`round`),
`m5_key_level_touches_sufficient`, `m5_key_level_role_<role>`
(`support`/`resistance`/`ambiguous`), `m5_key_level_rejection_confirmed`,
and (only when an opposing zone widened the reaction band)
`m5_key_level_opposing_zone_widened`.

## Bullish / bearish examples

Price above the level with no opposing structure → Buy (naive support).
Price below → Sell (naive resistance). A real opposing supply zone
overlapping a level price sits above → both directions tried, entry
widened to the zone's own edge, only the one that actually closes back
outside the widened band confirms.

## Valid / invalid examples

Valid: as above, once confirmed by a real closed-bar rejection. Invalid:
`Touches` below the configured floor, price > `proximity_atr` away, role
BROKEN, no closed candle to derive a current price from, no confirmed
rejection, both directions confirming
(`test/strategy/keylevel/keylevel_test.go`).

## Known old-engine false positive

None specifically flagged for the reaction-to-key-level thesis itself in
the catalog (row 1) beyond the general primitive/strategy split — the
level clustering (`levels.py`) was `TECHNIQUE_ONLY` even in the legacy
audit; this strategy is a genuinely new, explicit tradeability layer on
top of it.

## Deduplication identity

`internal/keylevel` re-clusters from the current swing window on every
closed bar, so `levelPrice` can differ by a tiny amount between two
evaluations of what is really the same ongoing level. The original
`setupKey` used `levelPrice` at raw 6-decimal precision, so that jitter
produced a new `SetupKey` (and therefore a new deterministic opportunity
ID) almost every evaluation — running against real XAU M5 data
(`cmd/replay`) surfaced 147 "live" `key_level` opportunities across a
300-bar/25-hour window for what was really a much smaller number of
distinct levels.

S11 removed that self-referential bucket. A reaction level now inherits a
stable ID from its earliest canonical structural swing; a round level is
anchored to its configured round price. Clustering, wick enrichment and small
band/price changes preserve that ID. Direction remains part of the opportunity
identity, so a genuine support/resistance role transition is still distinct.
`setupKey` since the 2026-09 port also carries the confirming candle's own
touch/confirmation bar times, so a later distinct reaction on the same
level is its own opportunity rather than colliding with an earlier one.

The same real 300-bar XAU M5 replay now discovers 18 `key_level`
opportunities rather than the prior 147, while deterministic variation tests
prove that ATR/band jitter preserves identity and distinct anchors remain
distinct. This is structural identity repair, not an output-count cap.

## 2026-09 port: role classification and opposing-zone awareness

Owner-directed (2026-09-28): the v1 Go strategy above was a simplified,
proximity-only rebuild — no role classification, no opposing-structure
awareness, no closed-bar confirmation — documented at the time as a real
platform limitation (`MarketContext` believed to expose no raw candle
feed). That belief was stale; see "Formation / trigger / confirmation."
This port wires up the already-existing, already-tested `internal/
keylevel.Role` primitive and adds `opposing.go`/`confirmation.go`,
faithfully reproducing the legacy detector's role-classification and
opposing-zone-contradiction decision logic (not its deeper candle-pattern
confirmation library — see the confirmation bullet above for the disclosed
scope reduction there). Full real-capture replay (`test/replaycapture`)
discovers 1731 total candidates across all strategies against the
committed XAU capture, versus 1702 before this port (+29, `key_level`'s
own share of that difference not isolated here) — golden regenerated
2026-09-28, `analysis-engine/testdata/replay-go-xau-20260921.meta.json`.
