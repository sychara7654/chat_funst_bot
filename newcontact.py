"""
newcontact.py — уведомления о новых контактах.

При первом сообщении от нового человека отправляет владельцу карточку.

Режимы работы:
  • Полный (все платные эндпоинты) — только для owner_id из FNST_ID + ADMIN_ID
  • Базовый (только бесплатные) — для всех остальных

Переменные окружения:
  FUNSTAT_TOKEN — токен Funstat API (обязателен для любых данных Funstat)
  ADMIN_ID      — Telegram ID владельца (всегда получает полный режим)
  FNST_ID       — дополнительные ID через запятую, напр. "123456,789012"

Эндпоинты и стоимость:
  БЕСПЛАТНО:
    GET /api/v1/users/{id}/stats_min       — базовая статистика
    GET /api/v1/users/{id}/groups_count    — кол-во групп
    GET /api/v1/users/{id}/messages_count  — кол-во сообщений
    GET /api/v1/users/reputation           — репутация

  ПЛАТНО (только для FNST_ID / ADMIN_ID):
    GET /api/v1/users/{id}/stats           — полная статистика (COST 1)
    GET /api/v1/users/{id}/names           — история имён (COST 3)
    GET /api/v1/users/{id}/usernames       — история юзернеймов (COST 3)
    GET /api/v1/users/{id}/groups          — известные группы (COST 5)
    GET /api/v1/users/{id}/stickers        — стикер-паки (COST 1)

Схема ответа: { "success": bool, "tech": {...}, "data": <payload> }

Поля user_stats (swagger):
  id, first_name, last_name, is_bot, is_active,
  first_msg_date, last_msg_date, total_msg_count,
  msg_in_groups_count, adm_in_groups_count,
  usernames_count, names_count, total_groups,
  is_cyrillic_primary, lang_code, unique_percent,
  circle_count, voice_count, reply_percent,
  media_percent, link_percent, stars_val,
  gift_count, stars_level, birth_day, birth_month,
  birth_year, about
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

# Owner IDs, которым доступны платные запросы
_admin_raw = os.getenv("ADMIN_ID", "").strip()
_ADMIN_ID: int | None = int("".join(filter(str.isdigit, _admin_raw))) if _admin_raw else None

_fnst_raw = os.getenv("FNST_ID", "").strip()
_FNST_IDS: set[int] = set()
for _part in _fnst_raw.split(","):
    _part = _part.strip()
    if _part.lstrip("-").isdigit():
        _FNST_IDS.add(int(_part))
if _ADMIN_ID:
    _FNST_IDS.add(_ADMIN_ID)


def _is_full_mode(owner_id: int) -> bool:
    """True если owner_id имеет доступ ко всем платным эндпоинтам."""
    return owner_id in _FNST_IDS


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
    if val is None:
        return "—"
    s = str(val).replace("T", " ").replace("Z", "")
    if "." in s:
        s = s[:s.index(".")]
    return s.strip() or "—"


def _fmt_date_short(val) -> str:
    full = _fmt_date(val)
    return full[:10] if len(full) >= 10 else full


# ── Funstat API ───────────────────────────────────────────────────────

async def _get(
    session: aiohttp.ClientSession,
    url: str,
    params: dict | None = None,
) -> object:
    """GET-запрос. Извлекает .data из { success, tech, data }."""
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


async def _fetch_funstat(user_id: int, full: bool) -> tuple[dict, list, list, list, list]:
    """
    Запрашивает Funstat.
    full=True  → все платные эндпоинты (только для FNST_ID/ADMIN_ID)
    full=False → только бесплатные

    Возвращает: (stats_dict, names_list, usernames_list, groups_list, stickers_list)
    """
    if not FUNSTAT_TOKEN:
        return {}, [], [], [], []

    headers = {"Authorization": f"Bearer {FUNSTAT_TOKEN}"}

    async with aiohttp.ClientSession(headers=headers) as session:
        if full:
            # Платные + бесплатные
            (
                stats,
                messages_count,
                groups_count,
                reputation,
                names_raw,
                usernames_raw,
                groups_raw,
                stickers_raw,
            ) = await asyncio.gather(
                _get(session, f"{FUNSTAT_BASE}/users/{user_id}/stats"),          # COST 1
                _get(session, f"{FUNSTAT_BASE}/users/{user_id}/messages_count"), # FREE
                _get(session, f"{FUNSTAT_BASE}/users/{user_id}/groups_count"),   # FREE
                _get(session, f"{FUNSTAT_BASE}/users/reputation", {"id": user_id}), # FREE
                _get(session, f"{FUNSTAT_BASE}/users/{user_id}/names"),          # COST 3
                _get(session, f"{FUNSTAT_BASE}/users/{user_id}/usernames"),      # COST 3
                _get(session, f"{FUNSTAT_BASE}/users/{user_id}/groups"),         # COST 5
                _get(session, f"{FUNSTAT_BASE}/users/{user_id}/stickers"),       # COST 1
            )
        else:
            # Только бесплатные
            (
                stats,
                messages_count,
                groups_count,
                reputation,
            ) = await asyncio.gather(
                _get(session, f"{FUNSTAT_BASE}/users/{user_id}/stats_min"),      # FREE
                _get(session, f"{FUNSTAT_BASE}/users/{user_id}/messages_count"), # FREE
                _get(session, f"{FUNSTAT_BASE}/users/{user_id}/groups_count"),   # FREE
                _get(session, f"{FUNSTAT_BASE}/users/reputation", {"id": user_id}), # FREE
            )
            names_raw = usernames_raw = groups_raw = stickers_raw = None

    # Собираем плоский dict
    combined: dict = {}
    for data in (stats, messages_count, groups_count, reputation):
        if isinstance(data, dict):
            combined.update(data)

    names_list    = names_raw    if isinstance(names_raw, list)    else []
    unames_list   = usernames_raw if isinstance(usernames_raw, list) else []
    groups_list   = groups_raw   if isinstance(groups_raw, list)   else []
    stickers_list = stickers_raw if isinstance(stickers_raw, list) else []

    log.info(
        f"[FUNSTAT] user_id={user_id} full={full} "
        f"fields={len(combined)} names={len(names_list)} "
        f"unames={len(unames_list)} groups={len(groups_list)} "
        f"stickers={len(stickers_list)}"
    )
    return combined, names_list, unames_list, groups_list, stickers_list


# ── Форматирование карточки ───────────────────────────────────────────

def _pct(val) -> str:
    """Форматирует процент: 0.42 → '42%'."""
    if val is None:
        return "—"
    try:
        return f"{float(val):.0f}%"
    except Exception:
        return _esc(val)


def _format_card(
    user,
    funstat: dict,
    names: list,
    usernames: list,
    groups: list,
    stickers: list,
    full: bool,
) -> str:
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

        # Основные даты
        first_msg  = _v("first_msg_date")
        last_msg   = _v("last_msg_date")
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
            lines.append(f"<b>Репутация:</b> {_esc(reputation)}")

        # Статистика сообщений и групп
        total_msg  = _v("total_msg_count")
        in_groups  = _v("msg_in_groups_count")
        adm_groups = _v("adm_in_groups_count")
        total_grp  = _v("total_groups")

        if total_msg is not None:
            lines.append(f"<b>Сообщений в базе:</b> {_esc(total_msg)}")
        if in_groups is not None:
            lines.append(f"<b>Сообщений в группах:</b> {_esc(in_groups)}")
        if total_grp is not None:
            lines.append(f"<b>Известных групп:</b> {_esc(total_grp)}")
        if adm_groups is not None:
            lines.append(f"<b>Администрировал групп:</b> {_esc(adm_groups)}")

        # Только в полном режиме — расширенные поля из /stats
        if full:
            lang_code    = _v("lang_code")
            circle_cnt   = _v("circle_count")
            voice_cnt    = _v("voice_count")
            unique_pct   = funstat.get("unique_percent")
            reply_pct    = funstat.get("reply_percent")
            media_pct    = funstat.get("media_percent")
            link_pct     = funstat.get("link_percent")
            stars_val    = _v("stars_val")
            gift_cnt     = _v("gift_count")
            stars_lvl    = _v("stars_level")
            birth_d      = funstat.get("birth_day")
            birth_m      = funstat.get("birth_month")
            birth_y      = funstat.get("birth_year")
            about        = _v("about")

            if lang_code:
                lines.append(f"<b>Язык (Funstat):</b> {_esc(lang_code)}")

            birth_parts = [x for x in (birth_d, birth_m, birth_y) if x]
            if len(birth_parts) >= 2:
                lines.append(
                    f"<b>Дата рождения:</b> "
                    f"{birth_d or '?'}.{birth_m or '?'}"
                    + (f".{birth_y}" if birth_y else "")
                )

            if about:
                lines.append(f"<b>О себе:</b> {_esc(str(about)[:200])}")

            media_stats = []
            if circle_cnt:
                media_stats.append(f"кружки: {_esc(circle_cnt)}")
            if voice_cnt:
                media_stats.append(f"голосовые: {_esc(voice_cnt)}")
            if media_stats:
                lines.append(f"<b>Медиа:</b> {', '.join(media_stats)}")

            pct_stats = []
            if unique_pct is not None:
                pct_stats.append(f"уникальных: {_pct(unique_pct)}")
            if reply_pct is not None:
                pct_stats.append(f"ответов: {_pct(reply_pct)}")
            if media_pct is not None:
                pct_stats.append(f"медиа: {_pct(media_pct)}")
            if link_pct is not None:
                pct_stats.append(f"ссылок: {_pct(link_pct)}")
            if pct_stats:
                lines.append(f"<b>Стиль:</b> {', '.join(pct_stats)}")

            star_parts = []
            if stars_val:
                star_parts.append(f"⭐ {_esc(stars_val)}")
            if stars_lvl:
                star_parts.append(f"ур. {_esc(stars_lvl)}")
            if gift_cnt:
                star_parts.append(f"🎁 {_esc(gift_cnt)}")
            if star_parts:
                lines.append(f"<b>Звёзды / подарки:</b> {' · '.join(star_parts)}")

    # История имён
    if names:
        lines.append("")
        rows = []
        for entry in names:
            n  = _esc(entry.get("name") or entry.get("first_name") or "")
            dt = _fmt_date_short(entry.get("date_time") or entry.get("date"))
            if n:
                rows.append(f"  {dt}  {n}")
        if rows:
            lines.append(f"<b>История имён ({len(names)}):</b>")
            lines.extend(rows)
    elif funstat:
        nc = funstat.get("names_count")
        if nc:
            lines.append(f"<b>Смен имени:</b> {_esc(nc)}")

    # История юзернеймов
    if usernames:
        lines.append("")
        rows = []
        for entry in usernames:
            un = entry.get("username") or entry.get("name") or ""
            dt = _fmt_date_short(entry.get("date_time") or entry.get("date"))
            un = _esc(un)
            if un:
                rows.append(f"  {dt}  {'@' if not un.startswith('@') else ''}{un}")
        if rows:
            lines.append(f"<b>История юзернеймов ({len(usernames)}):</b>")
            lines.extend(rows)
    elif funstat:
        uc = funstat.get("usernames_count")
        if uc:
            lines.append(f"<b>Смен юзернейма:</b> {_esc(uc)}")

    # Известные группы (только полный режим)
    if groups:
        lines.append("")
        lines.append(f"<b>Известные группы ({len(groups)}):</b>")
        for g in groups[:10]:  # не больше 10 чтобы карточка не раздулась
            title = _esc(g.get("title") or g.get("name") or "")
            uname = g.get("username") or ""
            link  = f" @{uname}" if uname else ""
            if title:
                lines.append(f"  • {title}{link}")
        if len(groups) > 10:
            lines.append(f"  <i>...и ещё {len(groups) - 10}</i>")

    # Стикер-паки (только полный режим)
    if stickers:
        lines.append("")
        lines.append(f"<b>Стикер-паки ({len(stickers)}):</b>")
        for s in stickers[:5]:
            title  = _esc(s.get("title") or "")
            sname  = s.get("short_name") or ""
            cnt    = s.get("stickers_count") or ""
            suffix = f" ({cnt} шт.)" if cnt else ""
            link   = f" — t.me/addstickers/{sname}" if sname else ""
            if title:
                lines.append(f"  • {title}{suffix}{link}")
        if len(stickers) > 5:
            lines.append(f"  <i>...и ещё {len(stickers) - 5}</i>")

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

    full = _is_full_mode(owner_id)

    # Аватарка
    photo_file_id: str | None = None
    try:
        photos = await bot.get_user_profile_photos(user.id, limit=1)
        if photos.total_count > 0:
            photo_file_id = photos.photos[0][0].file_id
    except Exception as e:
        log.warning(f"[NEWCONTACT] аватарка {user.id}: {e}")

    funstat, names, usernames, groups, stickers = await _fetch_funstat(user.id, full)
    card = _format_card(user, funstat, names, usernames, groups, stickers, full)

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
