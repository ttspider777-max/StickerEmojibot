"""Обязательная подписка: управление каналами из админ-панели.

Каналы добавляются и убираются кнопками, список живёт в базе. При
добавлении бот сразу проверяет, может ли он там читать подписчиков —
без прав администратора канал бесполезен: проверка будет молча
пропускать всех.
"""

from __future__ import annotations

import html
import logging
from typing import Any, List, Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import settings
import ui

log = logging.getLogger("emoji-bot.channels")

router = Router(name="admin-channels")
router.message.filter(F.from_user.id.in_(config.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(config.ADMIN_IDS))


class Channels(StatesGroup):
    adding = State()


def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


def _markup() -> InlineKeyboardMarkup:
    channels = settings.channels()
    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text=f"🗑 {str(channel.get('title'))[:30]}",
            callback_data=f"adm:chdel:{index}",
        )]
        for index, channel in enumerate(channels)
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить канал", callback_data="adm:chadd")])
    if channels:
        on = settings.subscription_on()
        rows.append([InlineKeyboardButton(
            text="🔕 Выключить проверку" if on else "🔔 Включить проверку",
            callback_data="adm:chtoggle",
        )])
        rows.append([InlineKeyboardButton(text="🔍 Проверить права", callback_data="adm:chcheck")])
    rows.append([InlineKeyboardButton(text="◀️ В панель", callback_data="adm:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _screen() -> str:
    channels = settings.channels()
    if not channels:
        state = "выключена — каналов нет"
    elif settings.subscription_on():
        state = "включена"
    else:
        state = "выключена вручную"

    lines = [
        "📣 <b>Обязательная подписка</b>\n",
        f"Статус: <b>{state}</b>",
        f"Каналов: <b>{len(channels)}</b>\n",
    ]
    for channel in channels:
        lines.append(f"• {_esc(channel.get('title'))} — <code>{_esc(channel.get('id'))}</code>")
    if not channels:
        lines.append("<i>Пока пусто: бот пускает всех.</i>")
    lines.append(
        "\n<i>Человек должен быть подписан на все каналы из списка. "
        "Админы проверку не проходят. Кнопка с корзиной убирает канал.</i>"
    )
    return "\n".join(lines)


async def _home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    await ui.show(callback, text=_screen(), markup=_markup())


@router.callback_query(F.data == "adm:channels")
async def cb_channels(callback: CallbackQuery, state: FSMContext) -> None:
    await _home(callback, state)
    await callback.answer()


@router.callback_query(F.data == "adm:chtoggle")
async def cb_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    await settings.set_subscription(not settings.subscription_on())
    await callback.answer("Проверка включена" if settings.subscription_on() else "Проверка выключена")
    await _home(callback, state)


@router.callback_query(F.data.startswith("adm:chdel:"))
async def cb_delete(callback: CallbackQuery, state: FSMContext) -> None:
    index = int(callback.data.rsplit(":", 1)[1])
    channels = settings.channels()
    if 0 <= index < len(channels):
        await settings.remove_channel(channels[index]["id"])
        await callback.answer("Канал убран")
    else:
        await callback.answer("Уже убран")
    await _home(callback, state)


@router.callback_query(F.data == "adm:chcheck")
async def cb_check(callback: CallbackQuery) -> None:
    """Проверяет каждый канал: сможет ли бот там читать подписчиков."""
    import bot as user_bot

    await callback.answer("Проверяю…")
    me = await callback.bot.me()
    lines = ["🔍 <b>Проверка прав</b>\n"]
    for channel in settings.channels():
        ok, reason = await user_bot.check_channel(callback.bot, me.id, channel["id"])
        mark = "✅" if ok else "⚠️"
        lines.append(f"{mark} {_esc(channel.get('title'))}" + ("" if ok else f"\n    <code>{_esc(reason)}</code>"))
    if len(lines) == 1:
        lines.append("Каналов нет.")
    else:
        lines.append(
            "\n<i>⚠️ означает, что канал никого не фильтрует: боту нужны "
            "права администратора в нём.</i>"
        )
    await ui.show(callback, text="\n".join(lines), markup=_markup())


@router.callback_query(F.data == "adm:chadd")
async def cb_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Channels.adding)
    await ui.show(
        callback,
        text=(
            "➕ <b>Добавить канал</b>\n\n"
            "Пришли <code>@username</code> канала, ссылку на него или "
            "<b>перешли сюда любой пост</b> из него.\n\n"
            "<i>Перед этим добавь бота администратором канала — иначе он "
            "не сможет видеть подписчиков, и проверка будет пропускать "
            "всех. Права на публикацию не нужны, достаточно самого "
            "статуса администратора.</i>"
        ),
        markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="adm:channels")],
        ]),
    )
    await callback.answer()


def _extract(message: Message) -> Optional[str]:
    """Достаёт из сообщения то, чем можно адресовать канал."""
    if message.forward_from_chat:
        return str(message.forward_from_chat.id)
    raw = (message.text or "").strip()
    if not raw:
        return None
    if raw.startswith("@"):
        return raw
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if raw.startswith(prefix):
            tail = raw[len(prefix):].strip("/")
            # Приватные ссылки вида t.me/+abc адресовать по имени нельзя.
            return None if tail.startswith("+") else "@" + tail.split("/")[0]
    if raw.lstrip("-").isdigit():
        return raw
    return None


@router.message(Channels.adding)
async def on_add(message: Message, state: FSMContext) -> None:
    target = _extract(message)
    if not target:
        await message.answer(
            "❌ Не понял канал. Пришли <code>@username</code>, ссылку "
            "<code>t.me/…</code> или перешли пост из канала.\n\n"
            "<i>Закрытый канал по ссылке-приглашению добавить нельзя — "
            "перешли из него пост.</i>",
        )
        return

    try:
        chat = await message.bot.get_chat(target)
    except Exception as exc:
        await message.answer(
            f"❌ Канал не найден: <code>{_esc(exc)}</code>\n\n"
            "Проверь, что бот добавлен в канал администратором.",
        )
        return

    me = await message.bot.me()
    import bot as user_bot

    ok, reason = await user_bot.check_channel(message.bot, me.id, chat.id)

    url = f"https://t.me/{chat.username}" if chat.username else (chat.invite_link or "")
    if not url:
        await message.answer(
            "❌ У канала нет ни публичного адреса, ни ссылки-приглашения — "
            "человеку некуда будет перейти.\n\n"
            "<i>Сделай канал публичным или выдай боту право приглашать "
            "и добавь заново.</i>",
        )
        return

    added = await settings.add_channel(chat.id, chat.title or target, url)
    await state.set_state(None)

    if not added:
        await message.answer("Такой канал уже в списке.", reply_markup=_markup())
        return

    note = "" if ok else (
        f"\n\n⚠️ <b>Бот не администратор канала</b> (<code>{_esc(reason)}</code>).\n"
        "Пока это так, проверка будет пропускать всех. Выдай права и "
        "нажми «Проверить права»."
    )
    await message.answer(
        f"✅ Канал <b>{_esc(chat.title)}</b> добавлен.{note}",
        reply_markup=_markup(),
    )
