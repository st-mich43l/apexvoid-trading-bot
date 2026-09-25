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


def format_price(value: float, symbol: str, *, digits: int | None = None) -> str:
  """``digits`` lets a caller that already resolved its own instrument
  digit count (e.g. setup_card.card_price_digits, which callers/tests
  patch independently of this module's own digits_for import) pass it
  through instead of this function re-deriving it a second, unpatchable
  way.

  XAU always displays as a whole number here, overriding whatever digits
  was resolved to (production XAU config is price_digits: 2, used for
  broker/technical precision - this is a presentation-only override, per
  the owner's explicit preference, and never touches the broker order or
  the underlying technical price this function's caller keeps precise
  elsewhere). This is display rounding, not a claim that a narrow zone
  collapsing to one whole number is still a valid trade - a caller
  building a plan from a zone too narrow to survive this must decide not
  to advertise it, not rely on this function to hide that.
  """
  if str(symbol).upper() == "XAU":
    return f"{round(value):,d}"
  if digits is None:
    # setup_card.py's own card_price_digits() falls back to 2 on an
    # unknown symbol (live 2026-08-21 GBPUSD incident: a hard-coded XAU
    # digit count rendered every price as "1.36") - kept here too so a
    # caller that doesn't pass digits explicitly still gets that floor.
    try:
      digits = int(digits_for(symbol))
    except KeyError:
      digits = 2
  return f"{value:,.{digits}f}".rstrip("0").rstrip(".")


def format_r_multiple(value: float) -> str:
  """"1.0R" style - no leading "+" (matches the owner's own approved
  entry-card example) and always one decimal place, e.g. "1.0R"/"2.0R".
  A prior Auto-only formatter (setup_card._format_r_multiple) both added
  a "+" and stripped the trailing ".0" instead - two divergences from
  Manual's own R-multiple convention (app.signals.broadcast's local
  ``_rr`` helper), fixed here as the one shared formatter both should use.
  """
  return f"{value:.1f}R"


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


def format_entry_line(
  symbol: str, low: float, high: float | None, *, digits: int | None = None,
) -> str:
  """"Entry Zone: low - high" or "Entry Price: low" per instrument config.

  ``uses_entry_price_display`` already applies uniformly by instrument
  (XAU always zone, FX pairs single price per their configured entry_mode)
  - reusing it here is what makes this decision automatically consistent
  between Manual and Auto Algo instead of being decided twice.
  """
  end = low if high is None else high
  if uses_entry_price_display(symbol, low, end):
    return f"⚡️ Entry Price:  <b>{format_price(low, symbol, digits=digits)}</b>"
  return (
    f"⚡️ Entry Zone:  <b>{format_price(low, symbol, digits=digits)} - "
    f"{format_price(end, symbol, digits=digits)}</b>"
  )


def format_sl_line(
  symbol: str,
  sl: float,
  risk_reference: float,
  *,
  digits: int | None = None,
  pip_size: float | None = None,
) -> str:
  """"SL: price · risk N pips", measured from the CURRENT sl/reference -
  see format_target_line's own doc comment for why targets use a
  separately pinned original risk instead.
  """
  resolved_pip = pip_for(symbol) if pip_size is None else pip_size
  risk = abs(risk_reference - sl)
  risk_pips = round(risk / resolved_pip) if resolved_pip > 0 else 0
  return (
    f"🛡 SL:     <b>{format_price(sl, symbol, digits=digits)}</b>  ·  "
    f"risk <b>{risk_pips} pips</b>"
  )


def format_target_line(
  index: int,
  symbol: str,
  price: float,
  suffix: str | None,
  *,
  digits: int | None = None,
) -> str:
  """"TPn: price · suffix" - suffix (an R-multiple, a pip offset, or None)
  is the caller's own, since Manual and Auto Algo compute it from
  different authoritative sources (see module doc comment).
  """
  label = f"TP{index + 1}"
  price_text = format_price(price, symbol, digits=digits)
  if suffix:
    return f"💰 {label}:   <b>{price_text}</b>  ·  <b>{suffix}</b>"
  return f"💰 {label}:   <b>{price_text}</b>"
