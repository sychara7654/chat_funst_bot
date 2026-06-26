#!/usr/bin/env python3
"""
Userbot-рассыльщик на Pyrogram.

Управление только из \u00abИзбранного\u00bb (Saved Messages):
  /spam <текст>  \u2014 рассылать текст во все группы каждую минуту
  /stop          \u2014 остановить рассылку
  /status        \u2014 показать статус (активна ли рассылка, сколько групп)
"""

import asyncio
import base64
import json
import logging
import os
import urllib.request

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

GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO     = "sychara7654/chat_funst_bot"
GITHUB_LOG_PATH = ".bot_state/userbot.log"

app = Client(
    name="spammer",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION,
)

_my_id: int | None = None
_spam_task: asyncio.Task | None = None
_spam_text: str = ""


# ---------------------------------------------------------------------------
# Memory log handler + GitHub push
# ---------------------------------------------------------------------------

class MemoryLogHandler(logging.Handler):
    def __init__(self, maxlines: int = 600) -> None:
        super().__init__()
        self._lines: list[str] = []
        self._maxlines = maxlines

    def emit(self, record: logging.LogRecord) -> None:
        self._lines.append(self.format(record))
        if len(self._lines) > self._maxlines:
            self._lines = self._lines[-self._maxlines:]

    def get_text(self) -> str:
        return "\n".join(self._lines)


_mem_handler = MemoryLogHandler(maxlines=600)
_mem_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger().addHandler(_mem_handler)


async def github_push_logs() -> None:
    if not GITHUB_TOKEN:
        return
    text = _mem_handler.get_text()
    encoded = base64.b64encode(text.encode()).decode()
    api = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_LOG_PATH}"

    sha = None
    try:
        req = urllib.request.Request(api, headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "userbot-logger",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            sha = data.get("sha")
    except Exception:
        pass

    payload: dict = {
        "message": "[userbot-log] push logs",
        "content": encoded,
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha

    try:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(api, data=body, method="PUT", headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "userbot-logger",
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            logging.info("[log-push] логи юзербота отправлены в GitHub")
    except Exception as e:
        logging.warning(f"[log-push] ошибка: {e}")


async def github_log_loop() -> None:
    """Каждые 2 минуты пушит логи юзербота в GitHub."""
    while True:
        await asyncio.sleep(120)
        try:
            await github_push_logs()
        except Exception as e:
            logging.warning(f"[log-loop] ошибка: {e}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
                logging.info(f"[spam] пропускаю '{chat.title}' \u2014 бан")
                continue
            if member.status == ChatMemberStatus.RESTRICTED:
                privs = getattr(member, "privileges", None)
                can_send = getattr(privs, "can_send_messages", True) if privs else True
                if not can_send:
                    logging.info(f"[spam] пропускаю '{chat.title}' \u2014 мут")
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
                f"[spam] итерация: отправлено={sent}, ошибок={failed}, групп={len(groups)}"
            )
            logging.info(summary)
            try:
                await client.send_message("me", f"\U0001f4ca {summary}")
            except Exception:
                pass

            await asyncio.sleep(60)
    except asyncio.CancelledError:
        logging.info("[spam] рассылка отменена")


def _is_saved_messages(msg: Message) -> bool:
    return _my_id is not None and msg.chat.id == _my_id


# ---------------------------------------------------------------------------
# Commands (only from Saved Messages)
# ---------------------------------------------------------------------------

@app.on_message(filters.me & filters.private & filters.command("spam", prefixes="/"))
async def cmd_spam(client: Client, msg: Message) -> None:
    await get_my_id(client)
    if not _is_saved_messages(msg):
        return

    global _spam_task, _spam_text

    parts = msg.text.split(None, 1)
    text = parts[1].strip() if len(parts) > 1 else ""

    if not text:
        await client.send_message("me", "\u274c Укажи текст: /spam <текст рассылки>")
        return

    if _spam_task and not _spam_task.done():
        _spam_task.cancel()
        try:
            await _spam_task
        except asyncio.CancelledError:
            pass

    _spam_text = text

    await client.send_message("me", "\U0001f50d Собираю список групп...")
    groups = await get_active_groups(client)

    if not groups:
        await client.send_message("me", "\u274c Нет доступных групп для рассылки.")
        return

    _spam_task = asyncio.create_task(spam_loop(client, text))
    await client.send_message(
        "me",
        f"\u2705 Рассылка запущена\n"
        f"\U0001f4dd Текст: {text[:150]}\n"
        f"\U0001f4ac Групп найдено: {len(groups)}\n"
        f"\u23f1 Интервал: 60 сек",
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
        await client.send_message("me", "\U0001f6d1 Рассылка остановлена.")
    else:
        await client.send_message("me", "\u2139\ufe0f Рассылка сейчас не активна.")


@app.on_message(filters.me & filters.private & filters.command("status", prefixes="/"))
async def cmd_status(client: Client, msg: Message) -> None:
    await get_my_id(client)
    if not _is_saved_messages(msg):
        return

    active = _spam_task is not None and not _spam_task.done()

    if active:
        groups = await get_active_groups(client)
        text = (
            f"\U0001f4ca Рассылка активна \U0001f7e2\n\n"
            f"\U0001f4dd Текст: {_spam_text[:150]}\n"
            f"\U0001f4ac Групп: {len(groups)}\n"
            f"\u23f1 Интервал: 60 сек"
        )
    else:
        text = "\U0001f4ca Рассылка остановлена \U0001f534"

    await client.send_message("me", text)


@app.on_message(filters.me & filters.private & filters.command("logs", prefixes="/"))
async def cmd_logs(client: Client, msg: Message) -> None:
    """Немедленный пуш логов в GitHub из Избранного."""
    await get_my_id(client)
    if not _is_saved_messages(msg):
        return
    await client.send_message("me", "\U0001f4e4 Отправляю логи в GitHub...")
    await github_push_logs()
    await client.send_message("me", "\u2705 Логи отправлены в .bot_state/userbot.log")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _run() -> None:
    async with app:
        logging.info("[userbot] запущен, жду команды в Избранном")
        await get_my_id(app)
        asyncio.create_task(github_log_loop())
        # Первый пуш логов через 30 сек после старта
        await asyncio.sleep(30)
        await github_push_logs()
        # Ждём вечно (обновления обрабатывает Pyrogram в фоне)
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(_run())
