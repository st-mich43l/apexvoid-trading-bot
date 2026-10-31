# TradingView indicators

This directory contains chart-side analytical tools that accompany the ApexVoid
trading stack. They are reference and discretionary-analysis tools; they are not
loaded by the Go analysis engine and cannot place orders through the bot.

## ApexVoid Price Action

`apexvoid_price_action.pine` is the Pine Script v6 market-structure and POI
indicator. It provides:

- separate internal and external structure;
- close-confirmed BOS and CHoCH, with wick sweeps kept separate;
- order-block, fair-value-gap, supply and demand zone lifecycles;
- qualified and actionable POIs with opposing-barrier context;
- completed Asian-session levels; and
- optional confirmed higher-timeframe context.

To install it, open TradingView's Pine Editor, paste the file, save it, and add
it to a chart. The default dashboard density is `Compact`. Higher-timeframe
context is disabled by default and does not alter chart-timeframe signals.

TradingView is the authoritative compiler and runtime. After changing the
script, compile it there and visually check structure transitions, zone
lifecycles, object limits, and alert behavior on closed bars.
