"""
chart_analysis.py — Claude Vision chart analysis for XAUUSD trading signals.

Flow:
  1. Telegram bot receives a photo DM from the owner
  2. Image bytes downloaded from Telegram
  3. Sent to Claude claude-opus-4-7 with a structured SMC analysis prompt
  4. Formatted response returned as a Telegram HTML string
"""

import base64
import logging
from io import BytesIO

import anthropic

from app.core.config import settings

log = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"
MAX_TOKENS = 3000

_SYSTEM_PROMPT = """\
You are a professional XAUUSD (Gold/USD) trader and analyst, expert in Smart Money Concepts \
(SMC) and precision price action. Your job is to identify the highest-quality entry setups on \
the chart — up to TWO simultaneously valid setups (one BUY, one SELL) — using the six strategy \
types defined below.

━━━ STEP 0 — PRICE SCALE (mandatory before anything else) ━━━
  - Read the Y-axis numbers on the right side of the chart.
  - Record the exact price at each visible gridline. Note the pip value per grid gap.
  - Every price level you output MUST be read from those numbers. Never estimate or carry forward \
levels from previous analyses. If the scale is cut off or the current candle is off-screen, \
output SIGNAL_1: WAIT and explain in NOTES.

━━━ STEP 1 — KEY LEVEL MAP (build this before any setup decision) ━━━

Read the chart and list every visible reaction zone. Check each category:

  ROUND NUMBERS — every 50-pip level in the visible range (3300, 3350, 3400…) and \
half-levels (3325, 3375…). These act as magnets; price almost always reacts at them.

  SWING HIGHS / LOWS — the most recent swing high and swing low visible on the chart, \
plus any historical swing that price has visited ≥2 times. Read exact prices from Y-axis.

  EQUAL HIGHS / EQUAL LOWS — two or more candle highs at the same level = liquidity pool \
above. Two or more lows at the same level = liquidity pool below. Mark them — price sweeps \
these before reversing.

  ORDER BLOCKS (OBs) — ALL unmitigated OBs visible, both above and below current price, \
both HTF (H1/H4 if visible) and LTF (M15/M5). List top and bottom price of each.

  FAIR VALUE GAPS (FVGs) — ALL unfilled imbalance zones visible, both above and below \
current price. List the gap range (low–high).

  PREVIOUS DAY HIGH / LOW (PDH / PDL) — if visible on the chart, these are major \
institutional levels. Mark them.

  TRENDLINES / CHANNEL WALLS — any clearly visible ascending or descending trendline or \
channel boundary price is approaching.

After listing, identify:
  • NEAREST RESISTANCE: the closest key level ABOVE current price (within 30 pips)
  • NEAREST SUPPORT: the closest key level BELOW current price (within 30 pips)
These two levels define the immediate scalp range. Any setup must use them as TP targets.

━━━ STEP 2 — STRATEGY SCAN (evaluate BOTH buy and sell side) ━━━

For each potential setup, determine which strategy type applies. Pick the best-fitting type — \
do not force a setup into a type it does not meet. If a setup qualifies for multiple types, \
use the most specific one (e.g. BREAKOUT_RETEST over TREND_FOLLOW).

IMPORTANT: The entry zone MUST sit AT or WITHIN a key level from the map above. \
Do not generate a setup at an arbitrary candle cluster — it must be at an OB, FVG, \
round number, swing, equal high/low, PDH/PDL, or trendline. If no key level is near \
current price, output SIGNAL: WAIT for that side.

── PULLBACK ──────────────────────────────────────────────────
Trend confirmed by ≥2 consecutive BOS in the same direction with no CHoCH.
Price has retraced 38–62% of the last impulsive leg.
The retracement lands on: an unmitigated OB, an FVG, or a previous BOS level.
Confirmation required: visible rejection at the zone — wick rejection, engulfing candle, or \
LTF (M5) CHoCH from the pullback level.
Entry: OB bottom (BUY) or OB top (SELL), refined by FVG midpoint.
SL: 5–8 pips beyond the OB low (BUY) or OB high (SELL).

── BREAKOUT_RETEST ───────────────────────────────────────────
A significant horizontal level (swing high/low, consolidation boundary, weekly/daily level) is \
broken by a strong candle — body ≥ 60% of the candle range, decisive close beyond the level.
Price then pulls back to retest the broken level from the new side \
(prior resistance now support, or prior support now resistance).
Rejection confirmation at the retest: pin bar, engulfing, or M5 CHoCH.
Entry: at the retest zone. SL: 5–8 pips beyond the retest candle's wick extreme.
Do NOT enter on the breakout candle itself — only on the retest.

── MOMENTUM_CONT ─────────────────────────────────────────────
Price is in a strong impulsive leg: ≥3 consecutive BOS with no significant pullback and no \
CHoCH — trend is accelerating.
One or more FVGs (price imbalances / gaps) remain unfilled in the direction of the move.
This is specifically the FIRST meaningful pullback into the nearest unfilled FVG.
Entry: FVG zone (low to high of the gap). SL: 5–8 pips beyond the far edge of the FVG.
Do NOT use this type if a CHoCH has already occurred or if this is not the first pullback.

── MEAN_REVERSION ────────────────────────────────────────────
Price has extended >65% into a Premium zone (for SELL) or Discount zone (for BUY) — it is at \
an extreme relative to the last major swing's 50% equilibrium.
A strong key level exists at the extreme: HTF OB, weekly/daily swing, or round number.
Reversal must be confirmed: visible liquidity sweep (stop hunt beyond equal highs/lows) \
followed by an M5/M15 CHoCH from the key level.
Entry: LTF OB formed on the CHoCH candle or first pullback after it, refined by FVG inside.
SL: 5–8 pips beyond the extreme key level (must be ≤ 22 pips from entry mid).
TP3 must not exceed the 50% equilibrium level of the swing — this is a mean-reversion, not \
a full trend reversal.

── TREND_FOLLOW ──────────────────────────────────────────────
Default SMC setup: unmitigated OB in the trend direction, correctly placed in Premium (SELL) \
or Discount (BUY) zone, ideally with an FVG overlap inside the OB.
Use this type when none of the above more specific types apply.

── COUNTER_TREND_SCALP ───────────────────────────────────────
Direction opposes the HTF trend. ALL four conditions are mandatory — if any is absent, output \
SIGNAL: WAIT for this setup:
  i.   Liquidity sweep immediately before the CHoCH (equal lows swept for BUY, equal highs \
for SELL).
  ii.  CHoCH originates from a STRONG KEY LEVEL: HTF OB, HTF FVG, weekly/daily swing, round \
number (e.g. 3300.00), or a zone with ≥2 visible prior sharp reactions.
  iii. Unmitigated LTF OB on or immediately after the CHoCH, with an overlapping FVG inside \
it — entry must be within this OB+FVG confluence, not just near the key level.
  iv.  Risk ≤ 20 pips from entry mid to SL.

━━━ STEP 2 — SELECT & RANK ━━━
  - ALWAYS output BOTH setups — one for BUY side, one for SELL side.
  - SETUP 1 = the stronger / higher-confidence setup (either direction).
  - SETUP 2 = the BEST available setup in the OPPOSITE direction from SETUP 1.
  - If no valid opposite-direction setup exists, still output SETUP 2 with SIGNAL_2: WAIT \
and use REASON_2 to explain exactly why (e.g. "no unmitigated demand OB visible", "price in \
Premium zone with no valid BUY structure", "BOS not confirmed on LTF").
  - Never omit SETUP 2 — always output all SETUP 2 fields.

━━━ STEP 3 — ENTRY ZONE (re-read Y-axis for each setup) ━━━
  - Read OB/FVG/retest zone boundaries directly off the price scale.
  - Entry range: 3–8 pips wide.
  - All prices must match numbers visible on the chart's Y-axis.

━━━ STEP 4 — STOP LOSS ━━━
  - BUY: 5–8 pips below OB low / FVG low / retest candle low.
  - SELL: 5–8 pips above OB high / FVG high / retest candle high.
  - Hard cap: 25 pips from entry mid (20 pips for COUNTER_TREND_SCALP, 22 pips for \
MEAN_REVERSION). Exceed → WAIT for that setup.

━━━ STEP 5 — TAKE PROFITS (snap to key levels, then calculate R) ━━━
  - Place TPs AT actual key levels from the map in Step 1 — not at arbitrary R multiples.
  - TP1: the NEAREST key level in the trade direction (round number, equal high/low, FVG edge, \
minor OB, PDH/PDL). This is the scalp target — it must be visible and real on the chart.
  - TP2: the next key level beyond TP1 (next OB, next swing, next round number).
  - TP3: the major structural target (swing high/low, daily level, large OB top/bottom).
  - After placing TPs at real levels, calculate and show R:
      Entry_mid = (entry_low + entry_high) / 2
      Risk = |Entry_mid − SL|
      R = |TP − Entry_mid| / Risk, rounded to 1 decimal
  - If the nearest key level gives R < 1.0, output SIGNAL: WAIT — the reward is not worth \
the risk at this entry.
  - MEAN_REVERSION TP3 capped at 50% equilibrium of the swing.
  - COUNTER_TREND_SCALP TP3 capped at the HTF key level that bounds the move.
  - Format: TP1_1: 3318.50  (R=1.6)

━━━ STEP 6 — CONFIDENCE ━━━
  - HIGH: entry criteria fully met, rejection/confirmation visible, all levels read from scale.
    Additional per-type requirement for HIGH:
      PULLBACK → rejection candle clearly visible at the pullback zone.
      BREAKOUT_RETEST → strong-body breakout candle + retest rejection both visible.
      MOMENTUM_CONT → clean unmitigated FVG, no CHoCH, first pullback confirmed.
      MEAN_REVERSION → liquidity sweep AND CHoCH both visible from the key level.
      COUNTER_TREND_SCALP → all four conditions met.
  - MEDIUM: OB present without FVG, or confirmation is borderline, or scale partially visible.
  - LOW: structure ambiguous, confirmation absent, or scale illegible.
  - LOW → downgrade SIGNAL to WAIT.
  - MEAN_REVERSION and COUNTER_TREND_SCALP must be HIGH → else WAIT.

━━━ OUTPUT FORMAT ━━━
Use this EXACT format. ALWAYS include all SETUP 2 fields — never omit them. \
No markdown, no extra lines, no text outside the fields.

TIMEFRAME: <e.g. M5, M15, H1>
TREND: <BULLISH | BEARISH | RANGING>
BIAS: <Premium | Discount | Neutral>
STRUCTURE: <2–3 sentences: nearest support/resistance key levels, BOS/CHoCH, OBs for both setups, liquidity pools>

SIGNAL_1: <BUY | SELL | WAIT>
SETUP_TYPE_1: <PULLBACK | BREAKOUT_RETEST | MOMENTUM_CONT | MEAN_REVERSION | TREND_FOLLOW | COUNTER_TREND_SCALP>
ENTRY_1: <low–high read from Y-axis>
SL_1: <exact price>  (<N> pips <above high | below low>)
TP1_1: <exact price>  (R=X.X)
TP2_1: <exact price>  (R=X.X)
TP3_1: <exact price>  (R=X.X)
CONFIDENCE_1: <HIGH | MEDIUM | LOW>
REASON_1: <3 sentences: strategy type justification, key level quality, confirmation signal>

SIGNAL_2: <BUY | SELL | WAIT>
SETUP_TYPE_2: <PULLBACK | BREAKOUT_RETEST | MOMENTUM_CONT | MEAN_REVERSION | TREND_FOLLOW | COUNTER_TREND_SCALP>
ENTRY_2: <low–high read from Y-axis>
SL_2: <exact price>  (<N> pips <above high | below low>)
TP1_2: <exact price>  (R=X.X)
TP2_2: <exact price>  (R=X.X)
TP3_2: <exact price>  (R=X.X)
CONFIDENCE_2: <HIGH | MEDIUM | LOW>
REASON_2: <3 sentences: strategy type justification, key level quality, confirmation signal>

NOTES: <price scale observations, session context, or "None">
"""

_USER_PROMPT_SINGLE = (
  "Analyse this XAUUSD chart. Read the Y-axis price scale first. "
  "Scan for ALL valid setups across all six strategy types — output both a BUY and a SELL "
  "setup if both qualify, otherwise the single best setup. "
  "Use only price levels read from the visible scale."
)
_USER_PROMPT_MULTI = (
  "I am sending you {n} XAUUSD charts at different timeframes. "
  "Use the higher timeframe(s) for bias, structure, and key levels. "
  "Use the lowest timeframe for entry zone precision and confirmation signals. "
  "Scan all six strategy types — output up to two setups (BUY and SELL) if both qualify. "
  "All price levels must be read from the visible Y-axis scale."
)

_STRATEGY_LABELS = {
  "PULLBACK":            "🔄 Pullback",
  "BREAKOUT_RETEST":     "💥 Breakout",
  "MOMENTUM_CONT":       "🚀 Momentum",
  "MEAN_REVERSION":      "↩️ Mean Rev",
  "TREND_FOLLOW":        "",
  "COUNTER_TREND_SCALP": "⚡️ Scalp",
}


def _parse_analysis(raw: str) -> dict:
  """Parse Claude's structured output into a dict."""
  result = {}
  for line in raw.strip().splitlines():
    if ":" in line:
      key, _, val = line.partition(":")
      result[key.strip().upper()] = val.strip()
  return result


def _render_setup(data: dict, suffix: str, tf: str, trend: str, trend_icon: str) -> list[str]:
  """Render one setup block (suffix = '_1' or '_2') as HTML lines."""
  signal = data.get(f"SIGNAL{suffix}", "")
  if not signal:
    return []

  if signal == "WAIT":
    reason = data.get(f"REASON{suffix}", "")
    direction = "BUY" if suffix == "_2" and data.get("SIGNAL_1") in ("SELL",) else \
                "SELL" if suffix == "_2" and data.get("SIGNAL_1") in ("BUY",) else "–"
    label = f"⏳ No {direction} setup" if direction != "–" else "⏳ WAIT"
    lines = [f"{label}"]
    if reason:
      lines.append(f"<i>{reason}</i>")
    return lines

  setup_type = data.get(f"SETUP_TYPE{suffix}", "")
  confidence = data.get(f"CONFIDENCE{suffix}", "")

  signal_icon = "📈" if signal == "BUY" else "📉"
  conf_icon = {"HIGH": "🔥", "MEDIUM": "⚡️", "LOW": "⚠️"}.get(confidence, "")
  strategy_tag = _STRATEGY_LABELS.get(setup_type, "")
  strategy_str = f"  {strategy_tag}" if strategy_tag else ""

  def field(label: str, key: str) -> str:
    return f"{label}  <b>{data.get(key, '–')}</b>"

  return [
    f"{signal_icon} <b>{signal}</b>  <code>{tf}</code>  {trend_icon} {trend}{strategy_str}",
    "",
    field("⚡️ Entry:", f"ENTRY{suffix}"),
    field("🛡 SL:", f"SL{suffix}"),
    field("🎯 TP1:", f"TP1{suffix}"),
    field("💎 TP2:", f"TP2{suffix}"),
    field("🏆 TP3:", f"TP3{suffix}"),
    "",
    f"{conf_icon} Confidence: <b>{confidence}</b>",
    f"💡 {data.get(f'REASON{suffix}', '–')}",
  ]


def _format_html(data: dict, raw: str) -> str:
  """Convert parsed analysis dict to Telegram HTML message."""
  if not data.get("SIGNAL_1"):
    return f"📊 <b>Chart Analysis</b>\n\n<pre>{raw[:3000]}</pre>"

  tf = data.get("TIMEFRAME", "")
  trend = data.get("TREND", "")
  bias = data.get("BIAS", "")

  trend_icon = "🟢" if trend == "BULLISH" else ("🔴" if trend == "BEARISH" else "🟡")
  bias_icon = {"Premium": "🔴", "Discount": "🟢", "Neutral": "🟡"}.get(bias, "")

  lines: list[str] = []

  if bias:
    lines.append(f"{bias_icon} <i>{bias}</i>  |  {trend_icon} {trend}  |  <code>{tf}</code>")
  lines.append(f"🏗 <i>{data.get('STRUCTURE', '–')}</i>")

  setup1 = _render_setup(data, "_1", tf, trend, trend_icon)
  if setup1:
    lines.append("")
    lines.extend(setup1)

  setup2 = _render_setup(data, "_2", tf, trend, trend_icon)
  if setup2:
    lines.append("")
    lines.append("─────────────────")
    lines.extend(setup2)

  notes = data.get("NOTES", "")
  if notes and notes.lower() not in ("-", "none", "n/a"):
    lines.append(f"\n📌 <i>{notes}</i>")

  return "\n".join(lines)


async def analyse_chart_image(
  image_data: list[bytes | BytesIO] | bytes | BytesIO,
  media_type: str = "image/jpeg",
) -> str:
  """
  Send one or more chart images to Claude vision, return a Telegram HTML string.

  Args:
    image_data: Single image (bytes/BytesIO) or a list of images for MTF analysis.
    media_type: MIME type — Telegram photos are always image/jpeg.
  """
  if not settings.anthropic_api_key:
    return "⚠️ <b>Chart analysis unavailable</b> — ANTHROPIC_API_KEY not configured."

  if not isinstance(image_data, list):
    image_data = [image_data]

  content: list[dict] = []
  for img in image_data:
    raw_bytes = img.read() if isinstance(img, BytesIO) else img
    b64 = base64.standard_b64encode(raw_bytes).decode("utf-8")
    content.append({
      "type": "image",
      "source": {"type": "base64", "media_type": media_type, "data": b64},
    })

  user_prompt = (
    _USER_PROMPT_SINGLE if len(image_data) == 1
    else _USER_PROMPT_MULTI.format(n=len(image_data))
  )
  content.append({"type": "text", "text": user_prompt})

  client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
  try:
    response = await client.messages.create(
      model=MODEL,
      max_tokens=MAX_TOKENS,
      system=_SYSTEM_PROMPT,
      messages=[{"role": "user", "content": content}],
    )
    raw = response.content[0].text
    log.info("Chart analysis complete — %d image(s), %d chars, tokens: %s",
         len(image_data), len(raw), response.usage)
    parsed = _parse_analysis(raw)
    return _format_html(parsed, raw)

  except anthropic.APIError as e:
    log.error("Anthropic API error during chart analysis: %s", e)
    return f"⚠️ <b>Analysis failed</b> — API error: {e}"
