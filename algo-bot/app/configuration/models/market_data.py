"""Complete inactive market-data configuration domain."""

from pydantic import Field

from app.configuration.metadata import ConfigKind
from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import ContextDefault
from app.configuration.metadata import DefaultContext
from app.configuration.metadata import MismatchPolicy
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field
from app.configuration.models.base import FrozenConfigModel


class MarketDataCtraderFeedConfig(FrozenConfigModel):
  backfill_bars: int = config_field(1500,
    item_id='ctrader.env.CTRADER_BACKFILL_BARS',
    legacy_attr=None,
    env='CTRADER_BACKFILL_BARS',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='cTrader runtime option CTRADER_BACKFILL_BARS mapped to market_data.ctrader_feed.backfill_bars.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 1500),
    ),
    validation_summary='FeedOptions.Env',
  )
  bar_quality_lookback_bars: int = config_field(6,
    item_id='ctrader.env.BAR_QUALITY_LOOKBACK',
    legacy_attr=None,
    env='BAR_QUALITY_LOOKBACK',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='cTrader runtime option BAR_QUALITY_LOOKBACK mapped to market_data.ctrader_feed.bar_quality_lookback_bars.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 6),
    ),
    validation_summary='FeedOptions.Env',
  )
  bars_channel: str = config_field('bars:new',
    item_id='ctrader.env.BARS_CHANNEL',
    legacy_attr=None,
    env='BARS_CHANNEL',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.INFRASTRUCTURE,
    shared_with_ctrader=True,
    description='cTrader runtime option BARS_CHANNEL mapped to market_data.ctrader_feed.bars_channel.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'bars:new'),
    ),
    validation_summary='FeedOptions.Env',
  )
  bars_window_max: int = config_field(1500,
    item_id='ctrader.env.BARS_WINDOW_MAX',
    legacy_attr=None,
    env='BARS_WINDOW_MAX',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='cTrader runtime option BARS_WINDOW_MAX mapped to market_data.ctrader_feed.bars_window_max.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 1500),
    ),
    validation_summary='FeedOptions.Env',
  )
  health_file: str = config_field('/tmp/ctrader-feed.heartbeat',
    item_id='ctrader.env.HEALTH_FILE',
    legacy_attr=None,
    env='HEALTH_FILE',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PATH,
    risk=RiskClassification.INFRASTRUCTURE,
    description='cTrader runtime option HEALTH_FILE mapped to market_data.ctrader_feed.health_file.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '/tmp/ctrader-feed.heartbeat'),
    ),
    validation_summary='FeedOptions.Env',
  )
  symbol: str = config_field('XAUUSD',
    item_id='ctrader.env.CTRADER_SYMBOL',
    legacy_attr=None,
    env='CTRADER_SYMBOL',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.IDENTIFIER,
    risk=RiskClassification.INFRASTRUCTURE,
    description='cTrader runtime option CTRADER_SYMBOL mapped to market_data.ctrader_feed.symbol.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'XAUUSD'),
    ),
    validation_summary='FeedOptions.Env',
  )
  timeframes: list[str] = config_field('M1,M5,M15,H1',
    item_id='ctrader.env.CTRADER_TIMEFRAMES',
    legacy_attr=None,
    env='CTRADER_TIMEFRAMES',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ENUM,
    risk=RiskClassification.INFRASTRUCTURE,
    description='cTrader runtime option CTRADER_TIMEFRAMES mapped to market_data.ctrader_feed.timeframes.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'M1,M5,M15,H1'),
    ),
    validation_summary='FeedOptions.Env',
  )


class MarketDataSpotConfig(FrozenConfigModel):
  fresh_secs: int = config_field(30,
    item_id='python.settings.spot_fresh_secs',
    legacy_attr='spot_fresh_secs',
    env='SPOT_FRESH_SECS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy spot_fresh_secs configuration mapped to market_data.spot.fresh_secs.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 30),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  max_deviation_pct: float = config_field(2.0,
    item_id='python.settings.spot_max_deviation_pct',
    legacy_attr='spot_max_deviation_pct',
    env='SPOT_MAX_DEVIATION_PCT',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PERCENT,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy spot_max_deviation_pct configuration mapped to market_data.spot.max_deviation_pct.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 2.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  maximum_age_seconds: int = config_field(5,
    item_id='python.settings.auto_trade_spot_max_age',
    legacy_attr='auto_trade_spot_max_age',
    env='AUTO_TRADE_SPOT_MAX_AGE_SECONDS',
    aliases=('AUTO_TRADE_SPOT_MAX_AGE',),
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.FATAL,
    description='Legacy auto_trade_spot_max_age configuration mapped to market_data.spot.maximum_age_seconds.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 5),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 5),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Int + AutoTradeOptions.Validate',
  )


class MarketDataCalendarConfig(FrozenConfigModel):
  currencies: str = config_field('USD',
    item_id='python.settings.calendar_currencies',
    legacy_attr='calendar_currencies',
    env='CALENDAR_CURRENCIES',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy calendar_currencies configuration mapped to market_data.calendar.currencies.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'USD'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  enabled: bool = config_field(True,
    item_id='python.settings.calendar_enabled',
    legacy_attr='calendar_enabled',
    env='CALENDAR_ENABLED',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy calendar_enabled configuration mapped to market_data.calendar.enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  event_guard_hours: float = config_field(4.0,
    item_id='python.settings.event_guard_hours',
    legacy_attr='event_guard_hours',
    env='EVENT_GUARD_HOURS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.HOURS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy event_guard_hours configuration mapped to market_data.calendar.event_guard_hours.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 4.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  feed_nextweek: str = config_field('https://nfs.faireconomy.media/ff_calendar_nextweek.json',
    item_id='python.settings.calendar_feed_nextweek',
    legacy_attr='calendar_feed_nextweek',
    env='CALENDAR_FEED_NEXTWEEK',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.URL,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy calendar_feed_nextweek configuration mapped to market_data.calendar.feed_nextweek.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'https://nfs.faireconomy.media/ff_calendar_nextweek.json'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  feed_thisweek: str = config_field('https://nfs.faireconomy.media/ff_calendar_thisweek.json',
    item_id='python.settings.calendar_feed_thisweek',
    legacy_attr='calendar_feed_thisweek',
    env='CALENDAR_FEED_THISWEEK',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.URL,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy calendar_feed_thisweek configuration mapped to market_data.calendar.feed_thisweek.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  news_brief_hour: int = config_field(7,
    item_id='python.settings.news_brief_hour',
    legacy_attr='news_brief_hour',
    env='NEWS_BRIEF_HOUR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.UTC_HOUR,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy news_brief_hour configuration mapped to market_data.calendar.news_brief_hour.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 7),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  news_guard_block: bool = config_field(False,
    item_id='python.settings.news_guard_block',
    legacy_attr='news_guard_block',
    env='NEWS_GUARD_BLOCK',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy news_guard_block configuration mapped to market_data.calendar.news_guard_block.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  oil_keywords: str = config_field('crude oil inventories,opec,cushing,api weekly crude',
    item_id='python.settings.oil_keywords',
    legacy_attr='oil_keywords',
    env='OIL_KEYWORDS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.IDENTIFIER,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy oil_keywords configuration mapped to market_data.calendar.oil_keywords.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'crude oil inventories,opec,cushing,api weekly crude'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  user_agent: str = config_field('apexvoid-trading-bot/1.0 (+contact)',
    item_id='python.settings.calendar_user_agent',
    legacy_attr='calendar_user_agent',
    env='CALENDAR_USER_AGENT',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy calendar_user_agent configuration mapped to market_data.calendar.user_agent.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'apexvoid-trading-bot/1.0 (+contact)'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class MarketDataSessionsConfig(FrozenConfigModel):
  asia_start: int = config_field(22,
    item_id='python.settings.session_asia_start',
    legacy_attr='session_asia_start',
    env='SESSION_ASIA_START',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.UTC_HOUR,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy session_asia_start configuration mapped to market_data.sessions.asia_start.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 22),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  daily_rollover_utc_hour: int = config_field(21,
    item_id='python.settings.daily_rollover_utc_hour',
    legacy_attr='daily_rollover_utc_hour',
    env='DAILY_ROLLOVER_UTC_HOUR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.UTC_HOUR,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy daily_rollover_utc_hour configuration mapped to market_data.sessions.daily_rollover_utc_hour.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 21),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  london_start: int = config_field(7,
    item_id='python.settings.session_london_start',
    legacy_attr='session_london_start',
    env='SESSION_LONDON_START',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.UTC_HOUR,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy session_london_start configuration mapped to market_data.sessions.london_start.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 7),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  ny_start: int = config_field(13,
    item_id='python.settings.session_ny_start',
    legacy_attr='session_ny_start',
    env='SESSION_NY_START',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.UTC_HOUR,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy session_ny_start configuration mapped to market_data.sessions.ny_start.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 13),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class MarketDataScannerConfig(FrozenConfigModel):
  alert_ttl: int = config_field(7200,
    item_id='python.settings.scanner_alert_ttl',
    legacy_attr='scanner_alert_ttl',
    env='SCANNER_ALERT_TTL',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy scanner_alert_ttl configuration mapped to market_data.scanner.alert_ttl.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 7200),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  confluence_floor: int = config_field(2,
    item_id='python.settings.scanner_confluence_floor',
    legacy_attr='scanner_confluence_floor',
    env='SCANNER_CONFLUENCE_FLOOR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy scanner_confluence_floor configuration mapped to market_data.scanner.confluence_floor.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 2),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  execution_timeframe: str = config_field('M5',
    item_id='python.settings.scanner_exec_tf',
    legacy_attr='scanner_exec_tf',
    env='SCANNER_EXEC_TF',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ENUM,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy scanner_exec_tf configuration mapped to market_data.scanner.execution_timeframe.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'M5'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  htf: str = config_field('H1,M15',
    item_id='python.settings.scanner_htf',
    legacy_attr='scanner_htf',
    env='SCANNER_HTF',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy scanner_htf configuration mapped to market_data.scanner.htf.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'H1,M15'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  level_bucket_pips: int = config_field(20,
    item_id='python.settings.scanner_level_bucket',
    legacy_attr='scanner_level_bucket',
    env='SCANNER_LEVEL_BUCKET',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PIPS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy scanner_level_bucket configuration mapped to market_data.scanner.level_bucket_pips.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 20),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  symbols: str = config_field('XAU',
    item_id='python.settings.scanner_symbols',
    legacy_attr='scanner_symbols',
    env='SCANNER_SYMBOLS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.IDENTIFIER,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy scanner_symbols configuration mapped to market_data.scanner.symbols.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'XAU'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  window: int = config_field(500,
    item_id='python.settings.scanner_window',
    legacy_attr='scanner_window',
    env='SCANNER_WINDOW',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy scanner_window configuration mapped to market_data.scanner.window.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 500),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class MarketDataTiingoConfig(FrozenConfigModel):
  api_key: str | None = config_field(None,
    item_id='python.settings.tiingo_api_key',
    legacy_attr='tiingo_api_key',
    env='TIINGO_API_KEY',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.IDENTIFIER,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    secret=True,
    description='Legacy tiingo_api_key configuration mapped to market_data.tiingo.api_key.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, '<redacted>'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class MarketDataWatcherConfig(FrozenConfigModel):
  ctrader_stale_seconds: int = config_field(180,
    item_id='python.settings.watcher_ctrader_stale_seconds',
    legacy_attr='watcher_ctrader_stale_seconds',
    env='WATCHER_CTRADER_STALE_SECONDS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy watcher_ctrader_stale_seconds configuration mapped to market_data.watcher.ctrader_stale_seconds.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 180),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  interval_seconds: int = config_field(30,
    item_id='python.settings.track_interval',
    legacy_attr='track_interval',
    env='TRACK_INTERVAL',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy track_interval configuration mapped to market_data.watcher.interval_seconds.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 30),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class MarketDataLookbacksConfig(FrozenConfigModel):
  h1_bars: int = config_field(400,
    item_id='python.settings.xau_lookback_h1_bars',
    legacy_attr='xau_lookback_h1_bars',
    env='XAU_LOOKBACK_H1_BARS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy xau_lookback_h1_bars configuration mapped to market_data.lookbacks.h1_bars.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 400),
    ),
    validation_summary='Pydantic required/type coercion only',
    ge=50,
  )
  m15_bars: int = config_field(650,
    item_id='python.settings.xau_lookback_m15_bars',
    legacy_attr='xau_lookback_m15_bars',
    env='XAU_LOOKBACK_M15_BARS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy xau_lookback_m15_bars configuration mapped to market_data.lookbacks.m15_bars.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 650),
    ),
    validation_summary='Pydantic required/type coercion only',
    ge=50,
  )
  m1_bars: int = config_field(150,
    item_id='python.settings.xau_lookback_m1_bars',
    legacy_attr='xau_lookback_m1_bars',
    env='XAU_LOOKBACK_M1_BARS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy xau_lookback_m1_bars configuration mapped to market_data.lookbacks.m1_bars.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 150),
    ),
    validation_summary='Pydantic required/type coercion only',
    ge=50,
  )
  m5_bars: int = config_field(1000,
    item_id='python.settings.xau_lookback_m5_bars',
    legacy_attr='xau_lookback_m5_bars',
    env='XAU_LOOKBACK_M5_BARS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy xau_lookback_m5_bars configuration mapped to market_data.lookbacks.m5_bars.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 1000),
    ),
    validation_summary='Pydantic required/type coercion only',
    ge=50,
  )


class MarketDataConfig(FrozenConfigModel):
  calendar: MarketDataCalendarConfig = Field(default_factory=MarketDataCalendarConfig)
  ctrader_feed: MarketDataCtraderFeedConfig = Field(default_factory=MarketDataCtraderFeedConfig)
  lookbacks: MarketDataLookbacksConfig = Field(default_factory=MarketDataLookbacksConfig)
  scanner: MarketDataScannerConfig = Field(default_factory=MarketDataScannerConfig)
  sessions: MarketDataSessionsConfig = Field(default_factory=MarketDataSessionsConfig)
  spot: MarketDataSpotConfig = Field(default_factory=MarketDataSpotConfig)
  tiingo: MarketDataTiingoConfig = Field(default_factory=MarketDataTiingoConfig)
  watcher: MarketDataWatcherConfig = Field(default_factory=MarketDataWatcherConfig)
