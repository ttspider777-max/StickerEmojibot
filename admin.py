"""Админ-панель: статистика, пользователи, заказы, рассылка, настройки.

Панель живёт в отдельном роутере с фильтром по списку ADMIN_IDS —
проверка стоит один раз на входе, а не в каждом хендлере, поэтому забыть
её в новом экране невозможно.

Весь обмен идёт по callback_data с префиксом ``adm:`` — так админские
кнопки не пересекаются с пользовательскими.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import html
import logging
import os
import time
from typing import Any, Dict, List, Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import branding
import config
import db
import notify
import settings
import texts
import tgs_engine as engine
import ui

log = logging.getLogger("emoji-bot.admin")

router = Router(name="admin")

# Фильтр на весь роутер: ниже по файлу проверять права уже не нужно.
router.message.filter(F.from_user.id.in_(config.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(config.ADMIN_IDS))

#: Сколько строк показываем на странице списков.
PAGE = 8

#: Пауза между сообщениями рассылки. 20 сообщений в секунду — потолок,
#: который Telegram держит без 429, поэтому идём чуть ниже.
BROADCAST_DELAY = 0.06

DAY = 86400


class Admin(StatesGroup):
    broadcast = State()
    price = State()
    search = State()
    message_user = State()
    give = State()
    give_target = State()


# --------------------------------------------------------------------------
# Вспомогательное
# --------------------------------------------------------------------------

def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


def _when(timestamp: Any) -> str:
    if not timestamp:
        return "—"
    return dt.datetime.fromtimestamp(int(timestamp)).strftime("%d.%m.%Y %H:%M")


def _user_line(user: Dict[str, Any]) -> str:
    handle = f"@{_esc(user['username'])}" if user.get("username") else _esc(user.get("first_name"))
    return (
        f"<code>{user['user_id']}</code> · {handle} · "
        f"✨{user.get('emoji_created', 0)} ⭐️{user.get('stars_spent', 0)} "
        f"💼{user.get('balance', 0)}"
    )


STATUS_LABELS = {
    "new": "🆕 не оплачен",
    "paid": "💸 оплачен, собирается",
    "done": "✅ выдан",
    "failed": "⚠️ сбой сборки",
    "refunded": "↩️ возвращён",
}


def _back(target: str = "adm:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ В панель", callback_data=target)],
    ])


def _pager(prefix: str, page: int, has_next: bool,
           extra: Optional[List[List[InlineKeyboardButton]]] = None) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = list(extra or [])
    nav: List[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="◀️ В панель", callback_data="adm:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --------------------------------------------------------------------------
# Главный экран
# --------------------------------------------------------------------------

def _home_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats"),
            InlineKeyboardButton(text="👥 Пользователи", callback_data="adm:users:0"),
        ],
        [
            InlineKeyboardButton(text="🧾 Заказы", callback_data="adm:orders:0"),
            InlineKeyboardButton(text="📢 Рассылка", callback_data="adm:cast"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="adm:set"),
            InlineKeyboardButton(text="🖼 Шаблоны", callback_data="adm:tpl"),
        ],
        [
            InlineKeyboardButton(text="💼 Выдать звёзды", callback_data="adm:giveany"),
            InlineKeyboardButton(text="🎨 Оформление", callback_data="adm:brand"),
        ],
        [
            InlineKeyboardButton(text="🎟 Промокоды", callback_data="adm:promo"),
            InlineKeyboardButton(text="🧩 Шаблоны юзеров", callback_data="adm:utpl:0"),
        ],
        [InlineKeyboardButton(text="📣 Обязательная подписка", callback_data="adm:channels")],
    ])


async def _home_text() -> str:
    data = await db.stats()
    mode = "🔧 техработы" if settings.maintenance() else "🟢 работает"
    # Оплаченные, но ещё не выданные заказы — то, за чем нужно следить
    # в первую очередь: это чужие деньги без товара.
    pending = await db.pending_orders(limit=50)
    queue = f"\n⏳ В очереди выдачи: <b>{len(pending)}</b>" if pending else ""
    return (
        "🛠 <b>Админ-панель</b>\n\n"
        f"Статус: <b>{mode}</b>\n"
        f"Цена: <b>{settings.price()}</b> ⭐️ за эмодзи\n\n"
        f"👥 {data['users']} · 🧾 {data['orders']} заказов · ⭐️ {data['stars']}"
        f"{queue}\n\n"
        f"<code>v{config.VERSION}</code> · шаблонов {len(engine.available_templates())}"
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(await _home_text(), reply_markup=_home_markup())


@router.message(Command("give"))
async def cmd_give(message: Message) -> None:
    """Быстрая выдача командой: /give @username 100."""
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Использование: <code>/give @username 100</code>")
        return

    handle, raw = parts[1], parts[2]
    try:
        amount = int(raw)
    except ValueError:
        await message.answer("❌ Третий аргумент — число звёзд.")
        return

    found = await db.find_users(handle, limit=5)
    exact = [u for u in found if str(u.get("username") or "").lower() == handle.lstrip("@").lower()
             or str(u["user_id"]) == handle]
    if not exact:
        await message.answer(f"❌ Не нашёл <code>{_esc(handle)}</code>.")
        return

    await message.answer(await _grant(message.bot, message.from_user, exact[0], amount))


@router.callback_query(F.data == "adm:home")
async def cb_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await ui.show(callback, text=await _home_text(), markup=_home_markup())
    await callback.answer()


# --------------------------------------------------------------------------
# Статистика
# --------------------------------------------------------------------------

@router.callback_query(F.data == "adm:stats")
async def cb_stats(callback: CallbackQuery) -> None:
    total = await db.stats()
    now = int(time.time())
    day = await db.period_stats(now - DAY)
    week = await db.period_stats(now - 7 * DAY)
    month = await db.period_stats(now - 30 * DAY)
    pending = await db.count_orders("new")
    failed = await db.count_orders("failed")

    text = (
        "📊 <b>Статистика</b>\n\n"
        "<b>Всего</b>\n"
        f"👥 Пользователей: <b>{total['users']}</b>\n"
        f"✨ Создано эмодзи: <b>{total['emoji']}</b>\n"
        f"📦 Наборов: <b>{total['packs']}</b>\n"
        f"🧾 Выдано заказов: <b>{total['orders']}</b>\n"
        f"⭐️ Получено звёзд: <b>{total['stars']}</b>\n\n"
        "<b>За период</b> (люди / заказы / звёзды)\n"
        f"Сутки: {day['users']} / {day['orders']} / {day['stars']}\n"
        f"Неделя: {week['users']} / {week['orders']} / {week['stars']}\n"
        f"Месяц: {month['users']} / {month['orders']} / {month['stars']}\n\n"
        "<b>Хвосты</b>\n"
        f"🆕 Незакрытых счетов: <b>{pending}</b>\n"
        f"⚠️ Сбоев сборки: <b>{failed}</b>"
    )
    await ui.show(callback, text=text, markup=_back())
    await callback.answer()


# --------------------------------------------------------------------------
# Пользователи
# --------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:users:"))
async def cb_users(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    page = int(callback.data.rsplit(":", 1)[1])
    users = await db.list_users(PAGE, page * PAGE)
    total = await db.count_users()

    if not users:
        body = "Пока никого."
    else:
        body = "\n".join(f"{i}. {_user_line(u)}" for i, u in enumerate(users, page * PAGE + 1))

    text = (
        f"👥 <b>Пользователи</b> — всего {total}\n"
        "<i>Новые сверху. Карточка открывается кнопкой с id.</i>\n\n"
        f"{body}"
    )
    cards = [
        [InlineKeyboardButton(text=str(u["user_id"]), callback_data=f"adm:user:{u['user_id']}")]
        for u in users
    ]
    # Кнопки-карточки по три в ряд: id длинный, больше в строку не влезает.
    packed = [sum(cards[i:i + 3], []) for i in range(0, len(cards), 3)]
    packed.insert(0, [InlineKeyboardButton(text="🔍 Найти", callback_data="adm:search")])

    await ui.show(
        callback,
        text=text,
        markup=_pager("adm:users:", page, len(users) == PAGE, packed),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:search")
async def cb_search(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Admin.search)
    await ui.show(
        callback,
        text="🔍 Пришли <b>id</b> или часть ника — покажу совпадения.",
        markup=_back("adm:users:0"),
    )
    await callback.answer()


@router.message(Admin.search, F.text)
async def on_search(message: Message, state: FSMContext) -> None:
    found = await db.find_users(message.text)
    await state.set_state(None)
    if not found:
        await message.answer("Ничего не нашёл.", reply_markup=_back("adm:users:0"))
        return
    rows = [
        [InlineKeyboardButton(
            text=f"{u['user_id']} · {u.get('username') or u.get('first_name') or '—'}",
            callback_data=f"adm:user:{u['user_id']}",
        )]
        for u in found
    ]
    rows.append([InlineKeyboardButton(text="◀️ В панель", callback_data="adm:home")])
    await message.answer(
        f"🔍 Найдено: <b>{len(found)}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("adm:user:"))
async def cb_user_card(callback: CallbackQuery) -> None:
    await _render_user_card(callback, int(callback.data.rsplit(":", 1)[1]))
    await callback.answer()


async def _render_user_card(callback: CallbackQuery, user_id: int) -> None:
    """Карточка вынесена из хендлера: её же перерисовывают блокировки.

    Переписать callback.data и позвать хендлер повторно нельзя — объекты
    aiogram неизменяемые.
    """
    user = await db.get_user(user_id)
    if not user:
        await callback.answer("Нет такого пользователя", show_alert=True)
        return

    packs = await db.get_packs(user_id)
    orders = await db.orders_of_user(user_id, limit=5)
    blocked = await db.is_blocked(user_id)

    lines = [
        "👤 <b>Карточка пользователя</b>\n",
        f"🆔 <code>{user_id}</code>",
        f"🔗 @{_esc(user['username'])}" if user.get("username") else f"🔗 {_esc(user.get('first_name'))}",
        f"📅 Пришёл: {_when(user.get('created_at'))}",
        f"👀 Последний раз: {_when(user.get('last_seen_at'))}",
        f"✨ Эмодзи: <b>{user.get('emoji_created', 0)}</b>",
        f"⭐️ Потрачено: <b>{user.get('stars_spent', 0)}</b>",
        f"💼 Баланс: <b>{user.get('balance', 0)}</b>",
        f"📦 Наборов: <b>{len(packs)}</b>",
        f"🚫 Блокировка: <b>{'да' if blocked else 'нет'}</b>",
    ]
    if orders:
        lines.append("\n<b>Последние заказы</b>")
        for order in orders:
            lines.append(
                f"• #{order['id']} · {len(order['numbers'])} шт · "
                f"{order['amount']} ⭐️ · {STATUS_LABELS.get(order['status'], order['status'])}"
            )
    if packs:
        lines.append("\n<b>Наборы</b>")
        for pack in packs:
            lines.append(f"• <a href=\"https://t.me/addemoji/{pack['name']}\">{_esc(pack['title'])}</a>"
                         f" — {pack['count']} шт.")

    rows = [
        [InlineKeyboardButton(text="💼 Выдать звёзды", callback_data=f"adm:give:{user_id}")],
        [InlineKeyboardButton(text="✍️ Написать", callback_data=f"adm:msg:{user_id}")],
        [InlineKeyboardButton(
            text="✅ Разблокировать" if blocked else "🚫 Заблокировать",
            callback_data=f"adm:{'unblock' if blocked else 'block'}:{user_id}",
        )],
        [InlineKeyboardButton(text="◀️ К списку", callback_data="adm:users:0")],
    ]
    await ui.show(callback, text="\n".join(lines),
                  markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:block:"))
async def cb_block(callback: CallbackQuery) -> None:
    user_id = int(callback.data.rsplit(":", 1)[1])
    await db.block_user(user_id, reason=f"admin {callback.from_user.id}")
    await callback.answer("Заблокирован")
    await _render_user_card(callback, user_id)


@router.callback_query(F.data.startswith("adm:unblock:"))
async def cb_unblock(callback: CallbackQuery) -> None:
    user_id = int(callback.data.rsplit(":", 1)[1])
    await db.unblock_user(user_id)
    await callback.answer("Разблокирован")
    await _render_user_card(callback, user_id)


@router.callback_query(F.data.startswith("adm:msg:"))
async def cb_message_user(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = int(callback.data.rsplit(":", 1)[1])
    await state.set_state(Admin.message_user)
    await state.update_data(target=user_id)
    await ui.show(
        callback,
        text=f"✍️ Пришли сообщение — отправлю его пользователю <code>{user_id}</code> от лица бота.",
        markup=_back(f"adm:user:{user_id}"),
    )
    await callback.answer()


@router.message(Admin.message_user)
async def on_message_user(message: Message, state: FSMContext) -> None:
    target = (await state.get_data()).get("target")
    await state.set_state(None)
    if not target:
        await message.answer("Потерял адресата, открой карточку заново.", reply_markup=_back())
        return
    try:
        await message.copy_to(chat_id=int(target))
    except Exception as exc:
        await message.answer(f"❌ Не доставлено: <code>{_esc(exc)}</code>", reply_markup=_back())
        return
    await message.answer("✅ Отправлено.", reply_markup=_back(f"adm:user:{target}"))


# --------------------------------------------------------------------------
# Заказы
# --------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:orders:"))
async def cb_orders(callback: CallbackQuery) -> None:
    page = int(callback.data.rsplit(":", 1)[1])
    orders = await db.list_orders(PAGE, page * PAGE)
    total = await db.count_orders()

    if not orders:
        body = "Заказов пока нет."
    else:
        body = "\n".join(
            f"#{o['id']} · <code>{o['user_id']}</code> · {len(o['numbers'])} шт · "
            f"{o['amount']} ⭐️ · {STATUS_LABELS.get(o['status'], o['status'])}\n"
            f"    {_when(o['created_at'])} · «{_esc(o['text'])[:20] or '—'}»"
            for o in orders
        )

    cards = [
        [InlineKeyboardButton(text=f"#{o['id']}", callback_data=f"adm:order:{o['id']}")]
        for o in orders
    ]
    packed = [sum(cards[i:i + 4], []) for i in range(0, len(cards), 4)]

    await ui.show(
        callback,
        text=f"🧾 <b>Заказы</b> — всего {total}\n\n{body}",
        markup=_pager("adm:orders:", page, len(orders) == PAGE, packed),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:order:"))
async def cb_order_card(callback: CallbackQuery) -> None:
    await _render_order_card(callback, int(callback.data.rsplit(":", 1)[1]))
    await callback.answer()


async def _render_order_card(callback: CallbackQuery, order_id: int) -> None:
    order = await db.get_order(order_id)
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return

    content = "SVG-логотип" if order["logo"] else f"«{_esc(order['text'])}»"
    text = (
        f"🧾 <b>Заказ #{order_id}</b>\n\n"
        f"👤 Пользователь: <code>{order['user_id']}</code>\n"
        f"📅 Создан: {_when(order['created_at'])}\n"
        f"🖼 Шаблоны ({len(order['numbers'])}): <code>{_esc(', '.join(map(str, order['numbers'])))}</code>\n"
        f"🔤 Шрифт: <b>{_esc(engine.font_label(order['font_id']))}</b>\n"
        f"✏️ Содержимое: <b>{content}</b>\n"
        f"⭐️ Сумма: <b>{order['amount']}</b>\n"
        f"📌 Статус: <b>{STATUS_LABELS.get(order['status'], order['status'])}</b>\n"
        f"🧷 Платёж: <code>{_esc(order['charge_id']) or '—'}</code>"
    )

    rows = [[InlineKeyboardButton(text="👤 Карточка", callback_data=f"adm:user:{order['user_id']}")]]
    if order["charge_id"] and order["status"] != "refunded":
        rows.insert(0, [InlineKeyboardButton(
            text="↩️ Вернуть звёзды", callback_data=f"adm:refund:{order_id}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ К заказам", callback_data="adm:orders:0")])

    await ui.show(callback, text=text, markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("adm:refund:"))
async def cb_refund_confirm(callback: CallbackQuery) -> None:
    """Возврат спрашивает подтверждение: отменить его уже нельзя."""
    order_id = int(callback.data.rsplit(":", 1)[1])
    order = await db.get_order(order_id)
    if not order or not order["charge_id"]:
        await callback.answer("Нечего возвращать", show_alert=True)
        return
    await ui.show(
        callback,
        text=(
            f"↩️ Вернуть <b>{order['amount']}</b> ⭐️ пользователю "
            f"<code>{order['user_id']}</code> по заказу #{order_id}?\n\n"
            "<i>Отменить возврат нельзя.</i>"
        ),
        markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, вернуть", callback_data=f"adm:refundgo:{order_id}")],
            [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"adm:order:{order_id}")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:refundgo:"))
async def cb_refund_do(callback: CallbackQuery) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    order = await db.get_order(order_id)
    if not order or not order["charge_id"]:
        await callback.answer("Нечего возвращать", show_alert=True)
        return
    try:
        await callback.bot.refund_star_payment(
            user_id=int(order["user_id"]),
            telegram_payment_charge_id=order["charge_id"],
        )
    except Exception as exc:
        await callback.answer(f"Не вышло: {exc}"[:190], show_alert=True)
        return

    await db.set_order_status(order_id, "refunded")
    await callback.answer("Возвращено")
    await _render_order_card(callback, order_id)


# --------------------------------------------------------------------------
# Рассылка
# --------------------------------------------------------------------------

@router.callback_query(F.data == "adm:cast")
async def cb_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Admin.broadcast)
    recipients = len(await db.all_user_ids())
    await ui.show(
        callback,
        text=(
            "📢 <b>Рассылка</b>\n\n"
            f"Получателей: <b>{recipients}</b> (заблокированные исключены).\n\n"
            "Пришли сообщение — текст, фото, что угодно. Оно уйдёт "
            "как есть, копией. Перед отправкой покажу подтверждение."
        ),
        markup=_back(),
    )
    await callback.answer()


@router.message(Admin.broadcast)
async def on_broadcast_content(message: Message, state: FSMContext) -> None:
    await state.update_data(cast_chat=message.chat.id, cast_message=message.message_id)
    await state.set_state(None)
    recipients = len(await db.all_user_ids())
    await message.answer(
        f"📢 Отправить это сообщение <b>{recipients}</b> пользователям?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Отправить", callback_data="adm:castgo")],
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="adm:home")],
        ]),
    )


@router.callback_query(F.data == "adm:castgo")
async def cb_broadcast_run(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    chat_id, message_id = data.get("cast_chat"), data.get("cast_message")
    if not chat_id or not message_id:
        await callback.answer("Сообщение потерялось, пришли заново", show_alert=True)
        return

    await callback.answer("Поехали")
    recipients = await db.all_user_ids()
    status = await callback.message.answer(f"📢 Отправляю… 0/{len(recipients)}")

    sent = blocked = errors = 0
    for index, user_id in enumerate(recipients, 1):
        try:
            await callback.bot.copy_message(
                chat_id=user_id, from_chat_id=chat_id, message_id=message_id,
            )
            sent += 1
        except TelegramForbiddenError:
            # Человек остановил бота — это не ошибка рассылки, а обычное дело.
            blocked += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            errors += 1
        except (TelegramBadRequest, Exception) as exc:  # noqa: B014
            errors += 1
            log.warning("Рассылка %s: %s", user_id, exc)

        if index % 25 == 0:
            try:
                await status.edit_text(f"📢 Отправляю… {index}/{len(recipients)}")
            except TelegramBadRequest:
                pass
        await asyncio.sleep(BROADCAST_DELAY)

    await ui.drop(status)
    await state.clear()
    await callback.message.answer(
        "📢 <b>Рассылка закончена</b>\n\n"
        f"✅ Доставлено: <b>{sent}</b>\n"
        f"🚫 Остановили бота: <b>{blocked}</b>\n"
        f"⚠️ Ошибок: <b>{errors}</b>",
        reply_markup=_back(),
    )


# --------------------------------------------------------------------------
# Настройки
# --------------------------------------------------------------------------

def _settings_markup() -> InlineKeyboardMarkup:
    toggle = "🟢 Включить приём" if settings.maintenance() else "🔧 Включить техработы"
    split = ("📦 Большой заказ: паками по 50" if settings.split_packs()
             else "🐌 Большой заказ: одним набором")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Изменить цену", callback_data="adm:price")],
        [InlineKeyboardButton(text=split, callback_data="adm:split")],
        [InlineKeyboardButton(text=toggle, callback_data="adm:maint")],
        [InlineKeyboardButton(text="◀️ В панель", callback_data="adm:home")],
    ])


def _settings_text() -> str:
    return (
        "⚙️ <b>Настройки</b>\n\n"
        f"💰 Цена: <b>{settings.price()}</b> ⭐️ за эмодзи\n"
        f"🔧 Техработы: <b>{'включены' if settings.maintenance() else 'выключены'}</b>\n"
        f"👮 Админов: <b>{len(config.ADMIN_IDS)}</b>\n\n"
        "<i>Цена и техработы применяются сразу и переживают перезапуск. "
        "Список админов задаётся в .env.</i>"
    )


@router.callback_query(F.data == "adm:set")
async def cb_settings(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    await ui.show(callback, text=_settings_text(), markup=_settings_markup())
    await callback.answer()


@router.callback_query(F.data == "adm:split")
async def cb_split(callback: CallbackQuery) -> None:
    await settings.set_split_packs(not settings.split_packs())
    await callback.answer(
        "Большие заказы пойдут паками по 50" if settings.split_packs()
        else "Большие заказы пойдут одним набором",
    )
    await ui.show(callback, text=_settings_text(), markup=_settings_markup())


@router.callback_query(F.data == "adm:maint")
async def cb_maintenance(callback: CallbackQuery) -> None:
    await settings.set_maintenance(not settings.maintenance())
    await callback.answer("Техработы включены" if settings.maintenance() else "Приём заказов включён")
    await ui.show(callback, text=_settings_text(), markup=_settings_markup())


@router.callback_query(F.data == "adm:price")
async def cb_price(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Admin.price)
    await ui.show(
        callback,
        text=(
            f"💰 Сейчас <b>{settings.price()}</b> ⭐️ за эмодзи.\n\n"
            "Пришли новое число — от 1 до 2500."
        ),
        markup=_back("adm:set"),
    )
    await callback.answer()


@router.message(Admin.price, F.text)
async def on_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    # Верхняя граница — лимит Telegram на сумму счёта в звёздах.
    if not raw.isdigit() or not (1 <= int(raw) <= 2500):
        await message.answer("❌ Нужно целое число от 1 до 2500.")
        return
    await settings.set_price(int(raw))
    await state.set_state(None)
    await message.answer(
        f"✅ Новая цена: <b>{settings.price()}</b> ⭐️ за эмодзи.",
        reply_markup=_back("adm:set"),
    )


# --------------------------------------------------------------------------
# Шаблоны
# --------------------------------------------------------------------------

@router.callback_query(F.data == "adm:tpl")
async def cb_templates(callback: CallbackQuery) -> None:
    numbers = engine.available_templates()
    missing = [n for n in range(1, config.TEMPLATE_COUNT + 1) if n not in numbers]
    text = (
        "🖼 <b>Шаблоны</b>\n\n"
        f"📦 На диске: <b>{len(numbers)}</b> из {config.TEMPLATE_COUNT}\n"
        f"🕳 Отсутствуют: <code>{', '.join(map(str, missing)) or '—'}</code>\n\n"
        "Проверка прогоняет подстановку текста по всем шаблонам и "
        "показывает те, что не собираются. Занимает несколько секунд."
    )
    await ui.show(
        callback,
        text=text,
        markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Проверить все", callback_data="adm:tplcheck")],
            [InlineKeyboardButton(text="◀️ В панель", callback_data="adm:home")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:tplcheck")
async def cb_templates_check(callback: CallbackQuery) -> None:
    await callback.answer("Проверяю…")
    await ui.show(callback, text="🔍 Проверяю шаблоны…", markup=None)

    def work() -> List[str]:
        problems: List[str] = []
        for number in engine.available_templates():
            try:
                data = engine.build_emoji(number, "ПРОВЕРКА ШАБЛОНА", config.DEFAULT_FONT)
                issue = engine.validate(data)
                if issue:
                    problems.append(f"{number}: {issue}")
            except Exception as exc:
                problems.append(f"{number}: {exc}")
        return problems

    problems = await asyncio.to_thread(work)
    if not problems:
        text = "✅ <b>Все шаблоны в порядке</b>\n\nПодстановка и проверка размера прошли на всех."
    else:
        listing = "\n".join(f"• {_esc(p)}" for p in problems[:20])
        tail = "" if len(problems) <= 20 else f"\n… и ещё {len(problems) - 20}"
        text = f"⚠️ <b>Проблемных шаблонов: {len(problems)}</b>\n\n{listing}{tail}"

    await ui.show(callback, text=text, markup=_back())


# --------------------------------------------------------------------------
# Выдача звёзд
# --------------------------------------------------------------------------

async def _grant(bot, admin_user, target: Dict[str, Any], amount: int) -> str:
    """Начисляет звёзды, сообщает получателю и остальным админам."""
    balance = await db.add_balance(int(target["user_id"]), amount)
    try:
        await bot.send_message(int(target["user_id"]), texts.balance_granted(amount, balance))
    except Exception as exc:
        # Человек мог остановить бота: выдача всё равно состоялась, и
        # админ должен увидеть, что уведомление не дошло.
        log.warning("Уведомление о выдаче не дошло до %s: %s", target["user_id"], exc)
    await notify.granted(bot, admin_user, int(target["user_id"]), amount, balance)
    verb = "Выдано" if amount >= 0 else "Списано"
    return (
        f"✅ {verb} <b>{abs(amount)}</b> ⭐️\n"
        f"👤 <code>{target['user_id']}</code>\n"
        f"💼 Баланс: <b>{balance}</b>"
    )


@router.callback_query(F.data.startswith("adm:give:"))
async def cb_give(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = int(callback.data.rsplit(":", 1)[1])
    await state.set_state(Admin.give)
    await state.update_data(give_to=user_id)
    await ui.show(
        callback,
        text=(
            f"💼 Сколько звёзд начислить пользователю <code>{user_id}</code>?\n\n"
            "Пришли число. Отрицательное — снять с баланса."
        ),
        markup=_back(f"adm:user:{user_id}"),
    )
    await callback.answer()


@router.message(Admin.give, F.text)
async def on_give_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        amount = int(raw)
    except ValueError:
        await message.answer("❌ Нужно целое число, можно со знаком минус.")
        return
    if amount == 0 or abs(amount) > 100_000:
        await message.answer("❌ Число от 1 до 100000, можно со знаком минус.")
        return

    user_id = (await state.get_data()).get("give_to")
    target = await db.get_user(int(user_id)) if user_id else None
    if not target:
        await state.set_state(None)
        await message.answer("Пользователь не найден.", reply_markup=_back())
        return

    await state.set_state(None)
    report = await _grant(message.bot, message.from_user, target, amount)
    await message.answer(report, reply_markup=_back(f"adm:user:{user_id}"))


@router.callback_query(F.data == "adm:giveany")
async def cb_give_any(callback: CallbackQuery, state: FSMContext) -> None:
    """Выдача по юзернейму — без захода в карточку."""
    await state.set_state(Admin.give_target)
    await ui.show(
        callback,
        text=(
            "💼 <b>Выдать звёзды</b>\n\n"
            "Пришли одной строкой: <code>@username 100</code> или "
            "<code>123456789 100</code>.\n\n"
            "<i>Человек должен был хотя бы раз запустить бота — иначе его "
            "не найти по нику.</i>"
        ),
        markup=_back(),
    )
    await callback.answer()


@router.message(Admin.give_target, F.text)
async def on_give_any(message: Message, state: FSMContext) -> None:
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("❌ Формат: <code>@username 100</code>")
        return

    handle, raw = parts
    try:
        amount = int(raw)
    except ValueError:
        await message.answer("❌ Второе значение — число звёзд.")
        return
    if amount == 0 or abs(amount) > 100_000:
        await message.answer("❌ Число от 1 до 100000, можно со знаком минус.")
        return

    found = await db.find_users(handle, limit=5)
    # Поиск по нику ищет подстрокой — для выдачи денег этого мало:
    # «@ivan» не должен молча попасть в «@ivanov».
    exact = [u for u in found if str(u.get("username") or "").lower() == handle.lstrip("@").lower()
             or str(u["user_id"]) == handle]
    if not exact:
        await message.answer(
            f"❌ Не нашёл <code>{_esc(handle)}</code>. Проверь ник или дай id.",
        )
        return

    await state.set_state(None)
    report = await _grant(message.bot, message.from_user, exact[0], amount)
    await message.answer(report, reply_markup=_back())


# --------------------------------------------------------------------------
# Оформление
