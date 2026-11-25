"""Offline reference model of the ApexVoid Price Action v2.27 right-edge annotation rail (`f_rail` and helpers).

It mirrors the Pine layout step by step (grouping, budget, weighted order-preserving placement, hiding, rows, connectors) so the
rules can be tested deterministically without TradingView. It does not render anything and says nothing about pixels: row height
is the same estimate the Pine code uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LIQUIDITY = "liquidity"  # the neutral liquidity colour (pools, iBSL/iSSL, EQ)
MAX_ROWS = 4
SPAN_ROWS = 2.0
HIDE_ROWS = 3.0
LINK_ROWS = 0.5
GAP_ROWS = 0.15
MAX_LINKS = 8


@dataclass
class Ann:
  y: float
  name: str
  badge: str = ""
  px: str = ""
  tone: str = LIQUIDITY
  priority: int = 50
  cat: int = 0
  tier: int = 2
  tip: str = ""


@dataclass
class Item:
  members: list[int]
  target: float
  rows: int
  tier: int
  lead: int
  score: int
  hideable: bool
  alive: bool = True
  y: float | None = None
  body: str = ""
  hidden: list[str] = field(default_factory=list)
  link: bool = False


@dataclass
class Rail:
  items: list[Item]
  row_h: float

  @property
  def shown(self) -> list[Item]:
    return [it for it in self.items if it.alive]


def neutral(a: Ann) -> bool:
  return a.cat in (4, 5) or a.tone == LIQUIDITY


def same_family(a: Ann, b: Ann) -> bool:
  na, nb = neutral(a), neutral(b)
  return (na and nb) or (not na and not nb and a.tone == b.tone)


def score(a: Ann) -> int:
  return (4 - a.tier) * 1000 + a.priority


def row_text(a: Ann) -> str:
  left = a.name + (" " + a.badge if a.badge else "")
  if a.px:
    left += " "
    while len(left) < 9:
      left += " "
    left += a.px
  return left


def solve(tgt: list[float], h: list[float], w: list[float], row_h: float) -> list[float]:
  """Pool-adjacent-violators on z = target + cumulative separation (z must not increase top to bottom)."""
  n = len(tgt)
  off = [0.0] * n
  for k in range(1, n):
    off[k] = off[k - 1] + (h[k - 1] + h[k]) / 2.0 + GAP_ROWS * row_h
  b_sum: list[float] = []
  b_w: list[float] = []
  b_end: list[int] = []
  for k in range(n):
    b_sum.append((tgt[k] + off[k]) * w[k])
    b_w.append(w[k])
    b_end.append(k)
    while len(b_w) >= 2 and b_sum[-2] / b_w[-2] < b_sum[-1] / b_w[-1]:
      s, ww, e = b_sum.pop(), b_w.pop(), b_end.pop()
      b_sum[-1] += s
      b_w[-1] += ww
      b_end[-1] = e
  ys = [0.0] * n
  start = 0
  for b in range(len(b_w)):
    z = b_sum[b] / b_w[b]
    for k in range(start, b_end[b] + 1):
      ys[k] = z - off[k]
    start = b_end[b] + 1
  return ys


def layout(anns: list[Ann], row_h: float, compact: bool = False) -> Rail:
  max_items = 14 if compact else 20
  order = sorted(range(len(anns)), key=lambda i: -anns[i].y)  # Pine: array.sort_indices(order.descending)
  groups: list[list[int]] = []
  item_top = prev_y = None
  for r, idx in enumerate(order):
    y = anns[idx].y
    join = (
      bool(groups) and prev_y - y < row_h and item_top - y <= SPAN_ROWS * row_h
      and len(groups[-1]) < MAX_ROWS and same_family(anns[order[r - 1]], anns[idx])
    )
    if join:
      groups[-1].append(idx)
    else:
      groups.append([idx])
      item_top = y
    prev_y = y
  items: list[Item] = []
  for g in groups:
    tier = min(anns[i].tier for i in g)
    lead = g[0]
    lead_score = -1
    for i in g:
      if score(anns[i]) > lead_score:
        lead_score, lead = score(anns[i]), i
    priced = sum(1 for i in g if anns[i].px)
    rows = priced + (0 if anns[lead].px else 1) if compact else len(g)
    items.append(Item(g, (anns[g[0]].y + anns[g[-1]].y) / 2.0, rows, tier, lead, lead_score, tier == 3 and priced == 0))
  alive = len(items)
  while alive > max_items:
    victim = None
    for it in items:
      if it.alive and it.hideable and (victim is None or it.score < victim.score):
        victim = it
    if victim is None:
      break
    victim.alive = False
    alive -= 1
  for solve_pass in (0, 1):
    act = [it for it in items if it.alive]
    w = [100.0 if it.tier == 1 else 10.0 if it.tier == 2 else 1.0 for it in act]
    ys = solve([it.target for it in act], [row_h * it.rows for it in act], w, row_h)
    again = False
    for it, y in zip(act, ys):
      it.y = y
      if solve_pass == 0 and it.hideable and abs(y - it.target) > HIDE_ROWS * row_h:
        it.alive = False
        again = True
    if not again:
      break
  for it in items:
    if not it.alive:
      host = None
      for j in items:
        if j.alive and (host is None or abs(j.target - it.target) < abs(host.target - it.target)):
          host = j
      if host is not None:
        host.hidden.extend(f"{anns[i].name} {anns[i].y:.2f}" for i in it.members)
  links = 0
  for it in items:
    if not it.alive:
      continue
    lines, folded = [], 0
    for i in it.members:
      if not compact or i == it.lead or anns[i].px:
        lines.append(row_text(anns[i]))
      else:
        folded += 1
    it.body = "\n".join(lines) + (f" +{folded}" if folded else "")
    if abs(it.y - it.target) > LINK_ROWS * row_h and links < MAX_LINKS:
      it.link = True
      links += 1
  return Rail(items, row_h)


def text_color(anns: list[Ann], it: Item) -> str:
  lead = anns[it.lead]
  if neutral(lead):
    return "#CBD5E1" if it.tier <= 2 else "#94A3B8"
  return lead.tone
