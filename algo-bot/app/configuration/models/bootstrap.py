"""Complete inactive bootstrap configuration domain."""

from decimal import Decimal

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


class BootstrapCtraderCredentialsConfig(FrozenConfigModel):
  access_token: str = config_field(
    item_id='ctrader.env.CTRADER_ACCESS_TOKEN',
    legacy_attr=None,
    env='CTRADER_ACCESS_TOKEN',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    secret=True,
    description='cTrader runtime option CTRADER_ACCESS_TOKEN mapped to bootstrap.ctrader.credentials.access_token.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '<redacted>'),
    ),
    validation_summary='FeedOptions.Env',
  )
  account_id: int = config_field(
    item_id='ctrader.env.CTRADER_ACCOUNT_ID',
    legacy_attr=None,
    env='CTRADER_ACCOUNT_ID',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.IDENTIFIER,
    risk=RiskClassification.INFRASTRUCTURE,
    description='cTrader runtime option CTRADER_ACCOUNT_ID mapped to bootstrap.ctrader.credentials.account_id.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '<required>'),
    ),
    validation_summary='FeedOptions.Env',
  )
  client_id: str = config_field(
    item_id='ctrader.env.CTRADER_CLIENT_ID',
    legacy_attr=None,
    env='CTRADER_CLIENT_ID',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.IDENTIFIER,
    risk=RiskClassification.INFRASTRUCTURE,
    description='cTrader runtime option CTRADER_CLIENT_ID mapped to bootstrap.ctrader.credentials.client_id.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '<required>'),
    ),
    validation_summary='FeedOptions.Env',
  )
  client_secret: str = config_field(
    item_id='ctrader.env.CTRADER_CLIENT_SECRET',
    legacy_attr=None,
    env='CTRADER_CLIENT_SECRET',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    secret=True,
    description='cTrader runtime option CTRADER_CLIENT_SECRET mapped to bootstrap.ctrader.credentials.client_secret.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '<redacted>'),
    ),
    validation_summary='FeedOptions.Env',
  )
  refresh_token: str = config_field(
    item_id='ctrader.env.CTRADER_REFRESH_TOKEN',
    legacy_attr=None,
    env='CTRADER_REFRESH_TOKEN',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    secret=True,
    description='cTrader runtime option CTRADER_REFRESH_TOKEN mapped to bootstrap.ctrader.credentials.refresh_token.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '<redacted>'),
    ),
    validation_summary='FeedOptions.Env',
  )


class BootstrapCtraderConnectionConfig(FrozenConfigModel):
  host: str = config_field('demo.ctraderapi.com',
    item_id='ctrader.env.CTRADER_HOST',
    legacy_attr=None,
    env='CTRADER_HOST',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.INFRASTRUCTURE,
    description='cTrader runtime option CTRADER_HOST mapped to bootstrap.ctrader.connection.host.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'demo.ctraderapi.com'),
    ),
    validation_summary='FeedOptions.Env',
  )
  port: int = config_field(5035,
    item_id='ctrader.env.CTRADER_PORT',
    legacy_attr=None,
    env='CTRADER_PORT',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PORT,
    risk=RiskClassification.INFRASTRUCTURE,
    description='cTrader runtime option CTRADER_PORT mapped to bootstrap.ctrader.connection.port.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 5035),
    ),
    validation_summary='FeedOptions.Env',
  )
  request_timeout_seconds: int = config_field(30,
    item_id='ctrader.env.CTRADER_REQUEST_TIMEOUT',
    legacy_attr=None,
    env='CTRADER_REQUEST_TIMEOUT',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='cTrader runtime option CTRADER_REQUEST_TIMEOUT mapped to bootstrap.ctrader.connection.request_timeout_seconds.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 30),
    ),
    validation_summary='FeedOptions.Env',
  )


class BootstrapCtraderTokenRotationConfig(FrozenConfigModel):
  check_interval_hours: Decimal = config_field(Decimal('6'),
    item_id='ctrader.env.CTRADER_TOKEN_CHECK_INTERVAL_HOURS',
    legacy_attr=None,
    env='CTRADER_TOKEN_CHECK_INTERVAL_HOURS',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.HOURS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='cTrader runtime option CTRADER_TOKEN_CHECK_INTERVAL_HOURS mapped to bootstrap.ctrader.token_rotation.check_interval_hours.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, Decimal('6')),
    ),
    validation_summary='FeedOptions.Env',
  )
  refresh_lead_days: Decimal = config_field(Decimal('5'),
    item_id='ctrader.env.CTRADER_TOKEN_REFRESH_LEAD_DAYS',
    legacy_attr=None,
    env='CTRADER_TOKEN_REFRESH_LEAD_DAYS',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.DAYS,
    risk=RiskClassification.INFRASTRUCTURE,
    description='cTrader runtime option CTRADER_TOKEN_REFRESH_LEAD_DAYS mapped to bootstrap.ctrader.token_rotation.refresh_lead_days.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, Decimal('5')),
    ),
    validation_summary='FeedOptions.Env',
  )
  refresh_token_file: str = config_field('/var/lib/apexvoid/ctrader-token.json',
    item_id='ctrader.env.CTRADER_REFRESH_TOKEN_FILE',
    legacy_attr=None,
    env='CTRADER_REFRESH_TOKEN_FILE',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PATH,
    risk=RiskClassification.INFRASTRUCTURE,
    description='cTrader runtime option CTRADER_REFRESH_TOKEN_FILE mapped to bootstrap.ctrader.token_rotation.refresh_token_file.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '/var/lib/apexvoid/ctrader-token.json'),
    ),
    validation_summary='FeedOptions.Env',
  )
  refresh_token_key: str = config_field('ctrader:refresh_token',
    item_id='ctrader.env.CTRADER_REFRESH_TOKEN_KEY',
    legacy_attr=None,
    env='CTRADER_REFRESH_TOKEN_KEY',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.IDENTIFIER,
    risk=RiskClassification.INFRASTRUCTURE,
    description='cTrader runtime option CTRADER_REFRESH_TOKEN_KEY mapped to bootstrap.ctrader.token_rotation.refresh_token_key.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'ctrader:refresh_token'),
    ),
    validation_summary='FeedOptions.Env',
  )


class BootstrapCtraderConfig(FrozenConfigModel):
  connection: BootstrapCtraderConnectionConfig = Field(default_factory=BootstrapCtraderConnectionConfig)
  credentials: BootstrapCtraderCredentialsConfig
  token_rotation: BootstrapCtraderTokenRotationConfig = Field(default_factory=BootstrapCtraderTokenRotationConfig)


class BootstrapLoggingConfig(FrozenConfigModel):
  ctrader_file_name: str = config_field('ctrader-engine.log',
    item_id='ctrader.env.LOG_FILE_NAME',
    legacy_attr=None,
    env='LOG_FILE_NAME',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.RESTART,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PATH,
    risk=RiskClassification.INFRASTRUCTURE,
    description='cTrader runtime option LOG_FILE_NAME mapped to bootstrap.logging.ctrader_file_name.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'ctrader-engine.log'),
    ),
    validation_summary='DailyFileLog direct environment parser',
  )
  directory: str = config_field('/var/log/apexvoid',
    item_id='python.settings.log_dir',
    legacy_attr='log_dir',
    env='LOG_DIR',
    aliases=('APEXVOID_LOG_DIR',),
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PATH,
    risk=RiskClassification.INFRASTRUCTURE,
    shared_with_ctrader=True,
    description='Legacy log_dir configuration mapped to bootstrap.logging.directory.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, '/var/log/apexvoid'),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '/var/log/apexvoid'),
    ),
    validation_summary='Pydantic required/type coercion only; DailyFileLog direct environment parser + AutoTradeOptions.Validate',
  )
  file_enabled: bool = config_field(True,
    item_id='python.settings.log_file_enabled',
    legacy_attr='log_file_enabled',
    env='LOG_FILE_ENABLED',
    aliases=('APEXVOID_LOG_FILE_ENABLED',),
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.INFRASTRUCTURE,
    shared_with_ctrader=True,
    description='Legacy log_file_enabled configuration mapped to bootstrap.logging.file_enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, True),
    ),
    validation_summary='Pydantic required/type coercion only; DailyFileLog direct environment parser + AutoTradeOptions.Validate',
  )
  level: str = config_field('INFO',
    item_id='python.settings.log_level',
    legacy_attr='log_level',
    env='LOG_LEVEL',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.RESTART,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy log_level configuration mapped to bootstrap.logging.level.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'INFO'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  retention_days: int = config_field(14,
    item_id='python.settings.log_retention_days',
    legacy_attr='log_retention_days',
    env='LOG_RETENTION_DAYS',
    aliases=('APEXVOID_LOG_RETENTION_DAYS',),
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.DAYS,
    risk=RiskClassification.INFRASTRUCTURE,
    shared_with_ctrader=True,
    description='Legacy log_retention_days configuration mapped to bootstrap.logging.retention_days.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 14),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 14),
    ),
    validation_summary='Pydantic required/type coercion only; DailyFileLog direct environment parser + AutoTradeOptions.Validate',
  )


class BootstrapBuildConfig(FrozenConfigModel):
  git_sha: str = config_field('unknown',
    item_id='environment.GIT_SHA',
    legacy_attr=None,
    env='GIT_SHA',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.INFRASTRUCTURE,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.WARNING,
    description='Operational environment option GIT_SHA.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'unknown'),
    ),
    validation_summary='direct environment read or deployment parser',
  )
  service_version: str = config_field('dev',
    item_id='environment.SERVICE_VERSION',
    legacy_attr=None,
    env='SERVICE_VERSION',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.RESTART,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Operational environment option SERVICE_VERSION.',
    validation_summary='direct environment read or deployment parser',
  )


class BootstrapProcessConfig(FrozenConfigModel):
  hostname: str = config_field('algo-worker',
    item_id='environment.HOSTNAME',
    legacy_attr=None,
    env='HOSTNAME',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.RESTART,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Operational environment option HOSTNAME.',
    validation_summary='direct environment read or deployment parser',
  )


class BootstrapPostgresConfig(FrozenConfigModel):
  db: str = config_field('signals',
    item_id='environment.POSTGRES_DB',
    legacy_attr=None,
    env='POSTGRES_DB',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.RESTART,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Operational environment option POSTGRES_DB.',
    validation_summary='direct environment read or deployment parser',
  )
  password: str = config_field(
    item_id='environment.POSTGRES_PASSWORD',
    legacy_attr=None,
    env='POSTGRES_PASSWORD',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.RESTART,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.INFRASTRUCTURE,
    secret=True,
    description='Operational environment option POSTGRES_PASSWORD.',
    validation_summary='direct environment read or deployment parser',
  )
  url: str = config_field('postgresql://apexvoid:apexvoid@localhost:5432/signals',
    item_id='python.settings.database_url',
    legacy_attr='database_url',
    env='DATABASE_URL',
    aliases=('POSTGRES_DSN',),
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.RESTART,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.URL,
    risk=RiskClassification.INFRASTRUCTURE,
    secret=True,
    description='Legacy database_url configuration mapped to bootstrap.postgres.url.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, '<redacted>'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  user: str = config_field('apexvoid',
    item_id='environment.POSTGRES_USER',
    legacy_attr=None,
    env='POSTGRES_USER',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.RESTART,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Operational environment option POSTGRES_USER.',
    validation_summary='direct environment read or deployment parser',
  )


class BootstrapRedisConfig(FrozenConfigModel):
  url: str = config_field('redis://redis:6379/0',
    item_id='python.settings.redis_url',
    legacy_attr='redis_url',
    env='REDIS_URL',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.URL,
    risk=RiskClassification.INFRASTRUCTURE,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.FATAL,
    description='Legacy redis_url configuration mapped to bootstrap.redis.url.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'redis://redis:6379/0'),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'redis://redis:6379/0'),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.String + AutoTradeOptions.Validate',
  )


class BootstrapTelegramConfig(FrozenConfigModel):
  bot_token: str = config_field(
    item_id='python.settings.telegram_bot_token',
    legacy_attr='telegram_bot_token',
    env='TELEGRAM_BOT_TOKEN',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.INFRASTRUCTURE,
    secret=True,
    description='Legacy telegram_bot_token configuration mapped to bootstrap.telegram.bot_token.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, '<redacted>'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class BootstrapConfig(FrozenConfigModel):
  build: BootstrapBuildConfig = Field(default_factory=BootstrapBuildConfig)
  ctrader: BootstrapCtraderConfig
  logging: BootstrapLoggingConfig = Field(default_factory=BootstrapLoggingConfig)
  postgres: BootstrapPostgresConfig
  process: BootstrapProcessConfig = Field(default_factory=BootstrapProcessConfig)
  redis: BootstrapRedisConfig = Field(default_factory=BootstrapRedisConfig)
  telegram: BootstrapTelegramConfig
