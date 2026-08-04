"""Complete Canonical Catalog V2 configuration domain. """
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
    maximum_tokens: int = config_field(3000, canonical_env=None, owner=ConfigOwner.PYTHON, reload=ReloadPolicy.CODE_RELEASE, runtime_reload=ReloadPolicy.CODE_RELEASE, unit=ConfigUnit.COUNT, risk=RiskClassification.DELIVERY, kind=ConfigKind.ALGORITHM_CONSTANT, description='Config-like hardcoded value proposed at delivery.chart_analysis.maximum_tokens.', validation_summary='none; source constant')
    model: str = config_field('claude-opus-4-7', canonical_env=None, owner=ConfigOwner.PYTHON, reload=ReloadPolicy.CODE_RELEASE, runtime_reload=ReloadPolicy.CODE_RELEASE, unit=ConfigUnit.ENUM, risk=RiskClassification.DELIVERY, kind=ConfigKind.ALGORITHM_CONSTANT, description='Config-like hardcoded value proposed at delivery.chart_analysis.model.', validation_summary='none; source constant')

class DeliveryMarketMapConfig(FrozenConfigModel):
    session_send: bool = config_field(True, canonical_env='MAP_SESSION_SEND', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.DELIVERY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),), validation_summary='Pydantic required/type coercion only')
    tag_limit: int = config_field(4, canonical_env=None, owner=ConfigOwner.PYTHON, reload=ReloadPolicy.CODE_RELEASE, runtime_reload=ReloadPolicy.CODE_RELEASE, unit=ConfigUnit.COUNT, risk=RiskClassification.DELIVERY, kind=ConfigKind.ALGORITHM_CONSTANT, description='Config-like hardcoded value proposed at delivery.market_map.tag_limit.', validation_summary='none; source constant')

class DeliveryTelegramConfig(FrozenConfigModel):
    delete_root_on_terminal: bool = config_field(False, canonical_env='AUTO_TRADE_TELEGRAM_DELETE_ROOT_ON_TERMINAL', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.DELIVERY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, False),), validation_summary='Pydantic required/type coercion only')
    photo_debounce_seconds: float = config_field(2.0, canonical_env=None, owner=ConfigOwner.PYTHON, reload=ReloadPolicy.CODE_RELEASE, runtime_reload=ReloadPolicy.CODE_RELEASE, unit=ConfigUnit.SECONDS, risk=RiskClassification.DELIVERY, kind=ConfigKind.ALGORITHM_CONSTANT, description='Config-like hardcoded value proposed at delivery.telegram.photo_debounce_seconds.', validation_summary='none; source constant')
    public_show_pips: bool = config_field(True, canonical_env='SIGNAL_PUBLIC_SHOW_PIPS', deprecated_env_aliases=('PUBLIC_SHOW_PIPS',), owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.DELIVERY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),), validation_summary='Pydantic required/type coercion only')
    scanner_telegram_bot_token: str | None = config_field(None, canonical_env='SCANNER_TELEGRAM_BOT_TOKEN', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.INFRASTRUCTURE, secret=True, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, '<redacted>'),), validation_summary='Pydantic required/type coercion only')
    signal_public_channel_id: int | None = config_field(None, canonical_env='SIGNAL_PUBLIC_CHANNEL_ID', deprecated_env_aliases=('XAU_PUBLIC_CHANNEL_ID',), owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.COUNT, risk=RiskClassification.DELIVERY, description='Controls  (count).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, None),), validation_summary='Pydantic type coercion + Settings cross-field model validator')
    single_root_card: bool = config_field(True, canonical_env='AUTO_TRADE_TELEGRAM_SINGLE_ROOT_CARD', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.DELIVERY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),), validation_summary='Pydantic required/type coercion only')
    telegram_channel_id: int = config_field(canonical_env='SIGNAL_VIP_CHANNEL_ID', deprecated_env_aliases=('TELEGRAM_CHANNEL_ID', 'TELEGRAM_CHAT_ID'), owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.COUNT, risk=RiskClassification.DELIVERY, description='Controls  (count).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, '<required>'),), validation_summary='Pydantic type coercion + Settings cross-field model validator')
    telegram_owner_id: int | None = config_field(None, canonical_env='TELEGRAM_OWNER_ID', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.COUNT, risk=RiskClassification.DELIVERY, description='Controls  (count).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, None),), validation_summary='Pydantic required/type coercion only')

class DeliveryPresentationConfig(FrozenConfigModel):
    anthropic_api_key: str | None = config_field(None, canonical_env='ANTHROPIC_API_KEY', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.IDENTIFIER, risk=RiskClassification.INFRASTRUCTURE, secret=True, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, '<redacted>'),), validation_summary='Pydantic required/type coercion only')
    auto_book_bare_pips: bool = config_field(False, canonical_env='AUTO_BOOK_BARE_PIPS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.DELIVERY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, False),), validation_summary='Pydantic required/type coercion only')
    seq_reset_tz: str = config_field('Asia/Ho_Chi_Minh', canonical_env='SEQ_RESET_TZ', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.DELIVERY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 'Asia/Ho_Chi_Minh'),), validation_summary='Pydantic required/type coercion only')

class DeliveryLifecycleConfig(FrozenConfigModel):
    delete_on_terminal: bool = config_field(True, canonical_env='DELIVERY_DELETE_ON_TERMINAL', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.DELIVERY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),), validation_summary='Pydantic required/type coercion only')
    thread_lifecycle: bool = config_field(True, canonical_env='DELIVERY_THREAD_LIFECYCLE', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.DELIVERY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),), validation_summary='Pydantic required/type coercion only')

class DeliveryScannerCardsConfig(FrozenConfigModel):
    maximum_cards: int = config_field(2, canonical_env='SCANNER_CARD_TOP_N', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.COUNT, risk=RiskClassification.DELIVERY, description='Controls  (count).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 2),), validation_summary='Pydantic required/type coercion only')
    top_n: int = config_field(3, canonical_env='SCANNER_TOP_N', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.COUNT, risk=RiskClassification.DELIVERY, description='Controls  (count).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 3),), validation_summary='Pydantic required/type coercion only')

class DeliveryReportsWeeklyConfig(FrozenConfigModel):
    day_of_week: int = config_field(6, canonical_env='WEEKLY_REPORT_DOW', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.DAY_OF_WEEK, risk=RiskClassification.DELIVERY, description='Controls  (day_of_week).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 6),), validation_summary='Pydantic required/type coercion only')
    enabled: bool = config_field(True, canonical_env='WEEKLY_REPORT_ENABLED', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.DELIVERY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),), validation_summary='Pydantic required/type coercion only')
    skip_empty: bool = config_field(False, canonical_env='WEEKLY_REPORT_SKIP_EMPTY', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.DELIVERY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, False),), validation_summary='Pydantic required/type coercion only')
    utc_hour: int = config_field(8, canonical_env='WEEKLY_REPORT_HOUR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.UTC_HOUR, risk=RiskClassification.DELIVERY, description='Controls  (utc_hour).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 8),), validation_summary='Pydantic required/type coercion only')

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
