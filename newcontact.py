"""
newcontact.py — уведомления о новых контактах.

При первом сообщении от нового человека отправляет владельцу карточку:
  • базовая информация из Telegram (ID, имя, юзернейм)
  • аватарка профиля (если есть)
  • бесплатные данные из Funstat API (stats_min, groups_count, messages_count, reputation)
  • история имён из /users/{id}/names  (COST 3)
  • история юзернеймов из /users/{id}/usernames  (COST 3)

Переменная окружения:
  FUNSTAT_TOKEN — токен Funstat API (задаётся на Railway)

Схема ответа: { "success": bool, "tech": {...}, "data": <payload> }
Поля user_stats_min: id, first_name, last_name, is_bot, is_active,
  first_msg_date, last_msg_date, total_msg_count,
  msg_in_groups_count, adm_in_groups_count,
  usernames_count, names_count, total_groups
Поля user_name_inf (история): name, date_time
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


def _fmt_date_short(val) -> str:
    """Только дата без времени — для истории имён/юзернеймов."""
    full = _fmt_date(val)
    return full[:10] if len(full) >= 10 else full  # "2024-03-15"


# ── Funstat API ───────────────────────────────────────────────────────

async def _get(
    session: aiohttp.ClientSession,
    url: str,
    params: dict | None = None,
) -> object:
    """
    GET-запрос к Funstat. Извлекает .data из обёртки { success, tech, data }.
    Возвращает dict/list/scalar или None при ошибке.
    """
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                body = await resp.json(content_type=None)
                if isinstance(body, dict):
                    return body.get("data", body)
                return body
            log.debug(f"[FUNSTAT] {url} → HTTP {resp.status}")
    except Exception as e:
        log.debug(f"[FUNSTAT] {url} → {e}")
    return None


async def _fetch_funstat(user_id: int) -> tuple[dict, list, list]:
    """
    Параллельно запрашивает все эндпоинты Funstat.
    Возвращает (stats_dict, names_list, usernames_list).
    """
    if not FUNSTAT_TOKEN:
        return {}, [], []

    headers = {"Authorization": f"Bearer {FUNSTAT_TOKEN}"}

    async with aiohttp.ClientSession(headers=headers) as session:
        (
            stats_min,
            groups_count,
            messages_count,
            reputation,
            names_raw,
            usernames_raw,
        ) = await asyncio.gather(
            _get(session, f"{FUNSTAT_BASE}/users/{user_id}/stats_min"),
            _get(session, f"{FUNSTAT_BASE}/users/{user_id}/groups_count"),
            _get(session, f"{FUNSTAT_BASE}/users/{user_id}/messages_count"),
            _get(session, f"{FUNSTAT_BASE}/users/reputation", {"id": user_id}),
            _get(session, f"{FUNSTAT_BASE}/users/{user_id}/names"),
            _get(session, f"{FUNSTAT_BASE}/users/{user_id}/usernames"),
        )

    # Собираем плоский dict из скалярных ответов
    combined: dict = {}
    for data in (stats_min, groups_count, messages_count, reputation):
        if isinstance(data, dict):
            combined.update(data)
        elif data is not None:
            pass  # скалярный ответ — игнорируем, данные в stats_min

    # Исторические списки
    names_list     = names_raw     if isinstance(names_raw, list)     else []
    usernames_list = usernames_raw if isinstance(usernames_raw, list) else []

    log.info(
        f"[FUNSTAT] user_id={user_id} "
        f"fields={list(combined.keys())} "
        f"names={len(names_list)} usernames={len(usernames_list)}"
    )
    return combined, names_list, usernames_list


# ── Форматирование карточки ───────────────────────────────────────────

def _format_card(user, funstat: dict, names: list, usernames: list) -> str:
    uid      = user.id
    name     = _esc(user.full_name)
    username = f"@{user.username}" if user.username else "—"

    lines = [
        "👤 <b>Новый контакт</b>\n",
        f"<b>Имя:</b> {name}",
        f"<b>ID:</b> <code>{uid}</code>",
        f"<b>Username:</b> {username}",
    ]

    if funstat:
        lines.append("")

        def _v(*keys):
            for k in keys:
                v = funstat.get(k)
                if v not in (None, "", 0, False, [], {}):
                    return v
            return None

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
        reputation = _v("reputation", "rep", "score", "value")

        if first_msg:
            lines.append(f"<b>Впервые замечен:</b> {_fmt_date(first_msg)}")
        if last_msg:
            lines.append(f"<b>Последняя активность:</b> {_fmt_date(last_msg)}")
        if is_active is not None:
            lines.append(f"<b>Активен в базе:</b> {'Да' if is_active else 'Нет'}")
        if is_bot:
            lines.append("<b>Бот:</b> Да")
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

    # История имён — поля: name, date_time
    if names:
        lines.append("")
        cnt = len(names)
        header = f"<b>История имён ({cnt}):</b>"
        rows = []
        for entry in names:
            n   = _esc(entry.get("name") or entry.get("first_name") or "")
            dt  = _fmt_date_short(entry.get("date_time") or entry.get("date"))
            if n:
                rows.append(f"  {dt}  {n}")
        if rows:
            lines.append(header)
            lines.extend(rows)
    elif funstat:
        names_cnt_val = funstat.get("names_count")
        if names_cnt_val:
            lines.append(f"<b>Смен имени:</b> {_esc(names_cnt_val)}")

    # История юзернеймов — поля: username / name, date_time
    if usernames:
        lines.append("")
        cnt = len(usernames)
        header = f"<b>История юзернеймов ({cnt}):</b>"
        rows = []
        for entry in usernames:
            un  = _esc(entry.get("username") or entry.get("name") or "")
            dt  = _fmt_date_short(entry.get("date_time") or entry.get("date"))
            if un:
                rows.append(f"  {dt}  @{un}" if not un.startswith("@") else f"  {dt}  {un}")
        if rows:
            lines.append(header)
            lines.extend(rows)
    elif funstat:
        unames_cnt_val = funstat.get("usernames_count")
        if unames_cnt_val:
            lines.append(f"<b>Смен юзернейма:</b> {_esc(unames_cnt_val)}")

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

    # Аватарка
    photo_file_id: str | None = None
    try:
        photos = await bot.get_user_profile_photos(user.id, limit=1)
        if photos.total_count > 0:
            photo_file_id = photos.photos[0][0].file_id
    except Exception as e:
        log.warning(f"[NEWCONTACT] аватарка {user.id}: {e}")

    funstat, names, usernames = await _fetch_funstat(user.id)
    card = _format_card(user, funstat, names, usernames)

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
