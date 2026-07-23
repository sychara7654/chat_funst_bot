import asyncio
import base64
import logging
import re
import textwrap
from io import BytesIO
from typing import Callable, Optional

from aiogram import Bot
from aiogram.types import Message, BufferedInputFile

from fun import delete_command

try:
    from PIL import Image, ImageDraw, ImageFont
    _PILLOW_OK = True
except ImportError:
    _PILLOW_OK = False
    logging.warning("Pillow не установлен — команда /q недоступна")

MAX_QUOTE_MESSAGES = 10
MAX_TEXT_LEN = 1500
MAX_MEDIA_BYTES = 5 * 1024 * 1024

# ── Цвета имён (Telegram palette) ─────────────────────────────────────────────
_NAME_COLORS = [
    (192,  61,  51),   # #c03d33
    ( 79, 173,  45),   # #4fad2d
    (208, 147,   6),   # #d09306
    ( 22, 138, 205),   # #168acd
    (133,  68, 214),   # #8544d6
    (205,  64, 115),   # #cd4073
    ( 41, 150, 173),   # #2996ad
    (206, 103,  27),   # #ce671b
]

# ── Шрифты ────────────────────────────────────────────────────────────────────
_FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_BOLD    = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# ── Размеры ───────────────────────────────────────────────────────────────────
_IMG_WIDTH     = 768
_PADDING       = 28
_AVATAR_SIZE   = 72
_AVATAR_GAP    = 14
_CORNER_RADIUS = 22
_MSG_GAP       = 14   # промежуток между сообщениями
_REPLY_BAR_W   = 3    # ширина вертикальной полоски reply

_FS_NAME    = 28
_FS_TEXT    = 26
_FS_REPLY   = 22

# ── Цвета ─────────────────────────────────────────────────────────────────────
_BG           = (41, 34, 50)
_TEXT         = (255, 255, 255)
_REPLY_BAR    = (180, 130, 255)
_REPLY_NAME   = (180, 130, 255)
_REPLY_TEXT   = (180, 175, 195)
_MEDIA_LABEL  = (160, 155, 175)

# текстовая зона: от правого края аватарки до правого паддинга
_TEXT_X    = _PADDING + _AVATAR_SIZE + _AVATAR_GAP
_TEXT_W    = _IMG_WIDTH - _TEXT_X - _PADDING


# ─────────────────────────────────────────────────────────────────────────────
# Утилиты
# ─────────────────────────────────────────────────────────────────────────────

def _name_color(user_id: int) -> tuple:
    return _NAME_COLORS[abs(user_id) % len(_NAME_COLORS)]


def _initials(name: str) -> str:
    parts = name.strip().split()
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _load_fonts():
    try:
        fn  = ImageFont.truetype(_FONT_REGULAR, _FS_TEXT)
        fb  = ImageFont.truetype(_FONT_BOLD,    _FS_NAME)
        frp = ImageFont.truetype(_FONT_REGULAR, _FS_REPLY)
        fbp = ImageFont.truetype(_FONT_BOLD,    _FS_REPLY)
        return fn, fb, frp, fbp
    except Exception:
        fallback = ImageFont.load_default()
        return fallback, fallback, fallback, fallback


def _wrap(text: str, font, max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Разбивает текст на строки, не выходящие за max_w пикселей."""
    if not text:
        return [""]
    words = text.split(" ")
    lines, cur = [], ""
    for w in words:
        candidate = (cur + " " + w).strip() if cur else w
        # getbbox для точного измерения ширины
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] <= max_w:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            # одно длинное слово — режем по символам
            if draw.textbbox((0, 0), w, font=font)[2] > max_w:
                chunk = ""
                for ch in w:
                    t = chunk + ch
                    if draw.textbbox((0, 0), t, font=font)[2] <= max_w:
                        chunk = t
                    else:
                        lines.append(chunk)
                        chunk = ch
                cur = chunk
            else:
                cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _text_height(lines: list[str], font, line_gap: int = 4) -> int:
    if not lines:
        return 0
    # Берём высоту одной строки из getbbox
    sample = "Ag"
    try:
        h = font.getbbox(sample)[3]
    except Exception:
        h = _FS_TEXT
    return h * len(lines) + line_gap * (len(lines) - 1)


def _draw_rounded_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill):
    x0, y0, x1, y1 = xy
    draw.rectangle([x0 + radius, y0, x1 - radius, y1], fill=fill)
    draw.rectangle([x0, y0 + radius, x1, y1 - radius], fill=fill)
    draw.ellipse([x0, y0, x0 + radius * 2, y0 + radius * 2], fill=fill)
    draw.ellipse([x1 - radius * 2, y0, x1, y0 + radius * 2], fill=fill)
    draw.ellipse([x0, y1 - radius * 2, x0 + radius * 2, y1], fill=fill)
    draw.ellipse([x1 - radius * 2, y1 - radius * 2, x1, y1], fill=fill)


def _draw_circle_avatar(img: Image.Image, avatar_img: Optional[Image.Image],
                         name: str, user_id: int,
                         cx: int, cy: int, size: int):
    """Рисует круглый аватар (или инициалы) в позиции (cx, cy) — центр."""
    r = size // 2
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([0, 0, size, size], fill=255)

    if avatar_img:
        av = avatar_img.resize((size, size), Image.LANCZOS).convert("RGBA")
        av.putalpha(mask)
        img.paste(av, (cx - r, cy - r), av)
    else:
        # Цветной круг с инициалами
        circle = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        cd = ImageDraw.Draw(circle)
        color = _name_color(user_id) + (255,)
        cd.ellipse([0, 0, size, size], fill=color)
        text = _initials(name)
        try:
            fnt = ImageFont.truetype(_FONT_BOLD, size // 3)
        except Exception:
            fnt = ImageFont.load_default()
        bbox = cd.textbbox((0, 0), text, font=fnt)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        cd.text(((size - tw) // 2, (size - th) // 2 - bbox[1]),
                text, font=fnt, fill=(255, 255, 255, 255))
        circle.putalpha(mask)
        img.paste(circle, (cx - r, cy - r), circle)


# ─────────────────────────────────────────────────────────────────────────────
# Скачивание аватарки
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_avatar(bot: Bot, user_id: int) -> Optional[Image.Image]:
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if not photos.total_count or not photos.photos:
            return None
        ph = photos.photos[0][-1]
        file = await bot.get_file(ph.file_id)
        if file.file_size and file.file_size > 2 * 1024 * 1024:
            return None
        buf = BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        buf.seek(0)
        return Image.open(buf).convert("RGB")
    except Exception as e:
        logging.warning(f"Аватар {user_id}: {e}")
        return None


async def _fetch_media_thumb(bot: Bot, msg: Message) -> Optional[Image.Image]:
    """Скачивает превью медиа-вложения (если есть)."""
    file_id = None
    if msg.photo:
        file_id = msg.photo[-1].file_id
    elif msg.sticker and msg.sticker.thumbnail:
        file_id = msg.sticker.thumbnail.file_id
    elif msg.video and msg.video.thumbnail:
        file_id = msg.video.thumbnail.file_id
    elif msg.video_note and msg.video_note.thumbnail:
        file_id = msg.video_note.thumbnail.file_id
    elif msg.animation and msg.animation.thumbnail:
        file_id = msg.animation.thumbnail.file_id
    elif msg.document and msg.document.thumbnail:
        file_id = msg.document.thumbnail.file_id

    if not file_id:
        return None
    try:
        file = await bot.get_file(file_id)
        if file.file_size and file.file_size > MAX_MEDIA_BYTES:
            return None
        buf = BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        buf.seek(0)
        return Image.open(buf).convert("RGB")
    except Exception as e:
        logging.warning(f"Медиа-превью: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Данные о сообщении
# ─────────────────────────────────────────────────────────────────────────────

def _media_label(msg: Message) -> str:
    if msg.photo:       return "📷 Фото"
    if msg.video:       return "📹 Видео"
    if msg.voice:       return "🎤 Голосовое"
    if msg.audio:       return "🎵 Аудио"
    if msg.video_note:  return "⭕ Кружок"
    if msg.animation:   return "🎞 GIF"
    if msg.sticker:     return "🎭 Стикер"
    if msg.document:    return "📎 Документ"
    if msg.contact:     return "📞 Контакт"
    if msg.location:    return "📍 Локация"
    return ""


def _user_name(user) -> str:
    if not user:
        return "Unknown"
    full = (user.full_name or "").strip()
    if full:
        return full
    if user.username:
        return f"@{user.username}"
    return "Unknown"


def _user_id(user) -> int:
    return user.id if user else 0


# ─────────────────────────────────────────────────────────────────────────────
# Высота одного блока сообщения (для предрасчёта)
# ─────────────────────────────────────────────────────────────────────────────

class _MsgData:
    __slots__ = ("name", "uid", "text", "avatar", "thumb",
                 "reply_name", "reply_uid", "reply_text")

    def __init__(self):
        self.name:        str = ""
        self.uid:         int = 0
        self.text:        str = ""
        self.avatar:      Optional[Image.Image] = None
        self.thumb:       Optional[Image.Image] = None
        self.reply_name:  Optional[str] = None
        self.reply_uid:   int = 0
        self.reply_text:  Optional[str] = None


async def _collect(bot: Bot, msg: Message, with_reply: bool, idx: int) -> _MsgData:
    d = _MsgData()
    user  = msg.from_user
    d.name = _user_name(user)
    d.uid  = _user_id(user)
    text   = msg.text or msg.caption or ""
    if not text:
        text = _media_label(msg)
    if len(text) > MAX_TEXT_LEN:
        text = text[:MAX_TEXT_LEN - 1] + "…"
    d.text = text

    d.avatar = await _fetch_avatar(bot, d.uid) if d.uid else None
    d.thumb  = await _fetch_media_thumb(bot, msg)

    if with_reply and idx == 0 and msg.reply_to_message:
        rep = msg.reply_to_message
        d.reply_name = _user_name(rep.from_user)
        d.reply_uid  = _user_id(rep.from_user)
        rt = rep.text or rep.caption or _media_label(rep) or ""
        if len(rt) > 200:
            rt = rt[:199] + "…"
        d.reply_text = rt

    return d


# ─────────────────────────────────────────────────────────────────────────────
# Рендер
# ─────────────────────────────────────────────────────────────────────────────

def _render_message(draw: ImageDraw.ImageDraw, img: Image.Image,
                    d: _MsgData, y: int,
                    fn, fb, frp, fbp) -> int:
    """
    Рисует одно сообщение начиная с y, возвращает новый y (после блока).
    """
    line_gap   = 4
    name_gap   = 6   # между именем и текстом
    reply_gap  = 8   # между reply-блоком и именем
    thumb_gap  = 8   # между превью и текстом

    # ── Высоты строк через getbbox ─────────────────────────────────────────
    try:
        name_h  = fb.getbbox("Ag")[3]
        text_lh = fn.getbbox("Ag")[3]
        repl_lh = frp.getbbox("Ag")[3]
    except Exception:
        name_h = text_lh = repl_lh = _FS_TEXT

    text_x = _TEXT_X
    text_w = _TEXT_W

    cur_y = y  # текущая позиция по Y внутри текстовой зоны

    # ── Reply-блок ────────────────────────────────────────────────────────
    if d.reply_name is not None:
        bar_x  = text_x
        text_rx = bar_x + _REPLY_BAR_W + 8

        rname_lines  = _wrap(d.reply_name or "", fbp, text_w - _REPLY_BAR_W - 8, draw)
        rtext_lines  = _wrap(d.reply_text  or "", frp, text_w - _REPLY_BAR_W - 8, draw)
        rblock_h = (
            repl_lh * len(rname_lines) + line_gap * (len(rname_lines) - 1) +
            4 +
            repl_lh * len(rtext_lines) + line_gap * (len(rtext_lines) - 1)
        )

        # Вертикальная полоска
        draw.rectangle(
            [bar_x, cur_y, bar_x + _REPLY_BAR_W, cur_y + rblock_h],
            fill=_REPLY_BAR
        )

        ry = cur_y
        for line in rname_lines:
            draw.text((text_rx, ry), line, font=fbp, fill=_REPLY_NAME)
            ry += repl_lh + line_gap
        ry += 2
        for line in rtext_lines:
            draw.text((text_rx, ry), line, font=frp, fill=_REPLY_TEXT)
            ry += repl_lh + line_gap

        cur_y += rblock_h + reply_gap

    # ── Имя отправителя ───────────────────────────────────────────────────
    name_color = _name_color(d.uid)
    draw.text((text_x, cur_y), d.name, font=fb, fill=name_color)
    cur_y += name_h + name_gap

    # ── Превью медиа (если есть) ──────────────────────────────────────────
    if d.thumb:
        th_size = min(160, text_w)
        thumb   = d.thumb.resize((th_size, th_size), Image.LANCZOS).convert("RGBA")
        # Маска с закруглёнными углами для превью
        tm = Image.new("L", (th_size, th_size), 0)
        tmd = ImageDraw.Draw(tm)
        tmd.rounded_rectangle([0, 0, th_size, th_size], radius=10, fill=255)
        img.paste(thumb, (text_x, cur_y), tm)
        cur_y += th_size + thumb_gap

    # ── Текст ─────────────────────────────────────────────────────────────
    lines = _wrap(d.text, fn, text_w, draw)
    for line in lines:
        draw.text((text_x, cur_y), line, font=fn, fill=_TEXT)
        cur_y += text_lh + line_gap

    # ── Аватарка (по центру блока по вертикали) ───────────────────────────
    block_h = cur_y - y
    av_cy   = y + max(block_h // 2, _AVATAR_SIZE // 2)
    av_cx   = _PADDING + _AVATAR_SIZE // 2
    _draw_circle_avatar(img, d.avatar, d.name, d.uid,
                        av_cx, av_cy, _AVATAR_SIZE)

    return cur_y


async def _render_quote(bot: Bot, messages: list[Message], with_reply: bool) -> Optional[bytes]:
    if not _PILLOW_OK:
        logging.error("Pillow не установлен, /q недоступна")
        return None

    # Загружаем данные всех сообщений параллельно
    try:
        tasks = [_collect(bot, m, with_reply, i) for i, m in enumerate(messages)]
        data: list[_MsgData] = await asyncio.gather(*tasks)
    except Exception as e:
        logging.error(f"Сбор данных для цитаты: {e}")
        return None

    if not data:
        return None

    fn, fb, frp, fbp = _load_fonts()

    # ── Предрасчёт высоты ─────────────────────────────────────────────────
    # Создаём временное изображение для измерений
    probe = Image.new("RGBA", (_IMG_WIDTH, 100))
    pd    = ImageDraw.Draw(probe)

    line_gap  = 4
    name_gap  = 6
    reply_gap = 8
    thumb_gap = 8

    try:
        name_h  = fb.getbbox("Ag")[3]
        text_lh = fn.getbbox("Ag")[3]
        repl_lh = frp.getbbox("Ag")[3]
    except Exception:
        name_h = text_lh = repl_lh = _FS_TEXT

    def _block_h(d: _MsgData) -> int:
        h = 0
        if d.reply_name is not None:
            rn = _wrap(d.reply_name or "", fbp, _TEXT_W - _REPLY_BAR_W - 8, pd)
            rt = _wrap(d.reply_text  or "", frp, _TEXT_W - _REPLY_BAR_W - 8, pd)
            h += (repl_lh * len(rn) + line_gap * (len(rn) - 1) +
                  4 +
                  repl_lh * len(rt) + line_gap * (len(rt) - 1) +
                  reply_gap)
        h += name_h + name_gap
        if d.thumb:
            h += min(160, _TEXT_W) + thumb_gap
        lines = _wrap(d.text, fn, _TEXT_W, pd)
        h += text_lh * len(lines) + line_gap * (len(lines) - 1)
        return max(h, _AVATAR_SIZE)

    total_h = (_PADDING +
               sum(_block_h(d) for d in data) +
               _MSG_GAP * (len(data) - 1) +
               _PADDING)
    total_h = max(total_h, _AVATAR_SIZE + _PADDING * 2)

    # ── Создаём итоговое изображение ──────────────────────────────────────
    img  = Image.new("RGBA", (_IMG_WIDTH, total_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Фон с закруглёнными углами
    _draw_rounded_rect(draw,
                       [0, 0, _IMG_WIDTH - 1, total_h - 1],
                       _CORNER_RADIUS, _BG)

    y = _PADDING
    for i, d in enumerate(data):
        y = _render_message(draw, img, d, y, fn, fb, frp, fbp)
        if i < len(data) - 1:
            y += _MSG_GAP

    # ── Сохраняем как WebP ────────────────────────────────────────────────
    out = BytesIO()
    try:
        img.save(out, format="WEBP", quality=90)
    except Exception:
        # Fallback: PNG если WebP недоступен
        img.save(out, format="PNG")
    return out.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Парсинг аргументов /q
# ─────────────────────────────────────────────────────────────────────────────

def parse_q_args(text: str) -> tuple[bool, int]:
    """Возвращает (with_reply_context, count).

    Поддерживаются ОБА формата:
        /q            — один стикер
        /q r          — с reply-контекстом
        /q 5          — 5 сообщений
        /q r 5        — 5 сообщений + reply-контекст
        /q 5 r        — то же
        /qr           — без пробела
        /q5           — без пробела
        /qr5 /q5r     — без пробелов в любом порядке
    """
    if not text:
        return False, 1

    tokens: list[str] = []
    parts = text.split()
    if not parts:
        return False, 1

    first = parts[0].lstrip("/").split("@")[0].lower()
    if first.startswith("q"):
        tail = first[1:]
        if tail:
            tokens.append(tail)
    tokens.extend(p.lower() for p in parts[1:])

    with_reply = False
    count = 1

    for tok in tokens:
        if "r" in tok:
            with_reply = True
        digits = re.findall(r"\d+", tok)
        for d in digits:
            try:
                n = int(d)
                if n >= 1:
                    count = min(n, MAX_QUOTE_MESSAGES)
            except ValueError:
                pass

    return with_reply, count


# ─────────────────────────────────────────────────────────────────────────────
# Вспомогательная отправка временной ошибки
# ─────────────────────────────────────────────────────────────────────────────

async def _send_temp_error(bot: Bot, chat_id: int, text: str, bc_id: str, delay: float = 4.0):
    try:
        sent = await bot.send_message(chat_id, text, business_connection_id=bc_id)
        await asyncio.sleep(delay)
        try:
            await bot.delete_messages(
                chat_id=chat_id,
                message_ids=[sent.message_id],
                business_connection_id=bc_id,
            )
        except Exception:
            pass
    except Exception as e:
        logging.warning(f"Ошибка временного сообщения /q: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Основной обработчик команды
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_quote(message: Message, bot: Bot, cache_lookup: Callable[[int, int], Optional[Message]]):
    bc_id   = message.business_connection_id
    chat_id = message.chat.id

    if not message.reply_to_message:
        await delete_command(message, bot)
        await _send_temp_error(bot, chat_id, "❌ Ответьте командой /q на сообщение.", bc_id)
        return

    with_reply, count = parse_q_args(message.text or "")
    logging.info(f"[/q] raw={message.text!r} → r={with_reply} count={count}")

    base_msg = message.reply_to_message

    # Обогащаем base_msg из кэша (Business-апдейты обрезают reply_to_message)
    try:
        import sys
        _bot_mod = sys.modules.get("__main__")
        if _bot_mod is None or not hasattr(_bot_mod, "cache"):
            import bot as _bot_mod
        cached_pair = _bot_mod.cache.get(chat_id, {}).get(base_msg.message_id)
        if cached_pair:
            cached_base = cached_pair[0]
            if cached_base.reply_to_message and not base_msg.reply_to_message:
                logging.info("[/q] base_msg обогащён из кэша")
                base_msg = cached_base
    except Exception as e:
        logging.warning(f"Кэш base_msg: {e}")

    messages_to_quote: list[Message] = [base_msg]

    if count > 1:
        try:
            import sys
            _bot_mod = sys.modules.get("__main__")
            if _bot_mod is None or not hasattr(_bot_mod, "cache"):
                import bot as _bot_mod
            chat_cache = _bot_mod.cache.get(chat_id, {})
            need       = count - 1
            next_ids   = sorted(mid for mid in chat_cache if mid > base_msg.message_id)
            forward: list[Message] = []
            for mid in next_ids[:need]:
                pair = chat_cache.get(mid)
                if pair:
                    forward.append(pair[0])
            messages_to_quote = [base_msg] + forward
            logging.info(f"/q {count}: {len(messages_to_quote)} сообщений (вперёд {len(forward)})")
        except Exception as e:
            logging.warning(f"Кэш для /q {count}: {e}")

    await delete_command(message, bot)

    img_bytes = await _render_quote(bot, messages_to_quote, with_reply)

    if not img_bytes:
        await _send_temp_error(bot, chat_id, "❌ Не удалось создать цитату. Попробуйте позже.", bc_id)
        return

    reply_to    = base_msg.message_id
    sticker_file = BufferedInputFile(img_bytes, filename="quote.webp")
    try:
        await bot.send_sticker(
            chat_id,
            sticker_file,
            business_connection_id=bc_id,
            reply_to_message_id=reply_to,
        )
    except Exception as e:
        logging.warning(f"send_sticker → fallback send_document: {e}")
        try:
            doc_file = BufferedInputFile(img_bytes, filename="quote.webp")
            await bot.send_document(
                chat_id,
                doc_file,
                business_connection_id=bc_id,
                reply_to_message_id=reply_to,
            )
        except Exception as e2:
            logging.error(f"Ошибка отправки цитаты: {e2}")
            await _send_temp_error(bot, chat_id, "❌ Не удалось отправить цитату.", bc_id)
