import asyncio
from app.bot.wiring import _format_manual_signal, _send_with_retry

async def send_test():
    sig = {
        'action': 'BUY',
        'entry': 4100.0,
        'entry_end': 4105.0,
        'sl': 4090.0,
        'tps': [4110.0, 4120.0, 4130.0],
        'risk': 12.5,
        'rr_entry': 4102.5
    }
    text = "🧪 <b>[TEST POST - IGNORE]</b>\n\n" + _format_manual_signal(sig, 999)
    print("Sending test message to channel...")
    await _send_with_retry(text)
    print("Sent!")

if __name__ == '__main__':
    asyncio.run(send_test())
