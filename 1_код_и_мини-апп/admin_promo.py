"""Админка мини-приложения: промокоды, пользовательские шаблоны, кристаллы.

Отдельный роутер по той же причине, что и у оформления: admin.py и так
велик. Все экраны живут под префиксом ``adm:`` и закрыты фильтром по
ADMIN_IDS на уровне всего роутера.
"""

from __future__ import annotations

import html
import time
from typing import Any, Dict, List, Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import db
import ui

router = Router(name="admin-promo")
router.message.filter(F.from_user.id.in_(config.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(config.ADMIN_IDS))

#: Сколько шаблонов показываем на странице модерации.
TPL_PAGE = 6


class PromoAdmin(StatesGroup):
    create = State()


def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


def _date(ts: int) -> str:
    return time.strftime("%d.%m.%Y", time.localtime(ts)) if ts else "—"


# --------------------------------------------------------------------------
# Промокоды
# --------------------------------------------------------------------------

async def _promo_screen():
    promos = await db.list_promos()
    lines = ["🎟 <b>Промокоды</b>\n"]
    rows: List[List[InlineKeyboardButton]] = []
    if not promos:
        lines.append("Пока ни одного. Создай первый — пользователь введёт его в "
                     "профиле мини-приложения и получит скидку на следующий заказ.")
    for promo in promos[:20]:
        limit = f"{promo['used']}/{promo['max_uses']}" if promo["max_uses"] else f"{promo['used']}/∞"
        state = "🟢" if promo["active"] else "⏸"
        until = f" · до {_date(promo['expires_at'])}" if promo["expires_at"] else ""
        lines.append(
            f"{state} <code>{_esc(promo['code'])}</code> — <b>-{promo['percent']}%</b> · "
            f"использовано {limit}{until}"
        )
        rows.append([
            InlineKeyboardButton(
                text=("⏸ Выкл " if promo["active"] else "▶️ Вкл ") + promo["code"],
                callback_data=f"adm:promot:{promo['code']}",
            ),
            InlineKeyboardButton(text="🗑", callback_data=f"adm:promod:{promo['code']}"),
        ])
    rows.append([InlineKeyboardButton(text="➕ Создать промокод", callback_data="adm:promonew")])
    rows.append([InlineKeyboardButton(text="◀️ В панель", callback_data="adm:home")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "adm:promo")
async def cb_promo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, markup = await _promo_screen()
    await ui.show(callback, text=text, markup=markup)
    await callback.answer()


@router.callback_query(F.data == "adm:promonew")
async def cb_promo_new(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PromoAdmin.create)
    await ui.show(
        callback,
        text=(
            "➕ <b>Новый промокод</b>\n\n"
            "Пришли одной строкой:\n"
            "<code>КОД ПРОЦЕНТ [МАКС_ИСПОЛЬЗОВАНИЙ] [ДНЕЙ]</code>\n\n"
            "Примеры:\n"
            "<code>SALE20 20</code> — скидка 20%, без ограничений\n"
            "<code>VIP50 50 100 30</code> — 50%, на 100 человек, 30 дней"
        ),
        markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="adm:promo")],
        ]),
    )
    await callback.answer()


@router.message(PromoAdmin.create, F.text)
async def on_promo_create(message: Message, state: FSMContext) -> None:
    parts = (message.text or "").split()
    try:
        code = parts[0]
        percent = int(parts[1])
        max_uses = int(parts[2]) if len(parts) > 2 else 0
        days = int(parts[3]) if len(parts) > 3 else 0
    except (IndexError, ValueError):
        await message.answer("❌ Формат: <code>КОД ПРОЦЕНТ [МАКС] [ДНЕЙ]</code>")
        return
    if not (code.isalnum() and 3 <= len(code) <= 24):
        await message.answer("❌ Код — 3–24 буквы или цифры без пробелов и знаков.")
        return
    if not (1 <= percent <= 99) or max_uses < 0 or days < 0:
        await message.answer("❌ Процент от 1 до 99, остальные числа не меньше нуля.")
        return
    if not await db.create_promo(code, percent, max_uses, days):
        await message.answer("❌ Такой промокод уже есть.")
        return
    await state.clear()
    text, markup = await _promo_screen()
    await message.answer(f"✅ Промокод <code>{_esc(code.upper())}</code> создан.\n\n{text}",
                         reply_markup=markup)


@router.callback_query(F.data.startswith("adm:promot:"))
async def cb_promo_toggle(callback: CallbackQuery) -> None:
    await db.toggle_promo(callback.data.split(":", 2)[2])
    text, markup = await _promo_screen()
    await ui.show(callback, text=text, markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:promod:"))
async def cb_promo_delete(callback: CallbackQuery) -> None:
    await db.delete_promo(callback.data.split(":", 2)[2])
    text, markup = await _promo_screen()
    await ui.show(callback, text=text, markup=markup)
    await callback.answer("Удалён")


# --------------------------------------------------------------------------
# Шаблоны, добавленные пользователями
# --------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:utpl:"))
async def cb_user_templates(callback: CallbackQuery) -> None:
    await _show_user_templates(callback, int(callback.data.split(":", 2)[2]))


async def _show_user_templates(callback: CallbackQuery, page: int) -> None:
    items = await db.list_user_templates(include_hidden=True)
    chunk = items[page * TPL_PAGE:(page + 1) * TPL_PAGE]

    lines = [f"🧩 <b>Шаблоны пользователей</b> · {len(items)}\n"]
    rows: List[List[InlineKeyboardButton]] = []
    if not items:
        lines.append("Пока никто ничего не добавил.")
    for item in chunk:
        number = config.USER_TEMPLATE_BASE + int(item["id"])
        mark = "🙈" if item["hidden"] else "👁"
        lines.append(
            f"{mark} <b>#{number}</b> {_esc(item['title'])} · "
            f"<code>{item['owner_id']}</code> · {_date(int(item['created_at']))}"
        )
        rows.append([InlineKeyboardButton(
            text=("👁 Показать #" if item["hidden"] else "🙈 Скрыть #") + str(number),
            callback_data=f"adm:utplt:{item['id']}:{page}",
        )])
    nav: List[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"adm:utpl:{page - 1}"))
    if (page + 1) * TPL_PAGE < len(items):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"adm:utpl:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="◀️ В панель", callback_data="adm:home")])
    await ui.show(callback, text="\n".join(lines), markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:utplt:"))
async def cb_user_template_toggle(callback: CallbackQuery) -> None:
    _, _, template_id, page = callback.data.split(":")
    row = await db.get_user_template(int(template_id))
    if row:
        await db.set_user_template_hidden(int(template_id), not row["hidden"])
    await _show_user_templates(callback, int(page))


# --------------------------------------------------------------------------
# Кристаллы и премиум вручную
# --------------------------------------------------------------------------

async def _find(handle: str) -> Optional[Dict[str, Any]]:
    found = await db.find_users(handle, limit=5)
    for user in found:
        if str(user["user_id"]) == handle or \
                str(user.get("username") or "").lower() == handle.lstrip("@").lower():
            return user
    return None


@router.message(Command("crystals"))
async def cmd_crystals(message: Message) -> None:
    """Выдача кристаллов: /crystals @username 50 (минус — снять)."""
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Использование: <code>/crystals @username 50</code>")
        return
    try:
        amount = int(parts[2])
    except ValueError:
        await message.answer("❌ Третий аргумент — число кристаллов.")
        return
    user = await _find(parts[1])
    if not user:
        await message.answer(f"❌ Не нашёл <code>{_esc(parts[1])}</code>.")
        return
    total = await db.add_crystals(int(user["user_id"]), amount)
    await message.answer(f"💎 Готово. У <code>{user['user_id']}</code> теперь <b>{total}</b> кристаллов.")


@router.message(Command("premium"))
async def cmd_premium(message: Message) -> None:
    """Выдача премиума: /premium @username 30 (дней)."""
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Использование: <code>/premium @username 30</code> (дней)")
        return
    try:
        days = int(parts[2])
    except ValueError:
        await message.answer("❌ Третий аргумент — число дней.")
        return
    user = await _find(parts[1])
    if not user:
        await message.answer(f"❌ Не нашёл <code>{_esc(parts[1])}</code>.")
        return
    until = await db.grant_premium(int(user["user_id"]), days)
    await message.answer(
        f"💎 Премиум у <code>{user['user_id']}</code> действует до <b>{_date(until)}</b>."
    )
