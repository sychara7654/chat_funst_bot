#!/usr/bin/env python3
"""
Userbot-рассыльщик на Pyrogram.
Команды отправлять в Saved Messages (Избранное):
  /spam <текст>  — запустить рассылку
  /stop          — остановить
  /status        — статус
  /logs          — пушит логи в GitHub
"""

import asyncio
import base64
import json
import logging
import os
import urllib.request

from pyrogram import Client
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
GITHUB_BRANCH   = "bot-state"

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
# Memory log handler
# ---------------------------------------------------------------------------

class MemoryLogHandler(logging.Handler):
    def __init__(self, maxlines: int = 800) -> None:
        super().__init__()
        self._lines: list[str] = []
        self._maxlines = maxlines

    def emit(self, record: logging.LogRecord) -> None:
        self._lines.append(self.format(record))
        if len(self._lines) > self._maxlines:
            self._lines = self._lines[-self._maxlines:]

    def get_text(self) -> str:
        return "\n".join(self._lines)


_mem_handler = MemoryLogHandler()
_mem_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger().addHandler(_mem_handler)


# ---------------------------------------------------------------------------
# GitHub log push
# ---------------------------------------------------------------------------

async def _gh_get_sha() -> str | None:
    api = (
        f"https://api.github.com/repos/{GITHUB_REPO}"
        f"/contents/{GITHUB_LOG_PATH}?ref={GITHUB_BRANCH}"
    )
    try:
        req = urllib.request.Request(api, headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "userbot-logger",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("sha")
    except Exception:
        return None


async def github_push_logs() -> None:
    if not GITHUB_TOKEN:
        return
    text = _mem_handler.get_text()
    encoded = base64.b64encode(text.encode()).decode()
    api = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_LOG_PATH}"
    sha = await _gh_get_sha()
    payload: dict = {"message": "[userbot-log] push logs", "content": encoded, "branch": GITHUB_BRANCH}
    if sha:
        payload["sha"] = sha
    for attempt in range(3):
        try:
            body = json.dumps(payload).encode()
            req = urllib.request.Request(api, data=body, method="PUT", headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "userbot-logger",
                "Content-Type": "application/json",
            })
            with urllib.request.urlopen(req, timeout=15):
                logging.info("[log-push] логи отправлены в GitHub")
                return
        except urllib.error.HTTPError as e:
            if e.code in (409, 422) and attempt < 2:
                sha = await _gh_get_sha()
                if sha:
                    payload["sha"] = sha
                elif "sha" in payload:
                    del payload["sha"]
            else:
                logging.warning(f"[log-push] ошибка: {e}")
                return
        except Exception as e:
            logging.warning(f"[log-push] ошибка: {e}")
            return


async def github_log_loop() -> None:
    while True:
        await asyncio.sleep(120)
        try:
            await github_push_logs()
        except Exception as e:
            logging.warning(f"[log-loop] ошибка: {e}")


# ---------------------------------------------------------------------------
# Spam helpers
# ---------------------------------------------------------------------------

async def get_active_groups() -> list[int]:
    groups = []
    async for dialog in app.get_dialogs():
        chat = dialog.chat
        if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            continue
        try:
            member = await app.get_chat_member(chat.id, "me")
            if member.status == ChatMemberStatus.BANNED:
                continue
            if member.status == ChatMemberStatus.RESTRICTED:
                privs = getattr(member, "privileges", None)
                can_send = getattr(privs, "can_send_messages", True) if privs else True
                if not can_send:
                    continue
        except Exception:
            pass
        groups.append(chat.id)
    logging.info(f"[spam] доступных групп: {len(groups)}")
    return groups


async def spam_loop(text: str) -> None:
    try:
        while True:
            groups = await get_active_groups()
            sent = 0
            failed = 0
            for chat_id in groups:
                try:
                    await app.send_message(chat_id, text)
                    sent += 1
                except Exception as e:
                    logging.warning(f"[spam] чат {chat_id}: {e}")
                    failed += 1
                await asyncio.sleep(0.5)
            summary = f"итерация: отправлено={sent}, ошибок={failed}, групп={len(groups)}"
            logging.info(f"[spam] {summary}")
            try:
                await app.send_message("me", f"\U0001f4ca {summary}")
            except Exception:
                pass
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        logging.info("[spam] рассылка отменена")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def do_spam(text: str) -> None:
    global _spam_task, _spam_text
    if not text:
        await app.send_message("me", "\u274c Укажи текст: /spam <текст>")
        return
    if _spam_task and not _spam_task.done():
        _spam_task.cancel()
        try:
            await _spam_task
        except asyncio.CancelledError:
            pass
    _spam_text = text
    logging.info(f"[cmd] /spam: {text[:80]!r}")
    await app.send_message("me", "\U0001f50d Собираю список групп...")
    groups = await get_active_groups()
    if not groups:
        await app.send_message("me", "\u274c Нет доступных групп.")
        return
    _spam_task = asyncio.create_task(spam_loop(text))
    await app.send_message(
        "me",
        f"\u2705 Рассылка запущена\n"
        f"\U0001f4dd {text[:150]}\n"
        f"\U0001f4ac Групп: {len(groups)}\n"
        f"\u23f1 Интервал: 60 сек",
    )


async def do_stop() -> None:
    global _spam_task
    logging.info("[cmd] /stop")
    if _spam_task and not _spam_task.done():
        _spam_task.cancel()
        try:
            await _spam_task
        except asyncio.CancelledError:
            pass
        _spam_task = None
        await app.send_message("me", "\U0001f6d1 Рассылка остановлена.")
    else:
        await app.send_message("me", "\u2139\ufe0f Рассылка не активна.")


async def do_status() -> None:
    logging.info("[cmd] /status")
    active = _spam_task is not None and not _spam_task.done()
    if active:
        groups = await get_active_groups()
        await app.send_message(
            "me",
            f"\U0001f4ca Рассылка активна \U0001f7e2\n"
            f"\U0001f4dd {_spam_text[:150]}\n"
            f"\U0001f4ac Групп: {len(groups)}",
        )
    else:
        await app.send_message("me", "\U0001f4ca Рассылка остановлена \U0001f534")


# ---------------------------------------------------------------------------
# Polling loop — читает Избранное каждые 3 сек, ищет новые команды
# Не зависит от on_message / update handlers — работает всегда
# ---------------------------------------------------------------------------

async def command_poll_loop() -> None:
    """
    Каждые 3 секунды читает последние 5 сообщений из Saved Messages
    и обрабатывает команды которые ещё не обработали (по message_id).
    """
    last_seen_id: int = 0

    # Инициализируем last_seen_id текущим последним сообщением (не обрабатываем старые)
    try:
        async for msg in app.get_chat_history("me", limit=1):
            last_seen_id = msg.id
        logging.info(f"[poll] инициализирован, last_seen_id={last_seen_id}")
    except Exception as e:
        logging.warning(f"[poll] ошибка инициализации: {e}")

    while True:
        await asyncio.sleep(3)
        try:
            async for msg in app.get_chat_history("me", limit=10):
                if msg.id <= last_seen_id:
                    break  # дошли до уже обработанных
                text = msg.text or ""
                logging.info(f"[poll] новое сообщение id={msg.id}: {text[:60]!r}")
                last_seen_id = max(last_seen_id, msg.id)
                if text.startswith("/spam "):
                    await do_spam(text[6:].strip())
                elif text.strip() == "/stop":
                    await do_stop()
                elif text.strip() == "/status":
                    await do_status()
                elif text.strip() == "/logs":
                    await github_push_logs()
                    await app.send_message("me", "\u2705 Логи запушены")
        except Exception as e:
            logging.warning(f"[poll] ошибка: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    await app.start()
    logging.info("[userbot] запущен")

    me = await app.get_me()
    _my_id_val = me.id
    logging.info(f"[userbot] аккаунт: {me.first_name} (id={_my_id_val})")

    # Прогрев кэша пиров
    count = 0
    async for _ in app.get_dialogs():
        count += 1
    logging.info(f"[userbot] кэш прогрет: {count} диалогов")

    # Запускаем фоновые задачи
    asyncio.create_task(github_log_loop())
    asyncio.create_task(command_poll_loop())

    logging.info("[userbot] готов. Пиши команды в Избранное (Saved Messages)")

    # Первый пуш логов
    await asyncio.sleep(15)
    await github_push_logs()

    # Ждём вечно
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
