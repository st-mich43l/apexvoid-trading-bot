# XAU loss attribution, October 2026

What the recent XAU losses were, and what they were not. Written from production
fills (`auto_trade_fills` joined to `auto_trade_results`, read-only, 8 Oct 2026) and
a replay of the committed real captures through the current engine. Simulated
outcomes below are a *proxy* (market entry at the candidate bar's close, the
candidate's own stop, 2R target, 48 M5 bars) and are never presented as realised
performance; they ignore that a resting limit order only fills after price has come
to it.

## Baseline

Production ran build `31b8e57` with a configuration identical to the repository's.
Fills of the four contained strategies (`ifvg`, `liquidity_sweep`, `session_level`,
`impulse_pullback`) all predate the build that contained them; none has filled
since (PR A, #743).

## Where the −401 pips of Asia came from (1 Oct onward, algo_auto XAU)

| strategy | trades | wins | net pips | status |
|---|---|---|---|---|
| Key Level | 18 | 3 | −293 | live |
| Session Level | 5 | 0 | −204 | contained from 7 Oct |
| Liquidity Sweep | 3 | 0 | −125 | contained from 6 Oct |
| Supply Demand | 2 | 0 | −91 | live |
| Range Sweep Scalp | 9 | 3 | +54 | live |
| Breakout Retest Scalp | 3 | 3 | +77 | live |
| Impulse Pullback Scalp | 4 | 2 | +28 | contained from 7 Oct |

The contained strategies account for −329 of the −401. Asia is not weak at the
detector level: Key Level theses reach 2R before their stop 34 % of the time in Asia
in the 14-21 Sep week and 36 % in the 28 Sep-6 Oct window (proxy). No clock gate is
justified, and none is added.

## Key Level

Since 1 Oct: 28 trades, 7 wins (25 %), average win +171 pips, average loss -46,
net +223. At that payoff the break-even win rate is 21 %, so a high loss count is the
shape of the strategy, not a malfunction.

The 21 trades that fall inside the committed capture were joined to the engine's own
Key Level candidates (level kind, role, reaction, stars, regime, location, bias):

* Regime looked decisive in that sample (0 of 14 in chop, 3 of 7 in trend). It is not
  supported out of sample: over all 336 Key Level theses of the same window, chop and
  trend reach 2R before the stop equally often (30 % and 30 %), and in the profit week
  chop was slightly better (37 % against 30 %). A regime filter would be a fit to 21
  trades and is **not** made.
* Premium/discount, level kind, reaction type, stars and HTF alignment show the same
  small-sample pattern and no independent support. Counter-bias entries are valid
  trades; none is removed.
* Fills long after the candidate (7 of 18 losses filled over an hour later, up to
  6.9 h) are a limit order waiting for price to return; an earlier analysis of 72 fills
  found freshness is not what hurts (within 15 min: -444, after: +246).
* Stops are 35-57 pips, inside the XAU envelope; no stop or target geometry defect.

Verdict: valid losing setups of a low-hit-rate, high-payoff strategy. The detector is
bar-for-bar identical to the profit-week Python (PR #737). **No Key Level rule changes.**

## Range Edge

4 trades (1 win, 3 full stops, -96): too few to attribute. The detector is
parity-proven (`test/detectorparity`); in the proxy, Range Edge theses reached 2R before
the stop 39 % of the time in the profit week (28 theses) and 20 % in the incident window
(40 theses), a market-regime difference between windows with overlapping intervals. Two of
the four fills have no candidate in the replay near their entry (they predate the engine
build replayed). **No change; a defect is not demonstrated.**

## Normalization audit (XAU: pip 0.1, 2 digits, round step 5.0)

Searched the Go engine and Algo Bot for FX-derived constants. Instrument scale comes from
`instruments.yml` everywhere (pip, price digits, round step, zone and FVG widths, stop
envelope). One defect found and fixed: the MAD phase classifier's sweep tolerance floor
used a single global `analysis.mad.pip_size: 0.0001` (an FX value) for every instrument,
where the frozen classifier received the symbol's own pip size. It is now set per
instrument. Effect: MAD telemetry only (confluence scoring is v1, MAD is never a gate);
every replay and parity golden is unchanged.

## Limits

No per-trade MFE/MAE exists in the database; the 14-18 Sep trade records and M1 bars no
longer exist. Nothing here claims restored profitability.
