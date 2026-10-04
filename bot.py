"""Телеграм-бот: наборы анимированных кастом-эмодзи из TGS-шаблонов.

Сценарий целиком:

    /start → меню (профиль / создать эмодзи / поддержка)
           → набор шаблонов
           → случайно или выбрать самому
             ├─ случайно: шрифт → надпись или логотип → количество
             └─ вручную:  страницы по 12 с отметками → шрифт → надпись
           → финальное превью: тип набора (премиум-эмодзи или обычные
             стикеры), куда добавить — в новый набор или в существующий
           → оплата: счёт в звёздах Telegram или списание с баланса
           → сборка набора и ссылка на него

Баланс пополняется из профиля отдельным счётом. О каждой покупке и
пополнении админам уходит уведомление.

Оплаченный заказ живёт в очереди в базе, а не в памяти: одна выдача за
раз, состояние переживает перезапуск, а если Telegram не даёт собрать
набор целые сутки — бот сам возвращает звёзды. Оплаченный и невыданный
заказ не может потеряться ни при каких лимитах.

Тяжёлые операции (подстановка текста в Lottie, растеризация превью)
уходят в поток через asyncio.to_thread: они целиком на CPU и без этого
блокировали бы весь цикл событий, а вместе с ним и всех пользователей.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import string
import sys
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputSticker,
    LabeledPrice,
    MenuButtonWebApp,
    Message,
    PreCheckoutQuery,
    User,
    WebAppInfo,
)

import admin
import admin_branding
import admin_channels
import admin_promo
import branding
import config
import crypto
import db
import keyboards as kb
import notify
import settings
import texts
import tgs_engine as engine
import ui
from webapp import server as webapp_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("emoji-bot")

router = Router()

#: По одной сборке на пользователя. Без этого частые нажатия запускают
#: несколько рендеров разом и последним приходит не тот ответ.
_user_locks: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

#: Сколько эмодзи кладём в createNewStickerSet за раз.
#:
#: API принимает до 50, но лимит Telegram считает стикеры, а не запросы:
#: пачка больше окна упирается в флуд-контроль всегда, сколько её ни
#: повторяй. Поэтому первый вызов берёт ровно столько, сколько влезает
#: в окно, а остальное доклеивается по одному в общем темпе.
CREATE_BATCH = config.CREATE_BATCH

#: Ограничение Telegram на запись в наборы: 8 обращений за 4 минуты.
#: Отсюда и время сборки: создание набора — один вызов на первые 50 штук,
#: а дозапись в существующий — по вызову на каждый стикер, то есть 8 штук
#: за окно. Числа в константах, потому что Telegram их уже менял.
STICKER_WRITE_LIMIT = 8
STICKER_WRITE_WINDOW = 4 * 60.0

#: Сколько раз пережидаем флуд-контроль на одном стикере, прежде чем сдаться.
FLOOD_ATTEMPTS = 4

#: Пауза длиннее этой — не очередь, а лимит Telegram на бота (обычно
#: приходит ровно та же цифра на каждый повтор). Сидеть в цикле по пять
#: минут бессмысленно: заказ откладывается и повторяется кнопкой.
LONG_FLOOD_SECONDS = 90

#: Пауза перед повтором, когда Telegram ещё не разнёс новый набор по своим
#: серверам и отвечает STICKERSET_INVALID на только что созданный пак.
PROPAGATION_DELAY = 1.5

#: Потолок на один запрос с файлами. Пятьдесят анимаций — это пара
#: мегабайт в одном multipart, и штатной минуты может не хватить.
UPLOAD_TIMEOUT = 180.0

#: Сколько плиток показываем в финальном превью. Больше — картинка
#: становится нечитаемой простынёй, а собирать их все до оплаты незачем.
PREVIEW_TILES = 24


class Step(StatesGroup):
    selection = State()
    font_file = State()
    content = State()
    quantity = State()
    topup = State()
    crypto = State()


# --------------------------------------------------------------------------
# Вспомогательное
# --------------------------------------------------------------------------

def _clean_text(raw: str) -> str:
    """Надпись без переводов строк и лишних пробелов.

    Перенос строки внутри шаблона превращается в пустой контур, поэтому
    склеиваем всё в одну строку ещё до генерации.
    """
    return re.sub(r"\s+", " ", (raw or "").replace("\n", " ")).strip()


def _parse_range(raw: str, allowed: Sequence[int]) -> List[int]:
    """Разбирает «1-12, 20-25, 7» в список номеров.

    Ввод диапазоном нужен для больших выборок: отметить 40 шаблонов
    кнопками — сорок нажатий, а строкой — одно сообщение.
    """
    allowed_set = set(allowed)
    found: List[int] = []
    for chunk in re.split(r"[,\s;]+", raw.strip()):
        if not chunk:
            continue
        match = re.fullmatch(r"(\d+)(?:\s*[-–—]\s*(\d+))?", chunk)
        if not match:
            return []
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if end < start:
            start, end = end, start
        found.extend(n for n in range(start, end + 1) if n in allowed_set)
    return found


def _pack_name(user_id: int, bot_username: str) -> str:
    """Служебное имя набора: только латиница, цифры и `_by_<бот>` на конце."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
    return f"e{user_id}_{suffix}_by_{bot_username}"


def _pack_title(first_name: Optional[str]) -> str:
    name = _clean_text(first_name or "") or "My"
    return f"{name} · {config.PACK_TITLE_SUFFIX}"[:64]


def _content_label(text: str, logo: Optional[bytes]) -> str:
    if logo:
        return "SVG-логотип" + (f" + «{text}»" if text else "")
    return f"«{text}»" if text else "—"


async def _sample(bot: Bot) -> str:
    """Надпись, которой бот подписывает витрины: собственный юзернейм.

    Показывать в каталоге слово TEXT бессмысленно — человек должен видеть
    ровно то, что получит, просто с чужим текстом вместо своего.
    """
    me = await bot.me()
    return f"@{me.username}"


#: Статусы getChatMember, при которых человек считается подписчиком.
SUBSCRIBED_STATUSES = {"member", "administrator", "creator"}


async def _missing_channels(bot: Bot, user_id: int) -> List[Dict[str, Any]]:
    """Каналы из списка, на которые человек ещё не подписан.

    Канал, который не удалось проверить, считаем пройденным: чтобы
    проверка работала, бота надо назначить администратором канала, и
    если этого не сделали, запирать всех пользователей нельзя — они не
    смогут ничего сделать, а причина видна только в логах.
    """
    if user_id in config.ADMIN_IDS or not settings.subscription_on():
        return []

    missing: List[Dict[str, Any]] = []
    for channel in settings.channels():
        try:
            member = await bot.get_chat_member(channel["id"], user_id)
        except Exception as exc:
            log.warning("Подписку на %s проверить не вышло: %s", channel.get("title"), exc)
            continue
        if member.status not in SUBSCRIBED_STATUSES:
            missing.append(channel)
    return missing


async def _subscribed(bot: Bot, user_id: int) -> bool:
    return not await _missing_channels(bot, user_id)


async def _gate(user_id: int) -> Optional[str]:
    """Причина, по которой человеку сейчас нельзя собирать набор.

    Проверка стоит на входе в создание, а не в каждом шаге: дальше по
    цепочке попасть можно только отсюда, а на профиль и поддержку ни
    техработы, ни блокировка влиять не должны.
    """
    if await db.is_blocked(user_id):
        return texts.blocked()
    if settings.maintenance() and user_id not in config.ADMIN_IDS:
        return texts.maintenance()
    return None


async def _custom_font_id(user_id: int) -> Optional[str]:
    """id личного шрифта пользователя, если он его загружал.

    Пресеты движка живут в памяти процесса, поэтому после перезапуска
    шрифт нужно зарегистрировать заново — делаем это здесь, а не при
    загрузке, чтобы старые сессии тоже продолжали работать.
    """
    path = engine.user_font_path(user_id)
    if not path:
        return None
    return engine.register_user_font(user_id, path)


# --------------------------------------------------------------------------
# Меню
# --------------------------------------------------------------------------

#: Тег премиум-эмодзи в тексте. Если бот не имеет права их отправлять,
#: Telegram отвечает ошибкой, и тег надо снять, а не терять всё меню.
_TG_EMOJI_RE = re.compile(r"</?tg-emoji[^>]*>")


async def _menu_text(bot: Bot, first_name: Optional[str]) -> str:
    me = await bot.me()
    return texts.main_menu(first_name, len(engine.available_templates()), me.username or "")


async def _send_menu(message: Message, first_name: Optional[str]) -> None:
    """Шлёт главное меню — с картинкой, гифкой или видео, если они заданы.

    Премиум-эмодзи в тексте разрешены не каждому боту, и отказ Telegram
    не должен оставлять человека без меню: на ошибке повторяем тем же
    текстом без этих тегов.
    """
    text = await _menu_text(message.bot, first_name)
    markup = kb.main_menu()
    media = branding.media()

    async def send(body: str) -> None:
        if not media:
            await message.answer(body, reply_markup=markup)
            return
        file = FSInputFile(media["path"])
        sender = {
            "photo": message.answer_photo,
            "animation": message.answer_animation,
            "video": message.answer_video,
        }[media["type"]]
        await sender(file, caption=body, reply_markup=markup)

    try:
        await send(text)
    except TelegramBadRequest as exc:
        if "EMOJI" not in str(exc).upper():
            raise
        log.warning("Премиум-эмодзи в меню не приняты (%s) — шлю без них", exc)
        await send(_TG_EMOJI_RE.sub("", text))


async def _send_main_menu(message: Message, first_name: Optional[str]) -> None:
    await _send_menu(message, first_name)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandObject) -> None:
    await state.clear()
    user = message.from_user
    is_new = await db.ensure_user(user.id, user.username, user.first_name)

    # Реферальная ссылка: t.me/bot?start=ref_<id пригласившего>. Считается
    # только для новых пользователей — уже знакомый человек не может
    # «приглашённым» стать задним числом.
    arg = (command.args or "").strip()
    if is_new and arg.startswith("ref_") and arg[4:].isdigit():
        await db.register_referral(user.id, int(arg[4:]))

    missing = await _missing_channels(message.bot, user.id)
    if missing:
        await message.answer(texts.subscribe(missing), reply_markup=kb.subscribe(missing))
        return
    await _reward_referral(message.bot, user.id)
    await _send_main_menu(message, user.first_name)


async def _reward_referral(bot: Bot, invited_id: int) -> None:
    """Награда пригласившему — когда друг прошёл проверку подписки.

    Награждать в момент /start нельзя: ботов-однодневок можно нагнать
    сотнями, а подписка на канал — хоть какое-то подтверждение живого
    человека.
    """
    reward = await db.reward_referral(invited_id)
    if not reward:
        return
    bonus = f"\n🎁 Бонус за ступень: <b>+{reward['bonus']}</b> 💎" if reward["bonus"] else ""
    try:
        await bot.send_message(
            reward["inviter"],
            "👥 <b>Друг присоединился!</b>\n\n"
            f"Начислено: <b>+{reward['gained']}</b> 💎{bonus}\n"
            f"Друзей: <b>{reward['friends']}</b> · кристаллов: <b>{reward['crystals']}</b>",
        )
    except Exception as exc:
        log.warning("Уведомление о друге не ушло: %s", exc)


@router.callback_query(F.data == "sub:check")
async def cb_check_subscription(callback: CallbackQuery, state: FSMContext) -> None:
    missing = await _missing_channels(callback.bot, callback.from_user.id)
    if missing:
        await callback.answer(texts.still_not_subscribed(), show_alert=True)
        return
    await state.clear()
    await callback.answer("Подписка на месте")
    await _reward_referral(callback.bot, callback.from_user.id)
    await ui.drop(callback.message)
    await _send_menu(callback.message, callback.from_user.first_name)


@router.message(Command("menu", "help"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _send_main_menu(message, message.from_user.first_name)


@router.callback_query(F.data == "menu:main")
async def cb_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    if branding.media():
        # Картинку меню поверх текстового экрана не «дорисовать»:
        # переписываем сообщение целиком.
        await ui.drop(callback.message)
        await _send_menu(callback.message, callback.from_user.first_name)
        return
    await ui.show(
        callback,
        text=await _menu_text(callback.bot, callback.from_user.first_name),
        markup=kb.main_menu(),
    )


@router.callback_query(F.data == "menu:profile")
async def cb_profile(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    await db.ensure_user(user_id, callback.from_user.username, callback.from_user.first_name)
    user = await db.get_user(user_id)
    packs = await db.get_packs(user_id)
    await ui.show(
        callback,
        text=texts.profile(user, packs, callback.from_user.username),
        markup=kb.profile(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:support")
async def cb_support(callback: CallbackQuery) -> None:
    await ui.show(callback, text=texts.support(), markup=kb.support())
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    """Подтверждаем счёт. Отказ здесь Telegram показывает пользователю."""
    payload = query.invoice_payload
    if payload.startswith(("topup:", "premium:")):
        # Пополнение и премиум проверять не по чему: сумма зашита в счёте.
        await query.answer(ok=True)
        return

    order_id = payload.split(":", 1)[1] if payload.startswith("order:") else ""
    order = await db.get_order(int(order_id)) if order_id.isdigit() else None
    if not order or order["status"] != "new":
        await query.answer(ok=False, error_message="Заказ устарел, соберите набор заново.")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(message: Message, state: FSMContext) -> None:
    payment = message.successful_payment
    payload = payment.invoice_payload
    await state.set_state(None)

    if payload.startswith("topup:"):
        amount = int(payload.split(":", 1)[1])
        balance = await db.add_balance(message.from_user.id, amount)
        await message.answer(texts.topup_done(amount, balance), reply_markup=kb.profile())
        await notify.topup(message.bot, message.from_user, amount, balance)
        return

    if payload.startswith("premium:"):
        days = int(payload.split(":", 1)[1])
        until = await db.grant_premium(message.from_user.id, days)
        await message.answer(
            "💎 <b>Премиум активирован!</b>\n\n"
            f"Действует до <b>{time.strftime('%d.%m.%Y', time.localtime(until))}</b>. "
            "Теперь можно создавать любое количество эмодзи."
        )
        await notify.to_admins(
            message.bot,
            f"💎 <b>Премиум</b>: <code>{message.from_user.id}</code> · "
            f"{payment.total_amount} ⭐️ · {days} дн.",
        )
        return

    order_id = int(payload.split(":", 1)[1]) if payload.startswith("order:") else 0
    order = await db.get_order(order_id)
    if not order:
        await message.answer(texts.pack_failed("заказ не найден"))
        return

    await _queue_order(message.bot, order_id, "stars", message.chat.id,
                       payment.telegram_payment_charge_id)
    await message.answer(texts.payment_done(len(order["numbers"])))
    await notify.purchase(message.bot, message.from_user, order, "stars")


# --------------------------------------------------------------------------
# Шаг 1. Набор шаблонов
# --------------------------------------------------------------------------

@router.callback_query(F.data == "create:start")
async def cb_choose_pack(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    # Подписку проверяем и здесь, а не только на /start: отписаться можно
    # в любой момент, а вход в создание — единственная дверь дальше.
    missing = await _missing_channels(callback.bot, callback.from_user.id)
    if missing:
        await ui.show(callback, text=texts.subscribe(missing), markup=kb.subscribe(missing))
        await callback.answer()
        return
    stop = await _gate(callback.from_user.id)
    if stop:
        await ui.show(callback, text=stop, markup=kb.simple_back("menu:main", "◀️ В меню"))
        await callback.answer()
        return
    counts = {pack_id: len(engine.pack_templates(pack_id)) for pack_id in config.PACKS}
    await ui.show(callback, text=texts.choose_pack(), markup=kb.packs(counts))
    await callback.answer()


@router.callback_query(F.data.startswith("pack:"))
async def cb_pick_pack(callback: CallbackQuery, state: FSMContext) -> None:
    pack_id = callback.data.split(":", 1)[1]
    if pack_id not in config.PACKS:
        await callback.answer("Такого набора нет", show_alert=True)
        return
    await state.update_data(pack=pack_id, selected=[], page=0)
    await _show_mode(callback, pack_id)
    await callback.answer()


async def _show_mode(callback: CallbackQuery, pack_id: str) -> None:
    pack = config.PACKS[pack_id]
    await ui.show(
        callback,
        text=texts.choose_mode(str(pack["title"]), len(engine.pack_templates(pack_id))),
        markup=kb.mode(),
    )


@router.callback_query(F.data == "step:mode")
async def cb_back_to_mode(callback: CallbackQuery, state: FSMContext) -> None:
    pack_id = (await state.get_data()).get("pack", "main")
    await state.set_state(None)
    await _show_mode(callback, pack_id)
    await callback.answer()


# --------------------------------------------------------------------------
# Шаг 2а. Ручной выбор шаблонов
# --------------------------------------------------------------------------

async def _show_selection(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    pack_id = data.get("pack", "main")
    page = int(data.get("page", 0))
    selected = list(data.get("selected", []))

    pages = engine.total_pages(pack_id)
    page = max(0, min(page, pages - 1))
    image, numbers = await asyncio.to_thread(
        engine.selection_grid, pack_id, page, await _sample(callback.bot), selected,
    )

    await state.update_data(page=page)
    await state.set_state(Step.selection)
    await ui.show(
        callback,
        photo=image,
        caption=texts.selection(page, pages, numbers, len(selected)),
        markup=kb.selection(page, pages, numbers, selected),
    )


@router.callback_query(F.data == "mode:manual")
async def cb_mode_manual(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(mode="manual")
    await callback.answer()
    await _show_selection(callback, state)


@router.callback_query(F.data.startswith("selpage:"))
async def cb_selection_page(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(page=int(callback.data.split(":", 1)[1]))
    await callback.answer()
    await _show_selection(callback, state)


@router.callback_query(F.data.startswith("sel:"))
async def cb_selection_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    number = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    selected = list(data.get("selected", []))
    if number in selected:
        selected.remove(number)
    else:
        selected.append(number)
    await state.update_data(selected=selected)
    await callback.answer(f"Выбрано: {len(selected)}")
    await _show_selection(callback, state)


@router.callback_query(F.data == "selall")
async def cb_selection_all(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    numbers = engine.page_templates(data.get("pack", "main"), int(data.get("page", 0)))
    selected = sorted(set(data.get("selected", [])) | set(numbers))
    await state.update_data(selected=selected)
    await callback.answer(f"Выбрано: {len(selected)}")
    await _show_selection(callback, state)


@router.callback_query(F.data == "selclear")
async def cb_selection_clear(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(selected=[])
    await callback.answer("Выбор сброшен")
    await _show_selection(callback, state)


@router.message(Step.selection, F.text)
async def on_range_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    pack_id = data.get("pack", "main")
    allowed = engine.pack_templates(pack_id)
    parsed = _parse_range(message.text, allowed)
    if not parsed:
        await message.answer(texts.range_failed())
        return

    selected = sorted(set(data.get("selected", [])) | set(parsed))
    await state.update_data(selected=selected)
    await message.answer(texts.range_parsed(len(parsed), len(selected)))
    await _send_selection_message(message, state)


async def _send_selection_message(message: Message, state: FSMContext) -> None:
    """Перерисовывает страницу выбора новым сообщением.

    После текстового ввода редактировать старое нельзя: между ними уже
    висит ответ бота, и обновлённая страница уехала бы вверх ленты.
    """
    data = await state.get_data()
    pack_id = data.get("pack", "main")
    page = int(data.get("page", 0))
    selected = list(data.get("selected", []))
    pages = engine.total_pages(pack_id)
    image, numbers = await asyncio.to_thread(
        engine.selection_grid, pack_id, page, await _sample(message.bot), selected,
    )
    await message.answer_photo(
        photo=BufferedInputFile(image, filename="page.png"),
        caption=texts.selection(page, pages, numbers, len(selected)),
        reply_markup=kb.selection(page, pages, numbers, selected),
    )


@router.callback_query(F.data == "seldone")
async def cb_selection_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = sorted(set(data.get("selected", [])))
    if not selected:
        await callback.answer(texts.nothing_selected(), show_alert=True)
        return
    await state.update_data(numbers=selected)
    await callback.answer()
    await _show_fonts(callback, state)


# --------------------------------------------------------------------------
# Шаг 2б/3. Шрифт
# --------------------------------------------------------------------------

async def _show_fonts(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    pack_id = data.get("pack", "main")
    mode = data.get("mode", "random")

    me = await callback.bot.me()
    sample = f"@{me.username}"
    custom_id = await _custom_font_id(callback.from_user.id)

    await state.set_state(None)
    image, _ = await asyncio.to_thread(
        engine.font_showcase, pack_id, sample, sample, custom_id,
    )
    back = "step:mode" if mode == "random" else "mode:manual"
    await ui.show(
        callback,
        photo=image,
        caption=texts.choose_font(sample),
        markup=kb.fonts(custom_id, back),
    )


@router.callback_query(F.data == "mode:random")
async def cb_mode_random(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(mode="random")
    await callback.answer()
    await _show_fonts(callback, state)


@router.callback_query(F.data == "step:font")
async def cb_back_to_font(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _show_fonts(callback, state)


@router.callback_query(F.data == "font:reroll")
async def cb_font_reroll(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Другие примеры")
    await _show_fonts(callback, state)


@router.callback_query(F.data == "font:upload")
async def cb_font_upload(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Step.font_file)
    await ui.show(callback, text=texts.ask_font_file(), markup=kb.simple_back("step:font", "◀️ К шрифтам"))
    await callback.answer()


@router.message(Step.font_file, F.document)
async def on_font_file(message: Message, state: FSMContext) -> None:
    document = message.document
    file = await message.bot.get_file(document.file_id)
    buffer = await message.bot.download_file(file.file_path)
    data = buffer.read()

    problem = engine.validate_font(data, document.file_name or "")
    if problem:
        await message.answer(texts.font_rejected(problem))
        return

    ext = os.path.splitext(document.file_name or "")[1].lower()
    path = os.path.join(config.USER_FONTS_DIR, f"{message.from_user.id}{ext}")
    # Старый шрифт с другим расширением иначе остался бы лежать рядом и
    # мог бы подхватиться вместо нового.
    for old_ext in config.FONT_EXTENSIONS:
        old = os.path.join(config.USER_FONTS_DIR, f"{message.from_user.id}{old_ext}")
        if os.path.exists(old):
            os.remove(old)
    with open(path, "wb") as fh:
        fh.write(data)

    font_id = engine.register_user_font(message.from_user.id, path)
    await state.update_data(font=font_id)
    await message.answer(texts.font_accepted())
    await _send_fonts_message(message, state)


@router.message(Step.font_file)
async def on_font_wrong(message: Message) -> None:
    await message.answer("❌ Жду файл шрифта .ttf или .otf — именно документом.")


async def _send_fonts_message(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    pack_id = data.get("pack", "main")
    mode = data.get("mode", "random")
    me = await message.bot.me()
    sample = f"@{me.username}"
    custom_id = await _custom_font_id(message.from_user.id)
    await state.set_state(None)
    image, _ = await asyncio.to_thread(
        engine.font_showcase, pack_id, sample, sample, custom_id,
    )
    back = "step:mode" if mode == "random" else "mode:manual"
    await message.answer_photo(
        photo=BufferedInputFile(image, filename="fonts.png"),
        caption=texts.choose_font(sample),
        reply_markup=kb.fonts(custom_id, back),
    )


@router.callback_query(F.data.startswith("font:"))
async def cb_pick_font(callback: CallbackQuery, state: FSMContext) -> None:
    font_id = callback.data.split(":", 1)[1]
    known = set(config.FONTS) | {f"u{callback.from_user.id}"}
    if font_id not in known:
        await callback.answer("Такого шрифта нет", show_alert=True)
        return
    await state.update_data(font=font_id)
    await callback.answer(engine.font_label(font_id))
    await _show_content(callback, state)


# --------------------------------------------------------------------------
# Шаг 4. Надпись или логотип
# --------------------------------------------------------------------------

async def _show_content(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(Step.content)
    await ui.show(
        callback,
        text=texts.ask_content(engine.font_label(data.get("font", config.DEFAULT_FONT))),
        markup=kb.content_input(),
    )


@router.callback_query(F.data == "step:content")
async def cb_back_to_content(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _show_content(callback, state)


@router.message(Step.content, F.document)
async def on_logo(message: Message, state: FSMContext) -> None:
    document = message.document
    if not (document.file_name or "").lower().endswith(".svg"):
        await message.answer(texts.logo_rejected("нужен файл .svg"))
        return

    file = await message.bot.get_file(document.file_id)
    data = (await message.bot.download_file(file.file_path)).read()
    problem = engine.validate_logo(data)
    if problem:
        await message.answer(texts.logo_rejected(problem))
        return

    await state.update_data(logo=data.hex(), text="")
    await _after_content(message, state)


@router.message(Step.content, F.text)
async def on_content_text(message: Message, state: FSMContext) -> None:
    text = _clean_text(message.text)
    if not text:
        await message.answer("❌ Пустая надпись. Пришли хотя бы один символ.")
        return
    if len(text) > config.MAX_TEXT_LENGTH:
        await message.answer(texts.text_too_long(len(text)))
        return
    await state.update_data(text=text, logo=None)
    await _after_content(message, state)


@router.message(Step.content)
async def on_content_wrong(message: Message) -> None:
    await message.answer("❌ Пришли надпись текстом или логотип файлом .svg.")


async def _after_content(message: Message, state: FSMContext) -> None:
    """После надписи расходимся: случайному режиму нужно количество."""
    data = await state.get_data()
    if data.get("mode") == "manual":
        await state.set_state(None)
        numbers = list(data.get("numbers", []))
        await _send_order_preview(message, state, numbers)
        return

    await state.set_state(Step.quantity)
    available = len(engine.pack_templates(data.get("pack", "main")))
    await message.answer(texts.ask_quantity(available), reply_markup=kb.quantity(available))


# --------------------------------------------------------------------------
# Шаг 5. Количество (только случайный режим)
# --------------------------------------------------------------------------

@router.callback_query(F.data.startswith("qty:"))
async def cb_quantity(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    pack_id = data.get("pack", "main")
    available = len(engine.pack_templates(pack_id))
    count = max(config.MIN_ORDER, min(int(callback.data.split(":", 1)[1]), available))
    numbers = engine.random_templates(pack_id, count)

    await state.set_state(None)
    await callback.answer()
    await _show_order_preview(callback, state, numbers)


@router.message(Step.quantity, F.text)
async def on_quantity_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    pack_id = data.get("pack", "main")
    available = len(engine.pack_templates(pack_id))
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (config.MIN_ORDER <= int(raw) <= min(config.MAX_ORDER, available)):
        await message.answer(texts.bad_quantity(available))
        return

    numbers = engine.random_templates(pack_id, int(raw))
    await state.set_state(None)
    await _send_order_preview(message, state, numbers)


# --------------------------------------------------------------------------
# Шаг 6. Финальное превью и заказ
# --------------------------------------------------------------------------

async def _prepare_order(
    user_id: int,
    state: FSMContext,
    numbers: List[int],
) -> Tuple[Optional[bytes], Optional[str], Dict[str, Any]]:
    """Собирает превью заказа и записывает сам заказ в базу.

    Заказ сохраняется до счёта: платёж приходит отдельным апдейтом и
    после перезапуска бота FSM-сессии уже нет, а восстановить состав
    набора надо обязательно.
    """
    data = await state.get_data()
    font_id = data.get("font", config.DEFAULT_FONT)
    text = data.get("text", "")
    logo = bytes.fromhex(data["logo"]) if data.get("logo") else None
    shown = numbers[:PREVIEW_TILES]

    # Без премиума за один заказ можно собрать не больше FREE_ORDER_LIMIT.
    if (len(numbers) > config.FREE_ORDER_LIMIT and user_id not in config.ADMIN_IDS
            and not await db.user_is_premium(user_id)):
        return None, (
            f"без премиума до {config.FREE_ORDER_LIMIT} эмодзи за заказ — "
            "оформите премиум во вкладке «Прочее» мини-приложения"
        ), {}

    def work() -> bytes:
        items = [(n, engine.build_emoji(n, text, font_id, logo)) for n in shown]
        return engine.result_grid(items)

    try:
        image = await asyncio.to_thread(work)
    except Exception as exc:
        log.warning("Превью заказа не собралось: %s", exc)
        return None, str(exc)[:200], {}

    kind = data.get("kind", config.DEFAULT_KIND)
    target = data.get("target")
    # Админам сборка бесплатна: им нужен способ проверить шаблоны и
    # шрифты на живом боте, не гоняя звёзды по кругу через себя же.
    amount = 0 if user_id in config.ADMIN_IDS else len(numbers) * settings.price()
    amount, promo = await db.apply_promo(user_id, amount)
    order_id = await db.create_order(
        user_id=user_id,
        pack_id=data.get("pack", "main"),
        font_id=font_id,
        # Путь сохраняем только для загруженного шрифта: встроенные бот
        # найдёт сам по id. Сравнивать нужно со списком встроенных, а не
        # с префиксом «u» — иначе под него попадал бы и Uni Sans.
        font_path=None if font_id in config.FONTS else engine.user_font_path(user_id),
        text=text,
        logo=logo,
        numbers=numbers,
        amount=amount,
        kind=kind,
        promo=promo,
    )

    # Набор-получатель мог быть выбран до смены типа или количества, и
    # тогда он больше не подходит: сбрасываем на новый, а не отправляем
    # заказ в набор, куда он уже не влезает.
    rooms = await db.packs_with_room(user_id, len(numbers), kind)
    if target and target not in {p["name"] for p in rooms}:
        target = None
    if target:
        await db.set_order_target(order_id, target)

    await state.update_data(numbers=numbers, order_id=order_id, kind=kind, target=target)
    target_pack = next((p for p in rooms if p["name"] == target), None)

    return image, None, {
        "count": len(numbers),
        "shown": len(shown),
        "amount": amount,
        "kind": kind,
        "font": engine.font_label(font_id),
        "content": _content_label(text, logo),
        "can_reroll": data.get("mode") == "random",
        "target": str(target_pack["title"]) if target_pack else texts.NEW_PACK_LABEL,
        "balance": await db.get_balance(user_id),
        "has_packs": bool(rooms),
    }


def _order_caption(info: Dict[str, Any]) -> str:
    caption = texts.order_preview(
        info["count"], info["shown"], info["font"], info["content"],
        info["amount"], info["target"], info["balance"], info["kind"],
    )
    # Про долгое ожидание честнее сказать до оплаты, а не показывать
    # зависший прогресс после неё.
    calls = info["count"]
    if calls > STICKER_WRITE_LIMIT:
        caption += texts.long_build_warning(info["count"], _write_limiter.eta(calls))
    return caption


def _write_calls(count: int, is_new: bool) -> int:
    """Во сколько «мест» окна обойдётся заказ.

    Раньше здесь считались запросы, и выходило, что новый набор стоит
    один вызов. Telegram считает стикеры, поэтому цена заказа — это
    просто их количество, и создание набора ничем не дешевле дозаписи.
    """
    return count


def _order_markup(info: Dict[str, Any]) -> InlineKeyboardMarkup:
    return kb.order_preview(
        info["amount"], info["can_reroll"], info["balance"], info["has_packs"], info["kind"],
    )


async def _show_order_preview(callback: CallbackQuery, state: FSMContext, numbers: List[int]) -> None:
    async with _user_locks[callback.from_user.id]:
        image, problem, info = await _prepare_order(callback.from_user.id, state, numbers)
    if problem:
        await ui.show(callback, text=texts.build_failed(problem), markup=kb.simple_back("step:content"))
        return
    await ui.show(callback, photo=image, caption=_order_caption(info), markup=_order_markup(info))


async def _send_order_preview(message: Message, state: FSMContext, numbers: List[int]) -> None:
    status = await message.answer(texts.WORKING)
    async with _user_locks[message.from_user.id]:
        image, problem, info = await _prepare_order(message.from_user.id, state, numbers)
    await ui.drop(status)
    if problem:
        await message.answer(texts.build_failed(problem), reply_markup=kb.simple_back("step:content"))
        return
    await message.answer_photo(
        photo=BufferedInputFile(image, filename="order.png"),
        caption=_order_caption(info),
        reply_markup=_order_markup(info),
    )


async def _refresh_order(callback: CallbackQuery, state: FSMContext) -> None:
    """Пересобирает превью текущего состава — после смены типа или набора."""
    numbers = list((await state.get_data()).get("numbers", []))
    if not numbers:
        await callback.answer(texts.SESSION_LOST, show_alert=True)
        return
    await _show_order_preview(callback, state, numbers)


@router.callback_query(F.data.startswith("kind:"))
async def cb_kind(callback: CallbackQuery, state: FSMContext) -> None:
    """Переключает премиум-эмодзи и обычные стикеры на финальном экране."""
    kind = callback.data.split(":", 1)[1]
    if kind not in config.KINDS:
        await callback.answer("Неизвестный тип", show_alert=True)
        return
    # Набор другого типа для этого заказа не годится — выбор сбрасываем.
    await state.update_data(kind=kind, target=None)
    await callback.answer(config.KINDS[kind]["title"])
    await _refresh_order(callback, state)


@router.callback_query(F.data == "target:open")
async def cb_target_open(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    numbers = list(data.get("numbers", []))
    kind = data.get("kind", config.DEFAULT_KIND)
    rooms = await db.packs_with_room(callback.from_user.id, len(numbers), kind)
    await ui.show(
        callback,
        text=texts.choose_target(len(numbers), len(rooms)),
        markup=kb.targets(rooms, data.get("target")),
    )
    await callback.answer()


@router.callback_query(F.data == "target:back")
async def cb_target_back(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _refresh_order(callback, state)


@router.callback_query(F.data.startswith("target:"))
async def cb_target_pick(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", 1)[1]
    target = None if value == "new" else value
    if target:
        pack = await db.get_pack(target)
        if not pack or int(pack["user_id"]) != callback.from_user.id:
            await callback.answer("Этот набор недоступен", show_alert=True)
            return
    await state.update_data(target=target)
    await callback.answer("Новый набор" if target is None else "Допишем в набор")
    await _refresh_order(callback, state)


@router.callback_query(F.data == "order:reroll")
async def cb_reroll(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    numbers = list(data.get("numbers", []))
    if not numbers:
        await callback.answer(texts.SESSION_LOST, show_alert=True)
        return
    await callback.answer("Собираю другой состав")
    fresh = engine.random_templates(data.get("pack", "main"), len(numbers))
    await _show_order_preview(callback, state, fresh)


# --------------------------------------------------------------------------
# Шаг 7. Оплата звёздами
# --------------------------------------------------------------------------

@router.callback_query(F.data == "order:pay")
async def cb_pay(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    order_id = data.get("order_id")
    order = await db.get_order(int(order_id)) if order_id else None
    if not order:
        await callback.answer(texts.SESSION_LOST, show_alert=True)
        return

    await callback.answer()
    count = len(order["numbers"])
    await callback.message.answer_invoice(
        title=texts.invoice_title(),
        description=texts.invoice_description(count, _content_label(order["text"], order["logo"])),
        payload=f"order:{order['id']}",
        currency=config.CURRENCY,
        prices=[LabeledPrice(label=f"{count} шт.", amount=int(order["amount"]))],
    )


@router.callback_query(F.data.startswith("order:retry:"))
async def cb_retry_order(callback: CallbackQuery) -> None:
    """Повторная сборка уже оплаченного заказа.

    Ничего не списывает: заказ и его состав лежат в базе, повторяем
    ровно ту же выдачу.
    """
    order_id = int(callback.data.rsplit(":", 1)[1])
    order = await db.get_order(order_id)
    if not order or int(order["user_id"]) != callback.from_user.id:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    if order["status"] == "done":
        await callback.answer("Этот заказ уже выдан", show_alert=True)
        return

    left = _create_cooldown_left()
    if left > 0 and not order.get("target_pack"):
        await callback.answer(texts.retry_too_early(left), show_alert=True)
        return

    # Заказ уже в очереди — кнопка только двигает его в начало.
    await db.retry_order_now(order_id)
    _delivery_wake.set()
    await callback.answer("Ставлю в очередь — соберу сейчас")


@router.callback_query(F.data == "order:free")
async def cb_build_free(callback: CallbackQuery, state: FSMContext) -> None:
    """Сборка без оплаты — только для админов.

    Права проверяем здесь заново, а не полагаемся на то, что кнопки у
    остальных нет: callback_data подделывается одной строкой.
    """
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("Кнопка только для админов", show_alert=True)
        return

    data = await state.get_data()
    order_id = data.get("order_id")
    order = await db.get_order(int(order_id)) if order_id else None
    if not order:
        await callback.answer(texts.SESSION_LOST, show_alert=True)
        return
    if order["status"] != "new":
        await callback.answer("Этот заказ уже собран", show_alert=True)
        return

    await _queue_order(callback.bot, int(order["id"]), "admin", callback.message.chat.id)
    await callback.answer()
    await callback.message.answer(texts.admin_free())


@router.callback_query(F.data == "order:paybalance")
async def cb_pay_balance(callback: CallbackQuery, state: FSMContext) -> None:
    """Оплата с баланса — без счёта, одним нажатием.

    Списание идёт первым: если после него что-то сорвётся, звёзды видно в
    заказе и их можно вернуть, а вот выданный бесплатно набор уже не
    отозвать.
    """
    data = await state.get_data()
    order_id = data.get("order_id")
    order = await db.get_order(int(order_id)) if order_id else None
    if not order:
        await callback.answer(texts.SESSION_LOST, show_alert=True)
        return
    if order["status"] != "new":
        await callback.answer("Этот заказ уже оплачен", show_alert=True)
        return

    amount = int(order["amount"])
    if not await db.spend_balance(callback.from_user.id, amount):
        have = await db.get_balance(callback.from_user.id)
        await callback.answer(texts.not_enough_balance(amount, have), show_alert=True)
        return

    await _queue_order(callback.bot, int(order["id"]), "balance", callback.message.chat.id)
    await callback.answer("Списано с баланса")
    await callback.message.answer(texts.payment_done(len(order["numbers"])))
    await notify.purchase(
        callback.bot, callback.from_user, order, "balance",
        balance=await db.get_balance(callback.from_user.id),
    )


# --------------------------------------------------------------------------
# Баланс
# --------------------------------------------------------------------------

@router.callback_query(F.data == "topup:open")
async def cb_topup(callback: CallbackQuery, state: FSMContext) -> None:
    """Выбор способа оплаты. Крипта появляется, только если настроена."""
    await state.set_state(None)
    if not crypto.enabled():
        # Способ один — лишний экран человеку не нужен.
        await state.set_state(Step.topup)
        await ui.show(callback, text=texts.topup(), markup=kb.topup())
        await callback.answer()
        return

    balance = await db.get_balance(callback.from_user.id)
    await ui.show(callback, text=texts.topup_method(balance), markup=kb.topup_methods())
    await callback.answer()


@router.callback_query(F.data == "topup:stars")
async def cb_topup_stars(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Step.topup)
    await ui.show(callback, text=texts.topup(), markup=kb.topup())
    await callback.answer()


@router.callback_query(F.data == "topup:crypto")
async def cb_topup_crypto(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Step.crypto)
    await ui.show(callback, text=texts.crypto_amounts(), markup=kb.crypto_amounts())
    await callback.answer()


async def _send_crypto_invoice(message: Message, user_id: int, stars: int) -> None:
    """Выставляет счёт в криптокошельке и показывает кнопку оплаты."""
    amount = crypto.stars_to_amount(stars)
    try:
        invoice = await crypto.create_invoice(
            amount=amount,
            description=f"{stars} звёзд на баланс бота",
            payload=f"topup:{user_id}:{stars}",
        )
    except Exception as exc:
        log.error("Счёт в Crypto Pay не выставился: %s", exc)
        await message.answer(texts.crypto_failed(str(exc)[:150]))
        return

    await db.add_invoice(invoice["id"], user_id, message.chat.id, stars,
                         amount, config.CRYPTO_ASSET)
    await message.answer(
        texts.crypto_invoice(stars, amount),
        reply_markup=kb.crypto_invoice(invoice["url"], invoice["id"]),
    )


@router.callback_query(F.data.startswith("crypto:"))
async def cb_crypto_amount(callback: CallbackQuery, state: FSMContext) -> None:
    stars = int(callback.data.split(":", 1)[1])
    await state.set_state(None)
    await callback.answer("Выставляю счёт…")
    await _send_crypto_invoice(callback.message, callback.from_user.id, stars)


@router.message(Step.crypto, F.text)
async def on_crypto_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (config.MIN_TOPUP <= int(raw) <= config.MAX_TOPUP):
        await message.answer(texts.bad_topup())
        return
    await state.set_state(None)
    await _send_crypto_invoice(message, message.from_user.id, int(raw))


@router.callback_query(F.data.startswith("paid:"))
async def cb_crypto_check(callback: CallbackQuery) -> None:
    """Ручная проверка счёта — для нетерпеливых.

    Опрос всё равно заберёт оплату сам, но человеку спокойнее нажать
    кнопку, чем ждать неизвестно сколько.
    """
    invoice_id = int(callback.data.split(":", 1)[1])
    invoice = await db.get_invoice(invoice_id)
    if not invoice or int(invoice["user_id"]) != callback.from_user.id:
        await callback.answer("Счёт не найден", show_alert=True)
        return
    if invoice["status"] == "paid":
        await callback.answer("Этот счёт уже зачислен", show_alert=True)
        return

    try:
        statuses = await crypto.statuses([invoice_id])
    except Exception as exc:
        await callback.answer(f"Кошелёк не ответил: {exc}"[:190], show_alert=True)
        return

    if statuses.get(invoice_id) != "paid":
        await callback.answer(texts.crypto_pending(), show_alert=True)
        return

    await callback.answer("Оплата найдена")
    await _credit_invoice(callback.bot, invoice)


@router.callback_query(F.data.startswith("topup:"))
async def cb_topup_amount(callback: CallbackQuery, state: FSMContext) -> None:
    amount = int(callback.data.split(":", 1)[1])
    await state.set_state(None)
    await callback.answer()
    await _send_topup_invoice(callback.message, amount)


@router.message(Step.topup, F.text)
async def on_topup_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (config.MIN_TOPUP <= int(raw) <= config.MAX_TOPUP):
        await message.answer(texts.bad_topup())
        return
    await state.set_state(None)
    await _send_topup_invoice(message, int(raw))


async def _send_topup_invoice(message: Message, amount: int) -> None:
    await message.answer_invoice(
        title=texts.topup_invoice_title(),
        description=texts.topup_invoice_description(amount),
        payload=f"topup:{amount}",
        currency=config.CURRENCY,
        prices=[LabeledPrice(label=f"{amount} ⭐️", amount=amount)],
    )


# --------------------------------------------------------------------------
# Сборка набора
# --------------------------------------------------------------------------

class _WriteLimiter:
    """Ровно столько стикеров в наборы, сколько разрешает Telegram.

    Считаем именно СТИКЕРЫ, а не запросы. Это принципиально: в
    createNewStickerSet можно вложить хоть пятьдесят штук одним вызовом,
    и по числу запросов это единица, но Telegram считает содержимое.
    Отсюда и брались вечные пятиминутные паузы на заказах, где стикеров
    в первом же вызове было больше окна.

    Ждём заранее, а не ловим 429 постфактум: флуд-контроль отвечает
    минутными паузами, и разбираться с ним после факта дороже, чем не
    упираться в него вовсе. Лимит общий на весь бот, поэтому счётчик
    один на процесс, а ожидание идёт под замком — иначе два параллельных
    заказа выбрали бы окно вдвоём.
    """

    def __init__(self, limit: int, window: float) -> None:
        self._limit = limit
        self._window = window
        self._calls: Deque[float] = deque()
        self._lock = asyncio.Lock()

    def eta(self, stickers: int) -> float:
        """Оценка, сколько секунд займут ``stickers`` штук."""
        if stickers <= self._limit:
            return stickers * 1.5
        return ((stickers - 1) // self._limit) * self._window + stickers * 1.5

    async def acquire(self, cost: int = 1) -> None:
        """Занимает место под ``cost`` стикеров, дождавшись окна."""
        cost = max(1, min(cost, self._limit))
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self._window:
                    self._calls.popleft()
                if len(self._calls) + cost <= self._limit:
                    self._calls.extend([now] * cost)
                    return
                wait = self._window - (now - self._calls[0]) + 0.5
                log.info("Лимит записи исчерпан, ждём %.0f с (нужно мест: %d)", wait, cost)
                await asyncio.sleep(wait)


_write_limiter = _WriteLimiter(STICKER_WRITE_LIMIT, STICKER_WRITE_WINDOW)


class FloodLimit(Exception):
    """Telegram держит долгую паузу и не пускает дальше.

    Отдельный тип, потому что реакция на него другая: заказ не потерян,
    его нужно повторить позже, а не объявлять сбоем сборки.
    """

    def __init__(self, description: str, retry_after: int) -> None:
        super().__init__(description)
        self.description = description
        self.retry_after = retry_after


async def _sticker_write(action, description: str, paced: bool = True, cost: int = 1):
    """Обращение к Telegram с ожиданием флуд-контроля и повтором.

    Короткие паузы пережидаем — это обычная очередь. А вот длинную
    (минуты) пересиживать в цикле бессмысленно: так отвечает не темп
    запросов, а лимит на бота, и повтор получает ровно тот же ответ.
    Именно так заказ висел двадцать минут и всё равно падал. Такую
    паузу возвращаем наверх, чтобы предложить человеку повтор позже.

    paced=False для uploadStickerFile: он не пишет в набор и под лимит
    восьми обращений не попадает, а гнать его через тот же счётчик значило
    бы растянуть каждый заказ на часы на ровном месте.
    """
    for attempt in range(FLOOD_ATTEMPTS):
        if paced:
            await _write_limiter.acquire(cost)
        try:
            return await action()
        except TelegramRetryAfter as exc:
            if exc.retry_after > LONG_FLOOD_SECONDS:
                log.error(
                    "Долгий флуд-контроль на «%s»: %s с. Это лимит Telegram на "
                    "бота, повтор не поможет.", description, exc.retry_after,
                )
                raise FloodLimit(description, exc.retry_after) from exc
            wait = exc.retry_after + 1
            log.warning("Флуд-контроль на «%s»: ждём %s с", description, wait)
            await asyncio.sleep(wait)
        except TelegramBadRequest as exc:
            # Только что созданный набор Telegram разносит по своим
            # серверам не мгновенно и до тех пор отвечает, что набора нет.
            if "STICKERSET_INVALID" in str(exc).upper() and attempt < FLOOD_ATTEMPTS - 1:
                await asyncio.sleep(PROPAGATION_DELAY * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"Telegram не принял «{description}» после {FLOOD_ATTEMPTS} попыток")


def _sticker(data: bytes, number: int) -> InputSticker:
    """Стикер, у которого файл лежит прямо внутри запроса.

    Раньше каждый .tgs сначала уходил отдельным uploadStickerFile, и
    заказ из 75 штук делал 75 лишних обращений к Telegram — на десятом
    он упирался в ограничение, а остальные 65 молча терялись. Telegram
    принимает файл вложением прямо в createNewStickerSet: пятьдесят штук
    уходят одним запросом вместо пятидесяти.
    """
    return InputSticker(
        sticker=BufferedInputFile(data, filename=f"emoji_{number:03d}.tgs"),
        format="animated",
        emoji_list=[config.DEFAULT_EMOJI],
    )


async def _progress(status: Message, done: int, total: int, uploading: bool) -> None:
    """Обновляет сообщение о ходе сборки, переживая «message is not modified»."""
    eta = 0.0 if uploading else _write_limiter.eta(max(0, total - done))
    try:
        await status.edit_text(texts.progress(done, total, uploading, eta))
    except TelegramBadRequest:
        pass


#: Когда Telegram снова разрешит создать набор (время по monotonic).
#: Лимит общий на бота, поэтому счётчик один на процесс: пока он не
#: истёк, дёргать createNewStickerSet бессмысленно — только жечь попытки
#: и получать в ответ ту же паузу.
_create_cooldown_until: float = 0.0

#: Когда снова можно дописывать в готовый набор. Это отдельная операция
#: со своим окном: бывает, что создавать наборы уже нельзя, а дописывать
#: ещё можно — на этом и построен запасной путь.
_add_cooldown_until: float = 0.0

#: Как часто очередь выдачи просыпается сама. Оплата будит её сразу,
#: этот тик нужен только для отложенных заказов.
DELIVERY_TICK = 30.0

#: Сколько всего бот пытается выдать заказ, прежде чем вернуть деньги.
#: Полтора десятка попыток с растущей паузой — это около суток.
MAX_DELIVERY_ATTEMPTS = 15

#: Предельный возраст оплаченного заказа. Дальше — возврат: держать
#: чужие деньги дольше суток нельзя ни при каких лимитах Telegram.
MAX_DELIVERY_AGE = 24 * 3600

#: Потолок паузы между попытками. Лимит Telegram продлевается и от
#: самих обращений, поэтому пауза растёт, но не бесконечно.
MAX_RETRY_DELAY = 30 * 60

#: Событие, которым оплата будит очередь, не дожидаясь тика.
_delivery_wake = asyncio.Event()


def _create_cooldown_left() -> int:
    """Сколько секунд ещё нельзя создавать наборы."""
    return max(0, int(_create_cooldown_until - time.monotonic()))


def _add_cooldown_left() -> int:
    """Сколько секунд ещё нельзя дописывать в готовый набор."""
    return max(0, int(_add_cooldown_until - time.monotonic()))


def _note_flood(description: str, retry_after: int) -> None:
    """Запоминает паузу отдельно для создания и для дозаписи.

    Это разные операции с разными окнами: бывает, что создавать наборы
    уже нельзя, а дописывать в готовый ещё можно — на этом и построен
    запасной путь. Общий счётчик стирал бы разницу.
    """
    global _create_cooldown_until, _add_cooldown_until
    until = time.monotonic() + retry_after
    if "добавление" in description:
        _add_cooldown_until = until
    else:
        _create_cooldown_until = until


# --------------------------------------------------------------------------
# Опрос криптосчетов
# --------------------------------------------------------------------------

async def _credit_invoice(bot: Bot, invoice: Dict[str, Any]) -> None:
    """Зачисляет оплаченный счёт ровно один раз.

    Опрос и кнопка «я оплатил» могут сойтись на одном счёте, поэтому
    решает не проверка статуса, а запись в базе: закрыть активный счёт
    удаётся только одному из них.
    """
    invoice_id = int(invoice["invoice_id"])
    if not await db.close_invoice(invoice_id, "paid"):
        return

    user_id = int(invoice["user_id"])
    stars = int(invoice["stars"])
    balance = await db.add_balance(user_id, stars)
    log.info("Крипта: счёт #%s оплачен, +%s звёзд пользователю %s",
             invoice_id, stars, user_id)

    try:
        await bot.send_message(int(invoice["chat_id"]), texts.crypto_paid(stars, balance),
                               reply_markup=kb.profile())
    except Exception as exc:
        log.warning("Сообщение о зачислении не ушло: %s", exc)

    user = await db.get_user(user_id)
    await notify.to_admins(
        bot,
        "🪙 <b>Пополнение криптой</b>\n\n"
        f"👤 <code>{user_id}</code> "
        f"{('@' + user['username']) if user and user.get('username') else ''}\n"
        f"💵 {invoice['amount']} {invoice['asset']}\n"
        f"⭐️ Зачислено: <b>{stars}</b>\n"
        f"💼 Баланс: <b>{balance}</b>",
    )


async def _crypto_worker(bot: Bot) -> None:
    """Спрашивает у кошелька, какие счета оплатили.

    Вебхук потребовал бы публичного HTTPS-адреса, которого у бота на
    обычном хостинге нет. Опрос дешевле: один запрос раз в пятнадцать
    секунд и только пока есть неоплаченные счета.
    """
    if not crypto.enabled():
        log.info("Оплата криптой выключена (CRYPTO_TOKEN пуст)")
        return

    name = await crypto.check_token()
    if not name:
        log.error("CRYPTO_TOKEN не принят — оплата криптой работать не будет")
        return
    log.info("Оплата криптой включена: %s, %s звёзд за 1 %s",
             name, int(1 / config.CRYPTO_RATE), config.CRYPTO_ASSET)

    while True:
        try:
            invoices = await db.open_invoices()
            if invoices:
                statuses = await crypto.statuses([int(i["invoice_id"]) for i in invoices])
                for invoice in invoices:
                    status = statuses.get(int(invoice["invoice_id"]))
                    if status == "paid":
                        await _credit_invoice(bot, invoice)
                    elif status == "expired":
                        await db.close_invoice(int(invoice["invoice_id"]), "expired")
        except Exception:
            log.exception("Сбой опроса криптосчетов")
        await asyncio.sleep(config.CRYPTO_POLL_TICK)


# --------------------------------------------------------------------------
# Очередь выдачи
# --------------------------------------------------------------------------

async def _queue_order(bot: Bot, order_id: int, source: str, chat_id: int,
                       charge_id: Optional[str] = None) -> None:
    """Ставит оплаченный заказ в очередь и будит выдачу."""
    await db.mark_paid_for_delivery(order_id, source, chat_id, charge_id)
    # Скидка по промокоду тратится в момент оплаты, а не при создании заказа.
    await db.consume_order_promo(order_id)
    _delivery_wake.set()


async def _delivery_worker(bot: Bot) -> None:
    """Единственное место, где заказы превращаются в наборы.

    Очередь одна и последовательная — это главное. Раньше каждый заказ
    сам заводил себе таймеры повторов, они шли параллельно, отбирали
    друг у друга одно и то же окно Telegram и мешали друг другу. Теперь
    выдача идёт по одному заказу за раз, а состояние живёт в базе,
    поэтому переживает перезапуск бота.
    """
    log.info("Очередь выдачи запущена")
    while True:
        try:
            orders = await db.due_orders(limit=3)
            for order in orders:
                await _deliver(bot, order)
        except Exception:
            log.exception("Сбой в очереди выдачи")

        try:
            await asyncio.wait_for(_delivery_wake.wait(), timeout=DELIVERY_TICK)
        except asyncio.TimeoutError:
            pass
        _delivery_wake.clear()


async def _give_up(bot: Bot, order: Dict[str, Any], reason: str) -> None:
    """Возвращает деньги, если выдать заказ так и не удалось.

    Оплаченный и невыданный заказ не должен висеть вечно: либо набор,
    либо деньги обратно. Возврат делается тем же способом, каким платили.
    """
    order_id = int(order["id"])
    user_id = int(order["user_id"])
    chat_id = int(order.get("chat_id") or user_id)
    amount = int(order["amount"])
    source = order.get("paid_from")

    returned = False
    if source == "balance":
        await db.add_balance(user_id, amount)
        returned = True
    elif source == "crystals":
        await db.add_crystals(user_id, int(order.get("crystals") or 0))
        returned = True
    elif order.get("charge_id"):
        try:
            await bot.refund_star_payment(
                user_id=user_id, telegram_payment_charge_id=order["charge_id"],
            )
            returned = True
        except Exception as exc:
            log.error("Возврат по заказу #%s не прошёл: %s", order_id, exc)

    await db.set_order_status(order_id, "refunded" if returned else "failed")
    log.error("Заказ #%s закрыт без выдачи (%s), возврат: %s", order_id, reason, returned)

    try:
        await bot.send_message(chat_id, texts.order_refunded(amount, returned))
    except Exception as exc:
        log.warning("Сообщение о возврате не ушло: %s", exc)
    await notify.stuck_order(bot, None, order, 0)


async def _deliver(bot: Bot, order: Dict[str, Any]) -> None:
    """Собирает и выдаёт один оплаченный заказ.

    Ошибка отдельного эмодзи не роняет весь заказ: набор из 29 штук
    вместо 30 лучше, чем ничего после оплаты, а разницу бот показывает
    в итоговом сообщении.
    """
    order_id = int(order["id"])
    user_id = int(order["user_id"])
    chat_id = int(order.get("chat_id") or user_id)
    attempts = int(order.get("attempts") or 0)
    numbers: List[int] = order["numbers"]
    font_id: str = order["font_id"]
    text: str = order["text"]
    logo: Optional[bytes] = order["logo"]
    kind = order.get("kind") or config.DEFAULT_KIND

    # Заказ старше суток или исчерпавший попытки закрываем возвратом,
    # не пытаясь снова.
    age = int(time.time()) - int(order.get("paid_at") or 0)
    if attempts >= MAX_DELIVERY_ATTEMPTS or age > MAX_DELIVERY_AGE:
        await _give_up(bot, order, f"попыток {attempts}, возраст {age // 3600} ч")
        return

    async def say(body: str, markup=None):
        try:
            return await bot.send_message(chat_id, body, reply_markup=markup)
        except Exception as exc:
            log.warning("Сообщение по заказу #%s не ушло: %s", order_id, exc)
            return None

    # Личный шрифт после перезапуска в реестре движка отсутствует —
    # возвращаем его по пути, сохранённому в заказе.
    if order.get("font_path") and os.path.exists(order["font_path"]):
        font_id = engine.register_user_font(user_id, order["font_path"])

    status = await say(texts.progress(0, len(numbers), uploading=True))
    stickers: List[InputSticker] = []
    built_numbers: List[int] = []
    failed = 0

    for index, number in enumerate(numbers, 1):
        try:
            data = await asyncio.to_thread(engine.build_emoji, number, text, font_id, logo)
            stickers.append(_sticker(data, number))
            built_numbers.append(number)
        except Exception as exc:
            failed += 1
            log.error("Эмодзи из шаблона %s не собралось: %s", number, exc)
        if status and (index % 10 == 0 or index == len(numbers)):
            await _progress(status, index, len(numbers), uploading=True)

    if not stickers:
        await ui.drop(status)
        await _give_up(bot, order, "ни одно эмодзи не собралось")
        return

    target = await db.get_pack(order["target_pack"]) if order.get("target_pack") else None
    # Чужой или удалённый из базы набор не трогаем — соберём новый.
    if target and (int(target["user_id"]) != user_id or target.get("kind") != kind):
        target = None

    # Наборы, которые получились: имя, название, сколько в них ушло.
    # При разбиении на паки по 50 их несколько.
    created: List[Tuple[str, str, int]] = []
    profile = await db.get_user(user_id)
    first_name = (profile or {}).get("first_name")

    if target:
        name, title, is_new = str(target["name"]), str(target["title"]), False
    else:
        me = await bot.me()
        name, title, is_new = _pack_name(user_id, me.username), _pack_title(first_name), True

    async def add_all(pack_name: str, items: List[InputSticker], done_before: int) -> int:
        """Доклеивает стикеры по одному, показывая, сколько уже в наборе."""
        done = 0
        total = done_before + len(items)
        for position, sticker in enumerate(items, 1):
            try:
                await _sticker_write(
                    lambda s=sticker: bot.add_sticker_to_set(
                        user_id=user_id, name=pack_name, sticker=s,
                        request_timeout=UPLOAD_TIMEOUT,
                    ),
                    "добавление в набор",
                )
                done += 1
            except FloodLimit:
                # Лимит на бота: остальные точно так же не пройдут.
                # Отдаём наверх, чтобы отложить заказ, а не терять их.
                raise
            except Exception as exc:
                log.warning("Эмодзи не доклеилось в набор %s: %s", pack_name, exc)
            if status and (position % 2 == 0 or position == len(items)):
                await _progress(status, done_before + done, total, uploading=False)
        return done

    # Создание набора бывает закрыто лимитом надолго. Дозапись в готовый
    # набор — отдельная операция со своим окном, поэтому если у человека
    # такой набор есть и дозапись открыта, лучше выдать заказ туда, чем
    # откладывать его в никуда.
    if is_new and _create_cooldown_left() > 0 and _add_cooldown_left() == 0:
        fallback = await db.packs_with_room(user_id, len(stickers), kind)
        if fallback:
            target = fallback[-1]
            name, title, is_new = str(target["name"]), str(target["title"]), False
            log.info("Создание набора закрыто лимитом — дописываем в %s", name)
            await say(texts.switched_to_existing(title, len(stickers)))

    try:
        if is_new:
            # Пауза ещё идёт, а запасного набора нет: обращаться к
            # Telegram незачем — ответ будет тот же.
            left = _create_cooldown_left()
            if left > 0:
                raise FloodLimit("создание набора", left)

            # createNewStickerSet принимает до 50 штук одним вызовом, а
            # каждый следующий стикер — отдельное обращение под лимит
            # «8 за 4 минуты». Отсюда развилка: либо один большой набор
            # и больше часа ожидания, либо несколько наборов по 50 и
            # минута. Что выбрать, решает владелец в настройках.
            split = settings.split_packs() and len(stickers) > CREATE_BATCH
            me = await bot.me()
            added = 0

            for index in range(0, len(stickers), CREATE_BATCH):
                batch = stickers[index:index + CREATE_BATCH]
                if index == 0:
                    pack_name, pack_title = name, title
                else:
                    if not split:
                        break
                    pack_name = _pack_name(user_id, me.username)
                    pack_title = f"{title} #{index // CREATE_BATCH + 1}"[:64]

                await _sticker_write(
                    lambda n=pack_name, t=pack_title, b=batch: bot.create_new_sticker_set(
                        user_id=user_id,
                        name=n,
                        title=t,
                        stickers=b,
                        sticker_type=config.KINDS[kind]["sticker_type"],
                        request_timeout=UPLOAD_TIMEOUT,
                    ),
                    "создание набора",
                    cost=len(batch),
                )
                added += len(batch)
                created.append((pack_name, pack_title, len(batch)))
                if status:
                    await _progress(status, added, len(stickers), uploading=False)

            if not split:
                # Остаток доклеиваем в первый набор — медленно, зато
                # человек получает всё одним паком.
                added += await add_all(name, stickers[CREATE_BATCH:config.PACK_LIMIT], added)
        else:
            added = await add_all(name, stickers, 0)
            if not added:
                raise RuntimeError("ни одно эмодзи не добавилось в набор")
    except FloodLimit as exc:
        # Заказ цел: он оплачен, состав лежит в базе, статус «оплачен».
        # Очередь вернётся к нему сама, когда лимит отпустит.
        _note_flood(exc.description, exc.retry_after)
        # Пауза растёт от попытки к попытке: если лимит продлевается от
        # самих обращений, ровный интервал будет упираться в него вечно.
        delay = min(exc.retry_after * max(1, attempts), MAX_RETRY_DELAY)
        await db.postpone_order(order_id, delay)
        log.warning("Заказ #%s отложен на %s с (попытка %s)", order_id, delay, attempts + 1)
        await ui.drop(status)

        # Сообщаем один раз: очередь может ждать сутки, и двадцать
        # одинаковых сообщений об этом человеку не нужны.
        if attempts == 0:
            alternative = bool(await db.packs_with_room(user_id, len(numbers), kind))
            await say(
                texts.pack_postponed(delay, alternative, auto=True),
                kb.retry_order(order_id),
            )
        return
    except Exception as exc:
        log.exception("Набор не собрался")
        await db.set_order_status(order_id, "failed")
        await ui.drop(status)
        await say(texts.pack_failed(str(exc)[:250]))
        return

    await ui.drop(status)

    failed += len(stickers) - added
    if not created:
        # Дозапись в существующий набор: новых наборов не появлялось.
        created.append((name, title, added))

    for pack_name, pack_title, count in created:
        await db.add_pack(user_id, pack_name, pack_title, kind)
        await db.bump_pack(pack_name, count)

    await db.add_emoji_counter(user_id, added, int(order["amount"]))
    await db.bump_downloads(built_numbers[:added] if added else [])
    await db.set_order_status(order_id, "done")
    log.info("Заказ #%s выдан: %s шт в %s наборах", order_id, added, len(created))

    if len(created) > 1:
        await say(texts.packs_ready(created, added, failed, kind),
                  kb.after_pack(created[0][0], kind))
        return

    pack = await db.get_pack(name)
    await say(
        texts.pack_ready(name, title, added, int(pack["count"]) if pack else added,
                         failed, is_new, kind),
        kb.after_pack(name, kind),
    )


# --------------------------------------------------------------------------
# Запуск
# --------------------------------------------------------------------------

def _log_environment() -> None:
    """Печатает при старте, какой код и с чем именно запущен.

    На хостинге это единственный способ отличить «обновилось» от
    «крутится старая сборка»: версия, число шаблонов и версия aiogram
    видны в логе сразу после запуска, до первого сообщения.
    """
    import aiogram

    log.info("=" * 52)
    log.info("StickerEmojiBot %s", config.VERSION)
    log.info("aiogram %s | шаблонов: %d | шрифтов: %d",
             aiogram.__version__, len(engine.available_templates()), len(config.FONTS))
    log.info("данные: %s", config.DATA_DIR)
    log.info("оформление: %s",
             "branding.json" if os.path.exists(branding.BRANDING_PATH) else "по умолчанию")
    if aiogram.__version__ < "3.30":
        log.error(
            "aiogram %s старее 3.30: цветные кнопки и премиум-эмодзи на "
            "кнопках работать не будут. Нужен pip install -r requirements.txt",
            aiogram.__version__,
        )
    log.info("=" * 52)


async def _check_channel_access(bot: Bot, bot_id: int) -> None:
    """Говорит при старте, работает ли проверка подписки.

    Без прав администратора Telegram отвечает «member list is
    inaccessible», проверка молча пропускает всех, и понять это можно
    было только вычитав предупреждение среди сотен строк лога. Теперь
    состояние каждого канала видно сразу после запуска.
    """
    channels = settings.channels()
    if not channels or not settings.subscription_on():
        log.info("Обязательная подписка выключена")
        return
    for channel in channels:
        ok, reason = await check_channel(bot, bot_id, channel["id"])
        if ok:
            log.info("Подписка на %s проверяется", channel.get("title"))
        else:
            log.error(
                "ПОДПИСКА НЕ ПРОВЕРЯЕТСЯ (%s): %s. Без прав администратора "
                "канал пропускает всех подряд.", channel.get("title"), reason,
            )


async def check_channel(bot: Bot, bot_id: int, chat_id: Any) -> Tuple[bool, str]:
    """Может ли бот проверять подписку на этот канал. Зовёт и админка."""
    try:
        member = await bot.get_chat_member(chat_id, bot_id)
    except Exception as exc:
        return False, str(exc)
    if member.status not in {"administrator", "creator"}:
        return False, f"бот не администратор (статус {member.status})"
    return True, "ок"


async def main() -> None:
    if not config.BOT_TOKEN:
        log.error("BOT_TOKEN не задан. Скопируй .env.example в .env и впиши токен от @BotFather.")
        sys.exit(1)

    templates = engine.available_templates()
    if not templates:
        log.error("В папке templates/ нет ни одного .tgs — боту нечего показывать.")
        sys.exit(1)

    _log_environment()
    await db.init()
    await settings.load()
    await settings.load_channels()
    log.info("Шаблонов загружено: %d, цена: %d ⭐️", len(templates), settings.price())

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dp = Dispatcher(storage=MemoryStorage())
    # Админский роутер идёт первым: его фильтр пропускает только ADMIN_IDS,
    # а всё остальное проваливается дальше в пользовательский.
    dp.include_router(admin.router)
    dp.include_router(admin_branding.router)
    dp.include_router(admin_channels.router)
    dp.include_router(admin_promo.router)
    dp.include_router(router)

    me = await bot.me()
    log.info("Запущен как @%s", me.username)
    await _check_channel_access(bot, me.id)

    await bot.delete_webhook(drop_pending_updates=True)
    # Очередь выдачи живёт рядом с поллингом: заказы, оставшиеся
    # оплаченными с прошлого запуска, подхватятся сами.
    asyncio.create_task(_delivery_worker(bot))
    asyncio.create_task(_crypto_worker(bot))
    await _start_webapp(bot)
    await dp.start_polling(bot)


async def _start_webapp(bot: Bot) -> None:
    """Поднимает мини-приложение, если задан его публичный адрес.

    Сбой здесь не должен ронять бота: основной сценарий в чате работает
    и без мини-приложения.
    """
    if not config.WEBAPP_URL:
        log.info("WEBAPP_URL не задан — мини-приложение выключено")
        return
    try:
        await webapp_server.start(bot, {
            "queue_order": _queue_order,
            "missing_channels": lambda user_id: _missing_channels(bot, user_id),
        })
        # Кнопка рядом с полем ввода открывает мини-приложение сразу.
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Открыть", web_app=WebAppInfo(url=config.WEBAPP_URL)),
        )
    except Exception:
        log.exception("Мини-приложение не запустилось")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановлен")
