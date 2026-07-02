"""Run this once to generate pyro_session.session for the Pyrogram user account.

Reads TELEGRAM_API_ID / TELEGRAM_API_HASH from the environment (or .env), so no
credentials are hardcoded. Get them from https://my.telegram.org.
"""
import asyncio
import os

from pyrogram import Client

API_ID = os.environ.get("TELEGRAM_API_ID")
API_HASH = os.environ.get("TELEGRAM_API_HASH")

if not API_ID or not API_HASH:
    raise SystemExit(
        "Set TELEGRAM_API_ID and TELEGRAM_API_HASH (export them or put them in "
        ".env and `set -a; . ./.env; set +a`) before running this script."
    )


async def main():
    async with Client("pyro_session", api_id=int(API_ID), api_hash=API_HASH) as app:
        me = await app.get_me()
        print(f"Authenticated as: {me.first_name} (@{me.username})")
        print("Session saved to pyro_session.session")


asyncio.run(main())
