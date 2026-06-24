#!/usr/bin/env python3
"""
Генератор SESSION_STRING для Railway.
Запусти один раз, введи номер телефона и код из SMS.
Скопируй вывод в переменную SPAM_SESSION на Railway.

Использует официальные credentials Telegram Desktop
(не нужен my.telegram.org).
"""
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded

API_ID   = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"

async def main():
    async with Client(
        name="session_gen",
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True,
    ) as app:
        session = await app.export_session_string()
        print("\n" + "="*60)
        print("Твой SESSION_STRING (скопируй целиком):")
        print("="*60)
        print(session)
        print("="*60 + "\n")
        print("Вставь это значение в Railway как переменную SPAM_SESSION")

import asyncio
asyncio.run(main())
