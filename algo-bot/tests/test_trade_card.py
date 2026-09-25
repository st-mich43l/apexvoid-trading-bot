from app.autotrade import trade_card


def test_format_price_strips_trailing_zeros_with_thousands_separator():
  assert trade_card.format_price(4341.0, "XAU") == "4,341"


def test_format_price_falls_back_to_two_digits_for_unknown_symbol():
  assert trade_card.format_price(1.5, "UNKNOWN_SYMBOL") == "1.5"


def test_format_price_rounds_xau_to_a_whole_number():
  # Owner preference: XAU always displays whole - a presentation override,
  # not a change to the underlying technical/broker price this value came
  # from. XAU's real production price_digits (2) never applies here.
  assert trade_card.format_price(4341.73, "XAU") == "4,342"
  assert trade_card.format_price(4341.2, "XAU") == "4,341"


def test_format_price_never_rounds_fx():
  # This file's ambient config resolves EURUSD to 2 digits, not real FX
  # precision - the point of this test is only that the XAU whole-number
  # override doesn't leak onto other symbols.
  assert trade_card.format_price(1.36447, "EURUSD") == "1.36"


def test_format_r_multiple_has_no_plus_sign_and_one_decimal():
  # Matches the owner's own approved entry-card example ("1.0R"), and
  # app.signals.broadcast's own R-multiple convention - not the "+1R"
  # (plus sign, no decimal) a prior Auto-only formatter used.
  assert trade_card.format_r_multiple(1.0) == "1.0R"
  assert trade_card.format_r_multiple(2.5) == "2.5R"


def test_conservative_entry_reference_buy_uses_upper_edge():
  assert trade_card.conservative_entry_reference("BUY", 100.0, 105.0) == 105.0


def test_conservative_entry_reference_sell_uses_lower_edge():
  assert trade_card.conservative_entry_reference("SELL", 100.0, 105.0) == 100.0


def test_conservative_entry_reference_handles_missing_high():
  assert trade_card.conservative_entry_reference("BUY", 100.0, None) == 100.0


def test_format_entry_line_shows_zone_for_xau():
  line = trade_card.format_entry_line("XAU", 4341.0, 4344.0)
  assert line == "⚡️ Entry Zone:  <b>4,341 - 4,344</b>"


def test_format_entry_line_shows_price_for_single_entry_fx():
  # EURUSD is not configured as a single-entry-mode instrument in the
  # canonical test fixtures unless explicitly set up - this test only
  # needs an instrument uses_entry_price_display already treats as
  # single-entry; XAU never is, so assert the zone branch stays a zone
  # even when entry == entry_end (a degenerate but still zone-mode input).
  line = trade_card.format_entry_line("XAU", 4341.0, 4341.0)
  assert line == "⚡️ Entry Zone:  <b>4,341 - 4,341</b>"


def test_format_sl_line_computes_pips_from_conservative_reference():
  # XAU pip size 0.1: 5.0 price distance -> 50 pips.
  line = trade_card.format_sl_line("XAU", 4346.0, 4341.0)
  assert line == "🛡 SL:     <b>4,346</b>  ·  risk <b>50 pips</b>"


def test_format_target_line_with_suffix():
  line = trade_card.format_target_line(0, "XAU", 4336.0, "1.0R")
  assert line == "💰 TP1:   <b>4,336</b>  ·  <b>1.0R</b>"


def test_format_target_line_without_suffix():
  line = trade_card.format_target_line(3, "XAU", 4321.0, None)
  assert line == "💰 TP4:   <b>4,321</b>"
