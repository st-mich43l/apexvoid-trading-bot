"""Complete Canonical Catalog V2 configuration domain. """
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
    access_token: str = config_field(canonical_env='CTRADER_ACCESS_TOKEN', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEXT_SCANNER_CYCLE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.BROKER_ACCOUNT_SAFETY, secret=True, description='cTrader configuration option CTRADER_ACCESS_TOKEN controlling .', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '<redacted>'),), validation_summary='FeedOptions.Env')
    account_id: int = config_field(canonical_env='CTRADER_ACCOUNT_ID', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEXT_SCANNER_CYCLE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.IDENTIFIER, risk=RiskClassification.INFRASTRUCTURE, description='cTrader configuration option CTRADER_ACCOUNT_ID controlling .', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '<required>'),), validation_summary='FeedOptions.Env')
    client_id: str = config_field(canonical_env='CTRADER_CLIENT_ID', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEXT_SCANNER_CYCLE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.IDENTIFIER, risk=RiskClassification.INFRASTRUCTURE, description='cTrader configuration option CTRADER_CLIENT_ID controlling .', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '<required>'),), validation_summary='FeedOptions.Env')
    client_secret: str = config_field(canonical_env='CTRADER_CLIENT_SECRET', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEXT_SCANNER_CYCLE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.BROKER_ACCOUNT_SAFETY, secret=True, description='cTrader configuration option CTRADER_CLIENT_SECRET controlling .', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '<redacted>'),), validation_summary='FeedOptions.Env')
    refresh_token: str = config_field(canonical_env='CTRADER_REFRESH_TOKEN', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEXT_SCANNER_CYCLE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.BROKER_ACCOUNT_SAFETY, secret=True, description='cTrader configuration option CTRADER_REFRESH_TOKEN controlling .', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '<redacted>'),), validation_summary='FeedOptions.Env')

class BootstrapCtraderConnectionConfig(FrozenConfigModel):
    host: str = config_field('demo.ctraderapi.com', canonical_env='CTRADER_HOST', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEXT_SCANNER_CYCLE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.INFRASTRUCTURE, description='cTrader configuration option CTRADER_HOST controlling .', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'demo.ctraderapi.com'),), validation_summary='FeedOptions.Env')
    port: int = config_field(5035, canonical_env='CTRADER_PORT', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEXT_SCANNER_CYCLE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PORT, risk=RiskClassification.INFRASTRUCTURE, description='cTrader configuration option CTRADER_PORT controlling  (port).', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 5035),), validation_summary='FeedOptions.Env')
    request_timeout_seconds: int = config_field(30, canonical_env='CTRADER_REQUEST_TIMEOUT', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEXT_SCANNER_CYCLE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.SECONDS, risk=RiskClassification.INFRASTRUCTURE, description='cTrader configuration option CTRADER_REQUEST_TIMEOUT controlling  (seconds).', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 30),), validation_summary='FeedOptions.Env')

class BootstrapCtraderTokenRotationConfig(FrozenConfigModel):
    check_interval_hours: Decimal = config_field(Decimal('6'), canonical_env='CTRADER_TOKEN_CHECK_INTERVAL_HOURS', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEXT_SCANNER_CYCLE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.HOURS, risk=RiskClassification.INFRASTRUCTURE, description='cTrader configuration option CTRADER_TOKEN_CHECK_INTERVAL_HOURS controlling  (hours).', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, Decimal('6')),), validation_summary='FeedOptions.Env')
    refresh_lead_days: Decimal = config_field(Decimal('5'), canonical_env='CTRADER_TOKEN_REFRESH_LEAD_DAYS', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEXT_SCANNER_CYCLE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.DAYS, risk=RiskClassification.INFRASTRUCTURE, description='cTrader configuration option CTRADER_TOKEN_REFRESH_LEAD_DAYS controlling  (days).', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, Decimal('5')),), validation_summary='FeedOptions.Env')
    refresh_token_file: str = config_field('/var/lib/apexvoid/ctrader-token.json', canonical_env='CTRADER_REFRESH_TOKEN_FILE', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEXT_SCANNER_CYCLE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PATH, risk=RiskClassification.INFRASTRUCTURE, description='cTrader configuration option CTRADER_REFRESH_TOKEN_FILE controlling .', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '/var/lib/apexvoid/ctrader-token.json'),), validation_summary='FeedOptions.Env')
    refresh_token_key: str = config_field('ctrader:refresh_token', canonical_env='CTRADER_REFRESH_TOKEN_KEY', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEXT_SCANNER_CYCLE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.IDENTIFIER, risk=RiskClassification.INFRASTRUCTURE, description='cTrader configuration option CTRADER_REFRESH_TOKEN_KEY controlling .', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'ctrader:refresh_token'),), validation_summary='FeedOptions.Env')

class BootstrapCtraderConfig(FrozenConfigModel):
    connection: BootstrapCtraderConnectionConfig = Field(default_factory=BootstrapCtraderConnectionConfig)
    credentials: BootstrapCtraderCredentialsConfig
    token_rotation: BootstrapCtraderTokenRotationConfig = Field(default_factory=BootstrapCtraderTokenRotationConfig)

class BootstrapLoggingConfig(FrozenConfigModel):
    ctrader_file_name: str = config_field('ctrader-engine.log', canonical_env='LOG_FILE_NAME', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.RESTART, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PATH, risk=RiskClassification.INFRASTRUCTURE, description='cTrader configuration option LOG_FILE_NAME controlling .', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'ctrader-engine.log'),), validation_summary='DailyFileLog direct environment parser')
    directory: str = config_field('/var/log/apexvoid', canonical_env='LOG_DIR', deprecated_env_aliases=('APEXVOID_LOG_DIR',), owner=ConfigOwner.SHARED, reload=ReloadPolicy.RESTART, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PATH, risk=RiskClassification.INFRASTRUCTURE, shared_with_ctrader=True, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, '/var/log/apexvoid'), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '/var/log/apexvoid')), validation_summary='Pydantic required/type coercion only; DailyFileLog direct environment parser + AutoTradeOptions.Validate')
    file_enabled: bool = config_field(True, canonical_env='LOG_FILE_ENABLED', deprecated_env_aliases=('APEXVOID_LOG_FILE_ENABLED',), owner=ConfigOwner.SHARED, reload=ReloadPolicy.RESTART, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.INFRASTRUCTURE, shared_with_ctrader=True, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, True)), validation_summary='Pydantic required/type coercion only; DailyFileLog direct environment parser + AutoTradeOptions.Validate')
    level: str = config_field('INFO', canonical_env='LOG_LEVEL', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.RESTART, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.INFRASTRUCTURE, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 'INFO'),), validation_summary='Pydantic required/type coercion only')
    retention_days: int = config_field(14, canonical_env='LOG_RETENTION_DAYS', deprecated_env_aliases=('APEXVOID_LOG_RETENTION_DAYS',), owner=ConfigOwner.SHARED, reload=ReloadPolicy.RESTART, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.DAYS, risk=RiskClassification.INFRASTRUCTURE, shared_with_ctrader=True, description='Controls  (days).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 14), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 14)), validation_summary='Pydantic required/type coercion only; DailyFileLog direct environment parser + AutoTradeOptions.Validate')

class BootstrapBuildConfig(FrozenConfigModel):
    git_sha: str = config_field('unknown', canonical_env='GIT_SHA', owner=ConfigOwner.SHARED, reload=ReloadPolicy.RESTART, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.INFRASTRUCTURE, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.WARNING, description='Operational environment option GIT_SHA.', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'unknown'),), validation_summary='direct environment read or deployment parser')
    service_version: str = config_field('dev', canonical_env='SERVICE_VERSION', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.RESTART, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.INFRASTRUCTURE, description='Operational environment option SERVICE_VERSION.', validation_summary='direct environment read or deployment parser')

class BootstrapProcessConfig(FrozenConfigModel):
    hostname: str = config_field('algo-worker', canonical_env='HOSTNAME', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.RESTART, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.INFRASTRUCTURE, description='Operational environment option HOSTNAME.', validation_summary='direct environment read or deployment parser')

class BootstrapPostgresConfig(FrozenConfigModel):
    db: str = config_field('signals', canonical_env='POSTGRES_DB', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.RESTART, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.INFRASTRUCTURE, description='Operational environment option POSTGRES_DB.', validation_summary='direct environment read or deployment parser')
    password: str = config_field(canonical_env='POSTGRES_PASSWORD', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.RESTART, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.INFRASTRUCTURE, secret=True, description='Operational environment option POSTGRES_PASSWORD.', validation_summary='direct environment read or deployment parser')
    url: str = config_field('postgresql://apexvoid:apexvoid@localhost:5432/signals', canonical_env='DATABASE_URL', deprecated_env_aliases=('POSTGRES_DSN',), owner=ConfigOwner.PYTHON, reload=ReloadPolicy.RESTART, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.URL, risk=RiskClassification.INFRASTRUCTURE, secret=True, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, '<redacted>'),), validation_summary='Pydantic required/type coercion only')
    user: str = config_field('apexvoid', canonical_env='POSTGRES_USER', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.RESTART, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.INFRASTRUCTURE, description='Operational environment option POSTGRES_USER.', validation_summary='direct environment read or deployment parser')

class BootstrapRedisConfig(FrozenConfigModel):
    url: str = config_field('redis://redis:6379/0', canonical_env='REDIS_URL', owner=ConfigOwner.SHARED, reload=ReloadPolicy.RESTART, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.URL, risk=RiskClassification.INFRASTRUCTURE, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 'redis://redis:6379/0'), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'redis://redis:6379/0')), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.String + AutoTradeOptions.Validate')

class BootstrapTelegramConfig(FrozenConfigModel):
    bot_token: str = config_field(canonical_env='TELEGRAM_BOT_TOKEN', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.IMMEDIATE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.INFRASTRUCTURE, secret=True, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, '<redacted>'),), validation_summary='Pydantic required/type coercion only')

class BootstrapConfig(FrozenConfigModel):
    build: BootstrapBuildConfig = Field(default_factory=BootstrapBuildConfig)
    ctrader: BootstrapCtraderConfig
    logging: BootstrapLoggingConfig = Field(default_factory=BootstrapLoggingConfig)
    postgres: BootstrapPostgresConfig
    process: BootstrapProcessConfig = Field(default_factory=BootstrapProcessConfig)
    redis: BootstrapRedisConfig = Field(default_factory=BootstrapRedisConfig)
    telegram: BootstrapTelegramConfig
