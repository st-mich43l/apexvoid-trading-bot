import asyncio
import aiohttp
from app.signals.price import get_xau_price

async def main():
    async with aiohttp.ClientSession() as session:
        price = await get_xau_price(session)
        print("Current XAU/USD price:", price)

asyncio.run(main())
