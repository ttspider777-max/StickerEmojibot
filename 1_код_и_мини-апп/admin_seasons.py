"""Админка сезонов: запуск, досрочное завершение, чаты, быстрый просмотр топа."""

from __future__ import annotations

import html
import time
from typing import Any, List

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import db
import seasons
import ui

router = Router(name="admin-seasons")
router.message.filter(F.from_user.id.in_(config.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(config.ADMIN_IDS))

class SeasonAdmin(StatesGroup):
    nft = State()


_STATE = {"planned": "⏳ ожидает", "active": "🟢 идёт", "finished": "✅ завершён"}


def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


def _dt(ts: int) -> str:
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts)) if ts else "—"


async def _screen():
    all_rows = {int(s["num"]): s for s in await seasons.all_seasons()}
    cur = seasons.active()
    lines = [f"🏆 <b>Сезоны</b> · всего {seasons.TOTAL_SEASONS} по {seasons.SEASON_DAYS} дней\n"]
    for n in range(1, seasons.TOTAL_SEASONS + 1):
        row = all_rows.get(n)
        status = row["status"] if row else "planned"
        extra = ""
        if row and status == "active":
            extra = f" · до {_dt(int(row['ends_at']))}"
        elif row and status == "finished":
            extra = f" · закрыт {_dt(int(row['finished_at']))}"
        lines.append(f"{n}. {_STATE[status]}{extra}")

    if cur:
        top = await seasons.standings(int(cur["num"]), limit=5)
        lines.append(f"\n<b>Сезон {cur['num']}</b> — участников с очками: {len(await seasons.standings(int(cur['num'])))}")
        for r in top:
            lines.append(f"{seasons.MEDALS.get(r['place'], str(r['place']) + '.')} {_esc(r['name'])} — {int(r['points'])}")
    chat_ids = seasons.chats()
    lines.append(
        "\n💬 <b>Чаты сезона:</b> " + (", ".join(f"<code>{c}</code>" for c in chat_ids) if chat_ids else "не подключены")
    )
    lines.append(
        "<i>Чтобы подключить чат (и чат обсуждения канала), напиши в нём команду "
        "<code>/season_chat</code> от своего аккаунта; <code>/season_chat off</code> — отключить. "
        "Для подсчёта реакций бот должен быть админом чата.</i>"
    )
    target = await seasons.target_season()
    nft_raw = await seasons.get_nft(target)
    given = (await db.get_settings()).get(f"season_nft_given_{target}") == "1"
    lines.append(
        f"\n🎁 Приз: топ-{seasons.PRIZE_WINNERS} — по {seasons.PRIZE_GENERATIONS} генераций "
        f"({seasons.PRIZE_GENERATIONS * config.CRYSTALS_PER_EMOJI} 💎, начисляет бот)."
    )
    lines.append(
        f"🥇 NFT сезона {target}: " + (seasons.nft_html(nft_raw) if nft_raw else "<b>не задан</b>")
        + (" · ✅ выдан" if given else "")
        + "\n<i>NFT выдаёшь ты сам: бот только объявит победителя и напомнит тебе.</i>"
    )

    rows: List[List[InlineKeyboardButton]] = []
    if cur:
        rows.append([InlineKeyboardButton(text="⏹ Завершить сейчас", callback_data="adm:seasonend")])
    else:
        nxt = await seasons.next_number()
        if nxt <= seasons.TOTAL_SEASONS:
            rows.append([InlineKeyboardButton(text=f"▶️ Запустить сезон {nxt}", callback_data="adm:seasonstart")])
    rows.append([InlineKeyboardButton(text=f"🎁 NFT для сезона {target}", callback_data="adm:seasonnft")])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="adm:seasons")])
    rows.append([InlineKeyboardButton(text="◀️ В панель", callback_data="adm:home")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "adm:seasons")
async def cb_seasons(callback: CallbackQuery) -> None:
    text, markup = await _screen()
    await ui.show(callback, text=text, markup=markup)
    await callback.answer()


def _confirm(text: str, yes: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data=yes)],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data="adm:seasons")],
    ])


@router.callback_query(F.data == "adm:seasonstart")
async def cb_start(callback: CallbackQuery) -> None:
    nxt = await seasons.next_number()
    await ui.show(
        callback,
        text=(f"▶️ <b>Запустить сезон {nxt}?</b>\n\nОн продлится {seasons.SEASON_DAYS} дней. "
              "Очки начнут копиться сразу, по окончании бот сам подведёт итоги и сделает рассылку."),
        markup=_confirm("", "adm:seasonstart:ok"),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:seasonstart:ok")
async def cb_start_ok(callback: CallbackQuery) -> None:
    result = await seasons.start_next()
    await callback.answer("Сезон запущен" if result["ok"] else result["error"], show_alert=not result["ok"])
    text, markup = await _screen()
    await ui.show(callback, text=text, markup=markup)


@router.callback_query(F.data == "adm:seasonend")
async def cb_end(callback: CallbackQuery) -> None:
    await ui.show(
        callback,
        text=("⏹ <b>Завершить сезон сейчас?</b>\n\nБудут подведены итоги, начислены призы и "
              "сразу отправлена рассылка всем пользователям. Отменить нельзя."),
        markup=_confirm("", "adm:seasonend:ok"),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:seasonend:ok")
async def cb_end_ok(callback: CallbackQuery) -> None:
    result = await seasons.finish(callback.bot)
    await callback.answer("Сезон завершён, идёт рассылка" if result["ok"] else result["error"], show_alert=not result["ok"])
    text, markup = await _screen()
    await ui.show(callback, text=text, markup=markup)


@router.message(Command("season_chat"))
async def cmd_season_chat(message: Message) -> None:
    """Подключает чат, в котором написана команда, к подсчёту сезона."""
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Напиши эту команду в чате, который нужно подключить.")
        return
    off = "off" in (message.text or "").lower().split()[1:]
    if off:
        await seasons.remove_chat(message.chat.id)
        await message.answer("🔕 Чат отключён от сезонов.")
    else:
        await seasons.add_chat(message.chat.id)
        await message.answer(
            "✅ Чат подключён: сообщения и реакции участников идут в сезонный зачёт.\n"
            "Для подсчёта реакций сделай бота администратором."
        )


@router.callback_query(F.data == "adm:seasonnft")
async def cb_nft(callback: CallbackQuery, state: FSMContext) -> None:
    target = await seasons.target_season()
    await state.set_state(SeasonAdmin.nft)
    await state.update_data(season=target)
    current = await seasons.get_nft(target)
    now = f"\n\nСейчас: {seasons.nft_html(current)}" if current else ""
    await ui.show(
        callback,
        text=(
            f"🎁 <b>NFT для сезона {target}</b>\n\n"
            "Пришли одним сообщением название и/или ссылку на NFT-подарок, например:\n"
            "<code>Plush Pepe https://t.me/nft/PlushPepe-1</code>\n\n"
            "Его увидят в мини-приложении и в рассылке итогов. Выдаёшь ты сам, когда сезон закончится.\n"
            "Пришли <code>-</code>, чтобы очистить." + now
        ),
        markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="adm:seasons")],
        ]),
    )
    await callback.answer()


@router.message(SeasonAdmin.nft, F.text)
async def on_nft(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target = int(data.get("season") or await seasons.target_season())
    raw = (message.text or "").strip()
    await seasons.set_nft(target, "" if raw == "-" else raw)
    await state.clear()
    text, markup = await _screen()
    saved = "🧹 NFT очищен." if raw == "-" else f"✅ NFT сезона {target} сохранён: {seasons.nft_html(raw)}"
    await message.answer(f"{saved}\n\n{text}", reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data.startswith("adm:nftgiven:"))
async def cb_given(callback: CallbackQuery) -> None:
    num = int(callback.data.rsplit(":", 1)[1])
    result = await seasons.mark_given(callback.bot, num)
    await callback.answer("Победитель уведомлён" if result["ok"] else result["error"], show_alert=not result["ok"])
    if result["ok"]:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
