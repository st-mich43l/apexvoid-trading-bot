# TradingView indicators

This directory contains chart-side analytical tools that accompany the ApexVoid
trading stack. They are reference and discretionary-analysis tools; they are not
loaded by the Go analysis engine and cannot place orders through the bot.

## ApexVoid Price Action

`apexvoid_price_action.pine` is the Pine Script v6 market-structure and POI
indicator. It provides:

- separate internal and external structure;
- close-confirmed BOS and CHoCH, with wick sweeps kept separate;
- order blocks (swing and internal, with Fresh / Tested / Mitigated / Invalidated
  states), fair-value-gap, supply and demand zone lifecycles;
- level-first BSL/SSL liquidity pools (confirmed swings, EQH/EQL bands, optional
  previous-day levels) with separate sweep (grab) and consumed states;
- qualified and actionable POIs with opposing-barrier context;
- completed Asian-session levels; and
- optional confirmed higher-timeframe context.

To install it, open TradingView's Pine Editor, paste the file, save it, and add
it to a chart. The default dashboard density is `Compact`. Higher-timeframe
context is disabled by default and does not alter chart-timeframe signals.

Version 2.1 definitions, the root-cause analysis of the missing order blocks and the
known limitations are in `docs/ob-liquidity-v2.1.md`. `reference/` holds a Python model of
the order-block and liquidity logic with regression checks on real XAUUSD closed bars
(`python tradingview/reference/test_pa_model.py`); it validates the algorithm only and
does not compile or render the Pine file. The Debug view for order blocks is the input
`Debug: OB candidate log and rejection reasons`.

TradingView is the authoritative compiler and runtime. After changing the
script, compile it there and visually check structure transitions, zone
lifecycles, object limits, and alert behavior on closed bars.
