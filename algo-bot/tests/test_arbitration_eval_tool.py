"""The offline arbitration evaluation is itself evidence, so it is tested.

* Model A must be the production arbitration exactly, and eligibility must be Algo Bot's own
  admission - read from the reviewed-scope registry and the adapter, never from source text.
* The outcome simulator must be conservative and strictly causal: stops act before targets inside
  one candle, no candle is read before it has completed, an expiry is valued only at the last
  candle completed by then, and a trade whose history ends early is censored, not timed out.
"""

from __future__ import annotations

import json
import random

import pytest

from app.analysis_client.models import OpportunityTopic, parse_analysis_event
from app.analysis_client.provenance import CATALOG_STRATEGY_IDS
from app.autotrade import go_opportunity_policy as pol
from app.autotrade.arbitration import ExecutionIntent, arbitrate_execution_intents
from app.autotrade.go_containment import observe_only_strategies
from tests.test_go_opportunity_policy import golden
from tools import arbitration_eval as ev

pytestmark = pytest.mark.no_database


# ---------------------------------------------------------------- model A == production

def _random_intents(rng: random.Random) -> list[ExecutionIntent]:
  intents = []
  for index in range(rng.randint(1, 7)):
    low = 4000 + rng.choice([0, 0, 3, 8, 40])
    intents.append(ExecutionIntent(
      intent_id=f"i{index}", source="go", strategy=rng.choice(["supply", "key_level", "range_edge", "fvg"]),
      direction=rng.choice(["BUY", "SELL"]), confluence=rng.choice([1, 2, 3]), freshness=1000.0 + rng.randint(0, 5),
      distance_pips=0.0, entry_low=low, entry_high=low + rng.choice([1, 2, 4]), structural_id=f"z{rng.randint(0, 4)}",
      quality_overall=rng.choice([0.5, 0.67, 0.85, 1.0, None]), structural_quality=rng.choice([None, 8.0, 12.5]),
      atr=rng.choice([0.0, 1.0, 2.5]), bias_relationship=rng.choice([None, "with_bias", "counter_bias"]),
      executable_now=rng.random() < 0.7,
    ))
  return intents


def test_model_a_is_the_production_arbitration_exactly():
  rng = random.Random(7)
  model = ev.model_a()
  for _ in range(400):
    intents = _random_intents(rng)
    production = arbitrate_execution_intents(intents, conflict_margin_quality=ev.CONFLICT_MARGIN)
    winners, suppressed, reason, losers = model.arbitrate(intents)
    assert tuple(i.intent_id for i in winners) == tuple(i.intent_id for i in production.ordered)
    assert {i.intent_id for i in suppressed} == {i.intent_id for i in production.suppressed}
    assert reason == production.reason_code and losers == production.thesis_losers


# ---------------------------------------------------------------- eligibility = Algo Bot's admission

def _envelope(strategy: str, *, confirmed: bool, symbol: str = "EURUSD", direction: str | None = None) -> dict:
  """A creation envelope for any reviewed strategy, built from the pinned Go golden."""
  profile = pol.REVIEWED_SCOPES[strategy]
  raw = golden()
  payload = raw["payload"]
  side = direction or profile.direction or "SELL"
  payload.update(
    id=f"opp_{strategy}_{symbol.lower()}_{'c' if confirmed else 'r'}", strategy=strategy, symbol=symbol,
    direction=side, timeframe=sorted(profile.allowed_timeframes)[0],
  )
  if side == "BUY":
    payload["entry"] = {"low": 4352.5, "high": 4356.0}
    payload["invalidation"] = {**payload["invalidation"], "price": 4350.0}
    payload["targets"] = [{"price": {"price": 4364.0, "label": "target"}}]
  payload["evidence"] = [{"code": (profile.evidence_prefixes[0] if profile.evidence_prefixes else "m5_generic") + "x"}]
  if not confirmed:
    payload["technical_context"].pop("confirmation", None)
  # Go's own confluence block (the worker's floor reads the selected stars).
  payload["technical_context"]["confluence"] = {
    "version": "v1", "selected_stars": 2, "v1_stars": 2, "v2_stars": 2, "v2_raw": 11, "raw_factor_score": 11,
    "zone_quality_score": 0, "mad_bonus": 0,
    "factors": {"htf_aligned": True, "touches": 2, "wick_rejection": True, "displacement_grade": False,
                "session_context": True, "structural_agreement": False, "fib_touch": False, "choch": False},
  }
  raw["event_id"] = payload["id"]
  return raw


def _admit(strategy: str, **kwargs):
  raw = _envelope(strategy, **kwargs)
  event = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  return ev.admit(raw, now=event.payload.created_at + 60, static=False)


@pytest.mark.parametrize("strategy", sorted(pol.REVIEWED_SCOPES))
def test_mandatory_reaction_confirmation_follows_the_registry_for_every_strategy(strategy):
  profile = pol.REVIEWED_SCOPES[strategy]
  if strategy in observe_only_strategies("EURUSD"):
    pytest.skip(f"{strategy} is execution-contained on EURUSD: its verdict is containment, tested separately")
  resting_match, resting_code = _admit(strategy, confirmed=False)
  confirmed_match, confirmed_code = _admit(strategy, confirmed=True)
  assert confirmed_match is not None, confirmed_code
  if profile.requires_reaction:
    assert resting_match is None and resting_code == "reaction_confirmation_unavailable"
  else:
    assert resting_match is not None, f"{strategy} has no mandatory reaction and must stay eligible: {resting_code}"


def test_supply_and_demand_require_confirmation():
  # The first P1 evaluation read the registry with a regex that only recognised the keyword form
  # and so treated these two - written positionally - as having no requirement.
  for strategy in ("supply", "demand"):
    assert pol.REVIEWED_SCOPES[strategy].requires_reaction is True
    match, code = _admit(strategy, confirmed=False)
    assert (match, code) == (None, "reaction_confirmation_unavailable")
    assert _admit(strategy, confirmed=True)[0] is not None


def test_every_registered_go_strategy_is_accounted_for_exactly_once():
  table = ev.registry_table()
  assert [row["strategy"] for row in table] == sorted(CATALOG_STRATEGY_IDS)
  assert len(table) == len(CATALOG_STRATEGY_IDS) == 21
  assert {row["strategy"]: row["requires_reaction"] for row in table} == {
    name: profile.requires_reaction for name, profile in pol.REVIEWED_SCOPES.items()
  }


def test_positional_and_keyword_registry_entries_evaluate_identically(monkeypatch):
  """How a registry entry is written must not change what the evaluator does with it."""
  positional = pol.ScopeProfile("supply", "Supply Demand", "supply", "SELL", frozenset({"M5"}), "go_m5_zone", True, ("m5_supply_zone_",))
  keyword = pol.ScopeProfile(
    catalog_id="supply", legacy_strategy="Supply Demand", structural_kind="supply", direction="SELL",
    allowed_timeframes=frozenset({"M5"}), strategy_mode="go_m5_zone", requires_reaction=True,
    evidence_prefixes=("m5_supply_zone_",),
  )
  assert positional == keyword
  outcomes = []
  for profile in (positional, keyword):
    monkeypatch.setitem(pol.REVIEWED_SCOPES, "supply", profile)
    outcomes.append((_admit("supply", confirmed=False)[1], _admit("supply", confirmed=True)[1]))
  assert outcomes[0] == outcomes[1] == ("reaction_confirmation_unavailable", None)


def test_the_evaluator_reads_no_source_text_for_eligibility():
  import inspect

  source = inspect.getsource(ev)
  assert "re.findall" not in source and "read_text()" not in inspect.getsource(ev.admit)
  assert "requires_reaction()" not in source


def test_containment_timeframe_and_expiry_come_from_production_not_the_tool():
  # Contained on the instrument -> the production containment verdict.
  contained = next(name for name in sorted(pol.REVIEWED_SCOPES) if name in observe_only_strategies("XAU"))
  raw = _envelope(contained, confirmed=True, symbol="XAU")
  event = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  assert ev.admit(raw, now=event.payload.created_at + 60, static=False) == (None, "execution_contained")
  # A timeframe the adapter has not reviewed for the strategy.
  raw = _envelope("key_level", confirmed=True)
  raw["payload"]["timeframe"] = "H1"
  assert ev.admit(raw, now=raw["payload"]["created_at"] + 60, static=False)[1] == "confirmation_timeframe_unreviewed"
  # Past its own expiry.
  raw = _envelope("key_level", confirmed=True)
  assert ev.admit(raw, now=raw["payload"]["expires_at"] + 1, static=False)[1] == "opportunity_expired"
  # Not a reviewed strategy at all.
  raw = _envelope("key_level", confirmed=True)
  raw["payload"]["strategy"] = "mystery"
  assert ev.admit(raw, now=raw["payload"]["created_at"] + 60, static=False) == (None, "scope_not_reviewed")


def _row_from(raw: dict, *, bar_time: int, close: float = 4354.0) -> dict:
  payload = raw["payload"]
  return {
    "capture": "c", "symbol": payload["symbol"], "bar_time": bar_time, "bar_tf": "M5", "id": payload["id"],
    "strategy": payload["strategy"], "direction": payload["direction"], "entry_low": payload["entry"]["low"],
    "entry_high": payload["entry"]["high"], "invalidation": payload["invalidation"]["price"], "quality": 0.8,
    "structural_id": "", "created_at": payload["created_at"], "expires_at": payload["expires_at"],
    "observed_tf": payload["timeframe"], "structure_tf": "", "has_reaction": "confirmation" in payload["technical_context"],
    "confluence": {"selected_stars": 2, "v2_raw": 8.0}, "atr": payload["technical_context"]["atr"],
    "reference_price": close, "envelope": raw,
  }


def test_resting_observations_do_not_compete_with_executable_signals():
  resting = _envelope("supply", confirmed=False)
  confirmed = _envelope("key_level", confirmed=True)
  bar_time = confirmed["payload"]["created_at"] - 240        # decision one minute after the opportunity
  rows = [_row_from(resting, bar_time=bar_time), _row_from(confirmed, bar_time=bar_time)]
  # They are on the same corridor in the same cycle: the resting zone must not enter at all.
  cycles, accounting = ev.build_cycles(rows)
  admitted = [intent.strategy for cycle in cycles for intent in cycle["intents"]]
  assert admitted == ["Key Level"]
  assert accounting["by_code"]["reaction_confirmation_unavailable"] == 1
  assert accounting["by_strategy_code"]["supply"]["reaction_confirmation_unavailable"] == 1


def test_the_correction_is_measured_against_the_first_cut_rule_by_strategy_and_symbol():
  resting_demand = _envelope("demand", confirmed=False)
  bar_time = resting_demand["payload"]["created_at"] - 240
  _cycles, accounting = ev.build_cycles([_row_from(resting_demand, bar_time=bar_time)])
  assert accounting["legacy_eligible_rows"] == 1 and accounting["corrected_eligible_rows"] == 0
  assert accounting["legacy_only_by_strategy_symbol"] == {"demand|EURUSD": 1}


# ---------------------------------------------------------------- simulator (strictly causal)

def _row(**overrides):
  row = dict(
    symbol="XAU", direction="BUY", entry_low=100.0, entry_high=101.0, reference_price=100.5, invalidation=99.0,
    bar_time=1000, bar_tf="M5", expires_at=1000 + 86400, stop_floor_pips=50.0, stop_cap_pips=60.0,
    id="x", structural_id="x", strategy="supply",
  )
  row.update(overrides)
  return row


def _bar(t, o, h, l, c):
  return (t, o, h, l, c, 0.0)


# decision = 1000 + 300 = 1300. Inside the zone: filled at the first candle's open + the 0.25
# assumed spread = 100.75 ; the widened 50 pip stop sits 5.0 below at 95.75.
FILL = _bar(1300, 100.5, 101.0, 100.2, 100.8)         # completes at 1600
QUIET = _bar(1600, 100.8, 101.2, 100.6, 101.0)        # completes at 1900


def test_a_buy_limit_waits_for_the_ask_to_reach_it():
  outside = _row(reference_price=105.0)
  bars = [_bar(1300, 105, 105, 101.2, 103), _bar(1600, 103, 104, 101.2, 103)]
  assert ev.simulate(outside, bars, "M5")["reason"] == "history_ended_before_fill"
  bars = [_bar(1300, 105, 105, 100.6, 103)]
  assert ev.simulate(outside, bars, "M5")["filled"] is True


def test_stop_acts_before_targets_in_the_same_candle_and_costs_one_r():
  bars = [FILL, _bar(1600, 100.8, 130, 90, 100)]       # spans both the stop (95.75) and every target
  outcome = ev.simulate(_row(), bars, "M5")
  assert outcome["filled"] and outcome["r"] == pytest.approx(-1.0) and outcome["reason"] == "stop"
  assert outcome["status"] == "complete" and outcome["closed_at"] == 1900


def test_break_even_after_the_first_target():
  # distance 5.0: TP1 = 105.75 -> +0.4R booked, then the stop at entry 100.75.
  bars = [FILL, _bar(1600, 100.8, 106.5, 101, 105), _bar(1900, 105, 105, 100.5, 101)]
  outcome = ev.simulate(_row(), bars, "M5")
  assert outcome["r"] == pytest.approx(0.4, abs=1e-6) and outcome["reason"] == "be_or_trail_stop"


def test_a_stop_beyond_the_envelope_cap_is_never_traded():
  outcome = ev.simulate(_row(invalidation=90.0), [FILL], "M5")
  assert (outcome["filled"], outcome["reason"], outcome["status"]) == (False, "stop_above_cap", "not_filled")


def test_a_wider_spread_never_improves_a_buy_entry():
  outside = _row(reference_price=105.0)
  assert ev.simulate(outside, [_bar(1300, 105, 105, 100.9, 103)], "M5")["filled"] is False   # 100.9 + 0.25 > 101
  bars = [_bar(1300, 105, 105, 100.7, 103)]
  assert ev.simulate(outside, bars, "M5", spread_scale=1.0)["filled"] is True
  assert ev.simulate(outside, bars, "M5", spread_scale=2.0)["filled"] is False


def test_a_trade_open_at_its_timeout_is_valued_at_the_last_candle_completed_by_the_expiry():
  # Expiry 1900: FILL (closes 1600) and QUIET (closes exactly 1900) are both usable. A later,
  # violent candle exists in the capture and must be ignored.
  bars = [FILL, QUIET, _bar(1900, 101, 101, 50, 60)]
  outcome = ev.simulate(_row(expires_at=1900), bars, "M5")
  assert (outcome["status"], outcome["reason"]) == ("complete", "timeout")
  assert outcome["r"] == pytest.approx((101.0 - 100.75) / 5.0)       # QUIET's close, not 60
  assert outcome["closed_at"] == 1900 and outcome["last_observation_at"] == 1900 and outcome["effective_expiry"] == 1900


def test_a_candle_closing_one_second_after_the_expiry_is_not_read():
  # Same bars, expiry one second earlier: QUIET completes at 1900 > 1899, so only FILL is usable.
  bars = [FILL, QUIET, _bar(1900, 101, 101, 50, 60)]
  outcome = ev.simulate(_row(expires_at=1899), bars, "M5")
  assert outcome["r"] == pytest.approx((100.8 - 100.75) / 5.0)        # FILL's close
  assert outcome["last_observation_at"] == 1600


def test_expiry_inside_an_unfinished_candle_uses_only_completed_candles():
  # The second candle would hit the stop, but it is still forming at the 1750 expiry.
  bars = [FILL, _bar(1600, 100.8, 100.9, 90, 91)]
  outcome = ev.simulate(_row(expires_at=1750), bars, "M5")
  assert (outcome["status"], outcome["reason"]) == ("complete", "timeout")
  assert outcome["r"] == pytest.approx((100.8 - 100.75) / 5.0) and outcome["r"] > -1.0


def test_history_that_ends_before_the_expiry_is_censored_not_a_timeout():
  outcome = ev.simulate(_row(expires_at=1000 + 86400), [FILL, QUIET], "M5")
  assert (outcome["status"], outcome["reason"]) == ("censored", "history_ended_before_expiry")
  assert outcome["filled"] and not ev.usable(outcome)                 # excluded from expectancy
  assert outcome["r"] == pytest.approx((101.0 - 100.75) / 5.0)        # labelled mark at the last completed candle


def test_no_candle_completes_before_the_expiry_means_no_trade_and_no_invented_price():
  outcome = ev.simulate(_row(expires_at=1500), [FILL, QUIET], "M5")   # first candle completes at 1600
  assert outcome["filled"] is False and not ev.usable(outcome)
  assert outcome["status"] in {"not_filled", "censored_unfilled"}


def test_no_bars_after_the_decision_is_not_filled():
  outcome = ev.simulate(_row(), [_bar(100, 1, 1, 1, 1)], "M5")
  assert (outcome["filled"], outcome["reason"]) == (False, "no_bars")


def test_buy_and_sell_are_symmetric():
  buy = ev.simulate(_row(expires_at=1900), [FILL, QUIET], "M5")
  # Mirror the market around 200 and flip the side; the ask/bid roles flip with the spread.
  mirror = lambda b: _bar(b[0], 200 - b[1], 200 - b[3], 200 - b[2], 200 - b[4])
  sell_row = _row(direction="SELL", entry_low=200 - 101.0, entry_high=200 - 100.0, reference_price=200 - 100.5,
                  invalidation=200 - 99.0, expires_at=1900)
  sell = ev.simulate(sell_row, [mirror(FILL), mirror(QUIET)], "M5")
  assert (buy["status"], buy["reason"]) == (sell["status"], sell["reason"])
  # A sell fills at the bid (no spread) where a buy pays it, so the mirrored result is the buy's plus the spread.
  assert sell["r"] == pytest.approx(buy["r"] + 2 * ev.SPREAD["XAU"] / 5.0 - (0.0), abs=0.06)
  stopped_buy = ev.simulate(_row(), [FILL, _bar(1600, 100.8, 130, 90, 100)], "M5")
  stopped_sell = ev.simulate(sell_row, [mirror(FILL), mirror(_bar(1600, 100.8, 130, 90, 100))], "M5")
  assert stopped_buy["r"] == stopped_sell["r"] == pytest.approx(-1.0)


def test_a_long_capture_with_a_short_holding_window_is_valued_inside_the_window():
  hours = [_bar(1300 + 300 * i, 100.8, 101.2, 100.6, 101.0 + 0.01 * i) for i in range(2000)]
  short = ev.simulate(_row(expires_at=1300 + 300 * 3), hours, "M5")
  assert short["status"] == "complete" and short["closed_at"] == 2200
  trimmed = ev.simulate(_row(expires_at=1300 + 300 * 3), hours[:6], "M5")
  assert short == trimmed                                              # nothing past the cutoff mattered


@pytest.mark.parametrize("bars_after", [
  [_bar(1900, 101, 101, 50, 60)],
  [_bar(1900, 101, 101, 50, 60), _bar(2200, 60, 70, 10, 20)],
])
def test_appending_later_bars_never_changes_a_completed_trade(bars_after):
  for row_overrides, early in (
    ({}, [FILL, _bar(1600, 100.8, 130, 90, 100)]),                    # stopped
    ({}, [FILL, _bar(1600, 100.8, 106.5, 101, 105), _bar(1900, 105, 105, 100.5, 101)]),   # break-even stop
    ({"expires_at": 1900}, [FILL, QUIET]),                            # timeout at its expiry
  ):
    row = _row(**row_overrides)
    base = ev.simulate(row, early, "M5")
    later = [b for b in bars_after if b[0] >= early[-1][0] + 300]
    assert ev.simulate(row, early + later, "M5") == base
    assert base["status"] == "complete"


def test_only_complete_filled_trades_count_toward_expectancy():
  stop = ev.simulate(_row(), [FILL, _bar(1600, 100.8, 130, 90, 100)], "M5")
  censored = ev.simulate(_row(), [FILL, QUIET], "M5")
  unfilled = ev.simulate(_row(invalidation=90.0), [FILL], "M5")
  assert [ev.usable(o) for o in (stop, censored, unfilled)] == [True, False, False]


# ---------------------------------------------------------------- validation levels

def test_level_b_compares_admission_on_the_stored_envelope_at_the_recorded_decision_time(tmp_path):
  confirmed = _envelope("key_level", confirmed=True)
  resting = _envelope("supply", confirmed=False)
  created = confirmed["payload"]["created_at"]
  records = [
    {"opportunity_id": "a", "envelope": confirmed, "decisions": [{"mode": "go", "outcome": "match_written", "reason": "go_live", "at": created + 60}]},
    {"opportunity_id": "b", "envelope": resting, "decisions": [{"mode": "go", "outcome": "rejected", "reason": "reaction_confirmation_unavailable", "at": created + 60}]},
    # production recorded an admission the current rules reject: counted as a disagreement.
    {"opportunity_id": "c", "envelope": resting, "decisions": [{"mode": "go", "outcome": "match_written", "reason": "go_live", "at": created + 60}]},
    # no recorded decision: unavailable, never a match.
    {"opportunity_id": "d", "envelope": confirmed, "decisions": []},
  ]
  path = tmp_path / "export.jsonl"
  path.write_text("\n".join(json.dumps(r) for r in records))
  result = ev.level_b(str(path), since=0)
  scope = result["all_history"]
  assert (scope["exported"], scope["compared"], scope["agree"], scope["disagree"]) == (4, 3, 2, 1)
  assert scope["unavailable"] == {"unavailable_no_envelope_or_decision": 1}
  later = ev.level_b(str(path), since=created + 61)
  assert later["same_code_version"]["compared"] == 0                   # nothing decided after the cutoff


def test_level_b_does_not_count_a_recovery_event_as_a_match_or_a_mismatch(tmp_path):
  stale = _envelope("key_level", confirmed=True)
  stale["payload"]["recovered_at"] = stale["payload"]["created_at"] + 5
  decided = stale["payload"]["created_at"] + 10_000          # far past the age limit
  path = tmp_path / "export.jsonl"
  path.write_text(json.dumps({"opportunity_id": "r", "envelope": stale, "decisions": [
    {"mode": "go", "outcome": "match_written", "reason": "go_live", "at": decided}]}))
  scope = ev.level_b(str(path), since=0)["all_history"]
  assert (scope["compared"], scope["agree"], scope["disagree"]) == (0, 0, 0)
  assert scope["unavailable"] == {"unavailable_recovery_event_needs_redis_live_book": 1}


def test_level_c_reports_what_it_cannot_compare():
  assert ev.level_c([], [], "", "")["available"] is False
