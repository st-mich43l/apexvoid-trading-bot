import os

# Importing the bot module builds the aiogram Bot objects, which only validate the shape of a token; no test calls Telegram.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
os.environ.setdefault("SCANNER_TELEGRAM_BOT_TOKEN", "123457:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
