"""
newcontact.py — уведомления о новых контактах.

При первом сообщении от нового человека отправляет владельцу карточку:
  • базовая информация из Telegram API (ID, имя, юзернейм, язык, premium)
  • аватарка профиля (если есть)
  • бесплатные данные из Funstat API

Переменная окружения:
  FUNSTAT_TOKEN — токен Funstat API (задаётся на Railway)

Бесплатные эндпоинты (без списания кредитов):
  GET /api/v1/users/{id}/stats_min      — базовая статистика пользователя
  GET /api/v1/users/{id}/groups_count   — количество известных групп
  GET /api/v1/users/{id}/messages_count — количество известных сообщений
  GET /api/v1/users/reputation          — репутация пользователя

Схема ответа любого эндпоинта:
  { "success": bool, "tech": { "request_cost": ..., ... }, "data": <payload> }

Поля user_stats_min (из swagger-схемы):
  id, first_name, last_name, is_bot, is_active,
  first_msg_date, last_msg_date, total_msg_count,
  msg_in_groups_count, adm_in_groups_count,
  usernames_count, names_count, total_groups
"""

import asyncio
import logging
import os

import aiohttp
from aiogram import Bot
from aiogram.types import Message

log = logging.getLogger(__name__)

FUNSTAT_TOKEN = os.getenv("FUNSTAT_TOKEN", "")
FUNSTAT_BASE  = "https://funstatbot.info/api/v1"


# ── Вспомогательные ──────────────────────────────────────────────────

def _esc(value) -> str:
    """HTML-экранирование для parse_mode=HTML."""
    if value is None:
        return "—"
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _fmt_date(val) -> str:
    """Укорачиваем дату — убираем миллисекунды и T-разделитель."""
    if val is None:
        return "—"
    s = str(val).replace("T", " ").replace("Z", "")
    if "." in s:
        s = s[:s.index(".")]
    return s.strip() or "—"


# ── Funstat API ───────────────────────────────────────────────────────

async def _get(
    session: aiohttp.ClientSession,
    url: str,
    params: dict | None = None,
) -> dict | None:
    """
    Один GET-запрос к Funstat.
    Автоматически извлекает .data из обёртки { success, tech, data }.
    Возвращает dict/value или None при ошибке.
    """
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                body = await resp.json(content_type=None)
                if isinstance(body, dict):
                    if not body.get("success", True) is False:
                        return body.get("data", body)
                return body
            log.debug(f"[FUNSTAT] {url} → HTTP {resp.status}")
    except Exception as e:
        log.debug(f"[FUNSTAT] {url} → {e}")
    return None


async def _fetch_funstat(user_id: int) -> dict:
    """
    Параллельно запрашивает все бесплатные эндпоинты Funstat.
    Возвращает объединённый dict с данными из всех источников.
    Пустой dict если токен не задан или все запросы не удались.
    """
    if not FUNSTAT_TOKEN:
        return {}

    headers = {"Authorization": f"Bearer {FUNSTAT_TOKEN}"}

    async with aiohttp.ClientSession(headers=headers) as session:
        results = await asyncio.gather(
            _get(session, f"{FUNSTAT_BASE}/users/{user_id}/stats_min"),
            _get(session, f"{FUNSTAT_BASE}/users/{user_id}/groups_count"),
            _get(session, f"{FUNSTAT_BASE}/users/{user_id}/messages_count"),
            _get(session, f"{FUNSTAT_BASE}/users/reputation", {"id": user_id}),
        )

    combined: dict = {}
    labels = ("stats_min", "groups_count", "messages_count", "reputation")
    for label, data in zip(labels, results):
        if data:
            if isinstance(data, dict):
                combined.update(data)
            else:
                combined[label] = data  # скалярный ответ (просто число)
            log.debug(f"[FUNSTAT] {label}: {data}")

    log.info(f"[FUNSTAT] user_id={user_id} fields={list(combined.keys())}")
    return combined


# ── Форматирование карточки ───────────────────────────────────────────

def _format_card(user, funstat: dict) -> str:
    uid      = user.id
    name     = _esc(user.full_name)
    username = f"@{user.username}" if user.username else "—"
    lang     = _esc(user.language_code) if user.language_code else "—"
    premium  = "⭐ Да" if getattr(user, "is_premium", False) else "Нет"

    lines = [
        "👤 <b>Новый контакт</b>\n",
        f"<b>Имя:</b> {name}",
        f"<b>ID:</b> <code>{uid}</code>",
        f"<b>Username:</b> {username}",
        f"<b>Язык аккаунта:</b> {lang}",
        f"<b>Premium:</b> {premium}",
    ]

    if funstat:
        lines.append("")  # визуальный разделитель

        def _v(*keys):
            """Первое ненулевое значение из funstat по списку ключей."""
            for k in keys:
                v = funstat.get(k)
                if v not in (None, "", 0, False, [], {}):
                    return v
            return None

        # Поля из user_stats_min (реальные имена из swagger-схемы)
        first_msg  = _v("first_msg_date")
        last_msg   = _v("last_msg_date")
        total_msg  = _v("total_msg_count")
        total_grp  = _v("total_groups", "groups_count")
        in_groups  = _v("msg_in_groups_count")
        adm_groups = _v("adm_in_groups_count")
        unames_cnt = _v("usernames_count")
        names_cnt  = _v("names_count")
        is_active  = funstat.get("is_active")
        is_bot     = funstat.get("is_bot")

        # Репутация (отдельный эндпоинт, имя поля уточним по ответу)
        reputation = _v("reputation", "rep", "score", "value")

        if first_msg:
            lines.append(f"<b>Впервые замечен:</b> {_fmt_date(first_msg)}")
        if last_msg:
            lines.append(f"<b>Последняя активность:</b> {_fmt_date(last_msg)}")
        if is_active is not None:
            lines.append(f"<b>Активен в базе:</b> {'Да' if is_active else 'Нет'}")
        if is_bot:
            lines.append(f"<b>Бот:</b> Да")
        if reputation is not None:
            lines.append(f"<b>Репутация Funstat:</b> {_esc(reputation)}")
        if total_msg is not None:
            lines.append(f"<b>Сообщений в базе:</b> {_esc(total_msg)}")
        if in_groups is not None:
            lines.append(f"<b>Сообщений в группах:</b> {_esc(in_groups)}")
        if total_grp is not None:
            lines.append(f"<b>Известных групп:</b> {_esc(total_grp)}")
        if adm_groups is not None:
            lines.append(f"<b>Групп был администратором:</b> {_esc(adm_groups)}")
        if unames_cnt is not None:
            lines.append(f"<b>Смен юзернейма:</b> {_esc(unames_cnt)}")
        if names_cnt is not None:
            lines.append(f"<b>Смен имени:</b> {_esc(names_cnt)}")

    return "\n".join(lines)


# ── Публичная функция ─────────────────────────────────────────────────

async def notify_new_contact(bot: Bot, owner_id: int, message: Message) -> None:
    """
    Вызывается из bot.py при первом входящем сообщении от нового chat_id.
    Отправляет владельцу карточку контакта в личку.
    """
    user = message.from_user
    if user is None:
        return

    # Параллельно: аватарка + Funstat (не блокируем друг друга)
    photo_file_id: str | None = None
    try:
        photos = await bot.get_user_profile_photos(user.id, limit=1)
        if photos.total_count > 0:
            photo_file_id = photos.photos[0][0].file_id
    except Exception as e:
        log.warning(f"[NEWCONTACT] аватарка {user.id}: {e}")

    funstat = await _fetch_funstat(user.id)
    card    = _format_card(user, funstat)

    try:
        if photo_file_id:
            await bot.send_photo(
                owner_id,
                photo=photo_file_id,
                caption=card,
                parse_mode="HTML",
            )
        else:
            await bot.send_message(owner_id, card, parse_mode="HTML")
    except Exception as e:
        log.warning(f"[NEWCONTACT] отправка карточки владельцу {owner_id}: {e}")
        try:
            await bot.send_message(owner_id, card, parse_mode="HTML")
        except Exception as e2:
            log.warning(f"[NEWCONTACT] fallback: {e2}")
