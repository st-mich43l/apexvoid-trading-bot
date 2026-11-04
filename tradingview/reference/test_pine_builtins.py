"""Guard against calling Pine built-ins that do not exist (for example `line.get_color`).

The syntax-only parser used in CI-less review cannot catch these; TradingView reports them as
compile errors. Every `namespace.function(` call in the indicator must be in VETTED, which holds
the calls that compiled in TradingView plus the few added since and confirmed in the Pine
reference. A new built-in fails this check until someone confirms it exists and adds it here.

Run: python tradingview/reference/test_pine_builtins.py
"""
import os
import re

PINE = os.path.join(os.path.dirname(__file__), "..", "apexvoid_price_action.pine")

VETTED = {
    "array.clear",
    "array.get",
    "array.median",
    "array.new_bool",
    "array.new_box",
    "array.new_color",
    "array.new_float",
    "array.new_int",
    "array.new_label",
    "array.new_line",
    "array.new_string",
    "array.pop",
    "array.push",
    "array.remove",
    "array.set",
    "array.shift",
    "array.size",
    "array.sort_indices",
    "box.delete",
    "box.get_left",
    "box.new",
    "box.set_bgcolor",
    "box.set_border_color",
    "box.set_border_style",
    "box.set_border_width",
    "box.set_bottom",
    "box.set_left",
    "box.set_lefttop",
    "box.set_right",
    "box.set_rightbottom",
    "box.set_text",
    "box.set_text_color",
    "box.set_text_halign",
    "box.set_text_size",
    "box.set_top",
    "color.new",
    "input.bool",
    "input.color",
    "input.float",
    "input.int",
    "input.session",
    "input.string",
    "input.timeframe",
    "label.delete",
    "label.get_text",
    "label.get_x",
    "label.get_y",
    "label.new",
    "label.set_color",
    "label.set_size",
    "label.set_text",
    "label.set_textcolor",
    "label.set_tooltip",
    "label.set_x",
    "label.set_xy",
    "label.set_y",
    "line.delete",
    "line.get_x2",
    "line.new",
    "line.set_color",
    "line.set_style",
    "line.set_x1",
    "line.set_x2",
    "line.set_xy1",
    "line.set_xy2",
    "math.abs",
    "math.ceil",
    "math.floor",
    "math.max",
    "math.min",
    "math.round",
    "request.security",
    "str.contains",
    "str.format_time",
    "str.length",
    "str.replace_all",
    "str.startswith",
    "str.substring",
    "str.tostring",
    "ta.atr",
    "ta.highest",
    "ta.lowest",
    "ta.pivothigh",
    "ta.pivotlow",
    "table.cell",
    "table.clear",
    "table.merge_cells",
    "table.new",
    "timeframe.in_seconds",
}


def calls(src):
  code = "\n".join(re.sub(r"//.*", "", line) for line in src.splitlines())
  code = re.sub(r'"(?:[^"\\]|\\.)*"', '""', code)
  return set(re.findall(r"\b((?:[a-z]+)\.(?:[a-z_0-9]+))\(", code))


def test_only_vetted_builtins_are_called():
  unknown = sorted(calls(open(PINE, encoding="utf-8").read()) - VETTED)
  assert not unknown, "unvetted Pine built-in calls (confirm they exist, then add to VETTED): %s" % unknown


# TradingView rejects a script whose main body is too long (CE10295). Measured on the committed
# versions, counting non-comment lines outside function bodies: v2.7.1 = 1645 lines (compiled),
# v2.8 = 1850 lines (rejected). Keep a margin below the last version known to compile.
MAIN_BODY_LINE_LIMIT = 1600


def main_body_lines(src):
  inside, count = False, 0
  for line in src.splitlines():
    if not line.strip() or line.lstrip().startswith("//"):
      continue
    if re.match(r"^f_\w+\(.*\) =>", line):
      inside = True
      continue
    if inside and line.startswith(" "):
      continue
    inside = False
    count += 1
  return count


def test_main_body_stays_within_the_size_limit():
  n = main_body_lines(open(PINE, encoding="utf-8").read())
  assert n <= MAIN_BODY_LINE_LIMIT, "main script body has %d lines (limit %d); move read-only/draw code into functions" % (n, MAIN_BODY_LINE_LIMIT)


def test_known_non_existent_builtins_are_absent():
  src = open(PINE, encoding="utf-8").read()
  for bad in ("line.get_color", "label.get_textcolor", "box.get_color"):
    assert bad not in src, bad


def test_no_reserved_word_is_declared_as_a_name():
  # CE10150: "text" cannot be used as a variable, parameter or function name (TradingView rejected `f_recordSweep(..., string text)` in v2.13).
  import re
  src = open(PINE, encoding="utf-8").read()
  code = "\n".join(l.split("//")[0] for l in src.splitlines())
  decl = re.compile(r"\b(?:int|float|bool|string|color|line|box|label)(?:\[\])?\s+(text)\b\s*(?=[,)=])")
  hits = [m.group(0) for m in decl.finditer(code)]
  assert not hits, hits


# TradingView rejects a script above 100256 compiled tokens (CE10117). v2.13 compiled to 107040 tokens at 36581 of the tokens counted below, i.e. about 2.93
# compiled tokens each; the budget keeps a margin of about 1.5% under the limit. This is an estimate: only the TradingView compiler gives the real number.
SOURCE_TOKEN_BUDGET = 33700


def source_tokens(src):
  import re
  code = "\n".join(l.split("//")[0] for l in src.splitlines())
  code = re.sub(r'"(?:[^"\\]|\\.)*"', '"s"', code)
  return len(re.findall(r"[A-Za-z_][A-Za-z_0-9]*|\d+\.?\d*|==|!=|<=|>=|:=|=>|\?|:|[-+*/%<>=(),\[\].]", code))


def test_no_request_security_of_the_charts_own_structure_series():
  # v2.14 requested `[bias[1], activeProtected[1]]` on two other frames and TradingView counted 104,204 compiled tokens at 33,514 source tokens (limit 100,256):
  # a request.security of a global series compiles its whole dependency chain once per call. Use a small pure function instead.
  src = open(PINE, encoding="utf-8").read()
  code = "\n".join(l.split("//")[0] for l in src.splitlines())
  assert not re.search(r"request\.security\([^\n]*\[[^\]\n]*(bias|activeProtected|protectedHigh|protectedLow)", code), "request.security of the structure series"


def test_the_indicator_call_does_not_declare_no_scale():
  # `scale = scale.none` put the drawings on their own hidden scale: after the price axis was dragged or zoomed they kept their pixels and drifted off the candles
  src = open(PINE, encoding="utf-8").read()
  call = [l for l in src.splitlines() if l.startswith("indicator(")]
  assert len(call) == 1 and "scale.none" not in call[0] and "scale =" not in call[0], call


def _code():
  src = open(PINE, encoding="utf-8").read()
  return "\n".join(l.split("//")[0] for l in src.splitlines())


def _args(code, name):
  """Top-level argument count of every `name(...)` call (strings and nested brackets respected)."""
  out = []
  for m in re.finditer(r"\b%s\(" % re.escape(name), code):
    i, depth, n, quote = m.end(), 1, 1, False
    while depth:
      c = code[i]
      if c == '"' and code[i - 1] != "\\":
        quote = not quote
      elif not quote:
        depth += (c in "([") - (c in ")]")
        n += 1 if c == "," and depth == 1 else 0
      i += 1
    out.append(n)
  return out


def test_annotations_use_the_gutter_and_three_tiers():
  # v2.15: objects end `gutter` bars right of the last candle (captions are right-aligned inside it) and text sizes come from three tiers
  code = _code()
  assert "bar_index + 5" not in code and "bar_index + 7" not in code, "a drawing still ends at a hard-coded offset"
  for old in ("majorTextSize", "structureTextSize", "zoneTextSize", "contextTextSize"):
    assert old not in code, old
  assert len(re.findall(r"\btier[123]Size\b", code)) >= 12


def test_label_merge_distance_is_a_thirty_sixth_of_the_candle_range():
  # v2.15.1: larger text needs more room, so labels merge at range / 36 (was / 48)
  code = _code()
  assert "/ 48.0" not in code and code.count("/ 36.0") == 2  # the zone label gap, and the right column gap (the column cluster merge and the key-level caption yield share it)


def test_prices_print_two_decimals_for_gold_like_symbols_only():
  code = _code()
  assert 'syminfo.mintick < 0.01 and math.abs(x) >= 100 ? str.tostring(x, "0.00") : str.tostring(x, format.mintick)' in code


def test_every_right_column_label_carries_a_category_and_a_tier():
  code = _code()
  counts = [n for n in _args(code, "f_regLabel")]
  assert len(counts) >= 10 and set(counts) == {7}, counts  # the definition has 7 parameters too


def test_text_formatting_is_only_ever_a_constant():
  # `text_formatting` needs a const text_format (community note, not confirmed in the official reference): a ternary on a series would not compile
  code = _code()
  uses = re.findall(r"text_formatting\s*=\s*([^,)]*)", code)
  assert all(re.fullmatch(r"text\.format_[a-z]+", u.strip()) for u in uses), uses
  assert "set_text_formatting" not in code


def test_key_level_loops_are_guarded_against_empty_arrays():
  # a Pine `for i = 0 to n - 1` with n = 0 runs twice (0, -1): every loop over an array that can be empty needs its guard
  code = _code()
  body = code[code.index("f_keyLevels() =>"):code.index("f_keyLevels()", code.index("f_keyLevels() =>") + 20)]
  assert "keyRing" not in code and "showBrokenKeys" not in code and "array.median" in body
  for guard in ("w >= 5", "ns > 0", "array.size(pr) > 0", "nl > 0"):
    assert guard in body, guard
  # every `for ... to nl - 1` / `to ns - 1` sits under its guard (nl = 0 would run it twice): the loops over the levels are all inside `if nl > 0`
  inside = body[body.index("        if nl > 0\n"):]
  assert inside.count("to nl - 1") == 2 and body.count("to nl - 1") == 2 and "for j = nl - 1 to 0" in inside


def test_no_block_opener_is_left_empty():
  # CE10013 "Mismatched input ... expecting end of line without line continuation": an `if` / `else` / `for` / `while` / function header whose body was deleted
  # (v2.16.1 removed an assignment and left `if choch` with nothing under it). Every block header needs a more indented line below it.
  lines = [l.rstrip() for l in open(PINE, encoding="utf-8").read().splitlines()]
  code = [(i + 1, l) for i, l in enumerate(lines) if l.strip() and not l.strip().startswith("//")]
  bad = []
  for (n, line), (_, nxt) in zip(code, code[1:]):
    head = re.match(r"\s*(if|else if|else|for|while)\b", line) or line.rstrip().endswith("=>")
    code_part = line.split("//")[0].rstrip()
    if head and (code_part.endswith("=>") or re.match(r"\s*(if|else if|else|for|while)\b", line)) and not code_part.endswith(("(", ",", "and", "or")):
      if len(nxt) - len(nxt.lstrip()) <= len(line) - len(line.lstrip()):
        bad.append(n)
  assert not bad, "empty block body after line(s) %s" % bad[:10]


def test_script_stays_under_the_compiled_token_budget():
  n = source_tokens(open(PINE, encoding="utf-8").read())
  assert n <= SOURCE_TOKEN_BUDGET, "%d source tokens (budget %d, about %d compiled of 100256)" % (n, SOURCE_TOKEN_BUDGET, n * 2.93)


if __name__ == "__main__":
  test_script_stays_under_the_compiled_token_budget()
  test_no_request_security_of_the_charts_own_structure_series()
  test_the_indicator_call_does_not_declare_no_scale()
  test_annotations_use_the_gutter_and_three_tiers()
  test_no_block_opener_is_left_empty()
  test_key_level_loops_are_guarded_against_empty_arrays()
  test_label_merge_distance_is_a_thirty_sixth_of_the_candle_range()
  test_prices_print_two_decimals_for_gold_like_symbols_only()
  test_every_right_column_label_carries_a_category_and_a_tier()
  test_text_formatting_is_only_ever_a_constant()
  test_only_vetted_builtins_are_called()
  test_no_reserved_word_is_declared_as_a_name()
  test_main_body_stays_within_the_size_limit()
  test_known_non_existent_builtins_are_absent()
  print("ok pine built-ins")
