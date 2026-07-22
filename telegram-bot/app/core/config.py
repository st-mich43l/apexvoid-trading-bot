from typing import Optional
from pydantic import AliasChoices, Field
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
  track_interval: int = 120
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
  scanner_top_n: int = 1
  alert_overlap_suppress: float = 0.5
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
  auto_trade_symbols: str = "XAU"
  auto_trade_spot_max_age: int = 5
  auto_trade_stream: str = "auto_trade:candidates"
  auto_trade_event_stream: str = "auto_trade:events"
  auto_trade_stream_maxlen: int = 1000
  auto_trade_candidate_ttl: int = 86400
  auto_trade_min_confluence: int = 2
  auto_trade_news_guard_minutes: int = 30
  auto_trade_box_retire_seconds: int = 14400
  # Trade-quality guards added after the 22 Jul 2026 incident (SELL filled at
  # box EQ, 13 pips below the nearest published supply zone). EQ exclusion and
  # edge proximity apply to box-scalp ("auto_box_scalp") candidates only - a
  # breakout/trend-continuation candidate legitimately transits the mid-range.
  auto_trade_eq_exclusion_fraction: float = 0.15
  auto_trade_edge_proximity_atr: float = 0.5
  # HTF supply/demand veto (worker.py only - gate.py/trend.py stay untouched).
  # Kill switch so the veto can be disabled without a redeploy if too strict.
  auto_trade_htf_veto_enabled: bool = True
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
  trend_allow_chase: bool = True
  trend_level_buffer_atr: float = 1.0
  tp_min_spacing_atr: float = 0.5
  # How many M1 bars a box break stays eligible for breakout-mode entry
  # before it's considered stale; how many consecutive closes beyond the
  # edge count as "accepted" absent a displacement-grade candle. Both are
  # initial/tunable starting values, not established facts.
  trend_breakout_max_age_bars: int = 5
  trend_breakout_accept_bars: int = 2
  reaction_max_atr: float = 0.5
  regime_chop_alert_share: float = 0.75
  auto_trade_trend_enabled: bool = False  # kill switch — default OFF

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
