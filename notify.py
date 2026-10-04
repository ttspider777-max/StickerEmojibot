"""Уведомления админам о деньгах: покупки и пополнения баланса.

Отдельный модуль, потому что зовут его и пользовательская часть, и
админка, а знать друг о друге они не должны.
"""

from __future__ import annotations

import html
import logging
from typing import Any, Dict, Optional

from aiogram import Bot
from aiogram.types import User

import config

log = logging.getLogger("emoji-bot.notify")


def _who(user: Optional[User]) -> str:
    if user is None:
        return "—"
    handle = f"@{user.username}" if user.username else html.escape(user.full_name or "")
    return f"{handle} (<code>{user.id}</code>)"


async def to_admins(bot: Bot, text: str) -> None:
    """Шлёт текст всем админам.

    Ошибку доставки глушим: админ мог не запускать бота, и падать из-за
    этого посреди выдачи оплаченного заказа нельзя.
    """
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception as exc:
            log.warning("Уведомление админу %s не ушло: %s", admin_id, exc)


async def purchase(bot: Bot, user: Optional[User], order: Dict[str, Any],
                   source: str, balance: Optional[int] = None) -> None:
    """Покупка набора: кто, сколько эмодзи, на сколько звёзд и чем платил."""
    method = {
        "balance": "с баланса", "crystals": "кристаллами", "admin": "бесплатно (админ)",
    }.get(source, "звёздами")
    if source == "crystals":
        amount_line = f"💎 Кристаллов: <b>{order.get('crystals', 0)}</b> ({method})\n"
    else:
        amount_line = f"⭐️ Сумма: <b>{order['amount']}</b> ({method})\n"
    tail = f"\n💼 Остаток баланса: <b>{balance}</b> ⭐️" if balance is not None else ""
    await to_admins(
        bot,
        "🧾 <b>Покупка</b>\n\n"
        f"👤 {_who(user)}\n"
        f"✨ Эмодзи: <b>{len(order['numbers'])}</b>\n"
        f"{amount_line}"
        f"📦 Заказ: <code>#{order['id']}</code>"
        f"{tail}",
    )


async def topup(bot: Bot, user: Optional[User], amount: int, balance: int) -> None:
    """Пополнение баланса."""
    await to_admins(
        bot,
        "💰 <b>Пополнение баланса</b>\n\n"
        f"👤 {_who(user)}\n"
        f"⭐️ Сумма: <b>+{amount}</b>\n"
        f"💼 Стало: <b>{balance}</b>",
    )


async def granted(bot: Bot, admin: Optional[User], target_id: int,
                  amount: int, balance: int) -> None:
    """Выдача звёзд админом — видят все админы, не только тот, кто выдал."""
    verb = "выдал" if amount >= 0 else "снял"
    await to_admins(
        bot,
        "🎁 <b>Выдача звёзд</b>\n\n"
        f"👮 {_who(admin)} {verb} <b>{abs(amount)}</b> ⭐️\n"
        f"👤 Кому: <code>{target_id}</code>\n"
        f"💼 Стало: <b>{balance}</b>",
    )


async def stuck_order(bot: Bot, user: Optional[User], order: Dict[str, Any],
                      retry_after: int) -> None:
    """Оплаченный заказ, который не собрался за все автоповторы.

    Такое нельзя оставлять только в логе: человек заплатил и остался
    без набора, и кто-то должен об этом узнать раньше, чем он придёт
    в поддержку сам.
    """
    await to_admins(
        bot,
        "🆘 <b>Заказ завис</b>\n\n"
        f"👤 {_who(user)}\n"
        f"🧾 Заказ: <code>#{order['id']}</code> — {len(order['numbers'])} шт, "
        f"{order['amount']} ⭐️\n"
        f"⏳ Telegram просит ещё {max(1, round(retry_after / 60))} мин\n\n"
        "Оплата прошла, набор не выдан. У человека есть кнопка "
        "«Повторить сборку»; если не поможет — вернуть звёзды из карточки "
        "заказа.\n\n"
        "<i>Если такое повторяется на всех заказах подряд: лимит Telegram "
        "на создание наборов считается по аккаунту, для которого набор "
        "создаётся, а не по боту. Проверьте заказ с другого аккаунта — "
        "у него счётчик свой.</i>",
    )
