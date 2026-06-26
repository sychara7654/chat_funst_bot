#!/usr/bin/env python3
"""
Userbot-рассыльщик на Pyrogram.

Управление только из «Избранного» (Saved Messages):
  /spam <текст>  — рассылать текст во все группы каждую минуту
  /stop          — остановить рассылку
  /status        — показать статус (активна ли рассылка, сколько групп)
"""

import asyncio
import logging
import os

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ChatType, ChatMemberStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

API_ID   = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"
SESSION  = os.environ["SPAM_SESSION"]

app = Client(
    name="spammer",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION,
)

# Кэшируем свой ID чтобы не дёргать get_me() на каждое сообщение
_my_id: int | None = None

_spam_task: asyncio.Task | None = None
_spam_text: str = ""


async def get_my_id(client: Client) -> int:
    global _my_id
    if _my_id is None:
        me = await client.get_me()
        _my_id = me.id
    return _my_id


async def get_active_groups(client: Client) -> list[int]:
    """Возвращает chat_id всех групп где пользователь не в муте и не забанен."""
    groups = []
    async for dialog in client.get_dialogs():
        chat = dialog.chat
        if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            continue
        try:
            member = await client.get_chat_member(chat.id, "me")
            if member.status == ChatMemberStatus.BANNED:
                logging.info(f"[spam] пропускаю '{chat.title}' — бан")
                continue
            if member.status == ChatMemberStatus.RESTRICTED:
                privs = getattr(member, "privileges", None)
                can_send = getattr(privs, "can_send_messages", True) if privs else True
                if not can_send:
                    logging.info(f"[spam] пропускаю '{chat.title}' — мут")
                    continue
        except Exception as e:
            logging.warning(f"[spam] статус в {chat.id} не проверить: {e}")
        groups.append(chat.id)
    logging.info(f"[spam] доступных групп: {len(groups)}")
    return groups


async def spam_loop(client: Client, text: str) -> None:
    """Бесконечно рассылает text во все группы раз в 60 секунд."""
    try:
        while True:
            groups = await get_active_groups(client)
            sent = 0
            failed = 0
            for chat_id in groups:
                try:
                    await client.send_message(chat_id, text)
                    sent += 1
                except Exception as e:
                    logging.warning(f"[spam] чат {chat_id}: {e}")
                    failed += 1
                await asyncio.sleep(0.5)

            summary = (
                f"[spam] итерация завершена: "
                f"отправлено={sent}, ошибок={failed}, групп={len(groups)}"
            )
            logging.info(summary)
            try:
                await client.send_message(
                    "me",
                    f"📊 {summary}"
                )
            except Exception:
                pass

            await asyncio.sleep(60)
    except asyncio.CancelledError:
        logging.info("[spam] рассылка отменена")


def _is_saved_messages(msg: Message) -> bool:
    """True если сообщение отправлено в «Избранное» (чат с самим собой)."""
    return _my_id is not None and msg.chat.id == _my_id


@app.on_message(filters.me & filters.private & filters.command("spam", prefixes="/"))
async def cmd_spam(client: Client, msg: Message) -> None:
    await get_my_id(client)
    if not _is_saved_messages(msg):
        return

    global _spam_task, _spam_text

    parts = msg.text.split(None, 1)
    text = parts[1].strip() if len(parts) > 1 else ""

    if not text:
        await client.send_message("me", "❌ Укажи текст: /spam <текст рассылки>")
        return

    if _spam_task and not _spam_task.done():
        _spam_task.cancel()
        try:
            await _spam_task
        except asyncio.CancelledError:
            pass

    _spam_text = text

    await client.send_message("me", "🔍 Собираю список групп...")
    groups = await get_active_groups(client)

    if not groups:
        await client.send_message("me", "❌ Нет доступных групп для рассылки.")
        return

    _spam_task = asyncio.create_task(spam_loop(client, text))
    await client.send_message(
        "me",
        f"✅ Рассылка запущена\n"
        f"📝 Текст: {text[:150]}\n"
        f"💬 Групп найдено: {len(groups)}\n"
        f"⏱ Интервал: 60 сек"
    )


@app.on_message(filters.me & filters.private & filters.command("stop", prefixes="/"))
async def cmd_stop(client: Client, msg: Message) -> None:
    await get_my_id(client)
    if not _is_saved_messages(msg):
        return

    global _spam_task

    if _spam_task and not _spam_task.done():
        _spam_task.cancel()
        try:
            await _spam_task
        except asyncio.CancelledError:
            pass
        _spam_task = None
        await client.send_message("me", "🛑 Рассылка остановлена.")
    else:
        await client.send_message("me", "ℹ️ Рассылка сейчас не активна.")


@app.on_message(filters.me & filters.private & filters.command("status", prefixes="/"))
async def cmd_status(client: Client, msg: Message) -> None:
    await get_my_id(client)
    if not _is_saved_messages(msg):
        return

    global _spam_task, _spam_text

    active = _spam_task is not None and not _spam_task.done()

    if active:
        groups = await get_active_groups(client)
        text = (
            f"📊 Рассылка активна 🟢\n\n"
            f"📝 Текст: {_spam_text[:150]}\n"
            f"💬 Групп: {len(groups)}\n"
            f"⏱ Интервал: 60 сек"
        )
    else:
        text = "📊 Рассылка остановлена 🔴"

    await client.send_message("me", text)


if __name__ == "__main__":
    app.run()
