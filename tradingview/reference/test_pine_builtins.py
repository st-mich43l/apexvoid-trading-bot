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
    "box.new",
    "box.set_bgcolor",
    "box.set_border_color",
    "box.set_border_style",
    "box.set_border_width",
    "box.set_left",
    "box.set_lefttop",
    "box.set_right",
    "box.set_rightbottom",
    "box.set_text",
    "box.set_text_color",
    "box.set_text_halign",
    "box.set_text_size",
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
    "math.max",
    "math.min",
    "math.round",
    "request.security",
    "str.contains",
    "str.format_time",
    "str.length",
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


if __name__ == "__main__":
  test_only_vetted_builtins_are_called()
  test_main_body_stays_within_the_size_limit()
  test_known_non_existent_builtins_are_absent()
  print("ok pine built-ins")
