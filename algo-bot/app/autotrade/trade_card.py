"""Shared entry-card line rendering for Manual and Auto Algo.

Phase S12 (Unified Manual & Auto Algo Trading Experience) asks for one
canonical entry-card design instead of the independently-maintained
formatters Manual (``app.signals.broadcast``) and Auto Algo
(``app.autotrade.setup_card``) each grew on their own. This module owns the
character-for-character presentation of the shared, price-bearing lines
(entry zone/price, stop + risk, target/R lines) so both callers produce
identical output for identical inputs - icon, spacing, and price formatting
live here exactly once.

What is deliberately NOT shared: how a caller decides what to show. Manual
computes its own R-multiples from live entry/stop/target prices
(``app.signals.pips_format``); Auto Algo instead reads the instrument's
configured target R-multiple ladder (``setup_card._configured_target_r_multiples``)
specifically because deriving R from Auto's own card prices has previously
produced nonsense values (live 2026-09-07: a GBPJPY SELL showed "+10R"/
"+15.6R" for what was actually a uniform 1R/2R trade) - see that function's
own doc comment. Each caller computes its own target display suffix (an
R-multiple, a pip offset, or nothing) and passes the finished string in;
this module only lays it out, never re-derives it.

Also deliberately out of scope here: Auto Algo's root-card headline/status-
slot mechanism (``setup_card.forming_card_headline``,
``apply_forming_card_status``, ``forming_card_matches_strategy``). That
machinery parses specific line positions/patterns in the card's own text to
edit it in place as a setup progresses through its lifecycle - unifying it
is Phase S12's separate "shared setup-lifecycle manager" step, not this
one. This module's functions render one line each and never assume
anything about which line position they'll land on.
"""

from app.core.symbols import digits_for, pip_for
from app.signals.fx_manual_algo import uses_entry_price_display


def format_price(value: float, symbol: str) -> str:
  # setup_card.py's own card_price_digits() falls back to 2 on an unknown
  # symbol (live 2026-08-21 GBPUSD incident: a hard-coded XAU digit count
  # rendered every price as "1.36") - kept here so both callers get the
  # same defensive floor instead of Auto Algo losing it by switching to
  # this shared formatter.
  try:
    digits = int(digits_for(symbol))
  except KeyError:
    digits = 2
  return f"{value:,.{digits}f}".rstrip("0").rstrip(".")


def conservative_entry_reference(
  direction: str, low: float, high: float | None,
) -> float:
  """The edge used as the risk/reward reference - the same convention
  ``app.signals.pips_format.rr_entry`` already established for Manual:
  BUY uses the upper (harder-to-reach) edge, SELL uses the lower one, so
  risk is never understated by measuring from the easy side of the zone.
  """
  edge_high = low if high is None else high
  return low if str(direction).upper() == "SELL" else edge_high


def format_entry_line(symbol: str, low: float, high: float | None) -> str:
  """"Entry Zone: low - high" or "Entry Price: low" per instrument config.

  ``uses_entry_price_display`` already applies uniformly by instrument
  (XAU always zone, FX pairs single price per their configured entry_mode)
  - reusing it here is what makes this decision automatically consistent
  between Manual and Auto Algo instead of being decided twice.
  """
  end = low if high is None else high
  if uses_entry_price_display(symbol, low, end):
    return f"⚡️ Entry Price:  <b>{format_price(low, symbol)}</b>"
  return (
    f"⚡️ Entry Zone:  <b>{format_price(low, symbol)} - "
    f"{format_price(end, symbol)}</b>"
  )


def format_sl_line(symbol: str, sl: float, risk_reference: float) -> str:
  """"SL: price · risk N pips", measured from the CURRENT sl/reference -
  see format_target_line's own doc comment for why targets use a
  separately pinned original risk instead.
  """
  risk = abs(risk_reference - sl)
  risk_pips = round(risk / pip_for(symbol)) if pip_for(symbol) > 0 else 0
  return (
    f"🛡 SL:     <b>{format_price(sl, symbol)}</b>  ·  "
    f"risk <b>{risk_pips} pips</b>"
  )


def format_target_line(index: int, symbol: str, price: float, suffix: str | None) -> str:
  """"TPn: price · suffix" - suffix (an R-multiple, a pip offset, or None)
  is the caller's own, since Manual and Auto Algo compute it from
  different authoritative sources (see module doc comment).
  """
  label = f"TP{index + 1}"
  price_text = format_price(price, symbol)
  if suffix:
    return f"💰 {label}:   <b>{price_text}</b>  ·  <b>{suffix}</b>"
  return f"💰 {label}:   <b>{price_text}</b>"
