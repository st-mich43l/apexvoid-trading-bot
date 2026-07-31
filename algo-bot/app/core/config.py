from typing import Optional
import math
import os
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.environment_options import RESOLVED_ENVIRONMENT_OPTIONS


class Settings(BaseSettings):
  model_config = SettingsConfigDict(env_file=".env", extra="ignore")

  telegram_bot_token: str
  telegram_channel_id: int = Field(
    validation_alias=AliasChoices(
      "SIGNAL_VIP_CHANNEL_ID",
      "TELEGRAM_CHANNEL_ID",
      "TELEGRAM_CHAT_ID",
    )
  )
  # PostgreSQL connection URL (libpq/asyncpg DSN). In production this is
  # injected via the compose environment; the localhost default is for local
  # development against a throwaway Postgres container.
  database_url: str = Field(
    default="postgresql://apexvoid:apexvoid@localhost:5432/signals",
    validation_alias=AliasChoices("DATABASE_URL", "POSTGRES_DSN"),
  )
  log_level: str = "INFO"
  telegram_owner_id: Optional[int] = None  # your Telegram user ID — only this user can DM the bot
  signal_public_channel_id: Optional[int] = Field(
    default=None,
    validation_alias=AliasChoices(
      "SIGNAL_PUBLIC_CHANNEL_ID",
      "XAU_PUBLIC_CHANNEL_ID",
    ),
  )
  public_show_pips: bool = Field(
    default=True,
    validation_alias=AliasChoices(
      "SIGNAL_PUBLIC_SHOW_PIPS",
      "PUBLIC_SHOW_PIPS",
    ),
  )
  anthropic_api_key: Optional[str] = None  # for chart screenshot analysis via Claude vision
  seq_reset_tz: str = "Asia/Ho_Chi_Minh"
  auto_book_bare_pips: bool = False
  tiingo_api_key: Optional[str] = None
  # Redis backs the watcher's TP/SL progress + bar cursor so state survives a
  # restart. Default host matches the compose service name; override locally.
  redis_url: str = "redis://redis:6379/0"
  # 30s under normal operation just polls the cTrader Redis bar window more
  # often (cheap). If the cTrader feed is down and Tiingo fallback kicks in,
  # this pace is ~120 req/hour - over Tiingo's free-tier 50/hour cap for the
  # duration of the outage; accepted tradeoff for faster TP/SL notifications.
  track_interval: int = 30
  # Watcher reads closed M1 bars from ctrader-feed's Redis ZSET first; if the
  # newest bar there is older than this, it falls back to Tiingo for that
  # tick instead (feed gap/restart). ~3x the M1 interval gives room for one
  # missed close without flapping between sources every tick.
  watcher_ctrader_stale_seconds: int = 180
  session_asia_start: int = 22
  session_london_start: int = 7
  session_ny_start: int = 13
  # Metals daily candle rolls at the NY futures close, 21:00 UTC.
  daily_rollover_utc_hour: int = 21
  calendar_enabled: bool = True
  calendar_feed_thisweek: str = (
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
  )
  calendar_feed_nextweek: str = (
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json"
  )
  calendar_user_agent: str = "apexvoid-trading-bot/1.0 (+contact)"
  calendar_currencies: str = "USD"
  oil_keywords: str = (
    "crude oil inventories,opec,cushing,api weekly crude"
  )
  news_brief_hour: int = 7
  event_guard_hours: float = 4.0
  news_guard_block: bool = False
  weekly_report_enabled: bool = True
  weekly_report_dow: int = 6
  weekly_report_hour: int = 8
  weekly_report_skip_empty: bool = False
  scanner_enabled: bool = False
  scanner_symbols: str = "XAU"
  scanner_exec_tf: str = "M5"
  scanner_htf: str = "H1,M15"
  scanner_telegram_bot_token: Optional[str] = None
  scanner_window: int = 500
  # refactor/p0-direct-zone-signal-execution: per-timeframe closed-bar
  # lookback for zone/key-level discovery. Before this, every timeframe
  # (H1 down to M1) fetched the same flat `scanner_window` bar count, which
  # under-fetches M5 (needs the deepest history to find durable supply/
  # demand and order-block evidence) and over-fetches M1 (timing/trigger
  # only, never a source of a primary key level). Defaults sit inside the
  # XAU-appropriate ranges documented in docs/. `window_for_timeframe()`
  # is the single place that resolves a timeframe string to its bar count -
  # detectors must never hardcode a lookback count themselves.
  xau_lookback_h1_bars: int = Field(
    default=400,
    validation_alias=AliasChoices("XAU_LOOKBACK_H1_BARS"),
  )
  xau_lookback_m15_bars: int = Field(
    default=650,
    validation_alias=AliasChoices("XAU_LOOKBACK_M15_BARS"),
  )
  xau_lookback_m5_bars: int = Field(
    default=1000,
    validation_alias=AliasChoices("XAU_LOOKBACK_M5_BARS"),
  )
  xau_lookback_m1_bars: int = Field(
    default=150,
    validation_alias=AliasChoices("XAU_LOOKBACK_M1_BARS"),
  )
  scanner_alert_ttl: int = 7200
  scanner_level_bucket: int = 20
  zone_alert_ttl: int = 14400
  scanner_confluence_floor: int = 2
  alert_overlap_suppress: float = 0.5
  # P0 zone/M1 simplification: BUY and SELL bands form one contested
  # corridor - never two independent opportunities, and never resolved by
  # picking whichever has the higher confluence score - whenever they
  # overlap at all, or their nearest edges sit within this many ATRs of
  # each other. See actionability.py::resolve_actionability.
  contested_corridor_gap_atr: float = 0.5
  scanner_conflict_margin: float = 1.0
  spot_fresh_secs: int = 30
  spot_max_deviation_pct: float = 2.0
  max_entry_atr: float = 2.0
  max_zone_width_atr: float = 1.5
  proximal_band_atr: float = 0.5
  max_merged_zone_atr: float = 3.0
  range_lookback: int = 50
  atr_length: int = 14
  swing_fractal_n: int = 2
  zigzag_pct: float = 0.0
  zigzag_atr_mult: float = 1.0
  displacement_atr_mult: float = 1.5
  zone_width: str = "body"
  zone_merge_overlap: float = 0.5
  equal_tol_atr: float = 0.15
  level_cluster_atr: float = 0.5
  round_step: float = 5.0
  key_level_min_touches: int = 2
  momentum_lookback: int = 8
  momentum_body_frac: float = 0.6
  eq_band: float = 0.10
  strict_pd_gate: bool = False
  sweep_body_frac: float = 0.5
  sweep_react_bars: int = 3
  inducement_band_atr: float = 0.3
  chop_filter_enabled: bool = True
  chop_range_atr: float = 4.0
  chop_lookback: int = 24
  chop_edge_frac: float = 0.25
  tl_min_touches: int = 3
  tl_tol_atr: float = 0.3
  tl_max_slope_atr: float = 0.15
  coil_contract: float = 0.8
  breakout_buffer_atr: float = 0.1
  breakout_accept_bars: int = 2
  breakout_max_age_bars: int = 6
  map_max_per_side: int = 4
  map_major_score: float = 12.0
  map_max_touches: int = 2
  map_min_zone_score: float = 6.0
  map_min_level_touches: int = 4
  map_max_distance_atr: float = 15.0
  map_band_max_atr: float = 2.0
  map_min_per_side: int = 2
  map_fallback_radius: float = 30.0
  map_scalp_radius: float = 15.0
  map_change_min: float = 1.0
  map_session_send: bool = True
  map_scan_interval_minutes: int = 60
  allow_counter_trend: bool = True
  counter_min_zone_score: float = 10.0
  counter_extreme_pd: float = 0.25
  counter_level_min_touches: int = 3
  range_scalp_enabled: bool = True
  range_scalp_lookback: int = 48
  range_scalp_cluster_atr: float = 0.25
  range_scalp_min_touches: int = 2
  range_scalp_min_wick_frac: float = 0.25
  range_scalp_entry_tol_atr: float = 0.25
  range_scalp_min_width_atr: float = 1.0
  range_scalp_max_width_atr: float = 6.0
  range_scalp_min_room_atr: float = 0.75
  range_scalp_break_closes: int = 2
  range_scalp_min_wick_rejections: int = 1
  range_scalp_allow_rejection_only: bool = True
  auto_trade_enabled: bool = False
  auto_trade_dry_run: bool = True
  auto_trade_profile: str = "conservative"
  # Structural guards are quality policy, not broker-safety checks.  Resolve
  # once here so every worker route observes the same profile semantics.
  auto_trade_structural_guard_mode: str = "balanced"
  auto_trade_require_demo_account: bool = True
  auto_trade_allow_concurrent_strategies: bool = False
  auto_trade_allow_hedged_xau: bool = False
  auto_trade_require_flat_for_range: bool = True
  auto_trade_range_two_sided_enabled: bool = False
  auto_trade_range_flip_enabled: bool = False
  auto_trade_range_enabled: bool = True
  auto_trade_multi_match_enabled: bool = False
  auto_trade_track_all_structural_matches: bool = False
  auto_trade_breakout_enabled: bool = True
  auto_trade_retest_enabled: bool = True
  auto_trade_reaction_enabled: bool = True
  auto_trade_liquidity_reversal_enabled: bool = True
  auto_trade_allow_counter_bias: bool = True
  auto_trade_candidate_contract_version: int = 6
  auto_trade_canonical_symbol: str = "XAU"
  auto_trade_sl_distance: float = 6.5
  auto_trade_add_stop_buffer_atr: float = 0.3
  auto_trade_stop_push_beyond_zone: bool = True
  auto_trade_add_min_stop_pips: int = 30
  auto_trade_max_tranches: int = Field(
    default=2,
    validation_alias=AliasChoices("AUTO_TRADE_MAX_TRANCHES"),
  )
  auto_trade_add_risk_fraction: float = Field(
    default=0.5,
    validation_alias=AliasChoices("AUTO_TRADE_ADD_RISK_FRACTION"),
  )
  auto_trade_add_size_ratio: float = Field(
    default=0.5,
    validation_alias=AliasChoices("AUTO_TRADE_ADD_SIZE_RATIO"),
  )
  auto_trade_wick_stop_buffer_atr: float = 0.15
  auto_trade_trend_stop_min_pips: int = 40
  auto_trade_trend_stop_max_pips: int = 60
  # Deprecated for the zone-scale owner path: reaction families now share
  # the trend 40–60 group envelope. Keys remain so older env files still
  # parse; stop_bounds_for_strategy ignores them for Key Level / Demand /
  # Supply / Session / Trendline Reaction.
  auto_trade_reaction_stop_min_pips: int = 40
  auto_trade_reaction_stop_max_pips: int = 60
  auto_trade_sizing_mode: str = Field(
    default="equity_table",
    validation_alias=AliasChoices("AUTO_TRADE_SIZING_MODE"),
  )
  auto_trade_equity_table_version: str = Field(
    default="owner_equity_v1",
    validation_alias=AliasChoices("AUTO_TRADE_EQUITY_TABLE_VERSION"),
  )
  auto_trade_zone_scale_undersized_policy: str = Field(
    default="single_entry",
    validation_alias=AliasChoices(
      "AUTO_TRADE_ZONE_SCALE_UNDERSIZED_POLICY",
    ),
  )
  auto_trade_group_close_allocation: str = Field(
    default="pro_rata",
    validation_alias=AliasChoices("AUTO_TRADE_GROUP_CLOSE_ALLOCATION"),
  )
  auto_trade_unfilled_leg_after_tp_policy: str = Field(
    default="cancel",
    validation_alias=AliasChoices(
      "AUTO_TRADE_UNFILLED_LEG_AFTER_TP_POLICY",
    ),
  )
  auto_trade_xau_price_digits: int = 2
  auto_trade_xau_pip_size: float = Field(
    default=0.1,
    validation_alias=AliasChoices(
      "AUTO_TRADE_XAU_PIP_SIZE",
      "AUTO_TRADE_PIP_SIZE",
    ),
  )
  auto_trade_contract_size: float = Field(
    default=100.0,
    validation_alias=AliasChoices(
      "AUTO_TRADE_XAU_CONTRACT_SIZE",
      "AUTO_TRADE_CONTRACT_SIZE",
    ),
  )
  auto_trade_symbols: str = "XAU"
  auto_trade_spot_max_age: int = Field(
    default=5,
    validation_alias=AliasChoices(
      "AUTO_TRADE_SPOT_MAX_AGE_SECONDS",
      "AUTO_TRADE_SPOT_MAX_AGE",
    ),
  )
  auto_trade_stream: str = Field(
    default="auto_trade:candidates",
    validation_alias=AliasChoices(
      "AUTO_TRADE_CANDIDATE_STREAM",
      "AUTO_TRADE_STREAM",
    ),
  )
  auto_trade_event_stream: str = "auto_trade:events"
  auto_trade_stream_maxlen: int = 1000
  # Cross-service contract handshake. See docs/adr-trade-plan-v7-boundary.md.
  # "v7_only" is the sole autonomous contract - algo-bot publishes only
  # TradePlan V7 (never a V6 TradeCandidate) for autonomous order creation,
  # unconditionally. The field stays a string (not a bool) so Python and
  # C# keep comparing an explicit, fatal-on-mismatch handshake value rather
  # than an implicit default; manual /algo candidates and open V6 position
  # management are unaffected by this value.
  auto_trade_contract_mode: str = Field(
    default="v7_only",
    validation_alias=AliasChoices("AUTO_TRADE_CONTRACT_MODE"),
  )
  auto_trade_trade_plan_stream: str = Field(
    default="execution:trade_plans",
    validation_alias=AliasChoices("AUTO_TRADE_TRADE_PLAN_STREAM"),
  )
  auto_trade_candidate_ttl: int = Field(
    default=86400,
    validation_alias=AliasChoices(
      "AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS",
      "AUTO_TRADE_CANDIDATE_TTL",
    ),
  )
  auto_trade_candidate_max_age_seconds: int = Field(
    default=90,
    validation_alias=AliasChoices(
      "AUTO_TRADE_CANDIDATE_MAX_AGE_SECONDS",
      "AUTO_TRADE_CANDIDATE_MAX_AGE",
    ),
  )
  auto_trade_min_confluence: int = 2
  # Mechanical last-mile anti-chase ceiling. Strategy authorization uses the
  # scanner entry zone plus AUTO_TRADE_ENTRY_CONTRACT_TOLERANCE_PIPS.
  auto_trade_max_entry_distance_pips: float = 40.0
  auto_trade_entry_contract_tolerance_pips: float = Field(
    default=3.0,
    validation_alias=AliasChoices(
      "AUTO_TRADE_ENTRY_CONTRACT_TOLERANCE_PIPS",
    ),
  )
  auto_trade_be_buffer_ticks: int = Field(
    default=6,
    validation_alias=AliasChoices(
      "AUTO_TRADE_BE_BUFFER_TICKS",
      "AUTO_TRADE_BE_BUFFER_PIPS",  # deprecated: value is tick count, not pips
    ),
  )
  auto_trade_post_fill_target_fallback: str = Field(
    default="fill_relative",
    validation_alias=AliasChoices(
      "AUTO_TRADE_POST_FILL_TARGET_FALLBACK",
    ),
  )
  auto_trade_news_guard_minutes: int = 30
  auto_trade_box_retire_seconds: int = 14400
  auto_trade_tp_pips: str = Field(
    default="30,60,90,120,200",
    validation_alias=AliasChoices(
      "AUTO_TRADE_TARGET_PLANS_PIPS",
      "AUTO_TRADE_TP_PIPS",
    ),
  )
  auto_trade_zone_fill_enabled: bool = False
  auto_trade_zone_fill_min_atr: float = Field(
    default=0.5,
    validation_alias=AliasChoices(
      "AUTO_TRADE_ZONE_FILL_MIN_ATR",
    ),
  )
  # DCA-into-zone scale ladder (owner spec): leg 1 fills at the zone's
  # proximal edge with this fraction of volume; the remainder only fills at
  # a further, momentum-confirmed price scale_step_atr*ATR deeper into the
  # zone (a real resting limit order - it only fills if price actually
  # travels there, so "momentum confirmed" needs no separate live check).
  # Only takes effect when the strategy's execution policy prefers "limit"
  # and the zone qualifies for zone_split (auto_trade_zone_fill_enabled +
  # auto_trade_zone_fill_min_atr); narrower zones fall back to a single
  # entry at the computed price, same as before.
  auto_trade_zone_scale_first_leg_fraction: float = Field(
    default=0.70,
    validation_alias=AliasChoices(
      "AUTO_TRADE_ZONE_SCALE_FIRST_LEG_FRACTION",
    ),
  )
  auto_trade_zone_scale_step_atr: float = Field(
    default=0.5,
    validation_alias=AliasChoices("AUTO_TRADE_ZONE_SCALE_STEP_ATR"),
  )
  auto_trade_non_hedged_opposite_policy: str = "reject"
  # Scanner detectors already own the complete strategy match.  The bridge
  # transports that typed decision to the executor without another regime or
  # timeframe confirmation layer.  The legacy aliases keep existing VPS envs
  # readable while deployments move to the accurate names.
  auto_trade_strategy_match_enabled: bool = Field(
    default=True,
    validation_alias=AliasChoices(
      "AUTO_TRADE_STRATEGY_MATCH_ENABLED",
      "AUTO_TRADE_STRATEGY_BRIDGE_ENABLED",
      "AUTO_TRADE_FORMING_GATE_ENABLED",
    ),
  )
  auto_trade_strategy_match_max_age_seconds: int = Field(
    default=420,
    validation_alias=AliasChoices(
      "AUTO_TRADE_STRATEGY_MATCH_MAX_AGE_SECONDS",
      "AUTO_TRADE_FORMING_MAX_AGE_SECONDS",
    ),
  )
  # refactor/p0-direct-zone-signal-execution: a match that is already
  # execution-eligible the instant the scanner confirms it (quote already
  # inside the entry zone, or an authoritative M5 reaction) is evaluated and
  # published synchronously in the scanner's own confirmation cycle instead
  # of being handed off through auto_trade:strategy_match_ready and waiting
  # for a separate worker consumer tick. A match that is NOT yet executable
  # still falls back to the durable ready-stream queue for retest/M1-trigger
  # waiting. Kept as a settings flag (default on) so a rollout can disable
  # the direct path without a code change if it needs to.
  auto_trade_direct_publish_enabled: bool = Field(
    default=True,
    validation_alias=AliasChoices("AUTO_TRADE_DIRECT_PUBLISH_ENABLED"),
  )
  # Executes only structural Market Map zones (never display-only round-number
  # fallbacks) after the latest M1 candle touches and rejects the zone.
  auto_trade_mapped_zone_enabled: bool = Field(
    default=True,
    validation_alias=AliasChoices(
      "AUTO_TRADE_MAPPED_ZONE_ENABLED",
      "AUTO_TRADE_MARKET_MAP_STRATEGY_ENABLED",
    ),
  )
  # Independent context guard. When omitted it follows mapped-zone execution,
  # making an explicitly disabled Market Map execution route neutral.
  auto_trade_market_map_guard_enabled: bool = True
  # Tracking vs execution reach for mapped reactions. Zones inside the track
  # window are reported as the working target; only the execute window may
  # produce an immediate market entry after M1 touch + rejection.
  auto_trade_map_track_distance_atr: float = 8.0
  auto_trade_map_execute_distance_atr: float = 1.5
  # How many closed M1 bars to search for touch → rejection/reclaim memory.
  # The latest bar does not need to be the touch bar.
  auto_trade_map_reaction_lookback_bars: int = Field(
    default=5,
    validation_alias=AliasChoices(
      "AUTO_TRADE_MAP_REACTION_LOOKBACK_BARS",
    ),
  )
  # Rearm a mapped zone only after the prior reaction group is terminal and
  # price has left then re-touched with a newer confirmation.
  auto_trade_map_reaction_rearm_bars: int = 3
  auto_trade_map_reaction_rearm_atr: float = 0.50
  # One active initial group per mapped-zone thesis (independent of reaction_id).
  # Default true; disabling must be explicit and produces a config warning.
  auto_trade_map_thesis_lock_enabled: bool = Field(
    default=True,
    validation_alias=AliasChoices(
      "AUTO_TRADE_MAP_THESIS_LOCK_ENABLED",
    ),
  )
  # Confluence-merge zone (P3): co-located structures (key level, demand/
  # supply, order block, FVG, breaker) within this gap or overlapping merge
  # into one ConfluenceZone, capped at this total width - one zone, one
  # order, instead of several strategies each ordering on the same band.
  zone_merge_max_width: float = 6.0
  zone_merge_gap: float = 1.0
  # refactor/p0-direct-zone-signal-execution: explicit XAU zone-width
  # contract, expressed in actual XAU price units (never pips/digits). A
  # normal tradable zone must land in [MIN_WIDTH, MAJOR_MAX_WIDTH]; anything
  # tighter is rejected as too narrow (a 0.5-price isolated level, not a
  # tradable band) rather than artificially stretched, and anything wider
  # than the major-zone ceiling is either trimmed to real structural
  # boundaries or rejected as too broad. PREFERRED_MIN/MAX describe the
  # sweet spot for a normal (non-major-H1) zone; MAJOR_MAX_WIDTH gives H1
  # zones a wider validated ceiling instead of forcing the same cap onto
  # every timeframe.
  xau_zone_min_width_price: float = Field(
    default=3.0,
    validation_alias=AliasChoices("XAU_ZONE_MIN_WIDTH_PRICE"),
  )
  xau_zone_preferred_min_width_price: float = Field(
    default=3.0,
    validation_alias=AliasChoices("XAU_ZONE_PREFERRED_MIN_WIDTH_PRICE"),
  )
  xau_zone_preferred_max_width_price: float = Field(
    default=6.0,
    validation_alias=AliasChoices("XAU_ZONE_PREFERRED_MAX_WIDTH_PRICE"),
  )
  xau_major_zone_max_width_price: float = Field(
    default=10.0,
    validation_alias=AliasChoices("XAU_MAJOR_ZONE_MAX_WIDTH_PRICE"),
  )
  # refactor/p0-direct-zone-signal-execution: enforce the XAU zone-width
  # contract (xau_zone_min_width_price / xau_major_zone_max_width_price) as
  # a hard reject on the scanner's confluence-merge path. Off by default -
  # the width contract itself (validate_zone_width in confluence_zone.py)
  # is fully implemented and unit-tested independent of this flag; rollout
  # of the live scanner gate is deliberately staged behind this switch so
  # existing zone-width telemetry can be audited before any zone starts
  # being dropped for width in production.
  scanner_zone_width_gate_enabled: bool = Field(
    default=False,
    validation_alias=AliasChoices("SCANNER_ZONE_WIDTH_GATE_ENABLED"),
  )
  # Actionability remains observable by default. Only a genuine overlapping
  # BUY/SELL conflict is always hard; operators may re-enable the broader
  # #140 policy and key-level ambiguity drops independently.
  scanner_actionability_gate_enabled: bool = False
  key_level_role_ambiguity_gate_enabled: bool = False
  range_context_disagreement_gate_enabled: bool = False
  # M1 candlestick patterns are optional timing evidence for an already
  # formed setup. A fresh pattern can anchor the stop wick, but distance alone
  # authorizes publication.
  m1_trigger_patterns: str = (
    "wick_rejection,body_close,strong_close,pin_bar,engulfing,hammer"
  )
  m1_trigger_wick_fraction: float = 0.5
  m1_trigger_strong_close_pct: float = 0.2
  # A retest trigger may authorize execution only for this many subsequent
  # M1 bars. It never carries across a zone exit/re-entry episode.
  auto_trade_retest_trigger_validity_bars: int = Field(
    default=2,
    validation_alias=AliasChoices(
      "AUTO_TRADE_RETEST_TRIGGER_VALIDITY_BARS",
    ),
  )
  # One forming card per setup (P4): lifecycle updates thread as replies to
  # it, and it is deleted (never a "rejected" message) on terminal.
  delivery_thread_lifecycle: bool = True
  delivery_delete_on_terminal: bool = True
  auto_trade_key_level_reaction_enabled: bool = Field(
    default=True,
    validation_alias=AliasChoices("AUTO_TRADE_KEY_LEVEL_REACTION_ENABLED"),
  )
  auto_trade_demand_reaction_enabled: bool = Field(
    default=True,
    validation_alias=AliasChoices("AUTO_TRADE_DEMAND_REACTION_ENABLED"),
  )
  auto_trade_supply_reaction_enabled: bool = Field(
    default=True,
    validation_alias=AliasChoices("AUTO_TRADE_SUPPLY_REACTION_ENABLED"),
  )
  auto_trade_session_level_reaction_enabled: bool = Field(
    default=True,
    validation_alias=AliasChoices("AUTO_TRADE_SESSION_LEVEL_REACTION_ENABLED"),
  )
  auto_trade_trendline_reaction_enabled: bool = Field(
    default=True,
    validation_alias=AliasChoices("AUTO_TRADE_TRENDLINE_REACTION_ENABLED"),
  )
  # Recovery mission (2026-07-30): live around 2026-07-28, dropped from
  # DEFAULT_DETECTORS during the P0 zone/M1 simplification with no
  # individual enable flag of their own. Registered in
  # detectors.LIVE_DETECTOR_REGISTRY with an explicit replay-only reason;
  # default False until each is individually re-verified against the
  # current band-kind/canonical-family pipeline (see
  # DetectorSettings.box_breakout_enabled et al. for why).
  #
  # 2026-07-31: trend_pullback/snap_back/fade_scalp retrofitted onto the
  # shared evaluate_structural_reaction confirmation path and re-enabled
  # (see DetectorSettings for the full reasoning). box_breakout/
  # break_retest/momentum_ride remain replay-only.
  auto_trade_box_breakout_enabled: bool = Field(
    default=False,
    validation_alias=AliasChoices("AUTO_TRADE_BOX_BREAKOUT_ENABLED"),
  )
  auto_trade_trend_pullback_enabled: bool = Field(
    default=True,
    validation_alias=AliasChoices("AUTO_TRADE_TREND_PULLBACK_ENABLED"),
  )
  auto_trade_break_retest_enabled: bool = Field(
    default=False,
    validation_alias=AliasChoices("AUTO_TRADE_BREAK_RETEST_ENABLED"),
  )
  auto_trade_momentum_ride_enabled: bool = Field(
    default=False,
    validation_alias=AliasChoices("AUTO_TRADE_MOMENTUM_RIDE_ENABLED"),
  )
  auto_trade_snap_back_enabled: bool = Field(
    default=True,
    validation_alias=AliasChoices("AUTO_TRADE_SNAP_BACK_ENABLED"),
  )
  auto_trade_fade_scalp_enabled: bool = Field(
    default=True,
    validation_alias=AliasChoices("AUTO_TRADE_FADE_SCALP_ENABLED"),
  )
  auto_trade_structural_reaction_lookback_bars: int = Field(
    default=3,
    validation_alias=AliasChoices(
      "AUTO_TRADE_STRUCTURAL_REACTION_LOOKBACK_BARS",
    ),
  )
  # Reject collapsed map geometry before it can become the nearest target.
  # Both thresholds apply; the effective minimum is their maximum.
  auto_trade_map_zone_min_width_atr: float = 0.15
  auto_trade_map_zone_min_width_abs: float = 1.0
  # Raw zones wider than either limit remain useful as context, but are not
  # execution barriers, stop containers, or target obstructions.
  auto_trade_execution_zone_max_width_atr: float = 2.0
  auto_trade_execution_zone_max_width_pips: float = 100.0
  # Counter-bias mean reversion is quality-gated independently of HTF-aligned
  # mapped reactions and enabled by default.
  auto_trade_map_counter_bias_enabled: bool = True
  auto_trade_map_counter_bias_min_score: float = 6.0
  auto_trade_map_counter_bias_min_confluence: int = 2
  # Trade-quality guards added after the 22 Jul 2026 incident (SELL filled at
  # box EQ, 13 pips below the nearest published supply zone). EQ exclusion and
  # edge proximity apply to box-scalp ("auto_box_scalp") candidates only - a
  # breakout/trend-continuation candidate legitimately transits the mid-range.
  auto_trade_eq_exclusion_fraction: float = 0.15
  auto_trade_edge_proximity_atr: float = 0.5
  # HTF supply/demand veto (worker.py only - gate.py/trend.py stay untouched).
  # Kill switch so the veto can be disabled without a redeploy if too strict.
  auto_trade_htf_veto_enabled: bool = True
  # Opposing-barrier veto added after the 22 Jul incident where a strategy-
  # match BUY filled straight into an untested round-number supply level with
  # no check at all (unlike the box-scalp/trend paths, which only check the
  # zone a trade retests *from*, not what could cap the move ahead of it).
  # Separate kill switch from auto_trade_htf_veto_enabled so either check can
  # be disabled independently if it proves too strict.
  auto_trade_opposing_barrier_veto_enabled: bool = True
  auto_trade_opposing_barrier_atr: float = 0.5
  # 2026-07-31: three same-evening incidents (15.18/15.2/19.9 pips of real
  # buffered room, all rejected outright for falling short of the smallest
  # *configured* target, 30 pips). A genuine minimum-viability floor,
  # independent of the configured ladder - when nothing on the ladder fits
  # but real room still clears this, the trade is allowed with its own
  # (smaller) achievable room as the target instead of being thrown away.
  # 0 restores the old all-or-nothing behavior.
  auto_trade_min_capped_target_pips: float = 15.0
  # An opposing barrier's own classification (e.g. an H1 breaker/flip) can
  # lag real price by up to a full HTF bar - the barrier can already be
  # decisively closed-through on the execution timeframe while it still
  # shows up here as unbroken. When the most recent N closed execution-tf
  # bars include a confirmed close beyond the barrier in the candidate's
  # own direction, that barrier is excluded from the opposing-room check
  # entirely rather than hard-blocking on a barrier price has already
  # broken. 0 disables the override (barriers are never excluded this way).
  auto_trade_displacement_override_lookback_bars: int = 3
  # Post-stop-out cooldown (23 Jul 2026 incident: a stopped-out zone was
  # re-entered same-direction 15 minutes later at essentially the same
  # price). The TTL itself lives on the C# side (AUTO_TRADE_ZONE_COOLDOWN_
  # MINUTES, AutoTradeOptions.cs) since only the engine knows when a
  # position closed; worker.py only needs the ATR band for the veto check.
  auto_trade_zone_cooldown_enabled: bool = True
  auto_trade_zone_cooldown_atr: float = 1.0
  # Overlapping opposing Market Map zones (23 Jul incident: published SELL
  # 4,116-4,127 and BUY 4,112-4,122 overlapped 4,116-4,122; the fill landed
  # inside it). Trade-time veto only - Market Map output/zones.py untouched.
  auto_trade_overlap_veto_enabled: bool = True
  # Reconciles overlapping supply/demand zones at the analysis source
  # (zones.py::reconcile_opposing) rather than only vetoing trades against
  # them. Kill switch so reconciliation can be disabled without a redeploy
  # if it trims a zone the strategy actually needed.
  auto_trade_zone_reconcile_enabled: bool = True
  # off = retain original zones; shadow = compute/measure reconciliation but
  # feed original zones to strategies; enforce = use reconciled zones.
  auto_trade_zone_reconcile_mode: str = "enforce"
  # Adaptive range-scalp target ladder (app/autotrade/range_targets.py) - the
  # single source of truth for turning available room into a take-profit
  # target. Previously hardcoded to {50,70} independently in four Python
  # modules and once in the C# executor; any setup with 0-49 pips of room
  # (the common case per the 23 Jul 09:00/11:00 incidents) silently produced
  # no executable candidate. C# must read this same env var - see
  # AutoTradeOptions.RangeTargetsPips.
  auto_trade_range_targets_pips: str = "20,30,40,50,70"
  auto_trade_range_box_scale_out_enabled: bool = True
  auto_trade_range_box_scale_out_threshold_pips: int = 70
  auto_trade_range_box_scale_out_trigger_pips: int = 30
  auto_trade_range_box_scale_out_fraction: float = 0.50
  auto_trade_range_box_move_sl_to_be_after_scale_out: bool = False
  auto_trade_range_tp_buffer_pips: float = 3.0
  auto_trade_range_min_target_pips: float = 20.0
  auto_trade_range_min_rr: float = 1.00
  # Structure-aware barrier / range controls.
  scalp_barrier_fallback_enabled: bool = True
  scalp_barrier_fallback_min_confirmations: int = 1
  scalp_range_provisional_enabled: bool = True
  scalp_post_impulse_range_enabled: bool = True
  range_scalp_min_inside_closes: int = 3
  range_scalp_max_edge_width_atr: float = 0.75
  range_scalp_cluster_min_abs: float = 0.0
  # Multi-strategy routing.
  scanner_top_n: int = 3
  # Advisory FORMING-card controls are independent from the execution digest.
  scanner_card_top_n: int = 2
  # Optional M5/M15 structure-quality gate. All filters ship disabled.
  scanner_gate_require_structural_anchor: bool = False
  scanner_gate_max_source_touches: int = 0
  scanner_gate_suppress_counter_bias_in_range: bool = False
  scanner_gate_counter_bias_min_confluence: int = 3
  auto_trade_max_tracked_candidates: int = 5
  auto_trade_max_active_positions_per_symbol: int = 1
  # Quality / risk tiers.
  auto_trade_tier_a_risk_multiplier: float = 1.0
  auto_trade_tier_b_risk_multiplier: float = 0.5
  auto_trade_post_impulse_risk_multiplier: float = 0.5
  auto_trade_one_sided_range_risk_multiplier: float = 0.5
  # Map execute tolerance + strategy-aware drift.
  auto_trade_map_execute_tolerance_pips: float = 3.0
  auto_trade_map_execute_tolerance_atr: float = 0.15
  auto_trade_range_max_entry_drift_atr: float = 0.35
  auto_trade_trend_max_entry_drift_atr: float = 0.85
  auto_trade_map_max_entry_drift_atr: float = 0.40
  auto_trade_range_min_entry_drift_pips: float = 10.0
  auto_trade_map_min_entry_drift_pips: float = 10.0
  auto_trade_trend_min_entry_drift_pips: float = 15.0
  auto_trade_range_hard_entry_drift_pips: float = 20.0
  auto_trade_map_hard_entry_drift_pips: float = 20.0
  auto_trade_trend_hard_entry_drift_pips: float = 30.0
  # Zone-fill geometry fallback (mirrored on C# AutoTradeOptions).
  auto_trade_zone_fill_fallback_enabled: bool = True
  auto_trade_inside_zone_market_entry_enabled: bool = True
  # Directional override for chop→trend. Height/containment stay as the
  # primary chop tests; when enabled, a staircase of LH/LL or HH/HL pairs
  # with enough net ATR displacement reclassifies as trend. Ships dark —
  # run regime_compare for 48h before enabling.
  auto_trade_regime_direction_enabled: bool = False
  auto_trade_regime_direction_lookback: int = 120
  auto_trade_regime_min_directional_swings: int = 3
  auto_trade_regime_min_displacement_atr: float = 4.0
  # Trend/breakout regime classifier (app/autotrade/trend.py). Named with a
  # trend_/auto_trade_trend_ prefix to avoid colliding with the existing
  # scanner-owned breakout_accept_bars/breakout_max_age_bars fields above,
  # which feed a different pipeline (app.analysis.regime.accepted_box_break
  # via detectors.py) and must keep their own tuning independent of this
  # feature.
  trend_min_bos: int = 2
  trend_min_height_atr: float = 3.0
  trend_atr_expansion: float = 1.15
  trend_atr_baseline_bars: int = 48
  trend_allow_chase: bool = False
  trend_level_buffer_atr: float = 1.0
  tp_min_spacing_atr: float = 0.5
  # How many M1 bars a box break stays eligible for breakout-mode entry
  # before it's considered stale; how many consecutive closes beyond the
  # edge count as "accepted" absent a displacement-grade candle. Both are
  # initial/tunable starting values, not established facts.
  trend_breakout_max_age_bars: int = 5
  trend_breakout_accept_bars: int = 2
  trend_breakout_min_room_pips: int = 35
  reaction_max_atr: float = 0.5
  regime_chop_alert_share: float = 0.75
  auto_trade_trend_enabled: bool = False  # kill switch — default OFF

  # `/ algo` DM suffix on a manual signal — owner opt-in per signal to also
  # arm cTrader broker-side execution using the owner's exact entered SL/TP,
  # instead of staying notify-only. Entirely independent of the AUTO_TRADE_*
  # box-scalp/trend engine flags above (different signal source, different
  # stop policy). Ships dark: no broker consumes manual_trade_intent_stream
  # yet, this only builds and publishes the contract.
  manual_algo_enabled: bool = False
  manual_algo_dry_run: bool = True
  manual_algo_risk_pct: float = 2.0
  # Owner-only debug DMs ("LIMIT ORDER PLACED"/"POSITION OPENED") duplicate
  # the real VIP/public channel update trade_ops.post_result already posts
  # for the same fill - the executor mechanically monitors/enters the
  # resting order on its own, so these are noise, not signal. Off by
  # default; flip on if the owner ever wants the raw executor-truth pings
  # back without a code change.
  manual_algo_owner_execution_dm_enabled: bool = False
  manual_trade_intent_stream: str = "manual_trade:intents"
  manual_trade_intent_stream_maxlen: int = 1000
  # Consumed by this PR's bridge/reconcile loops (app.signals.manual_execution)
  # and by ctrader-engine's owner-override command poll. The stream name must
  # match AutoTradeEngine.cs's hardcoded ManualCommandStream constant — it is
  # not itself wired through AUTO_TRADE_* options on the C# side.
  manual_trade_command_stream: str = "manual_trade:commands"
  manual_trade_command_stream_maxlen: int = 1000

  @model_validator(mode="after")
  def _resolve_auto_trade_profile(self):
    profile = self.auto_trade_profile.strip().lower()
    if profile not in {"conservative", "demo_eval"}:
      raise ValueError(
        "AUTO_TRADE_PROFILE must be conservative or demo_eval"
      )
    self.auto_trade_profile = profile
    if (
      profile == "demo_eval"
      and
      "auto_trade_require_demo_account" in self.model_fields_set
      and not self.auto_trade_require_demo_account
    ):
      raise ValueError(
        "AUTO_TRADE_PROFILE=demo_eval requires "
        "AUTO_TRADE_REQUIRE_DEMO_ACCOUNT=true"
      )
    explicitly_set = set(self.model_fields_set)
    demo_defaults = {
      "auto_trade_enabled": True,
      "auto_trade_dry_run": False,
      "auto_trade_require_demo_account": True,
      "auto_trade_allow_concurrent_strategies": True,
      "auto_trade_allow_hedged_xau": True,
      "auto_trade_require_flat_for_range": False,
      "auto_trade_range_two_sided_enabled": True,
      "auto_trade_range_flip_enabled": True,
      "auto_trade_range_enabled": True,
      "auto_trade_multi_match_enabled": True,
      "auto_trade_track_all_structural_matches": True,
      "auto_trade_trend_enabled": True,
      "auto_trade_mapped_zone_enabled": True,
      "auto_trade_market_map_guard_enabled": True,
      "auto_trade_strategy_match_enabled": True,
      "auto_trade_breakout_enabled": True,
      "auto_trade_retest_enabled": True,
      "auto_trade_reaction_enabled": True,
      "auto_trade_key_level_reaction_enabled": True,
      "auto_trade_demand_reaction_enabled": True,
      "auto_trade_supply_reaction_enabled": True,
      "auto_trade_session_level_reaction_enabled": True,
      "auto_trade_trendline_reaction_enabled": True,
      "auto_trade_structural_reaction_lookback_bars": 3,
      "auto_trade_liquidity_reversal_enabled": True,
      "auto_trade_allow_counter_bias": True,
      "auto_trade_map_counter_bias_enabled": True,
      "auto_trade_zone_fill_enabled": True,
      "auto_trade_structural_guard_mode": "observe",
      "auto_trade_opposing_barrier_veto_enabled": False,
      "auto_trade_overlap_veto_enabled": False,
      "auto_trade_zone_cooldown_enabled": False,
      "auto_trade_zone_reconcile_mode": "shadow",
      "auto_trade_range_min_entry_drift_pips": 10.0,
      "auto_trade_map_min_entry_drift_pips": 10.0,
      "auto_trade_trend_min_entry_drift_pips": 15.0,
      "auto_trade_range_max_entry_drift_atr": 1.0,
      "auto_trade_map_max_entry_drift_atr": 1.0,
      "auto_trade_trend_max_entry_drift_atr": 1.5,
      "auto_trade_range_hard_entry_drift_pips": 20.0,
      "auto_trade_map_hard_entry_drift_pips": 20.0,
      "auto_trade_trend_hard_entry_drift_pips": 30.0,
      "auto_trade_candidate_max_age_seconds": 420,
      "auto_trade_candidate_ttl": 604800,
      "auto_trade_non_hedged_opposite_policy": "broker_netting",
      "auto_trade_max_tracked_candidates": 0,
      "auto_trade_max_active_positions_per_symbol": 0,
      "scanner_top_n": 0,
    }
    if profile == "demo_eval":
      for field_name, value in demo_defaults.items():
        if field_name not in explicitly_set:
          setattr(self, field_name, value)
    elif (
      not self.auto_trade_require_demo_account
      and "auto_trade_structural_guard_mode" not in explicitly_set
    ):
      self.auto_trade_structural_guard_mode = "strict"
    if "auto_trade_market_map_guard_enabled" not in explicitly_set:
      self.auto_trade_market_map_guard_enabled = (
        self.auto_trade_mapped_zone_enabled
      )
    self.auto_trade_structural_guard_mode = (
      self.auto_trade_structural_guard_mode.strip().lower()
    )
    if self.auto_trade_structural_guard_mode not in {
      "observe",
      "balanced",
      "strict",
    }:
      raise ValueError(
        "AUTO_TRADE_STRUCTURAL_GUARD_MODE must be observe, balanced, or strict"
      )
    if int(self.auto_trade_structural_reaction_lookback_bars) < 1:
      raise ValueError(
        "AUTO_TRADE_STRUCTURAL_REACTION_LOOKBACK_BARS must be >= 1"
      )
    if not 1 <= int(self.auto_trade_retest_trigger_validity_bars) <= 5:
      raise ValueError(
        "AUTO_TRADE_RETEST_TRIGGER_VALIDITY_BARS must be between 1 and 5"
      )
    for lookback_name in (
      "xau_lookback_h1_bars",
      "xau_lookback_m15_bars",
      "xau_lookback_m5_bars",
      "xau_lookback_m1_bars",
    ):
      if int(getattr(self, lookback_name)) < 50:
        raise ValueError(
          f"{lookback_name.upper()} must be >= 50 closed bars"
        )
    if not (
      0
      < self.xau_zone_min_width_price
      <= self.xau_zone_preferred_min_width_price
      <= self.xau_zone_preferred_max_width_price
      <= self.xau_major_zone_max_width_price
    ):
      raise ValueError(
        "XAU zone-width settings must satisfy 0 < MIN_WIDTH <= "
        "PREFERRED_MIN_WIDTH <= PREFERRED_MAX_WIDTH <= MAJOR_MAX_WIDTH"
      )
    if self.auto_trade_max_entry_distance_pips <= 0:
      raise ValueError(
        "AUTO_TRADE_MAX_ENTRY_DISTANCE_PIPS must be positive"
      )
    if (
      self.auto_trade_execution_zone_max_width_atr <= 0
      or self.auto_trade_execution_zone_max_width_pips <= 0
    ):
      raise ValueError(
        "AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_ATR and "
        "AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_PIPS must be positive"
      )
    if (
      int(self.auto_trade_range_box_scale_out_threshold_pips) <= 0
      or int(self.auto_trade_range_box_scale_out_trigger_pips) <= 0
      or int(self.auto_trade_range_box_scale_out_trigger_pips)
        >= int(self.auto_trade_range_box_scale_out_threshold_pips)
      or float(self.auto_trade_range_box_scale_out_fraction) <= 0
      or float(self.auto_trade_range_box_scale_out_fraction) >= 1
    ):
      raise ValueError(
        "Range Box scale-out settings invalid: threshold > 0, trigger > 0, "
        "trigger < threshold, and 0 < fraction < 1"
      )
    self.auto_trade_zone_reconcile_mode = (
      self.auto_trade_zone_reconcile_mode.strip().lower()
    )
    if not self.auto_trade_zone_reconcile_enabled:
      self.auto_trade_zone_reconcile_mode = "off"
    if self.auto_trade_zone_reconcile_mode not in {
      "off",
      "shadow",
      "enforce",
    }:
      raise ValueError(
        "AUTO_TRADE_ZONE_RECONCILE_MODE must be off, shadow, or enforce"
      )
    self.auto_trade_non_hedged_opposite_policy = (
      self.auto_trade_non_hedged_opposite_policy.strip().lower()
    )
    if self.auto_trade_non_hedged_opposite_policy not in {
      "broker_netting",
      "close_then_reverse",
      "reject",
    }:
      raise ValueError(
        "AUTO_TRADE_NON_HEDGED_OPPOSITE_POLICY must be "
        "broker_netting, close_then_reverse, or reject"
      )
    ticks_raw = os.environ.get("AUTO_TRADE_BE_BUFFER_TICKS")
    pips_raw = os.environ.get("AUTO_TRADE_BE_BUFFER_PIPS")
    if (
      ticks_raw is not None
      and pips_raw is not None
      and ticks_raw.strip() != pips_raw.strip()
    ):
      raise ValueError(
        "AUTO_TRADE_BE_BUFFER_TICKS and AUTO_TRADE_BE_BUFFER_PIPS conflict; "
        "remove the deprecated PIPS variable or set both to the same tick count"
      )
    if int(self.auto_trade_be_buffer_ticks) < 0 or int(
      self.auto_trade_be_buffer_ticks
    ) >= 1000:
      raise ValueError(
        "AUTO_TRADE_BE_BUFFER_TICKS must be non-negative and below 1000"
      )
    if self.auto_trade_contract_mode != "v7_only":
      raise ValueError(
        "AUTO_TRADE_CONTRACT_MODE must be v7_only - it is the sole "
        "autonomous contract"
      )
    return self

  @property
  def telegram_chat_id(self) -> str:
    """Backward-compatible name for existing deployments and call sites."""
    return str(self.telegram_channel_id)

  @property
  def signal_vip_channel_id(self) -> int:
    return self.telegram_channel_id

  @property
  def xau_vip_channel_id(self) -> int:
    return self.signal_vip_channel_id

  @property
  def xau_public_channel_id(self) -> Optional[int]:
    return self.signal_public_channel_id


settings = Settings()
