# v2.8 — trade plan (long / short position boxes with paper results)

The indicator now turns its own structure into a rule-based trade plan: a price-action entry at
a qualified POI, a stop beyond the zone and a target at the nearest key level, drawn as
TradingView-style long/short boxes. **It sends no orders and is not a profitable system.** The
rules are implemented as asked; the paper results below do not show an edge.

## 0. Status

| Item | Status |
| --- | --- |
| Rules reproduced bar by bar in Python (`reference/trade_model.py`) and checked on real XAUUSD bars (`reference/test_trade_model.py`, 7 checks pass) | Done |
| Pine syntax | Parsed without errors by a third-party Pine parser (v5 grammar). Syntax only. |
| Built-in call guard (`reference/test_pine_builtins.py`) | Passes: no unvetted built-in is called, and the main script body is within the size limit (see below) |
| First TradingView compile | Failed with `CE10295: the main body of the script is too long`; fixed in v2.8.1 by moving the dashboard, the zone drawing pass and the pool drawing pass into functions (main body 1850 → 1455 lines; v2.7.1 had 1645 and compiled). |
| TradingView compile and look on a chart | **Not done here.** Re-paste the file and compile. |
| Evidence that the rules make money | **None.** See §5. |

## 1. Research (what the sources say, and how far to trust it)

Search results only (promotional TradingView script pages; not read in full):
- Confirmation before entry at a zone: an engulfing candle, a pin bar or rejection wick, a lower-timeframe
  BOS/CHoCH or an FVG rejection ([Supply/Demand Zones + Engulfment-based Execution](https://www.luxalgo.com/library/indicator/KszHWOSg-supply-demand-zones-engulfment-based-execution/)).
- Stop just beyond the zone or the signal candle with a buffer; targets either a fixed R multiple or
  the next structure; 1:2 or better is a common preference, not a consensus. These setups are said
  to suit ranges better than strong trends.
- Position-tool look: shade entry→target as the profit zone and stop→entry as the loss zone, show
  the reward : risk, derive direction from where target and stop sit
  ([risk/reward boxes](https://www.tradingview.com/script/tQD2Rgkm-Pinescript-Risk-Reward-boxes-Custom-Time-Elapsed-Markers),
  [position tool library](https://it.tradingview.com/script/X59JtSp6-riskRewardPositionTool/)).

Your direction: *price action entries, targets on key levels, boxes for the last 5 trades, paper-tracked results.*

## 2. Rules (all on closed bars)

1. **Direction** follows the external bias: long when bullish, short when bearish.
2. **Zone**: an active zone on that side that passes the **same qualification as the chart's POIs**
   (a shared function, so a trade never starts from an unmarked zone), first touched within
   `Trigger window` bars (default 2), not more than 0.75 ATR from its edge, and, by default, longs
   only from the discount half and shorts from the premium half of the confirmed range. Zones can
   be restricted to order blocks and/or supply-demand. A zone that produced a trade is spent.
3. **Trigger**: the closed bar is a *rejection wick* (body in the trade direction, wick at least
   40 % of the range) or an *engulfing* candle in the trade direction.
4. **Entry** at that close. **Stop** beyond the zone's far edge plus 0.15 ATR; skipped if the stop
   is under 0.4 ATR or over 3 ATR away.
5. **Target = a technical level.** The take-profit is the **next key level ahead** of the entry,
   placed 0.05 ATR before it so a touch fills it, and the trade is taken only if that level offers at
   least 1.5 R (and lies within 25 ATR). If the next level is too close there is **no trade**; the
   target never jumps over a nearer level. Key levels: active BSL/SSL pools (including EQH/EQL and
   previous-day levels), the confirmed swing high/low and the previous one, the Asian high/low,
   equilibrium, and the near edge of opposing supply/demand/order-block zones. The target is named in
   the box caption (`→ Swing high`). The alternative setting `Nearest level with minimum R:R` takes
   the first level that is far enough, which can sit behind a nearer one.
6. **One trade at a time.** On a bar that touches both stop and target the stop counts (conservative);
   a stop that gaps is filled at the open. A trade still open after 96 bars closes at that close.
   An optional round-turn cost is deducted from every result.

## 3. Chart

For each trade: a target box (entry→target, teal), a stop box (stop→entry, rose), a thin entry
line and a caption `LONG Demand · DBR → Swing high · 1.8R`. The boxes extend while the trade is open
and stop at the exit candle, fade, and get a result label (`+1.8R`, `−1.0R`, `+0.3R time`). Only the
**last 5 trades** are drawn (`Trades drawn on the chart`); statistics keep counting all of them. An open
trade also puts `ENTRY`, `TP` and `SL` in the right-edge label column.

Panel: a `Trades` row (`LONG open · 1.8R → Swing high`, or `11 · 27% · −1.6R`). Its tooltip has
closed trades, target/stop/timeout counts, total R, profit factor, max drawdown, skipped counts and
the last 8 trades. Alerts: `Trade Plan Long`, `Trade Plan Short`, `Trade Plan Target Hit`, `Trade Plan Stop Hit`.

Settings are in the new group `08 Trade Plan`.

## 4. Reference-model image

`docs/img/xauusd-m15-trade-plan.png` shows the reference model's last 5 trades on XAUUSD M15, one
panel per trade (target box teal, stop box rose, entry line white, the technical level dotted). It
uses the `Nearest level with minimum R:R` setting because the default technical mode produced only
3 trades on this capture. It is a matplotlib render, not a TradingView screenshot. Regenerate with
`python tradingview/reference/render_trades.py`.

## 5. Paper results on the available data (descriptive only)

Default settings, no costs, XAUUSD closed bars, seven capture/timeframe combinations (the two
captures overlap in time, so rows are not independent). `Skipped` counts trigger bars with no
acceptable key level.

**Next key level (default)**

| Capture | TF | Closed trades | Win rate | Total R | Skipped (no level / bad stop) |
| --- | --- | --- | --- | --- | --- |
| 2026-11-02 | M5 | 1 | 0 % | −1.0 | 7 / 0 |
| 2026-11-02 | M15 | 3 | 33 % | +0.1 | 10 / 0 |
| 2026-11-02 | H1 | 2 | 50 % | +1.1 | 2 / 1 |
| 2026-11-02 | H4 | 1 | 0 % | −1.0 | 1 / 0 |
| 2026-10-29 | M5 | 1 | 0 % | −1.0 | 7 / 0 |
| 2026-10-29 | M15 | 3 | 33 % | +0.1 | 5 / 0 |
| 2026-10-29 | H1 | 1 | 100 % | +2.1 | 0 / 1 |

Pooled 12 trades, 33 % winners, average +0.03 R. The technical level is usually closer than
1.5 R, so most triggers are skipped.

**Nearest level with minimum R:R**

Pooled 40 trades, 28 % winners, average −0.21 R (M5 2026-10-29: 8 trades, 12 % winners, −5.4 R).

With a 1.5 R minimum the break-even win rate is 40 %. Both samples are far too small and too
correlated to confirm or reject an edge, and the rules were not tuned on them. Earlier work on this
repository's Go engine also found no supported edge in zone-based setups. Treat the plan as a way to
**collect evidence** (the in-chart tracker keeps counting on your own history), not as a signal to
trade live. Lowering the minimum R:R takes more trades from the same levels; that is a choice to
make on more data, not on this sample.

## 6. Known limits

- No evidence of positive expectancy (§5); in-sample only; no costs unless you set one.
- Entries are at the trigger bar's close; real fills, spread and slippage are not modelled.
- Results use the bars loaded in TradingView; a different history length changes them.
- The reference model mirrors the Pine rules but is a port, and an unverified Pine file can still differ.
- Not compiled or viewed in TradingView.
