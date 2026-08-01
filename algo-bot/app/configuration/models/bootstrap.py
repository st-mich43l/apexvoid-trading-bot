"""Bootstrap configuration model shells."""

from pydantic import Field

from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field
from app.configuration.models.base import FrozenConfigModel


class TelegramBootstrapConfig(FrozenConfigModel):
  bot_token: str = config_field(
    item_id="python.settings.telegram_bot_token",
    legacy_attr="telegram_bot_token",
    env="TELEGRAM_BOT_TOKEN",
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.INFRASTRUCTURE,
    secret=True,
    description="Telegram bot authentication token.",
  )


class CTraderConnectionConfig(FrozenConfigModel):
  host: str = config_field(
    "demo.ctraderapi.com",
    item_id="ctrader.env.CTRADER_HOST",
    legacy_attr=None,
    env="CTRADER_HOST",
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.INFRASTRUCTURE,
    description="cTrader Open API endpoint host.",
  )
  port: int = config_field(
    5035,
    item_id="ctrader.env.CTRADER_PORT",
    legacy_attr=None,
    env="CTRADER_PORT",
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    unit=ConfigUnit.PORT,
    risk=RiskClassification.INFRASTRUCTURE,
    description="cTrader Open API endpoint port.",
  )


class CTraderBootstrapConfig(FrozenConfigModel):
  connection: CTraderConnectionConfig = Field(
    default_factory=CTraderConnectionConfig,
  )


class BootstrapConfig(FrozenConfigModel):
  telegram: TelegramBootstrapConfig
  ctrader: CTraderBootstrapConfig = Field(
    default_factory=CTraderBootstrapConfig,
  )
