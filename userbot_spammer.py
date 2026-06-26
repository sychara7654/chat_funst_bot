#!/usr/bin/env python3
"""
Userbot-спаммер на Pyrogram.

Команды (только от владельца аккаунта, в группах):
  /spam <текст>  — запускает рассылку каждую минуту в текущем чате
  /stop          — останавливает рассылку в текущем чате

• Команды удаляются сразу.
• В «Избранное» отправляется "я начинаю спам ✅".
• Несколько чатов работают одновременно.
"""

import asyncio
import logging
import os

from pyrogram import Client, filters
from pyrogram.types import Message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# Официальные credentials Telegram Desktop (публичные)
API_ID   = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"
SESSION  = os.environ["SPAM_SESSION"]

app = Client(
    name="spammer",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION,
)

# chat_id -> asyncio.Task
spam_tasks: dict[int, asyncio.Task] = {}


async def do_spam(client: Client, chat_id: int, text: str) -> None:
    """Отправляет text в chat_id каждые 60 секунд до отмены."""
    try:
        # Резолвим peer один раз перед циклом — без этого Pyrogram
        # падает с ValueError: Peer id invalid после рестарта контейнера
        # (session_string не сохраняет кэш entity между запусками).
        try:
            await client.get_chat(chat_id)
        except Exception as e:
            logging.warning(f"[spam] не удалось разрешить peer {chat_id}: {e}")
        while True:
            try:
                await client.send_message(chat_id, text)
            except ValueError as e:
                # Повторная попытка разрешить peer и отправить
                logging.warning(f"[spam] чат {chat_id}: Peer invalid, пробую get_chat: {e}")
                try:
                    await client.get_chat(chat_id)
                    await client.send_message(chat_id, text)
                except Exception as e2:
                    logging.warning(f"[spam] чат {chat_id}: повторная ошибка: {e2}")
            except Exception as e:
                logging.warning(f"[spam] чат {chat_id}: ошибка отправки: {e}")
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass


async def safe_delete(msg: Message) -> None:
    try:
        await msg.delete()
    except Exception:
        pass


@app.on_message(filters.me & filters.group & filters.command("spam", prefixes="/"))
async def cmd_spam(client: Client, msg: Message) -> None:
    parts = msg.text.split(None, 1)
    text = parts[1].strip() if len(parts) > 1 else ""

    await safe_delete(msg)

    if not text:
        return

    chat_id = msg.chat.id

    # Останавливаем предыдущий спам в этом чате (если был)
    old_task = spam_tasks.pop(chat_id, None)
    if old_task and not old_task.done():
        old_task.cancel()
        try:
            await old_task
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(do_spam(client, chat_id, text))
    spam_tasks[chat_id] = task

    try:
        await client.send_message("me", "я начинаю спам ✅")
    except Exception as e:
        logging.warning(f"Не удалось написать в Избранное: {e}")


@app.on_message(filters.me & filters.group & filters.command("stop", prefixes="/"))
async def cmd_stop(client: Client, msg: Message) -> None:
    chat_id = msg.chat.id

    await safe_delete(msg)

    task = spam_tasks.pop(chat_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        try:
            await client.send_message("me", "спам остановлен 🛑")
        except Exception:
            pass


if __name__ == "__main__":
    app.run()
