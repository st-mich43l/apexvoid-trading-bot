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
  db_path: str = "/data/signals.db"
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
  twelvedata_api_key: Optional[str] = None
  track_interval: int = 20
  session_asia_start: int = 22
  session_london_start: int = 7
  session_ny_start: int = 13
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
