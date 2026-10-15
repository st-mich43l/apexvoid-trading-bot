# Why the bot is not profitable: investigation (2026-10-15)

Written for: the owner. Reproduce with `algo-bot/tools/performance_investigation/` (inputs are read-only production
exports and the committed captures; none is committed). Nothing here changed any runtime behaviour.

**Verdict.** The bot loses because its signals have **no edge over random entries** and because its **risk is sized and
concentrated so that a zero-edge system swings violently**. It is not an implementation fault: the replay predicts
production on the same opportunities 88.5% of the time, stops are not systematically hunted, and the execution path does
what the plan declares. Parity with the old Python and quality scoring do not change this, because neither creates an edge.

## 1. What was measured

* 424 `algo_auto` trades, 29 Sep - 15 Oct (Range Sweep excluded, `trade_stream = 'algo_auto'` only). R per trade is
  `result_pips / stop_pips` for full stops, `realized_pips / stop_pips` where the executor recorded it, else the path's
  reference R. It is net of what the executor recorded, not of broker commission.
* 15,204 replay candidates (all strategies, all committed captures) with closed-candle causal fills; 10,388 of them
  since 2 Oct, when production ran the same code.
* 21,190 opportunities in 14 days, **14,143 became matches, 424 became trades (3.0%)**.
* Window: two weeks, one regime (mean-reverting against the 24 h move, a CPI shock). Every number below is a statement
  about this window.

## 2. The P&L

Mean R per trade is **-0.015 (95% interval +-0.12)**: flat before any cost the executor does not record. Dollars are
another matter, and they are a sizing result (section 6): XAU carries about three times the dollar risk of an FX trade
(the lot table sizes by equity, not by stop distance) and has the weaker R (XAU -0.16 +-0.20 per trade, n=162; the FX pairs between -0.00 and +0.19, each +-0.26 to +-0.32), so it is where the
account went: my dollar estimate (lots from the fills, standard pip values, not read from the broker) is about -$1,560 on XAU
against about -$1,400 for all strategies together, so the FX pairs net roughly zero.

## 3. The signals have no edge over random entries

Three tests, each against random entries with the same stops, spreads and exit rules.

| Test | Signals | Random | Difference |
|---|---|---|---|
| P(+1R before the stop), production envelope geometry, 5,933 thinned candidates | 46.0% | 49.5% | **-3.5 pp** (z = -3.6, naive) |
| same, by symbol | XAU 43.4, EURUSD 44.5, GBPUSD 46.7, USDJPY 49.0, GBPJPY 46.4 | 47.6 / 49.8 / 50.2 / 51.3 / 49.4 | negative in all five |
| same, by session | Asia 44.4, London 47.3, overlap 48.0, NY 45.8, late 45.3 | 48.3 / 49.3 / 49.5 / 49.5 / 53.2 | negative in all five |
| Technical geometry (the strategy's own stop at its invalidation and its first target, median RR 1.9), market entries inside the zone | EV -0.007R | +0.021R | -0.028R, CI [-0.101, +0.045] |

Production itself shows the same shape: 54% of trades stop out, and of those that reach 1R about half go on to 2R, so
neither stage beats a coin flip.

**Correction.** My first run of the technical-geometry test gave the signals **+3.7 pp** (CI +2.1..+5.4) over random. That
was an artifact: it filled a limit order at the zone edge the instant the signal printed, although price was not there.
With realistic fills (the limit must trade through; 445 never filled) the result flips: limit-fill entries are
**-0.179R vs +0.002R** (CI [-0.268, -0.082]) and the pooled result is -0.081R (CI [-0.147, -0.012]). The negative limit
result partly reflects adverse selection (a limit fills when price is moving through it), so the market-entry row above is
the cleaner statement: no edge.

By strategy at technical geometry, with realistic fills, nothing separates from zero except negatives (demand -0.225R,
flip_zone -0.326R, fvg -0.095R, EURUSD -0.312R); the positives (supply +0.088R, snap_back, momentum_ride) all have
intervals that include zero and tiny samples. Quality does not help either: see [ae-quality-calibration.md](ae-quality-calibration.md).

## 4. Stops, targets and the "stop hunt" hypothesis

* A stop is not special. Of 227 full stops, **70% would have reached +1R from entry within 24 h**; random entries that are
  stopped do so **67%** of the time. The stops are not being hunted more than any other level.
* Stopped trades had a median favourable excursion of **0.63R** and 59% reached 0.5R: the entries are often right for a
  while and then lose, which is what a coin flip with a 1R / 1R race looks like. For FX full stops only 0-16% ever
  reached 1R (XAU's 47% is not explained by this and its recorded stop distance disagrees with the bar-based excursion, so treat the XAU figure as unreliable).
* **The FX stop is not the strategy's stop.** The strategy's invalidation is a median 4-9 pips from the entry; the
  executed stop is the **envelope floor** (EURUSD 11.9, GBPUSD 15.0, USDJPY 17.9, GBPJPY 21.8): 71-81% of FX stops sit
  exactly on the floor, **about three times the technical distance**, with targets at 1R and 2R of that. The thesis is
  judged at 5 pips and traded at 12-22. With no edge this does not change the sign of the result, but it means the
  thesis the detector describes is not the trade the account takes.
* XAU stop-outs lose **-1.117R on average (FX: -0.99 to -1.01R)**; Key Level on XAU loses **-1.20R** (n=45). About 12% of
  every XAU stop is slippage or spread, which is roughly half of XAU's -0.14R per trade.

## 5. The execution path is faithful

For the 97 production trades whose opportunity id also exists in the replay, the replay's 1R outcome agrees with
production's on **77 of 87** decided cases (88.5%); the fill follows the signal by a median 4.2 minutes. The replay
simulator is therefore a fair proxy of what the executor does, and "the port is broken" is not what the data show.

## 6. Why it is unstable: sizing and concentration

* **Lots depend on equity only.** `lots_for_equity` returns 0.12 lots on XAU and 0.15 on FX whatever the stop. My estimate
  of the money at risk per trade is about **$52 on XAU (4.9% of a $1,072 account)** against **$16-21 on FX (1.5-2%)**;
  the configured `risk_pct: 2.0` is not what sizes the order.
* **Concurrency.** An average of **4.85 groups are open at once, up to 11**; same-USD-direction FX groups have an outcome
  correlation of **+0.35**, so they behave like one bigger position. The per-symbol cap (#784, #785) does not cap that.
* **Result.** Daily R has a standard deviation of **8.7R** (worst -22.3R, best +20.5R), cumulative drawdown 38R inside the
  window. At zero edge, 400 trades and these risk levels, a simulation gives the chance of losing half the account:
  0.5% risk 0%, 1% 0%, **2% 18%, 3% 40%, 5% 72%**. XAU's effective 5% is in the last row; the demo balance is now $1,072.
* There is no account-level daily-loss or drawdown halt; the daily-R limit in `app/scalping/risk.py` covers scalp entries
  only.
* Trade R has no serial dependence (lag-1 autocorrelation 0.049) and the longest losing streak (11) is what 424 coin flips
  at a 54% loss rate give (about 10): the pattern is variance, not a broken mechanism.

## 7. Hints that are not findings

* Asia-session trades average -0.174R (+-0.20, n=142), XAU Asia -0.42R (n=62); London +0.246R (+-0.24). A permutation test
  puts the chance that the best of six (symbol, session) slices is this good at **0.50**: noise. In the replay the random
  entries are also weaker in Asia, so it is not the signal.
* FX trades against the 24 h move average +0.264R, with the move -0.088R. The **random** entries show the same tilt
  (against 52.9%, with 46.1%): this window mean-reverted. It is the market, not skill.

## 8. What would change the answer

1. A signal that beats the random-entry benchmark above (`technical_geometry.py` and `regime_vs_random.py` are the gate),
   chronologically, on enough independent days; two weeks is not enough to find it or to rule one out for a rare strategy.
2. Risk set by stop distance and capped across the account (net USD exposure, daily loss), which removes the instability
   but not the lack of edge.
3. Regime-conditional strategies are plausible (the window rewarded mean reversion) but a regime filter fitted on this
   window would be fitted on one regime.

## 9. Not investigated

Broker commission and the real spread (spreads here are the replay's assumed constants, though full stops averaging
-0.99 to -1.01R on FX suggest the recorded results exclude them), swap, the opportunities that never became plans (97%: the
route outcomes expire after 23 h so the reasons are not recoverable), news-event conditioning, and anything before 29 Sep
(the production tables begin there).

## 10. Reproduce

```
export INV_DATA=<dir with inv_trades.csv, inv_env.csv, bars/>  INV_AEQ=<records.jsonl>  INV_REPO=<repo>
python3 algo-bot/tools/performance_investigation/mfe_stops.py            # section 4
python3 algo-bot/tools/performance_investigation/technical_geometry.py   # section 3 (realistic fills)
python3 algo-bot/tools/performance_investigation/regime_vs_random.py     # sections 3 and 7
python3 algo-bot/tools/performance_investigation/slices.py               # section 7
python3 algo-bot/tools/performance_investigation/replay_vs_production.py # section 5
python3 algo-bot/tools/performance_investigation/risk_path.py            # sections 4 and 6
```
