from typing import Optional
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
  scanner_htf: str = "M30,M15"
  scanner_telegram_bot_token: Optional[str] = None
  scanner_window: int = 500
  scanner_alert_ttl: int = 7200
  scanner_level_bucket: int = 20
  zone_alert_ttl: int = 14400
  scanner_confluence_floor: int = 2
  alert_overlap_suppress: float = 0.5
  # Opposite-direction, overlapping detections are a contradiction, not a
  # duplicate. Above this overlap ratio, the weaker one only survives if its
  # confluence trails by less than SCANNER_CONFLICT_MARGIN - otherwise both
  # are dropped and the conflict is recorded (see scanner.py::_suppress_overlaps).
  scanner_conflict_overlap: float = 0.5
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
  auto_trade_candidate_contract_version: int = 5
  auto_trade_canonical_symbol: str = "XAU"
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
  auto_trade_max_entry_distance_pips: float = 10.0
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
  auto_trade_non_hedged_opposite_policy: str = "reject"
  # Scanner detectors already own the complete strategy match.  The bridge
  # transports that typed decision to the executor without another regime or
  # timeframe confirmation layer.  The legacy aliases keep existing VPS envs
  # readable while deployments move to the accurate names.
  auto_trade_strategy_bridge_enabled: bool = Field(
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
  # Executes only structural Market Map zones (never display-only round-number
  # fallbacks) after the latest M1 candle touches and rejects the zone.
  auto_trade_market_map_strategy_enabled: bool = Field(
    default=True,
    validation_alias=AliasChoices(
      "AUTO_TRADE_MAPPED_ZONE_ENABLED",
      "AUTO_TRADE_MARKET_MAP_STRATEGY_ENABLED",
    ),
  )
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
  # Reject collapsed map geometry before it can become the nearest target.
  # Both thresholds apply; the effective minimum is their maximum.
  auto_trade_map_zone_min_width_atr: float = 0.15
  auto_trade_map_zone_min_width_abs: float = 1.0
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
  auto_trade_range_tp_buffer_pips: float = 3.0
  auto_trade_range_min_target_pips: float = 20.0
  auto_trade_range_min_rr: float = 1.10
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
      "auto_trade_market_map_strategy_enabled": True,
      "auto_trade_strategy_bridge_enabled": True,
      "auto_trade_breakout_enabled": True,
      "auto_trade_retest_enabled": True,
      "auto_trade_reaction_enabled": True,
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
      explicitly_set = self.model_fields_set
      for field_name, value in demo_defaults.items():
        if field_name not in explicitly_set:
          setattr(self, field_name, value)
    elif (
      not self.auto_trade_require_demo_account
      and "auto_trade_structural_guard_mode" not in self.model_fields_set
    ):
      self.auto_trade_structural_guard_mode = "strict"
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
