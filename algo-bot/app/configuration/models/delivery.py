"""Complete inactive delivery configuration domain."""

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


class DeliveryChartAnalysisConfig(FrozenConfigModel):
  maximum_tokens: int = config_field(3000,
    item_id='hardcoded.delivery.chart_analysis_max_tokens',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.DELIVERY,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at delivery.chart_analysis.maximum_tokens.',
    validation_summary='none; source constant',
  )
  model: str = config_field('claude-opus-4-7',
    item_id='hardcoded.delivery.chart_analysis_model',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.ENUM,
    risk=RiskClassification.DELIVERY,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at delivery.chart_analysis.model.',
    validation_summary='none; source constant',
  )


class DeliveryMarketMapConfig(FrozenConfigModel):
  session_send: bool = config_field(True,
    item_id='python.settings.map_session_send',
    legacy_attr='map_session_send',
    env='MAP_SESSION_SEND',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.DELIVERY,
    description='Legacy map_session_send configuration mapped to delivery.market_map.session_send.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  tag_limit: int = config_field(4,
    item_id='hardcoded.delivery.market_map_tag_limit',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.DELIVERY,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at delivery.market_map.tag_limit.',
    validation_summary='none; source constant',
  )


class DeliveryTelegramConfig(FrozenConfigModel):
  delete_root_on_terminal: bool = config_field(False,
    item_id='python.settings.auto_trade_telegram_delete_root_on_terminal',
    legacy_attr='auto_trade_telegram_delete_root_on_terminal',
    env='AUTO_TRADE_TELEGRAM_DELETE_ROOT_ON_TERMINAL',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.DELIVERY,
    description='Legacy auto_trade_telegram_delete_root_on_terminal configuration mapped to delivery.telegram.delete_root_on_terminal.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  photo_debounce_seconds: float = config_field(2.0,
    item_id='hardcoded.delivery.photo_debounce_seconds',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.DELIVERY,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at delivery.telegram.photo_debounce_seconds.',
    validation_summary='none; source constant',
  )
  public_show_pips: bool = config_field(True,
    item_id='python.settings.public_show_pips',
    legacy_attr='public_show_pips',
    env='SIGNAL_PUBLIC_SHOW_PIPS',
    aliases=('PUBLIC_SHOW_PIPS',),
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.DELIVERY,
    description='Legacy public_show_pips configuration mapped to delivery.telegram.public_show_pips.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  scanner_telegram_bot_token: str | None = config_field(None,
    item_id='python.settings.scanner_telegram_bot_token',
    legacy_attr='scanner_telegram_bot_token',
    env='SCANNER_TELEGRAM_BOT_TOKEN',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.INFRASTRUCTURE,
    secret=True,
    description='Legacy scanner_telegram_bot_token configuration mapped to delivery.telegram.scanner_telegram_bot_token.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, '<redacted>'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  signal_public_channel_id: int | None = config_field(None,
    item_id='python.settings.signal_public_channel_id',
    legacy_attr='signal_public_channel_id',
    env='SIGNAL_PUBLIC_CHANNEL_ID',
    aliases=('XAU_PUBLIC_CHANNEL_ID',),
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.DELIVERY,
    description='Legacy signal_public_channel_id configuration mapped to delivery.telegram.signal_public_channel_id.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, None),
    ),
    validation_summary='Pydantic type coercion + Settings cross-field model validator',
  )
  single_root_card: bool = config_field(True,
    item_id='python.settings.auto_trade_telegram_single_root_card',
    legacy_attr='auto_trade_telegram_single_root_card',
    env='AUTO_TRADE_TELEGRAM_SINGLE_ROOT_CARD',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.DELIVERY,
    description='Legacy auto_trade_telegram_single_root_card configuration mapped to delivery.telegram.single_root_card.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  telegram_channel_id: int = config_field(
    item_id='python.settings.telegram_channel_id',
    legacy_attr='telegram_channel_id',
    env='SIGNAL_VIP_CHANNEL_ID',
    aliases=('TELEGRAM_CHANNEL_ID', 'TELEGRAM_CHAT_ID'),
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.DELIVERY,
    description='Legacy telegram_channel_id configuration mapped to delivery.telegram.telegram_channel_id.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, '<required>'),
    ),
    validation_summary='Pydantic type coercion + Settings cross-field model validator',
  )
  telegram_owner_id: int | None = config_field(None,
    item_id='python.settings.telegram_owner_id',
    legacy_attr='telegram_owner_id',
    env='TELEGRAM_OWNER_ID',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.DELIVERY,
    description='Legacy telegram_owner_id configuration mapped to delivery.telegram.telegram_owner_id.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, None),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class DeliveryPresentationConfig(FrozenConfigModel):
  anthropic_api_key: str | None = config_field(None,
    item_id='python.settings.anthropic_api_key',
    legacy_attr='anthropic_api_key',
    env='ANTHROPIC_API_KEY',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.IDENTIFIER,
    risk=RiskClassification.INFRASTRUCTURE,
    secret=True,
    description='Legacy anthropic_api_key configuration mapped to delivery.presentation.anthropic_api_key.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, '<redacted>'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  auto_book_bare_pips: bool = config_field(False,
    item_id='python.settings.auto_book_bare_pips',
    legacy_attr='auto_book_bare_pips',
    env='AUTO_BOOK_BARE_PIPS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.DELIVERY,
    description='Legacy auto_book_bare_pips configuration mapped to delivery.presentation.auto_book_bare_pips.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  seq_reset_tz: str = config_field('Asia/Ho_Chi_Minh',
    item_id='python.settings.seq_reset_tz',
    legacy_attr='seq_reset_tz',
    env='SEQ_RESET_TZ',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.DELIVERY,
    description='Legacy seq_reset_tz configuration mapped to delivery.presentation.seq_reset_tz.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'Asia/Ho_Chi_Minh'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class DeliveryLifecycleConfig(FrozenConfigModel):
  delete_on_terminal: bool = config_field(True,
    item_id='python.settings.delivery_delete_on_terminal',
    legacy_attr='delivery_delete_on_terminal',
    env='DELIVERY_DELETE_ON_TERMINAL',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.DELIVERY,
    description='Legacy delivery_delete_on_terminal configuration mapped to delivery.lifecycle.delete_on_terminal.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  thread_lifecycle: bool = config_field(True,
    item_id='python.settings.delivery_thread_lifecycle',
    legacy_attr='delivery_thread_lifecycle',
    env='DELIVERY_THREAD_LIFECYCLE',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.DELIVERY,
    description='Legacy delivery_thread_lifecycle configuration mapped to delivery.lifecycle.thread_lifecycle.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class DeliveryScannerCardsConfig(FrozenConfigModel):
  maximum_cards: int = config_field(2,
    item_id='python.settings.scanner_card_top_n',
    legacy_attr='scanner_card_top_n',
    env='SCANNER_CARD_TOP_N',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.DELIVERY,
    description='Legacy scanner_card_top_n configuration mapped to delivery.scanner_cards.maximum_cards.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 2),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  top_n: int = config_field(3,
    item_id='python.settings.scanner_top_n',
    legacy_attr='scanner_top_n',
    env='SCANNER_TOP_N',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.DELIVERY,
    description='Legacy scanner_top_n configuration mapped to delivery.scanner_cards.top_n.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 3),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class DeliveryReportsWeeklyConfig(FrozenConfigModel):
  day_of_week: int = config_field(6,
    item_id='python.settings.weekly_report_dow',
    legacy_attr='weekly_report_dow',
    env='WEEKLY_REPORT_DOW',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.DAY_OF_WEEK,
    risk=RiskClassification.DELIVERY,
    description='Legacy weekly_report_dow configuration mapped to delivery.reports.weekly.day_of_week.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 6),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  enabled: bool = config_field(True,
    item_id='python.settings.weekly_report_enabled',
    legacy_attr='weekly_report_enabled',
    env='WEEKLY_REPORT_ENABLED',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.DELIVERY,
    description='Legacy weekly_report_enabled configuration mapped to delivery.reports.weekly.enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  skip_empty: bool = config_field(False,
    item_id='python.settings.weekly_report_skip_empty',
    legacy_attr='weekly_report_skip_empty',
    env='WEEKLY_REPORT_SKIP_EMPTY',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.DELIVERY,
    description='Legacy weekly_report_skip_empty configuration mapped to delivery.reports.weekly.skip_empty.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  utc_hour: int = config_field(8,
    item_id='python.settings.weekly_report_hour',
    legacy_attr='weekly_report_hour',
    env='WEEKLY_REPORT_HOUR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.UTC_HOUR,
    risk=RiskClassification.DELIVERY,
    description='Legacy weekly_report_hour configuration mapped to delivery.reports.weekly.utc_hour.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 8),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class DeliveryReportsConfig(FrozenConfigModel):
  weekly: DeliveryReportsWeeklyConfig = Field(default_factory=DeliveryReportsWeeklyConfig)


class DeliveryConfig(FrozenConfigModel):
  chart_analysis: DeliveryChartAnalysisConfig = Field(default_factory=DeliveryChartAnalysisConfig)
  lifecycle: DeliveryLifecycleConfig = Field(default_factory=DeliveryLifecycleConfig)
  market_map: DeliveryMarketMapConfig = Field(default_factory=DeliveryMarketMapConfig)
  presentation: DeliveryPresentationConfig = Field(default_factory=DeliveryPresentationConfig)
  reports: DeliveryReportsConfig = Field(default_factory=DeliveryReportsConfig)
  scanner_cards: DeliveryScannerCardsConfig = Field(default_factory=DeliveryScannerCardsConfig)
  telegram: DeliveryTelegramConfig
