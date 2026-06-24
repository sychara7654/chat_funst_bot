import asyncio
import logging
import random
from aiogram import Bot
from aiogram.types import Message
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest, TelegramForbiddenError
from fun import delete_command

# Таймеры авто-остановки /troll (chat_id → asyncio.Task)
_troll_timers: dict[int, asyncio.Task] = {}

# Сколько секунд работает /troll до авто-выключения
_TROLL_TIMEOUT: float = 180.0


def _stop_troll_flags(
    chat_id: int,
    owner_id: int,
    muted_chats: set,
    ignore_chats: set,
) -> None:
    """Сбрасывает все флаги /troll и отменяет таймер для chat_id."""
    mother_running[chat_id] = False
    typing_running[chat_id] = False
    muted_chats.discard((owner_id, chat_id))
    ignore_chats.discard((owner_id, chat_id))
    task = _troll_timers.pop(chat_id, None)
    if task and not task.done():
        task.cancel()


async def _troll_auto_stop(
    chat_id: int,
    owner_id: int,
    muted_chats: set,
    ignore_chats: set,
) -> None:
    """Автоматически останавливает /troll через _TROLL_TIMEOUT секунд."""
    await asyncio.sleep(_TROLL_TIMEOUT)
    logging.info(f"[TROLL] авто-стоп по таймеру ({_TROLL_TIMEOUT}s) для chat_id={chat_id}")
    _stop_troll_flags(chat_id, owner_id, muted_chats, ignore_chats)


def cancel_troll_timer(chat_id: int) -> None:
    """Отменяет таймер авто-остановки /troll (вызывается из /stop в bot.py)."""
    task = _troll_timers.pop(chat_id, None)
    if task and not task.done():
        task.cancel()


# === /mother (встроенная подкоманда внутри /troll) ===
# Раньше /mother была отдельной командой, потом её убрали (см. коммит
# 79b8f2e). По просьбе пользователя возвращаем — но НЕ как самостоятельную
# команду, а как часть комбо /troll. Просто /mother отдельно работать
# не должна.
MOTHER_MESSAGES = [
    '<tg-emoji emoji-id="5431824012307087977">❤</tg-emoji>| Твою мать разъебали',
    '<tg-emoji emoji-id="5429609793457264027">❤</tg-emoji>| Я ебал твою родословную',
    '<tg-emoji emoji-id="5467430798724470930">❤</tg-emoji>| Твой пахан немужчина',
    '<tg-emoji emoji-id="5467777273736237111">❤</tg-emoji>| Твоя семья умрет',
    '<tg-emoji emoji-id="5467460713171683916">❤</tg-emoji>| Забил гол в ворота твоей сестры',
    '<tg-emoji emoji-id="5467813493195441676">❤</tg-emoji>| Ты сын жирной хуйни',
    '<tg-emoji emoji-id="5911384560041463738">❤</tg-emoji>| Я ебал твой рот',
    '<tg-emoji emoji-id="5427290021491151861">❤</tg-emoji>| ты сынок шлюхи',
    '<tg-emoji emoji-id="5298937724168853193">❤</tg-emoji>| поплачь сын шлюхи',
    '<tg-emoji emoji-id="5211025120918785460">❤</tg-emoji>| уже ебу твою мать',
    '<tg-emoji emoji-id="5847995391122869577">❤</tg-emoji>| сын вонючей бляди ты должен поклоняться моему хую',
    '<tg-emoji emoji-id="5296365472550244967">❤</tg-emoji>| сын пузатой шлюхи',
]

# ID эффектов для тролль-спама — заполняется при старте через init_troll_effects()
TROLL_EFFECTS: list[str] = []


async def init_troll_effects(bot) -> None:
    """Загружает ID эффектов 💩 🍌 🤡 из Telegram API (getAvailableEffects).
    Логирует ВСЕ доступные эффекты — это помогает найти нужные ID.
    Вызывается один раз при старте бота из main() в bot.py."""
    global TROLL_EFFECTS
    target = {"💩", "🍌", "🤡"}
    found: list[str] = []
    all_ids: list[str] = []
    try:
        effects = await bot.get_available_effects()
        for eff in effects:
            emoji = getattr(eff, "emoji", None)
            eff_id = str(eff.id)
            is_premium = getattr(eff, "is_premium", False)
            # Логируем каждый эффект — чтобы в Railway-логах были видны правильные ID
            logging.info(f"[TROLL] эффект: {emoji!r} id={eff_id} premium={is_premium}")
            all_ids.append(eff_id)
            if emoji in target:
                found.append(eff_id)
    except Exception as e:
        logging.warning(f"[TROLL] get_available_effects не удалось: {e}")
    if found:
        TROLL_EFFECTS = found
        logging.info(f"[TROLL] найдены нужные эффекты ({len(found)} шт): {found}")
    elif all_ids:
        # Нужные эмодзи не найдены — используем все доступные (лучше чем ничего)
        TROLL_EFFECTS = all_ids
        logging.warning(f"[TROLL] 💩🍌🤡 не найдены, используем все {len(all_ids)} эффектов: {all_ids}")
    else:
        # Fallback: 6 стандартных эффектов Telegram (source: python-telegram-bot constants)
        TROLL_EFFECTS = [
            "5046888937679177542",  # 💩
            "5046509860389126442",  # 🔥
            "5046562334816506883",  # 🎉
            "5104841245755180586",  # 👍
            "5104858069142078462",  # 👎
            "5044134455711629726",  # ❤️
        ]
        logging.warning("[TROLL] API не вернул нужные эффекты, пробуем 6 стандартных")

# Флаг "идёт ли цикл mother в этом чате". Сбрасывается через /stop.
mother_running: dict[int, bool] = {}


# cmd_id перенесён в info.py (ОСИНТ-модуль, отправляет результат в ЛС владельца)


MUTE_TEXT = '<tg-emoji emoji-id="5431449413849486465">❤</tg-emoji>| Помолчи'
UNMUTE_TEXT = '<tg-emoji emoji-id="5388632425314140043">❤</tg-emoji>| Говори'


async def _edit_command_to(message: Message, bot: Bot, new_text: str):
    """Заменяет текст команды в чате на заданный (через edit_message_text).

    Если редактирование не удалось — fallback: удаляем команду и отправляем
    новое сообщение от имени владельца.
    """
    bc_id = message.business_connection_id
    try:
        await bot.edit_message_text(
            text=new_text,
            chat_id=message.chat.id,
            message_id=message.message_id,
            business_connection_id=bc_id,
            parse_mode="HTML",
        )
        return
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 0.3)
        try:
            await bot.edit_message_text(
                text=new_text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                business_connection_id=bc_id,
                parse_mode="HTML",
            )
            return
        except Exception as e2:
            logging.warning(f"Не удалось отредактировать команду после RetryAfter: {e2}")
    except Exception as e:
        logging.warning(f"Не удалось отредактировать команду: {e}")

    # Fallback: удалить и отправить заново
    try:
        await delete_command(message, bot)
    except Exception:
        pass
    try:
        await bot.send_message(
            message.chat.id,
            new_text,
            parse_mode="HTML",
            business_connection_id=bc_id,
        )
    except Exception as e:
        logging.error(f"Fallback send_message не удалось: {e}")


typing_running: dict[int, bool] = {}


async def cmd_typing(message: Message, bot: Bot):
    """Непрерывно показывает собеседнику индикатор «печатает…» пока
    не будет вызвана команда /stop. Удаление команды выполняется в bot.py
    приоритетно ДО запуска этой задачи.
    """
    chat_id = message.chat.id
    bc_id = message.business_connection_id

    if typing_running.get(chat_id, False):
        return

    typing_running[chat_id] = True
    try:
        while typing_running.get(chat_id, False):
            try:
                await bot.send_chat_action(
                    chat_id=chat_id,
                    action="typing",
                    business_connection_id=bc_id,
                )
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 0.3)
                continue
            except Exception as e:
                logging.warning(f"[TYPING] send_chat_action: {e}")
            # Индикатор живёт ~5 сек, обновляем каждые 4.
            await asyncio.sleep(4.0)
    finally:
        typing_running[chat_id] = False


async def cmd_ignore(message: Message, bot: Bot, ignore_chats: set, owner_id: int):
    """Включает авто-игнор: бот сразу помечает входящие сообщения
    собеседника как прочитанные. Удаление команды — в bot.py приоритетно.
    Останавливается командой /stop.
    """
    ignore_chats.add((owner_id, message.chat.id))


async def cmd_mirror(message: Message, bot: Bot, mirror_chats: set):
    """Включает режим зеркала: бот будет повторять сообщения собеседника
    от имени владельца. Удаление команды — в bot.py приоритетно.
    Ключ (owner_id, chat_id) добавляется в bot.py до вызова этой функции.
    Останавливается командой /stop.
    """
    # mirror_chats уже обновлён в bot.py (с ключом (owner_id, chat_id)).
    pass


async def cmd_mute(message: Message, bot: Bot, muted_chats: set, owner_id: int):
    """Добавляет чат в режим мута. Все сообщения собеседника будут удаляться."""
    chat_id = message.chat.id
    muted_chats.add((owner_id, chat_id))
    await _edit_command_to(message, bot, MUTE_TEXT)


async def cmd_unmute(message: Message, bot: Bot, muted_chats: set, owner_id: int):
    """Снимает режим мута. Должна срабатывать с первого раза, без задержек."""
    chat_id = message.chat.id
    # Сбрасываем состояние СРАЗУ — приоритет над любыми гонками.
    muted_chats.discard((owner_id, chat_id))
    await _edit_command_to(message, bot, UNMUTE_TEXT)


async def cmd_troll(
    message: Message,
    bot: Bot,
    muted_chats: set,
    mirror_chats: set,  # не используется, передаётся для единообразия
    ignore_chats: set,
    owner_id: int,
):
    """/troll = /mute + /ignore + /typing + рандомные mother-тексты.

    Удаление команды происходит в bot.py приоритетно (до запуска задачи).
    Здесь только включаем все режимы. Останавливается командой /stop,
    по таймеру через 3 минуты, или автоматически если собеседник заблокировал
    владельца или удалил чат с двух сторон.
    """
    chat_id = message.chat.id
    bc_id = message.business_connection_id

    # /mute — заглушаем собеседника
    muted_chats.add((owner_id, chat_id))

    # /ignore — авто-чтение входящих
    ignore_chats.add((owner_id, chat_id))

    # /typing — постоянный индикатор «печатает…»
    if not typing_running.get(chat_id, False):
        typing_running[chat_id] = True
        asyncio.create_task(_typing_loop(bot, chat_id, bc_id, muted_chats, ignore_chats, owner_id))

    # /mother — рандомные оскорбительные тексты в фоне (по просьбе
    # пользователя возвращена как часть /troll, но НЕ как самостоятельная
    # команда). Тексты крутятся пока не пришёл /stop или не истёк таймер.
    if not mother_running.get(chat_id, False):
        mother_running[chat_id] = True
        asyncio.create_task(_mother_loop(bot, chat_id, bc_id, muted_chats, ignore_chats, owner_id))

    # Авто-стоп через 3 минуты — отменяем старый таймер если был
    old = _troll_timers.pop(chat_id, None)
    if old and not old.done():
        old.cancel()
    _troll_timers[chat_id] = asyncio.create_task(
        _troll_auto_stop(chat_id, owner_id, muted_chats, ignore_chats)
    )


async def _typing_loop(
    bot: Bot,
    chat_id: int,
    bc_id: str | None,
    muted_chats: set | None = None,
    ignore_chats: set | None = None,
    owner_id: int = 0,
):
    try:
        while typing_running.get(chat_id, False):
            try:
                await bot.send_chat_action(
                    chat_id=chat_id,
                    action="typing",
                    business_connection_id=bc_id,
                )
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 0.3)
                continue
            except TelegramForbiddenError as e:
                logging.warning(f"[TROLL/TYPING] собеседник заблокировал или удалил чат: {e}")
                if muted_chats is not None and ignore_chats is not None:
                    _stop_troll_flags(chat_id, owner_id, muted_chats, ignore_chats)
                return
            except Exception as e:
                err = str(e).lower()
                if any(k in err for k in ("blocked", "forbidden", "deactivated", "peer_id_invalid", "chat not found")):
                    logging.warning(f"[TROLL/TYPING] фатальная ошибка — авто-стоп: {e}")
                    if muted_chats is not None and ignore_chats is not None:
                        _stop_troll_flags(chat_id, owner_id, muted_chats, ignore_chats)
                    return
                logging.warning(f"[TROLL/TYPING] send_chat_action: {e}")
            await asyncio.sleep(4.0)
    finally:
        typing_running[chat_id] = False


async def _mother_loop(
    bot: Bot,
    chat_id: int,
    bc_id: str | None,
    muted_chats: set | None = None,
    ignore_chats: set | None = None,
    owner_id: int = 0,
):
    """Шлёт случайный текст из MOTHER_MESSAGES каждые 0.35 сек,
    пока mother_running[chat_id] = True.
    — Одинаковый текст не повторяется два раза подряд.
    — На каждое сообщение прикрепляется случайный эффект (💩🍌🤡).
    — Если собеседник заблокировал или удалил чат — авто-стоп.
    /stop сбрасывает флаг в bot.py.
    """
    last_text: str | None = None

    def _fatal_stop(reason: str) -> None:
        logging.warning(f"[TROLL/MOTHER] авто-стоп ({reason}) для chat_id={chat_id}")
        if muted_chats is not None and ignore_chats is not None:
            _stop_troll_flags(chat_id, owner_id, muted_chats, ignore_chats)

    try:
        while mother_running.get(chat_id, False):
            # Выбираем текст без повтора подряд
            if last_text is not None and len(MOTHER_MESSAGES) > 1:
                pool = [t for t in MOTHER_MESSAGES if t != last_text]
                text = random.choice(pool)
            else:
                text = random.choice(MOTHER_MESSAGES)
            last_text = text

            # Выбираем эффект
            effect_id = random.choice(TROLL_EFFECTS) if TROLL_EFFECTS else None

            send_kwargs: dict = dict(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                business_connection_id=bc_id,
            )
            if effect_id:
                send_kwargs["message_effect_id"] = effect_id
            try:
                await bot.send_message(**send_kwargs)
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 0.5)
                continue
            except TelegramForbiddenError as e:
                # Собеседник заблокировал или удалил чат с двух сторон
                _fatal_stop(f"TelegramForbiddenError: {e}")
                return
            except TelegramBadRequest as e:
                err = str(e).lower()
                # Проверяем: это фатальная ошибка (блок/чат удалён) или просто плохой effect?
                if any(k in err for k in ("blocked", "forbidden", "deactivated", "peer_id_invalid", "chat not found", "user_is_blocked")):
                    _fatal_stop(f"TelegramBadRequest: {e}")
                    return
                # Невалидный effect_id — удаляем из списка навсегда, шлём без эффекта
                if effect_id and effect_id in TROLL_EFFECTS:
                    TROLL_EFFECTS.remove(effect_id)
                    logging.warning(f"[TROLL] effect {effect_id!r} удалён (INVALID). Осталось: {TROLL_EFFECTS}")
                else:
                    logging.warning(f"[TROLL] effect {effect_id!r} отклонён: {e}")
                send_kwargs.pop("message_effect_id", None)
                try:
                    await bot.send_message(**send_kwargs)
                except TelegramForbiddenError as e2:
                    _fatal_stop(f"TelegramForbiddenError (повтор): {e2}")
                    return
                except Exception as e2:
                    logging.warning(f"[TROLL] повторная отправка без эффекта: {e2}")
            except Exception as e:
                err = str(e).lower()
                if any(k in err for k in ("blocked", "forbidden", "deactivated", "peer_id_invalid", "chat not found", "user_is_blocked")):
                    _fatal_stop(f"Exception: {e}")
                    return
                logging.warning(f"[TROLL/MOTHER] send: {e}")
            await asyncio.sleep(0.35)
    finally:
        mother_running[chat_id] = False


