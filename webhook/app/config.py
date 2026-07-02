from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
  model_config = SettingsConfigDict(env_file=".env", extra="ignore")

  telegram_bot_token: str
  telegram_chat_id: str
  db_path: str = "/data/signals.db"
  log_level: str = "INFO"
  telegram_api_id: Optional[int] = None
  telegram_api_hash: Optional[str] = None
  telegram_owner_id: Optional[int] = None  # your Telegram user ID — only this user can DM the bot
  anthropic_api_key: Optional[str] = None  # for chart screenshot analysis via Claude vision


settings = Settings()
